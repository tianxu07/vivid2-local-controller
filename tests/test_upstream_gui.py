"""Real Tk widgets with synthetic devices and mocked Bluetooth operations."""

import asyncio
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import AsyncMock, patch

from chihiros.transport import ScanResult
from chihiros.upstream_profiles import PROFILES, UNAVAILABLE, supported_model
from chihiros.upstream_protocol import Action, Request, Telemetry
from chihiros.upstream_control import OperationFailed, Receipt
from gui.app import ChihirosApplication
from gui.controller import ApplicationController, CompatibleDevice, filter_compatible_devices


def lamp(prefix, address="02:00:00:00:00:01"):
    return CompatibleDevice(prefix + "_SYNTHETIC", address, supported_model(prefix).name)


class UpstreamGuiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.temp = tempfile.TemporaryDirectory()
        self.controller = ApplicationController(Path(self.temp.name))
        self.app = ChihirosApplication(self.root, self.controller)
        self.panel = self.app.upstream_panel

    def tearDown(self):
        # Cancel pending Tk callbacks to avoid cross-test Tcl warnings.
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.app.close()
        self.temp.cleanup()

    def select(self, selected):
        self.app._select_address(selected.identity)
        self.app._device_selected()
        self.root.update()

    def test_every_profile_has_its_own_semantic_controls_and_no_local_apply(self):
        for profile in PROFILES:
            selected = lamp(profile.prefixes[0])
            self.app._set_devices((selected,))
            self.select(selected)
            with self.subTest(profile=profile.name):
                self.assertTrue(self.panel.winfo_ismapped())
                self.assertEqual(len([k for k in self.panel.inputs if k.startswith("light")]), len(profile.controls))
                self.assertEqual(len(self.panel.notebook.tabs()), 2 if profile.vivid3 else 1)
                for frame in (self.app.rgb_frame, self.app.brightness_frame, self.app.rg_frame,
                              self.app.wrgb_frame, self.app.white_frame, self.app.fan_frame):
                    self.assertFalse(frame.winfo_ismapped())
                for button in (self.app.apply_button, self.app.apply_brightness_button,
                               self.app.apply_rg_button, self.app.apply_wrgb_button, self.app.apply_white_button):
                    self.assertFalse(button.winfo_ismapped())

    def test_all_six_local_views_remain_separate(self):
        pairs = (("DYNV", self.app.rgb_frame), ("DYNCMC", self.app.brightness_frame),
                 ("DYCX", self.app.rg_frame), ("DYMNC", self.app.wrgb_frame),
                 ("DYSSD", self.app.white_frame), ("DYNFAN", self.app.fan_frame))
        for prefix, frame in pairs:
            selected = lamp(prefix)
            self.app._set_devices((selected,))
            self.select(selected)
            self.assertTrue(frame.winfo_ismapped(), prefix)
            self.assertFalse(self.panel.winfo_ismapped())
        self.assertEqual(self.app.fan_speed_scale.cget("to"), 20.0)

    def test_same_names_inputs_reports_and_tabs_survive_reorder(self):
        first, second = lamp("DYVVD3"), lamp("DYVVD3", "02:00:00:00:00:02")
        self.app._set_devices((first, second))
        self.select(first)
        self.panel.inputs["light0"].set("71")
        self.panel.inputs["fan"].set("24")
        self.panel.inputs["start"].set("43")
        self.panel.notebook.select(1)
        self.select(second)
        self.assertEqual(self.panel.inputs["fan"].get(), "0")
        self.assertEqual(self.panel.notebook.index("current"), 0)
        self.panel.inputs["fan"].set("100")
        self.app._set_devices((second, first))
        self.select(first)
        self.assertEqual(self.panel.inputs["light0"].get(), "71")
        self.assertEqual(self.panel.inputs["fan"].get(), "24")
        self.assertEqual(self.panel.inputs["start"].get(), "43")
        self.assertEqual(self.panel.notebook.index("current"), 1)

    def test_submission_is_bound_to_full_address_and_typed_request(self):
        selected = lamp("DYVVD3")
        self.app._set_devices((selected,))
        self.select(selected)
        self.panel.inputs["fan"].set("24")
        def submit(operation, coroutine):
            self.assertEqual(operation, "upstream")
            asyncio.run(coroutine)
        with patch.object(self.app, "_submit", side_effect=submit), patch.object(
                self.controller, "execute_upstream", new_callable=AsyncMock) as call:
            self.panel.submit(Action.FAN_MANUAL)
        self.assertEqual(call.await_args.args, (selected, Request(Action.FAN_MANUAL, (24,))))

    def test_invalid_and_busy_submissions_never_start_operation(self):
        selected = lamp("DYVVD3")
        self.app._set_devices((selected,))
        self.select(selected)
        with patch.object(self.app, "_submit") as submit:
            for value in ("", "1.5", "True", "101", "-1"):
                self.panel.inputs["fan"].set(value)
                self.panel.submit(Action.FAN_MANUAL)
            self.panel.inputs["start"].set("33")
            self.panel.inputs["stop"].set("33")
            self.panel.submit(Action.FAN_THRESHOLDS)
            self.app.active_job_id = 12
            self.panel.inputs["fan"].set("25")
            self.panel.submit(Action.FAN_MANUAL)
            self.app.active_job_id = None
            submit.assert_not_called()
        self.app._set_busy(True)
        self.assertTrue(all(str(w.cget("state")) == "disabled" for w in self.panel.widgets))
        self.app._set_busy(False)

    def test_unknown_sent_and_uncertain_state_are_not_readback(self):
        first, second = lamp("DYVVD3"), lamp("DYVVD3", "02:00:00:00:00:02")
        self.app._set_devices((first, second))
        self.select(first)
        self.assertIn("unknown", self.panel.report.get())
        receipt = Receipt(first.identity, Request(Action.FAN_MANUAL, (90,)), None, Path("synthetic.json"))
        self.panel.finish(receipt, None)
        self.assertEqual(self.panel.report.get(),
                         "Last requested fan: 90%; encoded payload: 89 (0x59). "
                         "Actual speed/mode is not confirmed.")
        self.select(second)
        self.assertIn("unknown", self.panel.report.get())
        self.panel.pending_address = first.identity
        self.panel.finish(None, OperationFailed(1, Path("synthetic.json")))
        self.assertIn("unknown", self.panel.report.get())
        self.select(first)
        self.assertIn("not confirmed", self.panel.report.get())

    def test_telemetry_cannot_establish_mode_or_thresholds(self):
        selected = lamp("DYVVD3")
        self.app._set_devices((selected,))
        self.select(selected)
        self.panel.finish(Receipt(selected.identity, Request(Action.TELEMETRY),
                                  Telemetry(600, 25, "5B/0B"), Path("synthetic.json")), None)
        self.assertIn("600 rpm", self.panel.report.get())
        self.assertIn("not read back", self.panel.report.get())

    def test_unavailable_devices_are_filtered_without_configuration_ui(self):
        scans = [ScanResult(prefix, "02:00:00:00:00:01", candidate.name, -40)
                 for candidate in UNAVAILABLE for prefix in candidate.prefixes]
        self.assertEqual(filter_compatible_devices(scans), ())

    def test_upstream_view_fits_existing_window_and_footer(self):
        selected = lamp("DYVVD3")
        self.app._set_devices((selected,))
        self.select(selected)
        self.panel.notebook.select(1)
        self.root.update()
        self.assertLessEqual(self.panel.winfo_rooty() + self.panel.winfo_height(),
                             self.app.footer_frame.winfo_rooty())
        self.assertTrue(self.app.author_label.winfo_ismapped())


if __name__ == "__main__":
    unittest.main()
