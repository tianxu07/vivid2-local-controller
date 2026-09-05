"""Shared Tkinter shell with controls selected by supported lamp model."""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Any, Coroutine

from chihiros.constants import (
    COOLING_FAN_MODEL,
    FAN_MANUAL_SPEED_MAX,
    FAN_MANUAL_SPEED_MIN,
    MAGNETIC_II_MODEL,
    MAGNETIC_LIGHT_MODEL,
    WINDOWS_APP_VERSION,
    Z_LIGHT_MODEL,
)
from chihiros.fan_controller import CoolingFanTelemetry
from gui.controller import (
    ApplicationController,
    CompatibleDevice,
    DISPLAY_NAME,
    build_device_choices,
    controls_for_device,
    friendly_error,
    parse_brightness_input,
    parse_fan_speed_input,
    parse_fan_temperature_inputs,
    parse_rg_inputs,
    parse_rgb_inputs,
    parse_wrgb_inputs,
    parse_white_inputs,
    preferred_device,
)

DEFAULT_WINDOW_WIDTH = 620
DEFAULT_WINDOW_HEIGHT = 760
MINIMUM_WINDOW_WIDTH = 560
MINIMUM_WINDOW_HEIGHT = 700


@dataclass(frozen=True)
class WorkerResult:
    job_id: int
    value: Any = None
    error: BaseException | None = None


