from __future__ import annotations

import inspect
import json
import tempfile
import tkinter as tk
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from chihiros.constants import (
    DFU_BUTTONLESS_UUID,
    DFU_SERVICE_UUID,
    NUS_RX_UUID,
    NUS_SERVICE_UUID,
    NUS_TX_UUID,
    RGB_VIVID_II_MODEL,
)
from chihiros.transport import (
    DeviceConfig,
    LocalSessionLog,
    NusSession,
    ScanResult,
    TransportSafetyError,
    serialize_gatt,
    strict_resolve_nus,
)
from gui import app
from gui.controller import (
    ApplicationController,
    CompatibleDevice,
    DevicePreferences,
    RgbValidationError,
    filter_compatible_devices,
    parse_rgb_inputs,
    preferred_device,
)


@dataclass
class FakeCharacteristic:
    uuid: str
    handle: int
    properties: list[str]
    service_uuid: str
    descriptors: list[object] = field(default_factory=list)


@dataclass
class FakeService:
    uuid: str
    handle: int
    characteristics: list[FakeCharacteristic]


def alternate_layout() -> tuple[list[FakeService], FakeCharacteristic, FakeCharacteristic]:
    rx = FakeCharacteristic(NUS_RX_UUID, 101, ["write-without-response"], NUS_SERVICE_UUID)
    tx = FakeCharacteristic(NUS_TX_UUID, 205, ["notify"], NUS_SERVICE_UUID)
    nus = FakeService(NUS_SERVICE_UUID, 77, [rx, tx])
    dfu_char = FakeCharacteristic(
        DFU_BUTTONLESS_UUID,
        301,
        ["write", "indicate"],
        DFU_SERVICE_UUID,
    )
    dfu = FakeService(DFU_SERVICE_UUID, 300, [dfu_char])
    return [nus, dfu], rx, tx


class DiscoveryTests(unittest.TestCase):
    def test_all_and_only_verified_vivid2_prefixes_are_shown(self) -> None:
        raw = [
            ScanResult("DYNVONE", "AA:BB:CC:DD:EE:01", RGB_VIVID_II_MODEL, -50),
            ScanResult("DYNVVDTWO", "AA:BB:CC:DD:EE:02", RGB_VIVID_II_MODEL, -51),
            ScanResult("DYRGBVTHREE", "AA:BB:CC:DD:EE:03", RGB_VIVID_II_MODEL, -52),
            ScanResult("UNRELATED_NUS", "AA:BB:CC:DD:EE:04", RGB_VIVID_II_MODEL, -20),
        ]
        found = filter_compatible_devices(raw)
        self.assertEqual([item.name for item in found], ["DYNVONE", "DYNVVDTWO", "DYRGBVTHREE"])

    def test_different_addresses_and_multiple_lights_remain_distinct(self) -> None:
        raw = [
            ScanResult("DYNVSAME", "AA:BB:CC:DD:EE:01", RGB_VIVID_II_MODEL, -40),
            ScanResult("DYNVSAME", "AA:BB:CC:DD:EE:99", RGB_VIVID_II_MODEL, -41),
        ]
        found = filter_compatible_devices(raw)
        self.assertEqual(len(found), 2)
        self.assertEqual({item.address for item in found}, {"AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:99"})
        self.assertIsNone(preferred_device(found, None))

    def test_identical_duplicate_advertisements_are_deduplicated(self) -> None:
        raw = [
            ScanResult("DYNVONE", "aa-bb-cc-dd-ee-01", RGB_VIVID_II_MODEL, -80),
            ScanResult("DYNVONE", "AA:BB:CC:DD:EE:01", RGB_VIVID_II_MODEL, -30),
        ]
        found = filter_compatible_devices(raw)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].rssi, -30)

    def test_no_supported_device_returns_empty(self) -> None:
        found = filter_compatible_devices(
            [ScanResult("NORDIC_UART_DEVICE", "AA:BB:CC:DD:EE:01", "unknown", -10)]
        )
        self.assertEqual(found, ())

    def test_single_supported_device_is_selected_but_never_controlled(self) -> None:
        device = CompatibleDevice("DYNVONE", "AA:BB:CC:DD:EE:01")
        self.assertIs(preferred_device((device,), None), device)

    def test_saved_device_round_trip_contains_only_normal_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DevicePreferences(Path(directory) / "selected.json")
            device = CompatibleDevice("DYRGBVOWNER2", "10:20:30:40:50:60")
            store.save(device)
            self.assertEqual(store.load(), device)
            data = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(set(data), {"name", "address", "model"})
            store.forget()
            self.assertIsNone(store.load())


