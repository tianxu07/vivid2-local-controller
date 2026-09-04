from __future__ import annotations

import asyncio
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import tkinter as tk
import types
import unittest
from pathlib import Path
from unittest import mock

from chihiros.a2max_controller import A2MaxManualSession
from chihiros.a2max_protocol import build_a2max_manual_plan
from chihiros.constants import DEVELOPMENT_APP_VERSION, DFU_SERVICE_UUID, RGB_VIVID_II_MODEL
from chihiros.models import A2_MAX_MODEL, SUPPORTED_MODELS, detect_supported_model
from chihiros.protocol import Command, create_auto_mode
from chihiros.transport import LocalSessionLog, ScanResult, TransportSafetyError
from chihiros.vivid2 import build_manual_plan
from gui import app
from gui.controller import (
    ApplicationController, BrightnessValidationError, CompatibleDevice, DevicePreferences,
    DiscoverySafetyError, controls_for_device, filter_compatible_devices, parse_brightness_input,
    build_device_choices,
)
from tests.test_windows_gui import alternate_layout


A2 = CompatibleDevice("DYNCMCTESTUNIT", "12:34:56:78:9A:BC", A2_MAX_MODEL)
VIVID = CompatibleDevice("DYNVTESTUNIT", "12:34:56:78:9A:BD")


class ModelTests(unittest.TestCase):
    def test_candidate_registry_is_explicit_and_keeps_upstream_vivid_prefixes(self) -> None:
        self.assertEqual(SUPPORTED_MODELS[1].prefixes, ("DYNCMC",))
        self.assertTrue(SUPPORTED_MODELS[1].candidate)
        self.assertEqual(SUPPORTED_MODELS[1].physical_samples, 1)
        for prefix in ("DYNV", "DYNVVD", "DYRGBV"):
            self.assertEqual(detect_supported_model(prefix + "TEST"), RGB_VIVID_II_MODEL)
        for name in ("DYNA2TEST", "NORDIC_UART", "DYNCMTEST", "DYNWRGBTEST", None):
            self.assertIsNone(detect_supported_model(name))

    def test_mixed_discovery_labels_and_capabilities(self) -> None:
        raw = [ScanResult(d.name, d.address, "untrusted scan label", -50) for d in (A2, VIVID)]
        raw.append(ScanResult("NORDIC_UART", "11:22:33:44:55:66", A2_MAX_MODEL, -30))
        found = filter_compatible_devices(raw)
        self.assertEqual({d.model for d in found}, {A2_MAX_MODEL, RGB_VIVID_II_MODEL})
        choices = build_device_choices((A2, VIVID))
        self.assertEqual(choices.labels, ("A2 Max — …9ABC", "RGB Vivid II — …9ABD"))
        self.assertEqual(controls_for_device(A2), ("Brightness",))
        self.assertEqual(controls_for_device(VIVID), ("Red", "Green", "Blue"))
        self.assertEqual(controls_for_device(None), ())

    def test_conflicting_names_for_same_address_rejected(self) -> None:
        with self.assertRaises(DiscoverySafetyError):
            filter_compatible_devices([
                ScanResult(d.name, A2.address, d.model, -50) for d in (A2, VIVID)
            ])

    def test_brightness_boundaries_and_invalid_values(self) -> None:
        for value in (1, 20, 60, 100, "1", "100"):
            self.assertEqual(parse_brightness_input(value), int(value))
        for value in (0, 101, -1, True, False, 60.0, "1.2", "", None, "NaN"):
            with self.subTest(value=value), self.assertRaises(BrightnessValidationError):
                parse_brightness_input(value)

    def test_preferences_validate_model_name_and_round_trip_both_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            preferences = DevicePreferences(Path(temp) / "selected.json")
            for device in (A2, VIVID):
                preferences.save(device)
                self.assertEqual(preferences.load(), device)
                preferences.forget()
                self.assertIsNone(preferences.load())
            for device in (
                CompatibleDevice(A2.name, A2.address, RGB_VIVID_II_MODEL),
                CompatibleDevice(VIVID.name, VIVID.address, A2_MAX_MODEL),
                CompatibleDevice("NORDIC_UART", A2.address, A2_MAX_MODEL),
                CompatibleDevice(A2.name, "invalid", A2_MAX_MODEL),
            ):
                with self.subTest(device=device), self.assertRaises(DiscoverySafetyError):
                    preferences.save(device)
                preferences.path.write_text(json.dumps({
                    "name": device.name, "address": device.address, "model": device.model,
                }), encoding="utf-8")
                self.assertIsNone(preferences.load())

    def test_gui_import_graph_excludes_private_probe_identity_and_bleak(self) -> None:
        # A fresh interpreter avoids the exact-unit CLI imported by other tests.
        result = subprocess.run([sys.executable, "-c", """
import pathlib, sys
import gui.app
assert 'chihiros.a2max' not in sys.modules
assert 'chihirosctl' not in sys.modules
assert 'bleak' not in sys.modules
for name, module in tuple(sys.modules.items()):
    if name.startswith(('chihiros.', 'gui.')) and getattr(module, '__file__', None):
        source = pathlib.Path(module.__file__).read_text(encoding='utf-8')
        assert 'PRIVATE_PROBE_CONFIG' not in source, name
"""], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)


