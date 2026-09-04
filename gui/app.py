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

from chihiros.constants import WINDOWS_APP_VERSION
from gui.controller import (
    ApplicationController,
    CompatibleDevice,
    DISPLAY_NAME,
    build_device_choices,
    controls_for_device,
    friendly_error,
    parse_brightness_input,
    parse_rgb_inputs,
    preferred_device,
)

DEFAULT_WINDOW_WIDTH = 620
DEFAULT_WINDOW_HEIGHT = 600
MINIMUM_WINDOW_WIDTH = 560
MINIMUM_WINDOW_HEIGHT = 600


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
        self.status_var = tk.StringVar(value="Ready. Click Scan to find your light.")
        self.red_var = tk.IntVar(value=50)
        self.green_var = tk.IntVar(value=50)
        self.blue_var = tk.IntVar(value=50)
        self.brightness_var = tk.IntVar(value=20)

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
            text="Local manual control for RGB Vivid II and A2 Max",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 16))

        device_frame = ttk.LabelFrame(outer, text="Device", padding=12)
        device_frame.pack(fill="x")
        button_row = ttk.Frame(device_frame)
        button_row.pack(fill="x")
        self.scan_button = ttk.Button(button_row, text="Scan for Lights", command=self.scan)
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

        self.smart_plug_label = ttk.Label(
            outer,
            text="Use a physical switch or smart plug to turn the light on or off.",
            wraplength=560,
            foreground="#555555",
        )
        self.smart_plug_label.pack(anchor="w", pady=(12, 0))
        self.disclaimer_label = ttk.Label(
            outer,
            text="Unofficial community tool. Not affiliated with Chihiros Aquatic Studio.",
            wraplength=560,
            foreground="#666666",
            font=("Segoe UI", 8),
        )
        self.disclaimer_label.pack(anchor="w", pady=(9, 0))
        self.author_label = ttk.Label(
            outer,
            text="Created by Tianxu Yang · Instagram: @tianxu_07",
            wraplength=560,
            foreground="#777777",
            font=("Segoe UI", 8),
        )
        self.author_label.pack(anchor="w", pady=(3, 0))

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
        self.rgb_frame.pack_forget()
        self.brightness_frame.pack_forget()
        self.apply_button.pack_forget()
        self.apply_brightness_button.pack_forget()
        controls = controls_for_device(self._selected_device())
        if controls == ("Red", "Green", "Blue"):
            self.rgb_frame.pack(fill="x")
            self.apply_button.pack(fill="x", expand=True, padx=(90, 90))
        elif controls == ("Brightness",):
            self.brightness_frame.pack(fill="x")
            self.apply_brightness_button.pack(fill="x", expand=True, padx=(90, 90))

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
            message = "Select a supported light first. Click Scan to find nearby lights."
            self.status_var.set(message)
            messagebox.showinfo("Select a light", message, parent=self.root)
        return device

    def _set_busy(self, busy: bool) -> None:
        button_state = "disabled" if busy else "normal"
        combo_state = "disabled" if busy else "readonly"
        self.scan_button.configure(state=button_state)
        self.apply_button.configure(state=button_state)
        self.apply_brightness_button.configure(state=button_state)
        self.device_combo.configure(state=combo_state)
        for scale in (*self.scales, self.brightness_scale):
            scale.configure(state=button_state)

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
        self.status_var.set("Scanning for supported Chihiros lights…")
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
            message = "No supported light was found. Move closer and scan again."
            self.status_var.set(message)
            messagebox.showinfo("No supported light found", message, parent=self.root)
        elif chosen is not None:
            self.status_var.set(f"Found and selected {chosen.name}. Ready.")
        else:
            self.status_var.set(f"Found {len(devices)} supported lights. Choose one from the list.")

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
