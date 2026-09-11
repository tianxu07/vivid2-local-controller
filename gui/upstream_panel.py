"""Address-scoped UI for upstream devices; no controls for unknown models."""

import re
import tkinter as tk
import weakref
from tkinter import ttk

from chihiros.upstream_profiles import profile_for
from chihiros.upstream_protocol import Action, Request, fan_wire, validate_request


class UpstreamPanel(ttk.Frame):
    def __init__(self, parent, application):
        super().__init__(parent)
        # Do not keep the Tk application in a reference cycle that a BLE worker
        # could later garbage-collect (Tk variables must die on the UI thread).
        self.app = weakref.proxy(application)
        self.device = None
        self.inputs = {}
        self.notebook = None
        self.memory = {}
        self.reports = {}
        self.widgets = []
        self.pending_address = None
        self.report = tk.StringVar(master=self, value="Device state unknown.")

    def _remember(self):
        if self.device is not None:
            self.memory[self.device.identity] = (
                {key: value.get() for key, value in self.inputs.items()},
                self.notebook.index("current") if self.notebook else 0,
            )

    def show(self, device):
        self._remember()
        self.pack_forget()
        self.device = None
        self.inputs = {}
        self.widgets = []
        self.notebook = None
        for child in self.winfo_children():
            child.destroy()
        profile = profile_for(device.name) if device else None
        if profile is None:
            return False
        self.device = device
        saved, tab = self.memory.get(device.identity, ({}, 0))
        ttk.Label(self, text="Upstream-derived support; not physically validated here.",
                  foreground="#805000").pack(anchor="w", pady=(0, 5))
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="x")
        light = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(light, text="Light")
        for index, label in enumerate(profile.controls):
            self._field(light, label, f"light{index}", saved, "20")
        self._button(light, "Send brightness", Action.LIGHT)
        ttk.Label(light, text="0–100. Requested 90 is encoded as payload 89 (0x59) for upstream compatibility.\n"
                  "Sending updates the device clock and selects manual light mode.",
                  wraplength=490).pack(anchor="w", pady=5)
        if profile.vivid3:
            fan = ttk.Frame(self.notebook, padding=10)
            self.notebook.add(fan, text="Integrated fan")
            self._field(fan, "Manual % (0–100)", "fan", saved, "0")
            self._button(fan, "Send manual fan", Action.FAN_MANUAL)
            self._button(fan, "Send Fan Auto", Action.FAN_AUTO)
            self._field(fan, "Start °C", "start", saved, "38")
            self._field(fan, "Stop °C", "stop", saved, "33")
            self._button(fan, "Send thresholds", Action.FAN_THRESHOLDS)
            self._button(fan, "Refresh RPM / temperature", Action.TELEMETRY)
            ttk.Label(fan, text="1–24% clamps to 25%. Requested 90 is encoded as payload 89 (0x59)\n"
                      "for upstream compatibility. Thresholds: 15–60 °C,\n"
                      "start ≥ stop + 2. Defaults are inputs, not device readback.\n"
                      "Fan writes also update the device clock.\n"
                      "Protection / indicator unavailable: upstream command collision.",
                      wraplength=490).pack(anchor="w", pady=5)
            self.notebook.select(min(tab, 1))
        self.report.set(self.reports.get(device.identity, "Device state unknown; no command sent this session."))
        ttk.Label(self, textvariable=self.report, wraplength=500).pack(anchor="w", pady=5)
        self.pack(fill="x")
        return True

    def _field(self, parent, label, key, saved, default):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=24).pack(side="left")
        variable = tk.StringVar(master=self, value=saved.get(key, default))
        self.inputs[key] = variable
        entry = ttk.Entry(row, textvariable=variable, width=7)
        entry.pack(side="left")
        self.widgets.append(entry)

    def _button(self, parent, label, action):
        button = ttk.Button(parent, text=label, command=lambda: self.submit(action))
        button.pack(anchor="w", pady=2)
        self.widgets.append(button)

    def set_busy(self, busy):
        for widget in self.widgets:
            widget.configure(state="disabled" if busy else "normal")

    def submit(self, action):
        device = self.device
        if device is None or self.app.active_job_id is not None:
            return
        profile = profile_for(device.name)
        keys = {Action.LIGHT: tuple(f"light{i}" for i in range(len(profile.controls))),
                Action.FAN_MANUAL: ("fan",), Action.FAN_AUTO: (),
                Action.FAN_THRESHOLDS: ("start", "stop"), Action.TELEMETRY: ()}[action]
        try:
            values = []
            for key in keys:
                value = self.inputs[key].get().strip()
                if not re.fullmatch(r"[+-]?\d+", value):
                    raise ValueError("Enter whole numbers only")
                values.append(int(value))
            request = Request(action, tuple(values))
            validate_request(profile, request)
        except ValueError as exc:
            self.report.set(str(exc))
            return
        self.pending_address = device.identity
        self.app.status_var.set("Working with the selected upstream device…")
        self.app._submit("upstream", self.app.controller.execute_upstream(device, request))

    def finish(self, receipt, error):
        address = receipt.address if receipt is not None else self.pending_address
        if error is not None:
            text = "Operation failed; device state is not confirmed. Check the diagnostic log."
            if getattr(error, "attempted", None) == 0:
                text = "No command sent. Previous device state remains unconfirmed."
        elif receipt.telemetry is not None:
            telemetry = receipt.telemetry
            text = (f"Last telemetry: {telemetry.rpm} rpm, {telemetry.temperature_c} °C "
                    f"({telemetry.source}). Mode and thresholds are not read back.")
        else:
            request = receipt.request
            text = f"Last sent: {request.action.value} {request.values}. Device acceptance is not read back."
            if request.action is Action.FAN_MANUAL:
                payload = fan_wire(request.values[0])
                text = (f"Last requested fan: {request.values[0]}%; encoded payload: {payload} (0x{payload:02X}). "
                        "Actual speed/mode is not confirmed.")
        if address is not None:
            self.reports[address] = text
        if self.device is not None and self.device.identity == address:
            self.report.set(text)
        self.pending_address = None