class BleakFixture:
    """No real Bluetooth API exists in this fixture."""

    def __init__(self, device=A2, *, advertised_name=None, fail_write=None):
        self.services, self.rx, self.tx = alternate_layout()
        self.scan_calls = self.connect_calls = self.disconnect_calls = 0
        self.scan_timeouts = []
        self.connect_timeouts = []
        self.notifications = []
        self.writes = []
        self.fail_write = fail_write
        self.after_write = None
        fixture = self
        ble_device = types.SimpleNamespace(name=device.name, address=device.address)
        self.discovery = {device.address: (ble_device, types.SimpleNamespace(
            local_name=device.name if advertised_name is None else advertised_name, rssi=-40,
        ))}

        class Scanner:
            @staticmethod
            async def discover(*, timeout, return_adv):
                fixture.scan_calls += 1
                fixture.scan_timeouts.append(timeout)
                assert return_adv is True
                return fixture.discovery

        class Client:
            def __init__(self, selected, *, pair):
                assert pair is False
                assert selected is ble_device
                self.is_connected = False
                self.services = fixture.services

            async def connect(self, *, timeout):
                fixture.connect_calls += 1
                fixture.connect_timeouts.append(timeout)
                self.is_connected = True

            async def start_notify(self, characteristic, callback):
                assert device.model == RGB_VIVID_II_MODEL, "A2 Max must not subscribe"
                assert characteristic is fixture.tx
                fixture.notifications.append(characteristic)

            async def stop_notify(self, characteristic):
                assert characteristic is fixture.tx

            async def write_gatt_char(self, characteristic, packet, *, response):
                assert characteristic is fixture.rx
                assert response is False
                fixture.writes.append(bytes(packet))
                if fixture.fail_write == len(fixture.writes):
                    raise TimeoutError()
                if fixture.after_write:
                    fixture.after_write()

            async def disconnect(self):
                fixture.disconnect_calls += 1
                self.is_connected = False

        self.module = types.SimpleNamespace(BleakClient=Client, BleakScanner=Scanner)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_adapter_through_mocked_bleak_boundaries_exact_packets_and_pacing(self) -> None:
        for level in (1, 20, 60, 100):
            fixture = BleakFixture()
            with self.subTest(level=level), tempfile.TemporaryDirectory() as temp, mock.patch.dict(
                sys.modules, {"bleak": fixture.module}
            ), contextlib.redirect_stdout(io.StringIO()), mock.patch(
                "chihiros.a2max_controller.asyncio.sleep", new_callable=mock.AsyncMock
            ) as sleep:
                controller = ApplicationController(Path(temp))
                try:
                    log_path = await controller.apply_brightness(A2, level)
                    self.assertFalse(controller.busy)
                finally:
                    controller.close()
                document = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(fixture.writes, [c.packet for c in build_a2max_manual_plan(level)])
            self.assertEqual(fixture.scan_calls, 1)
            self.assertEqual(fixture.connect_calls, 1)
            self.assertEqual(fixture.disconnect_calls, 1)
            self.assertEqual(fixture.notifications, [])
            self.assertEqual(fixture.scan_timeouts, [20.0])
            self.assertEqual(fixture.connect_timeouts, [30.0])
            sleep.assert_awaited_once_with(0.03)
            self.assertEqual(document["events"][0]["normalized_wire_level"], level)
            self.assertEqual(document["events"][0]["development_app_version"], DEVELOPMENT_APP_VERSION)

    async def test_vivid_ble_defaults_notifications_and_packets_remain_unchanged(self) -> None:
        fixture = BleakFixture(VIVID)
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            sys.modules, {"bleak": fixture.module}
        ), contextlib.redirect_stdout(io.StringIO()), mock.patch(
            "chihiros.vivid2.asyncio.sleep", new_callable=mock.AsyncMock
        ) as sleep:
            controller = ApplicationController(Path(temp))
            try:
                await controller.apply_rgb(VIVID, 10, 20, 30)
            finally:
                controller.close()
        self.assertEqual(fixture.writes, [c.packet for c in build_manual_plan(10, 20, 30)])
        self.assertEqual(fixture.notifications, [fixture.tx])
        self.assertEqual(fixture.scan_timeouts, [10.0])
        self.assertEqual(fixture.connect_timeouts, [15.0])
        self.assertEqual(fixture.disconnect_calls, 1)
        self.assertEqual(sleep.await_args_list, [mock.call(0.03)] * 3)

    async def test_scan_is_single_pass_and_does_not_control(self) -> None:
        fixture = BleakFixture()
        fixture.discovery[VIVID.address] = (types.SimpleNamespace(name=VIVID.name, address=VIVID.address),
                                           types.SimpleNamespace(local_name=VIVID.name, rssi=-50))
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(sys.modules, {"bleak": fixture.module}):
            controller = ApplicationController(Path(temp))
            try:
                devices = await controller.scan()
            finally:
                controller.close()
        self.assertEqual({d.model for d in devices}, {A2_MAX_MODEL, RGB_VIVID_II_MODEL})
        self.assertEqual(fixture.scan_calls, 1)
        self.assertEqual(fixture.connect_calls, 0)
        self.assertEqual(fixture.writes, [])

    async def test_wrong_route_invalid_device_or_level_rejected_before_ble(self) -> None:
        fixture = BleakFixture()
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(sys.modules, {"bleak": fixture.module}):
            controller = ApplicationController(Path(temp))
            try:
                for device in (VIVID, CompatibleDevice(VIVID.name, A2.address, A2_MAX_MODEL),
                               CompatibleDevice("NUS", A2.address, A2_MAX_MODEL),
                               CompatibleDevice(A2.name, "bad", A2_MAX_MODEL)):
                    with self.subTest(device=device), self.assertRaises(DiscoverySafetyError):
                        await controller.apply_brightness(device, 60)
                with self.assertRaises(DiscoverySafetyError):
                    await controller.apply_rgb(A2, 1, 2, 3)
                for level in (0, 101):
                    with self.assertRaises(BrightnessValidationError):
                        await controller.apply_brightness(A2, level)
            finally:
                controller.close()
        self.assertEqual(fixture.scan_calls, 0)
        self.assertEqual(fixture.writes, [])

    async def test_exact_selected_advertisement_checked_again_before_control(self) -> None:
        for name in ("DYNCMCOTHER", "DYNVTEST", "NORDIC_UART"):
            fixture = BleakFixture(advertised_name=name)
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp, mock.patch.dict(
                sys.modules, {"bleak": fixture.module}
            ):
                controller = ApplicationController(Path(temp))
                try:
                    with self.assertRaises(TransportSafetyError):
                        await controller.apply_brightness(A2, 60)
                finally:
                    controller.close()
            self.assertEqual(fixture.connect_calls, 0)
            self.assertEqual(fixture.writes, [])

    async def test_fe59_and_ambiguous_nus_rejected_in_both_paths(self) -> None:
        for device in (A2, VIVID):
            for layout in ("dfu-only", "rx-in-dfu", "duplicate-nus", "wrong-parent", "wrong-properties"):
                fixture = BleakFixture(device)
                if layout == "dfu-only":
                    fixture.services.pop(0)
                elif layout == "rx-in-dfu":
                    fixture.services[1].characteristics.append(fixture.rx)
                elif layout == "duplicate-nus":
                    fixture.services.append(fixture.services[0])
                elif layout == "wrong-parent":
                    fixture.rx.service_uuid = DFU_SERVICE_UUID
                else:
                    fixture.rx.properties = ["write"]
                with self.subTest(device=device.model, layout=layout), tempfile.TemporaryDirectory() as temp, mock.patch.dict(
                    sys.modules, {"bleak": fixture.module}
                ), contextlib.redirect_stdout(io.StringIO()):
                    controller = ApplicationController(Path(temp))
                    try:
                        with self.assertRaises(TransportSafetyError):
                            if device == A2:
                                await controller.apply_brightness(device, 60)
                            else:
                                await controller.apply_rgb(device, 1, 2, 3)
                    finally:
                        controller.close()
                self.assertEqual(fixture.writes, [])
                self.assertEqual(fixture.notifications, [])
                self.assertEqual(fixture.disconnect_calls, 1)

    async def test_changed_endpoints_abort_before_brightness(self) -> None:
        fixture = BleakFixture()
        fixture.after_write = lambda: fixture.services[1].characteristics.append(fixture.rx)
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(sys.modules, {"bleak": fixture.module}), contextlib.redirect_stdout(io.StringIO()):
            controller = ApplicationController(Path(temp))
            try:
                with self.assertRaises(TransportSafetyError):
                    await controller.apply_brightness(A2, 60)
            finally:
                controller.close()
        self.assertEqual(len(fixture.writes), 1)
        self.assertEqual(fixture.disconnect_calls, 1)

    async def test_uncertain_write_has_no_retry_and_log_is_finished(self) -> None:
        for failed_write in (1, 2):
            fixture = BleakFixture(fail_write=failed_write)
            with self.subTest(write=failed_write), tempfile.TemporaryDirectory() as temp, mock.patch.dict(
                sys.modules, {"bleak": fixture.module}
            ), contextlib.redirect_stdout(io.StringIO()):
                controller = ApplicationController(Path(temp))
                try:
                    with self.assertRaises(TimeoutError):
                        await controller.apply_brightness(A2, 60)
                    self.assertFalse(controller.busy)
                finally:
                    controller.close()
                document = json.loads(next((Path(temp) / "logs").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(len(fixture.writes), failed_write)
            self.assertEqual(fixture.disconnect_calls, 1)
            self.assertIn("finished_at_utc", document)
            error = next(e for e in document["events"] if e["event"] == "manual_brightness_failed")
            self.assertEqual(error["exception_repr"], "TimeoutError()")

    async def test_a2_transport_allows_only_next_manual_packet_no_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = LocalSessionLog(Path(temp), A2.as_core_config(), "test")
            session = A2MaxManualSession(A2.as_core_config(), log, level=60)
            manual, brightness = build_a2max_manual_plan(60)
            auto = create_auto_mode((0, 2))
            forbidden = Command("auto", (0, 2), auto, True)
            for command in (brightness, forbidden):
                with self.assertRaises(TransportSafetyError):
                    await session.send(command)
            # No active connection: first allowed send fails, and no continuation is allowed.
            with self.assertRaises(TransportSafetyError):
                await session.send(manual)
            for command in (manual, brightness):
                with self.assertRaises(TransportSafetyError):
                    await session.send(command)
            log.finish()


class WidgetRoutingTests(unittest.TestCase):
    def test_model_selection_exposes_only_relevant_controls_and_never_applies(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.attributes("-alpha", 0.0)
        with tempfile.TemporaryDirectory() as temp:
            controller = ApplicationController(Path(temp))
            application = app.ChihirosApplication(root, controller=controller)
            try:
                with mock.patch.object(application, "_submit") as submit:
                    application._scan_completed((VIVID, A2))
                    for device in (VIVID, A2, VIVID):
                        application.device_combo.current(application.device_addresses.index(device.identity))
                        application._device_selected()
                        root.update()
                        is_rgb = device == VIVID
                        self.assertEqual(bool(application.rgb_frame.winfo_ismapped()), is_rgb)
                        self.assertEqual(bool(application.brightness_frame.winfo_ismapped()), not is_rgb)
                        self.assertEqual(bool(application.apply_button.winfo_ismapped()), is_rgb)
                        self.assertEqual(bool(application.apply_brightness_button.winfo_ismapped()), not is_rgb)
                        for scale in application.scales:
                            self.assertEqual(bool(scale.winfo_ismapped()), is_rgb)
                        self.assertEqual(bool(application.brightness_scale.winfo_ismapped()), not is_rgb)
                        self.assertEqual(float(application.brightness_scale.cget("from")), 1)
                        self.assertEqual(float(application.brightness_scale.cget("to")), 100)
                        bottom = root.winfo_rooty() + root.winfo_height()
                        for widget in (application.smart_plug_label, application.author_label, application.status_label):
                            self.assertTrue(widget.winfo_ismapped())
                            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom)
                    submit.assert_not_called()
                    self.assertEqual(controller.preferences.load().identity, VIVID.identity)
                self.assertEqual(root.title(), "Chihiros Local Controller " + DEVELOPMENT_APP_VERSION)
            finally:
                application.close()

    def test_apply_buttons_route_to_only_the_selected_adapter(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.attributes("-alpha", 0.0)
        with tempfile.TemporaryDirectory() as temp:
            controller = ApplicationController(Path(temp))
            application = app.ChihirosApplication(root, controller=controller)
            try:
                application._scan_completed((VIVID, A2))
                def capture(_operation, coroutine):
                    asyncio.run(coroutine)
                with mock.patch.object(application, "_submit", side_effect=capture), mock.patch.object(
                    controller, "apply_brightness", new_callable=mock.AsyncMock
                ) as brightness, mock.patch.object(controller, "apply_rgb", new_callable=mock.AsyncMock) as rgb:
                    application.device_combo.current(application.device_addresses.index(A2.identity))
                    application._device_selected()
                    application.brightness_var.set(60)
                    application.apply_brightness_button.invoke()
                    brightness.assert_awaited_once_with(A2, 60)
                    rgb.assert_not_awaited()
                    application.device_combo.current(application.device_addresses.index(VIVID.identity))
                    application._device_selected()
                    application.apply_button.invoke()
                    rgb.assert_awaited_once_with(VIVID, 50, 50, 50)
                    self.assertEqual(brightness.await_count, 1)
            finally:
                application.close()


if __name__ == "__main__":
    unittest.main()