class RgbInputTests(unittest.TestCase):
    def test_rgb_zero_and_one_hundred_are_valid(self) -> None:
        self.assertEqual(parse_rgb_inputs("0", 100, "50"), (0, 100, 50))

    def test_out_of_range_and_invalid_text_are_rejected(self) -> None:
        for values in ((-1, 0, 0), (101, 0, 0), ("ten", 0, 0), ("1.5", 0, 0)):
            with self.subTest(values=values), self.assertRaises(RgbValidationError):
                parse_rgb_inputs(*values)


class PublicGattSafetyTests(unittest.TestCase):
    def test_non_reference_handles_are_diagnostic_only(self) -> None:
        services, rx, tx = alternate_layout()
        resolved = strict_resolve_nus(services)
        self.assertIs(resolved.rx, rx)
        self.assertIs(resolved.tx, tx)

    def test_wrong_or_missing_nus_service_fails_closed(self) -> None:
        services, _rx, _tx = alternate_layout()
        services[0].uuid = "0000180f-0000-1000-8000-00805f9b34fb"
        with self.assertRaises(TransportSafetyError):
            strict_resolve_nus(services)

    def test_ambiguous_nus_services_fail_closed(self) -> None:
        services, _rx, _tx = alternate_layout()
        duplicate = FakeService(NUS_SERVICE_UUID, 400, [])
        with self.assertRaises(TransportSafetyError):
            strict_resolve_nus([*services, duplicate])

    def test_wrong_rx_or_tx_properties_fail_closed(self) -> None:
        for endpoint in ("rx", "tx"):
            services, rx, tx = alternate_layout()
            (rx if endpoint == "rx" else tx).properties = []
            with self.subTest(endpoint=endpoint), self.assertRaises(TransportSafetyError):
                strict_resolve_nus(services)

    def test_fe59_subtree_is_blacklisted_and_never_selected(self) -> None:
        services, rx, tx = alternate_layout()
        resolved = strict_resolve_nus(services)
        self.assertIs(resolved.rx, rx)
        self.assertIs(resolved.tx, tx)
        dfu = serialize_gatt(services)[1]
        self.assertTrue(dfu["blacklisted"])
        self.assertTrue(all(item["blacklisted"] for item in dfu["characteristics"]))

    def test_buttonless_dfu_characteristic_cannot_replace_nus_rx(self) -> None:
        services, rx, _tx = alternate_layout()
        services[0].characteristics.remove(rx)
        with self.assertRaises(TransportSafetyError):
            strict_resolve_nus(services)


class RecordingSession:
    instances: list["RecordingSession"] = []
    fail_on_send: int | None = None

    def __init__(self, device: DeviceConfig, session_log: LocalSessionLog) -> None:
        self.device = device
        self.log = session_log
        self.commands = []
        self.closed = False
        type(self).instances.append(self)

    async def __aenter__(self) -> "RecordingSession":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self.closed = True
        self.log.finish()
        return False

    async def send(self, command) -> None:
        self.commands.append(command)
        if self.fail_on_send == len(self.commands):
            raise RuntimeError("synthetic write failure")


class UiOnlyPreferences:
    def load(self):
        return None


class UiOnlyController:
    def __init__(self) -> None:
        self.preferences = UiOnlyPreferences()

    def close(self) -> None:
        pass


class FailingConnectSession(NusSession):
    instances: list["FailingConnectSession"] = []

    def __init__(self, device: DeviceConfig, session_log: LocalSessionLog) -> None:
        super().__init__(device, session_log)
        self.closed_for_test = False
        type(self).instances.append(self)

    async def connect(self) -> None:
        raise RuntimeError("synthetic connection failure")

    async def close(self) -> None:
        self.closed_for_test = True
        await super().close()


class GuiCoreIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        RecordingSession.instances.clear()
        RecordingSession.fail_on_send = None
        FailingConnectSession.instances.clear()

    async def test_fake_session_receives_only_verified_manual_packets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ApplicationController(Path(directory), session_factory=RecordingSession)
            device = CompatibleDevice("DYNVOTHEROWNER", "12:34:56:78:9A:BC")
            try:
                await controller.apply_rgb(device, 0, 100, 50)
            finally:
                controller.close()

            self.assertEqual(len(RecordingSession.instances), 1)
            manual_packets = [item.packet.hex(" ").upper() for item in RecordingSession.instances[0].commands]
            self.assertEqual(
                manual_packets,
                [
                    "5A 01 08 00 02 05 0B FF FF 05",
                    "5A 01 07 00 03 07 00 00 02",
                    "5A 01 07 00 04 07 01 64 60",
                    "5A 01 07 00 05 07 02 32 34",
                ],
            )
            for command in RecordingSession.instances[0].commands:
                self.assertIn(command.packet[5], {0x05, 0x07})

    async def test_failure_during_connection_guarantees_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ApplicationController(Path(directory), session_factory=FailingConnectSession)
            device = CompatibleDevice("DYNVFAIL", "12:34:56:78:9A:BC")
            try:
                with self.assertRaisesRegex(RuntimeError, "synthetic connection failure"):
                    await controller.apply_rgb(device, 1, 2, 3)
                self.assertFalse(controller.busy)
            finally:
                controller.close()
            self.assertTrue(FailingConnectSession.instances[0].closed_for_test)

    async def test_failure_during_write_exits_and_disconnects_fake_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            RecordingSession.fail_on_send = 2
            controller = ApplicationController(Path(directory), session_factory=RecordingSession)
            device = CompatibleDevice("DYNVFAIL", "12:34:56:78:9A:BC")
            try:
                with self.assertRaisesRegex(RuntimeError, "synthetic write failure"):
                    await controller.apply_rgb(device, 1, 2, 3)
            finally:
                controller.close()
            self.assertTrue(RecordingSession.instances[0].closed)
            self.assertEqual(len(RecordingSession.instances[0].commands), 2)

    async def test_empty_fake_scan_requires_no_hardware(self) -> None:
        async def empty_scan(_seconds: float) -> list[ScanResult]:
            return []

        with tempfile.TemporaryDirectory() as directory:
            controller = ApplicationController(Path(directory), scan_func=empty_scan)
            try:
                self.assertEqual(await controller.scan(0.01), ())
            finally:
                controller.close()

    async def test_session_log_has_required_public_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ApplicationController(Path(directory), session_factory=RecordingSession)
            device = CompatibleDevice("DYNVLOG", "12:34:56:78:9A:BC")
            try:
                log_path = await controller.apply_rgb(device, 10, 20, 30)
            finally:
                controller.close()
            data = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(data["application"]["application_version"], "1.2.0")
            self.assertIn("controller_version", data["application"])
            self.assertIn("windows_version", data["application"])
            self.assertEqual(data["device"]["name"], "DYNVLOG")
            self.assertEqual(data["device"]["address"], "12:34:56:78:9A:BC")
            plan = data["events"][0]
            self.assertEqual(plan["requested_rgb"], {"red": 10, "green": 20, "blue": 30})
            self.assertEqual(len(plan["commands"]), 4)
            self.assertTrue(all("packet_hex" in item for item in plan["commands"]))
            self.assertNotIn("credential", json.dumps(data).lower())

    def test_public_gui_has_no_arbitrary_packet_interface(self) -> None:
        public_methods = set(dir(ApplicationController)) | set(dir(app.Vivid2Application))
        for name in (
            "send_raw",
            "write_hex",
            "send_packet",
            "turn_off",
            "firmware",
            "dfu",
            "reset",
        ):
            self.assertNotIn(name, public_methods)
        source = inspect.getsource(app.Vivid2Application)
        self.assertNotIn("write_gatt_char", source)
        self.assertNotIn("Turn Off", source)
        self.assertNotIn("argparse", source)
        self.assertIn("Created by Tianxu Yang", source)
        self.assertIn("Unofficial community tool", source)


class StartupLayoutTests(unittest.TestCase):
    def test_default_window_shows_footer_and_status_without_resizing(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is unavailable: {exc}")

        root.attributes("-alpha", 0.0)
        application = app.Vivid2Application(root, controller=UiOnlyController())
        try:
            root.update()
            self.assertEqual(
                (root.winfo_width(), root.winfo_height()),
                (app.DEFAULT_WINDOW_WIDTH, app.DEFAULT_WINDOW_HEIGHT),
            )
            self.assertEqual(
                root.minsize(),
                (app.MINIMUM_WINDOW_WIDTH, app.MINIMUM_WINDOW_HEIGHT),
            )

            root_bottom = root.winfo_rooty() + root.winfo_height()
            for widget in (
                application.smart_plug_label,
                application.disclaimer_label,
                application.author_label,
                application.status_label,
            ):
                with self.subTest(text=widget.cget("text")):
                    self.assertTrue(widget.winfo_ismapped())
                    self.assertLessEqual(
                        widget.winfo_rooty() + widget.winfo_height(),
                        root_bottom,
                    )
        finally:
            application.close()


if __name__ == "__main__":
    unittest.main()
