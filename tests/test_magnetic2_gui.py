from __future__ import annotations

import asyncio
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from chihiros.constants import DFU_SERVICE_UUID, MAGNETIC_II_MODEL, RGB_VIVID_II_MODEL, WINDOWS_APP_VERSION
from chihiros.magnetic2_controller import Magnetic2ManualSession
from chihiros.magnetic2_protocol import build_magnetic2_manual_plan, validate_magnetic2_manual_plan
from chihiros.models import A2_MAX_MODEL, detect_supported_model, supported_model
from chihiros.protocol import Command, calculate_checksum, create_auto_mode
from chihiros.transport import LocalSessionLog, TransportSafetyError
from gui.app import ChihirosApplication
from gui.controller import (
    ApplicationController, BusyOperationError, CompatibleDevice, DevicePreferences,
    DiscoverySafetyError, WrgbValidationError, build_device_choices, parse_wrgb_inputs, preferred_device,
)
from tests.test_windows_gui import alternate_layout


# All identities are synthetic. Identical names and colliding suffixes exercise routing.
LIGHTS = tuple(CompatibleDevice("DYMNC_SYNTHETIC", address, MAGNETIC_II_MODEL) for address in (
    "00:11:22:1A:44:55", "00:11:22:2A:44:55", "00:11:22:3A:44:55",
))
LEVELS = (20, 40, 60, 30)
GOLDEN = tuple(bytes.fromhex(value) for value in (
    "5A 01 08 00 02 05 0B FF FF 05", "5A 01 07 00 03 07 00 14 16",
    "5A 01 07 00 04 07 01 28 2C", "5A 01 07 00 05 07 02 3C 3A",
    "5A 01 07 00 06 07 03 1E 1A",
))


class ThreeLights:
    """Only fake Bluetooth exists here; every call is tied to one full address."""

    def __init__(self):
        self.layouts = {d.identity: alternate_layout() for d in LIGHTS}
        self.discovery = {d.identity: (NS(address=d.identity, name=d.name), NS(local_name=d.name, rssi=-40))
                          for d in LIGHTS}
        self.events = []
        self.fail_at = None
        self.after_write = lambda: None
        self.wrong_client_address = False
        fixture = self

        class Scanner:
            @staticmethod
            async def discover(*, timeout, return_adv):
                assert return_adv
                fixture.events.append(("scan",))
                return fixture.discovery

        class Client:
            def __init__(self, selected, *, pair):
                assert pair is False
                address = selected.address
                assert selected is fixture.discovery[address][0]
                self.address = LIGHTS[2].identity if fixture.wrong_client_address else address
                self.services, self.rx, self.tx = fixture.layouts[address]
                self.is_connected = False
                fixture.client = self

            async def connect(self, *, timeout):
                self.is_connected = True
                fixture.events.append(("connect", self.address))

            async def write_gatt_char(self, characteristic, packet, *, response):
                assert characteristic is self.rx  # Neither UUID strings nor handles are accepted.
                assert response is False
                fixture.events.append(("write", self.address, bytes(packet)))
                if len(fixture.writes) == fixture.fail_at:
                    raise OSError("uncertain synthetic write")
                fixture.after_write()

            async def disconnect(self):
                fixture.events.append(("disconnect", self.address))
                self.is_connected = False

        # No read, subscribe, pairing or raw-transport helper exists on this fake.
        self.module = NS(BleakScanner=Scanner, BleakClient=Client)

    @property
    def writes(self):
        return [event for event in self.events if event[0] == "write"]


