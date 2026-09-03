"""Responsive Tkinter front end for the Vivid II-only application."""

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
    friendly_error,
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
            name="Vivid2BluetoothWorker",
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


class Vivid2Application:
    POLL_INTERVAL_MS = 75

    def __init__(self, root: tk.Tk, controller: ApplicationController | None = None) -> None:
        self.root = root
        self.controller = controller or ApplicationController()
        self.worker = AsyncWorker()
        self.devices_by_label: dict[str, CompatibleDevice] = {}
        self.active_job_id: int | None = None
        self.active_operation: str | None = None
        self.closing = False

        self.device_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready. Click Scan to find your light.")
        self.red_var = tk.IntVar(value=50)
        self.green_var = tk.IntVar(value=50)
        self.blue_var = tk.IntVar(value=50)

        self._build_window()
        self._load_saved_device()
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
            text="Safe, account-free manual color control for RGB Vivid II lights.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 16))

        device_frame = ttk.LabelFrame(outer, text="Device", padding=12)
        device_frame.pack(fill="x")
        button_row = ttk.Frame(device_frame)
        button_row.pack(fill="x")
        self.scan_button = ttk.Button(button_row, text="Scan for Vivid II", command=self.scan)
        self.scan_button.pack(side="left")
        self.forget_button = ttk.Button(
            button_row,
            text="Forget Device",
            command=self.forget_device,
        )
        self.forget_button.pack(side="left", padx=(8, 0))

        ttk.Label(device_frame, text="Detected device:").pack(anchor="w", pady=(12, 4))
        self.device_combo = ttk.Combobox(
            device_frame,
            textvariable=self.device_var,
            state="readonly",
            width=66,
        )
        self.device_combo.pack(fill="x")
        self.device_combo.bind("<<ComboboxSelected>>", self._device_selected)

        rgb_frame = ttk.LabelFrame(outer, text="Manual RGB brightness", padding=12)
        rgb_frame.pack(fill="x", pady=(14, 0))
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

        action_row = ttk.Frame(outer)
        action_row.pack(fill="x", pady=(16, 0))
        self.apply_button = ttk.Button(
            action_row,
            text="Apply RGB",
            style="Action.TButton",
            command=self.apply_rgb,
        )
        self.apply_button.pack(fill="x", expand=True, padx=(90, 90))

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
            self.forget_button.configure(state="disabled")
            return
        self.devices_by_label = {saved.label: saved}
        self.device_combo.configure(values=(saved.label,))
        self.device_var.set(saved.label)
        self.forget_button.configure(state="normal")
        self.status_var.set("Saved Vivid II selected. Click Scan to confirm it is nearby.")

    def _selected_device(self) -> CompatibleDevice | None:
        return self.devices_by_label.get(self.device_var.get())

    def _require_device(self) -> CompatibleDevice | None:
        device = self._selected_device()
        if device is None:
            message = "Select a supported RGB Vivid II first. Click Scan to find nearby lights."
            self.status_var.set(message)
            messagebox.showinfo("Select a light", message, parent=self.root)
        return device

    def _set_busy(self, busy: bool) -> None:
        button_state = "disabled" if busy else "normal"
        combo_state = "disabled" if busy else "readonly"
        self.scan_button.configure(state=button_state)
        self.apply_button.configure(state=button_state)
        self.device_combo.configure(state=combo_state)
        for scale in self.scales:
            scale.configure(state=button_state)
        if busy:
            self.forget_button.configure(state="disabled")
        else:
            self.forget_button.configure(
                state="normal" if self.controller.preferences.load() is not None else "disabled"
            )

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
        self.status_var.set("Scanning for nearby RGB Vivid II lights…")
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

    def forget_device(self) -> None:
        if self.active_job_id is not None:
            return
        self.controller.preferences.forget()
        self.devices_by_label.clear()
        self.device_combo.configure(values=())
        self.device_var.set("")
        self.forget_button.configure(state="disabled")
        self.status_var.set("Saved device forgotten. Click Scan to choose a light.")

    def _device_selected(self, _event: object = None) -> None:
        device = self._selected_device()
        if device is None:
            return
        try:
            self.controller.preferences.save(device)
            self.forget_button.configure(state="normal")
            self.status_var.set(f"Selected {device.name}. Ready to apply RGB.")
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
        self.root.after(self.POLL_INTERVAL_MS, self._poll_worker)

    def _scan_completed(self, devices: tuple[CompatibleDevice, ...]) -> None:
        saved = self.controller.preferences.load()
        self.devices_by_label = {device.label: device for device in devices}
        labels = tuple(self.devices_by_label)
        self.device_combo.configure(values=labels)
        chosen = preferred_device(devices, saved)
        if chosen is None:
            self.device_var.set("")
        else:
            self.device_var.set(chosen.label)
            try:
                self.controller.preferences.save(chosen)
            except OSError:
                self.controller.logger.exception("preference_save_failed")
        self.forget_button.configure(
            state="normal" if self.controller.preferences.load() is not None else "disabled"
        )
        if not devices:
            message = "No supported RGB Vivid II was found. Move closer and scan again."
            self.status_var.set(message)
            messagebox.showinfo("No Vivid II found", message, parent=self.root)
        elif chosen is not None:
            self.status_var.set(f"Found and selected {chosen.name}. Ready.")
        else:
            self.status_var.set(f"Found {len(devices)} Vivid II lights. Choose one from the list.")

    def _show_error(self, error: BaseException, operation: str) -> None:
        message = friendly_error(error, operation)
        self.status_var.set(f"Error: {message}")
        messagebox.showerror("Vivid II Controller", message, parent=self.root)

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.status_var.set("Disconnecting and closing…")
        self.worker.shutdown()
        self.controller.close()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    Vivid2Application(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