class AsyncWorker:
    """Run all asyncio/Bleak work on one background event-loop thread."""

    def __init__(self) -> None:
        self.results: queue.Queue[WorkerResult] = queue.Queue()
        self._ready = threading.Event()
        self._closed = False
        self._next_job_id = 1
        self._futures: set[concurrent.futures.Future[Any]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(
            target=self._thread_main,
            name="ChihirosBluetoothWorker",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self._loop is None:
            raise RuntimeError("Bluetooth worker could not start")

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> int:
        if self._closed or self._loop is None:
            coroutine.close()
            raise RuntimeError("Bluetooth worker is closed")
        job_id = self._next_job_id
        self._next_job_id += 1
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        self._futures.add(future)

        def completed(done: concurrent.futures.Future[Any]) -> None:
            self._futures.discard(done)
            try:
                self.results.put(WorkerResult(job_id, value=done.result()))
            except BaseException as exc:
                self.results.put(WorkerResult(job_id, error=exc))

        future.add_done_callback(completed)
        return job_id

    def drain(self) -> list[WorkerResult]:
        items: list[WorkerResult] = []
        while True:
            try:
                items.append(self.results.get_nowait())
            except queue.Empty:
                return items

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        for future in tuple(self._futures):
            future.cancel()
        if self._loop is not None and self._loop.is_running():
            async def finish_cancellations() -> None:
                current = asyncio.current_task()
                pending = [
                    task
                    for task in asyncio.all_tasks()
                    if task is not current and not task.done()
                ]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            cleanup = asyncio.run_coroutine_threadsafe(finish_cancellations(), self._loop)
            try:
                cleanup.result(timeout=6.0)
            except (concurrent.futures.CancelledError, concurrent.futures.TimeoutError):
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=7.0)


class ChihirosApplication:
    POLL_INTERVAL_MS = 75

    def __init__(self, root: tk.Tk, controller: ApplicationController | None = None) -> None:
        self.root = root
        self.controller = controller or ApplicationController()
        self.worker = AsyncWorker()
        self.devices_by_address: dict[str, CompatibleDevice] = {}
        self.device_addresses: tuple[str, ...] = ()
        self._selected_address: str | None = None
        self.active_job_id: int | None = None
        self.active_operation: str | None = None
        self.closing = False

        self.device_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready. Click Scan to find your device.")
        self.red_var = tk.IntVar(value=50)
        self.green_var = tk.IntVar(value=50)
        self.blue_var = tk.IntVar(value=50)
        self.brightness_var = tk.IntVar(value=20)
        # Original Magnetic Light RG requests are isolated by canonical address.
        self.rg_vars = tuple(tk.IntVar(value=20) for _ in range(2))
        self._rg_address: str | None = None
        self._rg_values_by_address: dict[str, tuple[int, ...]] = {}
        # Independent from Vivid II's RGB variables and from other lamps' requests.
        self.wrgb_vars = tuple(tk.IntVar(value=20) for _ in range(4))
        self._wrgb_address: str | None = None
        self._wrgb_values_by_address: dict[str, tuple[int, ...]] = {}
        # Z Light white requests are isolated by canonical address.
        self.white_vars = tuple(tk.IntVar(value=20) for _ in range(2))
        self._white_address: str | None = None
        self._white_values_by_address: dict[str, tuple[int, ...]] = {}
        self.fan_speed_var = tk.IntVar(value=0)
        self.fan_start_temperature_var = tk.StringVar(value="24")
        self.fan_max_temperature_var = tk.StringVar(value="28")
        self.fan_water_temperature_var = tk.StringVar(value="Not read")
        self.fan_room_temperature_var = tk.StringVar(value="Not read")
        self.fan_humidity_var = tk.StringVar(value="Not read")
        self._fan_address: str | None = None
        self._fan_inputs_by_address: dict[str, tuple[int, str, str]] = {}

        self._build_window()
        self._load_saved_device()
        self._refresh_controls()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(self.POLL_INTERVAL_MS, self._poll_worker)

    def _build_window(self) -> None:
        self.root.title(f"{DISPLAY_NAME} {WINDOWS_APP_VERSION}")
        self.root.geometry(f"{DEFAULT_WINDOW_WIDTH}x{DEFAULT_WINDOW_HEIGHT}")
        self.root.minsize(MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT)

        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("Action.TButton", font=("Segoe UI", 11, "bold"), padding=(16, 9))
        style.configure("Status.TLabel", padding=(10, 8))

        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=DISPLAY_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Local control for supported Chihiros lights and DYNFAN Cooling Fan",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 16))

        # Reserve a common footer before packing model-specific content so
        # taller views cannot push the shared attribution outside the window.
        self.footer_frame = ttk.Frame(outer)
        self.footer_frame.pack(side="bottom", fill="x")
        self.smart_plug_label = ttk.Label(
            self.footer_frame,
            text="Use a physical switch or smart plug to turn the light on or off.",
            wraplength=560,
            foreground="#555555",
        )
        self.smart_plug_label.pack(anchor="w", pady=(12, 0))
        self.disclaimer_label = ttk.Label(
            self.footer_frame,
            text="Unofficial community tool. Not affiliated with Chihiros Aquatic Studio.",
            wraplength=560,
            foreground="#666666",
            font=("Segoe UI", 8),
        )
        self.disclaimer_label.pack(anchor="w", pady=(9, 0))
        self.author_label = ttk.Label(
            self.footer_frame,
            text="Created by Tianxu Yang · Instagram: @tianxu_07",
            wraplength=560,
            foreground="#777777",
            font=("Segoe UI", 8),
        )
        self.author_label.pack(anchor="w", pady=(3, 0))

        device_frame = ttk.LabelFrame(outer, text="Device", padding=12)
        device_frame.pack(fill="x")
        button_row = ttk.Frame(device_frame)
        button_row.pack(fill="x")
        self.scan_button = ttk.Button(button_row, text="Scan for Devices", command=self.scan)
        self.scan_button.pack(side="left")

        ttk.Label(device_frame, text="Detected device:").pack(anchor="w", pady=(12, 4))
        self.device_combo = ttk.Combobox(
            device_frame,
            textvariable=self.device_var,
            state="readonly",
            width=66,
        )
        self.device_combo.pack(fill="x")
        self.device_combo.bind("<<ComboboxSelected>>", self._device_selected)

        controls_frame = ttk.Frame(outer)
        controls_frame.pack(fill="x", pady=(14, 0))
        self.rgb_frame = rgb_frame = ttk.LabelFrame(controls_frame, text="Manual RGB brightness", padding=12)
        self.scales: list[tk.Scale] = []
        for row, (label, variable, color) in enumerate(
            (
                ("Red", self.red_var, "#b42318"),
                ("Green", self.green_var, "#16803a"),
                ("Blue", self.blue_var, "#175cd3"),
            )
        ):
            label_widget = tk.Label(
                rgb_frame,
                text=label,
                width=7,
                anchor="w",
                fg=color,
                font=("Segoe UI", 10, "bold"),
            )
            label_widget.grid(row=row, column=0, sticky="w", pady=3)
            scale = tk.Scale(
                rgb_frame,
                from_=0,
                to=100,
                orient="horizontal",
                resolution=1,
                showvalue=True,
                variable=variable,
                length=410,
                highlightthickness=0,
            )
            scale.grid(row=row, column=1, sticky="ew", padx=(8, 0))
            self.scales.append(scale)
        rgb_frame.columnconfigure(1, weight=1)

        self.brightness_frame = ttk.LabelFrame(controls_frame, text="Manual brightness", padding=12)
        ttk.Label(self.brightness_frame, text="Brightness").pack(anchor="w")
        self.brightness_scale = tk.Scale(
            self.brightness_frame, from_=1, to=100, orient="horizontal", resolution=1,
            showvalue=True, variable=self.brightness_var, highlightthickness=0,
        )
        self.brightness_scale.pack(fill="x")
        ttk.Label(
            self.brightness_frame,
            text="DYNCMC candidate: validated on one A2 Max.\nThis slider requests a value; it does not read the current brightness.",
            foreground="#666666", wraplength=490,
        ).pack(anchor="w", pady=(6, 0))

        self.rg_frame = ttk.LabelFrame(controls_frame, text="Manual RG brightness", padding=12)
        self.rg_scales: list[tk.Scale] = []
        for row, (label, variable, color) in enumerate(zip(
            ("Red", "Green"), self.rg_vars, ("#b42318", "#16803a")
        )):
            tk.Label(
                self.rg_frame,
                text=label,
                width=7,
                anchor="w",
                fg=color,
                font=("Segoe UI", 10, "bold"),
            ).grid(row=row, column=0, sticky="w", pady=3)
            scale = tk.Scale(
                self.rg_frame,
                from_=0,
                to=100,
                orient="horizontal",
                resolution=1,
                showvalue=True,
                variable=variable,
                length=410,
                highlightthickness=0,
            )
            scale.grid(row=row, column=1, sticky="ew", padx=(8, 0))
            self.rg_scales.append(scale)
        self.rg_frame.columnconfigure(1, weight=1)
        ttk.Label(
            self.rg_frame,
            text="DYCX support was physically validated on a real Magnetic Light. Requested levels only.",
            foreground="#666666",
            wraplength=490,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.wrgb_frame = ttk.LabelFrame(controls_frame, text="Manual WRGB brightness", padding=12)
        self.wrgb_scales: list[tk.Scale] = []
        for row, (label, variable, color) in enumerate(zip(
            ("Red", "Green", "Blue", "White"), self.wrgb_vars,
            ("#b42318", "#16803a", "#175cd3", "#555555"),
        )):
            tk.Label(self.wrgb_frame, text=label, width=7, anchor="w", fg=color,
                     font=("Segoe UI", 10, "bold")).grid(row=row, column=0, sticky="w", pady=3)
            scale = tk.Scale(self.wrgb_frame, from_=0, to=100, orient="horizontal",
                             resolution=1, showvalue=True, variable=variable,
                             length=410, highlightthickness=0)
            scale.grid(row=row, column=1, sticky="ew", padx=(8, 0))
            self.wrgb_scales.append(scale)
        self.wrgb_frame.columnconfigure(1, weight=1)
        ttk.Label(self.wrgb_frame, text="Requested levels. Apply switches the selected light to manual mode.",
                  foreground="#666666", wraplength=490).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.white_frame = ttk.LabelFrame(controls_frame, text="Manual white brightness", padding=12)
        self.white_scales: list[tk.Scale] = []
        for row, (label, variable, color) in enumerate(zip(
            ("Cool White", "Warm White"), self.white_vars, ("#4267a9", "#9a6a20")
        )):
            tk.Label(
                self.white_frame, text=label, width=11, anchor="w", fg=color,
                font=("Segoe UI", 10, "bold"),
            ).grid(row=row, column=0, sticky="w", pady=3)
            scale = tk.Scale(
                self.white_frame, from_=0, to=100, orient="horizontal", resolution=1,
                showvalue=True, variable=variable, length=390, highlightthickness=0,
            )
            scale.grid(row=row, column=1, sticky="ew", padx=(8, 0))
            self.white_scales.append(scale)
        self.white_frame.columnconfigure(1, weight=1)
        ttk.Label(
            self.white_frame,
            text="DYSSD support was physically validated on a real Z Light. Requested levels only.",
            foreground="#666666", wraplength=490,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.fan_frame = ttk.LabelFrame(controls_frame, text="Cooling Fan", padding=10)
        fan_status_frame = ttk.LabelFrame(self.fan_frame, text="Status", padding=8)
        fan_status_frame.pack(fill="x")
        for row, (label, variable) in enumerate(
            (
                ("Water Temperature:", self.fan_water_temperature_var),
                ("Room Temperature:", self.fan_room_temperature_var),
                ("Humidity:", self.fan_humidity_var),
            )
        ):
            ttk.Label(fan_status_frame, text=label).grid(row=row, column=0, sticky="w", pady=1)
            ttk.Label(fan_status_frame, textvariable=variable).grid(
                row=row, column=1, sticky="w", padx=(10, 0), pady=1
            )
        self.refresh_fan_status_button = ttk.Button(
            fan_status_frame, text="Refresh Status", command=self.refresh_fan_status
        )
        self.refresh_fan_status_button.grid(row=0, column=2, rowspan=3, padx=(20, 0))
        fan_status_frame.columnconfigure(1, weight=1)

        fan_manual_frame = ttk.LabelFrame(self.fan_frame, text="Manual", padding=8)
        fan_manual_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(
            fan_manual_frame,
            text=f"Speed Level: {FAN_MANUAL_SPEED_MIN}–{FAN_MANUAL_SPEED_MAX}",
        ).grid(row=0, column=0, sticky="w")
        self.fan_speed_scale = tk.Scale(
            fan_manual_frame,
            from_=FAN_MANUAL_SPEED_MIN,
            to=FAN_MANUAL_SPEED_MAX,
            orient="horizontal",
            resolution=1,
            showvalue=True,
            variable=self.fan_speed_var,
            length=300,
            highlightthickness=0,
        )
        self.fan_speed_scale.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.apply_fan_manual_button = ttk.Button(
            fan_manual_frame, text="Apply Manual", command=self.apply_fan_manual
        )
        self.apply_fan_manual_button.grid(row=0, column=2)
        fan_manual_frame.columnconfigure(1, weight=1)
        ttk.Label(
            fan_manual_frame,
            text="An explicit speed switches the fan away from autonomous thermostat behavior.",
            foreground="#666666",
            wraplength=500,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        fan_automatic_frame = ttk.LabelFrame(self.fan_frame, text="Automatic", padding=8)
        fan_automatic_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(fan_automatic_frame, text="Start Temperature:").grid(row=0, column=0, sticky="w")
        self.fan_start_temperature_entry = ttk.Entry(
            fan_automatic_frame, textvariable=self.fan_start_temperature_var, width=8
        )
        self.fan_start_temperature_entry.grid(row=0, column=1, sticky="w", padx=(8, 22))
        ttk.Label(fan_automatic_frame, text="Max-Speed Temperature:").grid(
            row=0, column=2, sticky="w"
        )
        self.fan_max_temperature_entry = ttk.Entry(
            fan_automatic_frame, textvariable=self.fan_max_temperature_var, width=8
        )
        self.fan_max_temperature_entry.grid(row=0, column=3, sticky="w", padx=(8, 0))
        self.apply_fan_automatic_button = ttk.Button(
            fan_automatic_frame, text="Apply Automatic", command=self.apply_fan_automatic
        )
        self.apply_fan_automatic_button.grid(row=1, column=0, columnspan=4, pady=(7, 0))
        ttk.Label(
            fan_automatic_frame,
            text=(
                "Locally remembered values; thresholds are not read back. After configuration, "
                "the fan regulates itself and the PC disconnects."
            ),
            foreground="#666666",
            wraplength=500,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(5, 0))

        action_row = ttk.Frame(outer)
        action_row.pack(fill="x", pady=(16, 0))
        self.apply_button = ttk.Button(
            action_row,
            text="Apply RGB",
            style="Action.TButton",
            command=self.apply_rgb,
        )
        self.apply_brightness_button = ttk.Button(
            action_row, text="Apply Brightness", style="Action.TButton", command=self.apply_brightness,
        )
        self.apply_wrgb_button = ttk.Button(
            action_row, text="Apply WRGB", style="Action.TButton", command=self.apply_wrgb,
        )
        self.apply_rg_button = ttk.Button(
            action_row, text="Apply RG", style="Action.TButton", command=self.apply_rg,
        )
        self.apply_white_button = ttk.Button(
            action_row, text="Apply White", style="Action.TButton", command=self.apply_white,
        )

        status_frame = ttk.Frame(self.root, relief="sunken")
        status_frame.pack(side="bottom", fill="x")
        self.status_label = ttk.Label(
            status_frame,
            textvariable=self.status_var,
            style="Status.TLabel",
        )
        self.status_label.pack(anchor="w")

    def _load_saved_device(self) -> None:
        saved = self.controller.preferences.load()
        if saved is None:
            return
        self._set_devices((saved,))
        self._select_address(saved.identity)
        self.status_var.set(f"Saved {saved.model} selected. Click Scan to confirm it is nearby.")

    def _refresh_controls(self) -> None:
        """Selection changes only visibility; they never submit a BLE operation."""
        if self._rg_address is not None:
            self._rg_values_by_address[self._rg_address] = tuple(v.get() for v in self.rg_vars)
        if self._wrgb_address is not None:
            self._wrgb_values_by_address[self._wrgb_address] = tuple(v.get() for v in self.wrgb_vars)
        if self._white_address is not None:
            self._white_values_by_address[self._white_address] = tuple(v.get() for v in self.white_vars)
        if self._fan_address is not None:
            self._fan_inputs_by_address[self._fan_address] = (
                self.fan_speed_var.get(),
                self.fan_start_temperature_var.get(),
                self.fan_max_temperature_var.get(),
            )
        device = self._selected_device()
        self._rg_address = device.identity if device and device.model == MAGNETIC_LIGHT_MODEL else None
        if self._rg_address is not None:
            for variable, value in zip(
                self.rg_vars, self._rg_values_by_address.get(self._rg_address, (20, 20))
            ):
                variable.set(value)
        self._wrgb_address = device.identity if device and device.model == MAGNETIC_II_MODEL else None
        if self._wrgb_address is not None:
            for variable, value in zip(self.wrgb_vars, self._wrgb_values_by_address.get(self._wrgb_address, (20, 20, 20, 20))):
                variable.set(value)
        self._white_address = device.identity if device and device.model == Z_LIGHT_MODEL else None
        if self._white_address is not None:
            for variable, value in zip(
                self.white_vars, self._white_values_by_address.get(self._white_address, (20, 20))
            ):
                variable.set(value)
        self._fan_address = device.identity if device and device.model == COOLING_FAN_MODEL else None
        if self._fan_address is not None:
            state = self.controller.fan_state(device)
            speed, start, maximum = self._fan_inputs_by_address.get(
                self._fan_address,
                (state.manual_speed, str(state.start_temperature), str(state.max_temperature)),
            )
            self.fan_speed_var.set(speed)
            self.fan_start_temperature_var.set(start)
            self.fan_max_temperature_var.set(maximum)
            self._show_fan_telemetry(state.telemetry)
        self.rgb_frame.pack_forget()
        self.brightness_frame.pack_forget()
        self.rg_frame.pack_forget()
        self.wrgb_frame.pack_forget()
        self.white_frame.pack_forget()
        self.fan_frame.pack_forget()
        self.apply_button.pack_forget()
        self.apply_brightness_button.pack_forget()
        self.apply_rg_button.pack_forget()
        self.apply_wrgb_button.pack_forget()
        self.apply_white_button.pack_forget()
        if device is not None and device.model == COOLING_FAN_MODEL:
            self.smart_plug_label.pack_forget()
        elif not self.smart_plug_label.winfo_ismapped():
            self.smart_plug_label.pack(anchor="w", pady=(12, 0), before=self.disclaimer_label)
        controls = controls_for_device(device)
        if controls == ("Red", "Green", "Blue"):
            self.rgb_frame.pack(fill="x")
            self.apply_button.pack(fill="x", expand=True, padx=(90, 90))
        elif controls == ("Brightness",):
            self.brightness_frame.pack(fill="x")
            self.apply_brightness_button.pack(fill="x", expand=True, padx=(90, 90))
        elif controls == ("Red", "Green"):
            self.rg_frame.pack(fill="x")
            self.apply_rg_button.pack(fill="x", expand=True, padx=(90, 90))
        elif controls == ("Red", "Green", "Blue", "White"):
            self.wrgb_frame.pack(fill="x")
            self.apply_wrgb_button.pack(fill="x", expand=True, padx=(90, 90))
        elif controls == ("Cool White", "Warm White"):
            self.white_frame.pack(fill="x")
            self.apply_white_button.pack(fill="x", expand=True, padx=(90, 90))
        elif controls == ("Fan",):
            self.fan_frame.pack(fill="x")

    def _show_fan_telemetry(self, telemetry: CoolingFanTelemetry | None) -> None:
        if telemetry is None:
            values = ("Not read",) * 3
        else:
            values = (
                f"{telemetry.water_temperature_c:.1f} °C",
                f"{telemetry.room_temperature_c:.2f} °C",
                f"{telemetry.humidity_percent:.2f}%",
            )
        for variable, value in zip(
            (
                self.fan_water_temperature_var,
                self.fan_room_temperature_var,
                self.fan_humidity_var,
            ),
            values,
        ):
            variable.set(value)

    def _set_devices(self, devices: tuple[CompatibleDevice, ...]) -> None:
        choices = build_device_choices(devices)
        self._selected_address = None
        self.device_var.set("")
        self.devices_by_address = choices.devices_by_address
        self.device_addresses = choices.addresses
        self.device_combo.configure(values=choices.labels)

    def _select_address(self, address: str) -> None:
        """Select a known canonical identity, without matching any display text."""
        index = self.device_addresses.index(address)
        self.device_combo.current(index)
        self._selected_address = self.device_addresses[index]

    def _selected_device(self) -> CompatibleDevice | None:
        return self.devices_by_address.get(self._selected_address)

    def _require_device(self) -> CompatibleDevice | None:
        device = self._selected_device()
        if device is None:
            message = "Select a supported device first. Click Scan to find nearby devices."
            self.status_var.set(message)
            messagebox.showinfo("Select a device", message, parent=self.root)
        return device

    def _set_busy(self, busy: bool) -> None:
        button_state = "disabled" if busy else "normal"
        combo_state = "disabled" if busy else "readonly"
        self.scan_button.configure(state=button_state)
        self.apply_button.configure(state=button_state)
        self.apply_brightness_button.configure(state=button_state)
        self.apply_rg_button.configure(state=button_state)
        self.apply_wrgb_button.configure(state=button_state)
        self.apply_white_button.configure(state=button_state)
        self.refresh_fan_status_button.configure(state=button_state)
        self.apply_fan_manual_button.configure(state=button_state)
        self.apply_fan_automatic_button.configure(state=button_state)
        self.device_combo.configure(state=combo_state)
        for scale in (
            *self.scales,
            self.brightness_scale,
            *self.rg_scales,
            *self.wrgb_scales,
            *self.white_scales,
            self.fan_speed_scale,
        ):
            scale.configure(state=button_state)
        self.fan_start_temperature_entry.configure(state=button_state)
        self.fan_max_temperature_entry.configure(state=button_state)

    def _submit(self, operation: str, coroutine: Coroutine[Any, Any, Any]) -> None:
        if self.active_job_id is not None:
            coroutine.close()
            return
        self._set_busy(True)
        self.active_operation = operation
        try:
            self.active_job_id = self.worker.submit(coroutine)
        except BaseException as exc:
            self.active_operation = None
            self.active_job_id = None
            self._set_busy(False)
            self._show_error(exc, operation)

    def scan(self) -> None:
        self.status_var.set("Scanning for supported Chihiros devices…")
        self._submit("scan", self.controller.scan())

    def apply_rgb(self) -> None:
        device = self._require_device()
        if device is None:
            return
        try:
            red, green, blue = parse_rgb_inputs(
                self.red_var.get(), self.green_var.get(), self.blue_var.get()
            )
        except BaseException as exc:
            self._show_error(exc, "apply_rgb")
            return
        self.status_var.set("Connecting and applying RGB…")
        self._submit(
            "apply_rgb",
            self.controller.apply_rgb(device, red, green, blue),
        )

    def apply_brightness(self) -> None:
        device = self._require_device()
        if device is None:
            return
        try:
            level = parse_brightness_input(self.brightness_var.get())
        except BaseException as exc:
            self._show_error(exc, "apply_brightness")
            return
        self.status_var.set("Connecting and applying brightness…")
        self._submit("apply_brightness", self.controller.apply_brightness(device, level))

    def apply_wrgb(self) -> None:
        device = self._require_device()
        if device is None:
            return
        try:
            levels = parse_wrgb_inputs(*(variable.get() for variable in self.wrgb_vars))
        except BaseException as exc:
            self._show_error(exc, "apply_wrgb")
            return
        self.status_var.set("Connecting and applying WRGB…")
        self._submit("apply_wrgb", self.controller.apply_wrgb(device, *levels))

    def apply_rg(self) -> None:
        device = self._require_device()
        if device is None:
            return
        try:
            levels = parse_rg_inputs(*(variable.get() for variable in self.rg_vars))
        except BaseException as exc:
            self._show_error(exc, "apply_rg")
            return
        self.status_var.set("Connecting and applying RG…")
        self._submit("apply_rg", self.controller.apply_rg(device, *levels))

    def apply_white(self) -> None:
        device = self._require_device()
        if device is None:
            return
        try:
            levels = parse_white_inputs(*(variable.get() for variable in self.white_vars))
        except BaseException as exc:
            self._show_error(exc, "apply_white")
            return
        self.status_var.set("Connecting and applying white channels…")
        self._submit("apply_white", self.controller.apply_white(device, *levels))

    def refresh_fan_status(self) -> None:
        device = self._require_device()
        if device is None:
            return
        self.status_var.set("Connecting briefly to refresh Cooling Fan status…")
        self._submit("refresh_fan_status", self.controller.refresh_fan_status(device))

    def apply_fan_manual(self) -> None:
        device = self._require_device()
        if device is None:
            return
        try:
            speed = parse_fan_speed_input(self.fan_speed_var.get())
        except BaseException as exc:
            self._show_error(exc, "apply_fan_manual")
            return
        self.status_var.set("Connecting and applying manual fan speed level…")
        self._submit("apply_fan_manual", self.controller.apply_fan_manual(device, speed))

    def apply_fan_automatic(self) -> None:
        device = self._require_device()
        if device is None:
            return
        try:
            start_temperature, max_temperature = parse_fan_temperature_inputs(
                self.fan_start_temperature_var.get(), self.fan_max_temperature_var.get()
            )
        except BaseException as exc:
            self._show_error(exc, "apply_fan_automatic")
            return
        self.status_var.set("Configuring the fan's device-side thermostat…")
        self._submit(
            "apply_fan_automatic",
            self.controller.apply_fan_automatic(
                device, start_temperature, max_temperature
            ),
        )

    def _device_selected(self, _event: object = None) -> None:
        index = self.device_combo.current()
        self._selected_address = self.device_addresses[index] if 0 <= index < len(self.device_addresses) else None
        self._refresh_controls()
        device = self._selected_device()
        if device is None:
            return
        try:
            self.controller.preferences.save(device)
            self.status_var.set(f"Selected {device.model} — {device.name}. Ready.")
        except OSError as exc:
            self.controller.logger.exception("preference_save_failed")
            self._show_error(exc, "save_selection")

    def _poll_worker(self) -> None:
        if self.closing:
            return
        for result in self.worker.drain():
            if result.job_id != self.active_job_id:
                continue
            operation = self.active_operation or "operation"
            self.active_job_id = None
            self.active_operation = None
            self._set_busy(False)
            if result.error is not None:
                self._show_error(result.error, operation)
            elif operation == "scan":
                self._scan_completed(result.value)
            elif operation == "apply_rgb":
                self.status_var.set(
                    f"RGB applied successfully: R={self.red_var.get()} "
                    f"G={self.green_var.get()} B={self.blue_var.get()}"
                )
            elif operation == "apply_brightness":
                self.status_var.set(f"Brightness {self.brightness_var.get()} sent. Verify the visible result.")
            elif operation == "apply_wrgb":
                self.status_var.set("WRGB applied to the selected Magnetic Light II. Verify the visible result.")
            elif operation == "apply_rg":
                self.status_var.set("RG applied to the selected Magnetic Light. Verify the visible result.")
            elif operation == "apply_white":
                self.status_var.set("White channels applied to the selected Z Light. Verify the visible result.")
            elif operation == "refresh_fan_status":
                telemetry, _log_path = result.value
                self._show_fan_telemetry(telemetry)
                self.status_var.set("Cooling Fan status refreshed; the BLE connection is closed.")
            elif operation == "apply_fan_manual":
                self.status_var.set(
                    f"Manual fan device level {self.fan_speed_var.get()} applied; "
                    "the BLE connection is closed."
                )
            elif operation == "apply_fan_automatic":
                self.status_var.set(
                    "Automatic thermostat configured. The fan now regulates itself with the PC disconnected."
                )
        self.root.after(self.POLL_INTERVAL_MS, self._poll_worker)

    def _scan_completed(self, devices: tuple[CompatibleDevice, ...]) -> None:
        saved = self.controller.preferences.load()
        self._set_devices(devices)
        chosen = preferred_device(tuple(self.devices_by_address.values()), saved)
        if chosen is not None:
            self._select_address(chosen.identity)
            try:
                self.controller.preferences.save(chosen)
            except OSError:
                self.controller.logger.exception("preference_save_failed")
        self._refresh_controls()
        if not devices:
            message = "No supported device was found. Move closer and scan again."
            self.status_var.set(message)
            messagebox.showinfo("No supported device found", message, parent=self.root)
        elif chosen is not None:
            self.status_var.set(f"Found and selected {chosen.name}. Ready.")
        else:
            self.status_var.set(f"Found {len(devices)} supported devices. Choose one from the list.")

    def _show_error(self, error: BaseException, operation: str) -> None:
        message = friendly_error(error, operation)
        self.status_var.set(f"Error: {message}")
        messagebox.showerror(DISPLAY_NAME, message, parent=self.root)

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.status_var.set("Disconnecting and closing…")
        self.worker.shutdown()
        self.controller.close()
        self.root.destroy()


# Source compatibility for existing launchers and Vivid II regression tests.
Vivid2Application = ChihirosApplication


def main() -> int:
    root = tk.Tk()
    ChihirosApplication(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