class MagneticProtocolTests(unittest.TestCase):
    def test_example_matches_physically_validated_combined_sequence(self):
        self.assertEqual(tuple(c.packet for c in build_magnetic2_manual_plan(*LEVELS)), GOLDEN)

    def test_all_levels_preserve_payload_including_zero_ninety_and_hundred(self):
        for value in range(101):
            with self.subTest(value=value):
                commands = build_magnetic2_manual_plan(value, value, value, value)
                self.assertEqual([c.packet[6:8] for c in commands[1:]], [bytes([channel, value]) for channel in range(4)])
                ids = [int.from_bytes(c.packet[3:5], "big") for c in commands]
                self.assertEqual(ids, sorted(set(ids)))
                for command in commands:
                    p = command.packet
                    self.assertEqual(calculate_checksum(p[:-1]), p[-1])
                    self.assertNotEqual(p[-1], 0x5A)
                    self.assertNotIn(0x5A, p[3:5])
                    self.assertEqual(p[2], len(p) - 2)
        # Red 88 would yield reserved checksum 5A at ID 0003. Skip it and continue.
        self.assertEqual([c.message_id for c in build_magnetic2_manual_plan(88, 0, 0, 0)],
                         [(0, 2), (0, 4), (0, 5), (0, 6), (0, 7)])

    def test_invalid_levels_and_plan_variants_rejected(self):
        for index in range(4):
            for invalid in (-1, 101, True, False, 20.0, "20", None):
                values = list(LEVELS)
                values[index] = invalid
                with self.subTest(index=index, invalid=invalid), self.assertRaises(ValueError):
                    build_magnetic2_manual_plan(*values)
        plan = build_magnetic2_manual_plan(*LEVELS)
        auto = Command("auto", (0, 2), create_auto_mode((0, 2)), True)
        for altered in (plan[:-1], plan + plan[-1:], (plan[0], plan[2], plan[1], *plan[3:]),
                        (auto, *plan[1:]), (replace(plan[0], changes_state=False), *plan[1:]),
                        build_magnetic2_manual_plan(21, 40, 60, 30)):
            with self.subTest(altered=altered), self.assertRaises(TransportSafetyError):
                validate_magnetic2_manual_plan(altered, LEVELS)

    def test_classification_and_gui_parsing_are_explicit(self):
        self.assertEqual(detect_supported_model(" dymncSYNTHETIC "), MAGNETIC_II_MODEL)
        metadata = supported_model(LIGHTS[0].name)
        self.assertEqual((metadata.controls, metadata.minimum, metadata.maximum),
                         (("Red", "Green", "Blue", "White"), 0, 100))
        for name in ("DYMN", "DYMIX", "Magnetic Light", "NORDIC_UART"):
            self.assertIsNone(detect_supported_model(name))
        self.assertEqual(parse_wrgb_inputs("0", "100", "90", "30"), (0, 100, 90, 30))
        for invalid in ("1.2", "", True, 2.0, 101, None):
            with self.assertRaises(WrgbValidationError):
                parse_wrgb_inputs(20, 40, 60, invalid)

    def test_import_has_no_research_cli_or_bluetooth_side_effects(self):
        result = subprocess.run([sys.executable, "-c", """
import sys
import gui.app
assert 'chihiros.magnetic2_diagnostic' not in sys.modules
assert 'chihiros.a2max' not in sys.modules
assert 'bleak' not in sys.modules
"""], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)


class MagneticAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = ApplicationController(Path(self.temp.name))
        self.addCleanup(self.controller.close)
        self.fixture = ThreeLights()
        self.patch = mock.patch.dict(sys.modules, {"bleak": self.fixture.module})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def apply(self, device=LIGHTS[0], levels=LEVELS):
        with contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(self.controller.apply_wrgb(device, *levels))

    def test_three_scanned_lights_and_only_selected_address_gets_five_writes(self):
        found = asyncio.run(self.controller.scan())
        self.assertEqual({d.identity for d in found}, {d.identity for d in LIGHTS})
        choices = build_device_choices(found)
        self.assertEqual(len(set(choices.labels)), 3)
        self.assertIsNone(preferred_device(found, None))
        for selected in found:
            with self.subTest(address=selected.identity):
                self.fixture.events.clear()

                async def gap(seconds):
                    self.fixture.events.append(("gap", seconds))

                with mock.patch("chihiros.magnetic2_controller.asyncio.sleep", side_effect=gap):
                    path = self.apply(selected)
                self.assertEqual([e[0] for e in self.fixture.events],
                                 ["scan", "connect", "write"] + ["gap", "write"] * 4 + ["disconnect"])
                self.assertEqual(self.fixture.writes, [("write", selected.identity, packet) for packet in GOLDEN])
                self.assertEqual([e[1] for e in self.fixture.events if e[0] == "gap"], [0.030] * 4)
                report = json.loads(path.read_text())
                self.assertEqual(report["device"]["address"], selected.identity)
                self.assertEqual(report["application"]["application_version"], "1.4.0")
                self.assertEqual(report["events"][-1]["send_calls_completed"], 5)
                self.assertFalse(self.controller.busy)

    def test_normalization_preferences_and_model_guards(self):
        device = replace(LIGHTS[0], address=LIGHTS[0].address.lower().replace(":", "-"))
        self.apply(device)
        self.assertEqual({w[1] for w in self.fixture.writes}, {LIGHTS[0].identity})
        preferences = DevicePreferences(Path(self.temp.name) / "selected.json")
        preferences.save(device)
        self.assertEqual(preferences.load().identity, LIGHTS[0].identity)
        self.fixture.events.clear()
        for other in (CompatibleDevice("DYNVSYNTHETIC", LIGHTS[0].address),
                      CompatibleDevice("DYNCMCSYNTHETIC", LIGHTS[0].address, A2_MAX_MODEL)):
            with self.assertRaises(DiscoverySafetyError):
                self.apply(other)
        for operation in (self.controller.apply_rgb(LIGHTS[0], 20, 40, 60),
                          self.controller.apply_brightness(LIGHTS[0], 20)):
            with self.assertRaises(DiscoverySafetyError):
                asyncio.run(operation)
        with self.assertRaises(WrgbValidationError):
            self.apply(levels=(20, 40, 60, 101))
        self.controller._begin("scan")
        with self.assertRaises(BusyOperationError):
            self.apply()
        self.controller._finish("scan")
        self.assertEqual(self.fixture.events, [])

    def test_failed_writes_never_continue_retry_or_restore(self):
        for failure in range(1, 6):
            with self.subTest(failure=failure):
                self.fixture.events.clear()
                self.fixture.fail_at = failure
                with self.assertRaises(OSError):
                    self.apply()
                self.assertEqual([w[2] for w in self.fixture.writes], list(GOLDEN[:failure]))
                self.assertEqual(self.fixture.events[-1][0], "disconnect")
                self.assertFalse(self.controller.busy)

    def test_identity_and_topology_changes_block_each_next_write(self):
        for completed in range(1, 5):
            for case in ("address", "dfu_parent", "removed_rx"):
                with self.subTest(completed=completed, case=case):
                    self.fixture.events.clear()
                    self.fixture.layouts[LIGHTS[0].identity] = alternate_layout()

                    def change():
                        if len(self.fixture.writes) == completed:
                            client = self.fixture.client
                            if case == "address":
                                client.address = LIGHTS[1].identity
                            elif case == "dfu_parent":
                                client.rx.service_uuid = DFU_SERVICE_UUID
                            else:
                                client.services[0].characteristics.remove(client.rx)

                    self.fixture.after_write = change
                    with self.assertRaises(TransportSafetyError):
                        self.apply()
                    self.assertEqual([w[2] for w in self.fixture.writes], list(GOLDEN[:completed]))
                    self.assertEqual(self.fixture.events[-1][0], "disconnect")

    def test_bad_initial_identity_and_dfu_or_ambiguous_topology_never_write(self):
        for case in ("wrong_name", "wrong_address", "missing", "duplicate", "dfu_parent", "outside_duplicate", "no_notify"):
            with self.subTest(case=case):
                self.fixture.events.clear()
                self.fixture.wrong_client_address = case == "wrong_address"
                self.fixture.discovery[LIGHTS[0].identity][1].local_name = "WRONG" if case == "wrong_name" else LIGHTS[0].name
                services, rx, tx = self.fixture.layouts[LIGHTS[0].identity] = alternate_layout()
                if case == "missing":
                    services.pop(0)
                elif case == "duplicate":
                    services.append(alternate_layout()[0][0])
                elif case == "dfu_parent":
                    rx.service_uuid = DFU_SERVICE_UUID
                elif case == "outside_duplicate":
                    services[1].characteristics.append(alternate_layout()[1])
                elif case == "no_notify":
                    tx.properties = ["indicate"]
                with self.assertRaises(TransportSafetyError):
                    self.apply()
                self.assertEqual(self.fixture.writes, [])
                self.assertFalse(self.controller.busy)

    def test_cancellation_after_each_write_disconnects_without_restore(self):
        for completed in range(1, 5):
            self.fixture.events.clear()

            async def gap(seconds):
                if len(self.fixture.writes) == completed:
                    raise asyncio.CancelledError

            with mock.patch("chihiros.magnetic2_controller.asyncio.sleep", side_effect=gap):
                with self.assertRaises(asyncio.CancelledError):
                    self.apply()
            self.assertEqual([w[2] for w in self.fixture.writes], list(GOLDEN[:completed]))
            self.assertEqual(self.fixture.events[-1][0], "disconnect")
            self.assertFalse(self.controller.busy)

    def test_session_allows_only_ordered_plan_and_poisoned_session_cannot_resume(self):
        async def exercise():
            log = LocalSessionLog(Path(self.temp.name), LIGHTS[0].as_core_config(), "guard")
            session = Magnetic2ManualSession(LIGHTS[0].as_core_config(), log, levels=LEVELS)
            async with session:
                plan = build_magnetic2_manual_plan(*LEVELS)
                with self.assertRaises(TransportSafetyError):
                    await session.send(plan[1])
                with self.assertRaises(TransportSafetyError):
                    await session.send(plan[0])
        with contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(exercise())
        self.assertEqual(self.fixture.writes, [])
        self.assertEqual(self.fixture.events[-1][0], "disconnect")


class MagneticWidgetTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.attributes("-alpha", 0.0)
        self.temp = tempfile.TemporaryDirectory()
        self.controller = ApplicationController(Path(self.temp.name))
        self.app = ChihirosApplication(self.root, self.controller)

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
            self.temp.cleanup()

    def select(self, device):
        self.app.device_combo.current(self.app.device_addresses.index(device.identity))
        self.app._device_selected()
        self.root.update()

    def test_model_switching_shows_only_matching_controls_and_preserves_separate_values(self):
        vivid = CompatibleDevice("DYNVSYNTHETIC", "00:11:22:33:44:01")
        a2 = CompatibleDevice("DYNCMCSYNTHETIC", "00:11:22:33:44:02", A2_MAX_MODEL)
        with mock.patch.object(self.app, "_submit") as submit:
            self.app._scan_completed((*LIGHTS, vivid, a2))
            for device in (LIGHTS[0], vivid, LIGHTS[1], a2, LIGHTS[2], LIGHTS[0]):
                self.select(device)
                is_mag = device.model == MAGNETIC_II_MODEL
                self.assertEqual(bool(self.app.wrgb_frame.winfo_ismapped()), is_mag)
                self.assertEqual(bool(self.app.apply_wrgb_button.winfo_ismapped()), is_mag)
                self.assertEqual(bool(self.app.rgb_frame.winfo_ismapped()), device == vivid)
                self.assertEqual(bool(self.app.brightness_frame.winfo_ismapped()), device == a2)
            submit.assert_not_called()
        for variable, value in zip(self.app.wrgb_vars, LEVELS):
            variable.set(value)
        self.select(LIGHTS[1])
        self.assertEqual(tuple(v.get() for v in self.app.wrgb_vars), (20, 20, 20, 20))
        self.app.wrgb_vars[3].set(90)
        self.select(vivid)
        self.assertEqual((self.app.red_var.get(), self.app.green_var.get(), self.app.blue_var.get()), (50, 50, 50))
        self.select(LIGHTS[0])
        self.assertEqual(tuple(v.get() for v in self.app.wrgb_vars), LEVELS)
        self.select(LIGHTS[1])
        self.assertEqual(self.app.wrgb_vars[3].get(), 90)

    def test_three_light_scan_selection_apply_reaches_only_selected_ble_client(self):
        fixture = ThreeLights()

        def submit(operation, coroutine):
            result = asyncio.run(coroutine)
            if operation == "scan":
                self.app._scan_completed(result)

        with mock.patch.dict(sys.modules, {"bleak": fixture.module}), mock.patch.object(
            self.app, "_submit", side_effect=submit
        ), contextlib.redirect_stdout(io.StringIO()):
            self.app.scan_button.invoke()
            self.assertEqual(len(self.app.device_combo.cget("values")), 3)
            self.assertEqual(fixture.writes, [])
            self.assertIsNone(self.app._selected_device())
            for device in LIGHTS:
                self.select(device)
                for variable, value in zip(self.app.wrgb_vars, LEVELS):
                    variable.set(value)
                fixture.events.clear()
                # Presentation labels and row lookup must not route Apply.
                self.app.device_var.set("same display label")
                with mock.patch.object(self.app.device_var, "get", side_effect=AssertionError("label routing")), mock.patch.object(
                    self.app.device_combo, "current", side_effect=AssertionError("widget routing")
                ):
                    self.app.apply_wrgb_button.invoke()
                self.assertEqual(fixture.writes, [("write", device.identity, p) for p in GOLDEN])
                self.assertEqual(fixture.events[-1], ("disconnect", device.identity))

    def test_rescan_reorders_and_preferences_keep_full_address(self):
        self.app._scan_completed(LIGHTS)
        self.select(LIGHTS[1])
        self.app.wrgb_vars[0].set(75)
        self.app._scan_completed(tuple(reversed(LIGHTS)))
        self.assertEqual(self.app._selected_device().identity, LIGHTS[1].identity)
        self.assertEqual(self.app.wrgb_vars[0].get(), 75)
        self.assertEqual(self.controller.preferences.load().identity, LIGHTS[1].identity)
        self.assertEqual(set(self.app.devices_by_address), {d.identity for d in LIGHTS})

    def test_four_slider_layout_range_and_busy_state(self):
        self.app._scan_completed((LIGHTS[0],))
        self.root.update()
        self.assertEqual(self.root.title(), "Chihiros Local Controller " + WINDOWS_APP_VERSION)
        self.assertEqual(len(self.app.wrgb_scales), 4)
        bottom = self.root.winfo_rooty() + self.root.winfo_height()
        for widget in (*self.app.wrgb_scales, self.app.apply_wrgb_button, self.app.author_label, self.app.status_label):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom)
        for scale in self.app.wrgb_scales:
            self.assertEqual((float(scale.cget("from")), float(scale.cget("to")), float(scale.cget("resolution"))), (0, 100, 1))
        self.app._set_busy(True)
        self.assertEqual(str(self.app.device_combo.cget("state")), "disabled")
        self.assertEqual(str(self.app.apply_wrgb_button.cget("state")), "disabled")
        self.assertTrue(all(str(s.cget("state")) == "disabled" for s in self.app.wrgb_scales))
        self.app._set_busy(False)
        self.assertEqual(str(self.app.apply_wrgb_button.cget("state")), "normal")


if __name__ == "__main__":
    unittest.main()
