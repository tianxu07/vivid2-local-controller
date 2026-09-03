from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

import chihirosctl
from chihiros.vivid2 import Vivid2Controller
from chihiros.constants import (
    DFU_BUTTONLESS_UUID,
    DFU_SERVICE_UUID,
    NUS_RX_UUID,
    NUS_SERVICE_UUID,
    NUS_TX_UUID,
    RGB_VIVID_II_MODEL,
    vivid2_curve_family,
)
from chihiros.transport import configure_vivid2_device, detect_model, load_devices, serialize_gatt, strict_resolve_nus


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


class Phase5PortabilitySafetyTests(unittest.TestCase):
    def test_generic_service_scoped_resolution_accepts_different_handles(self) -> None:
        rx = FakeCharacteristic(NUS_RX_UUID, 101, ["write-without-response"], NUS_SERVICE_UUID)
        tx = FakeCharacteristic(NUS_TX_UUID, 205, ["notify"], NUS_SERVICE_UUID)
        nus = FakeService(NUS_SERVICE_UUID, 77, [rx, tx])
        dfu_char = FakeCharacteristic(DFU_BUTTONLESS_UUID, 301, ["write", "indicate"], DFU_SERVICE_UUID)
        dfu = FakeService(DFU_SERVICE_UUID, 300, [dfu_char])
        resolved = strict_resolve_nus([nus, dfu])
        self.assertIs(resolved.service, nus)
        self.assertIs(resolved.rx, rx)
        self.assertIs(resolved.tx, tx)
        inventory = serialize_gatt([nus, dfu])
        self.assertTrue(inventory[1]["blacklisted"])
        self.assertTrue(inventory[1]["characteristics"][0]["blacklisted"])

    def test_all_verified_vivid2_prefixes_detect(self) -> None:
        for name in ("DYRGBV123", "DYNVVD123", "DYNV123"):
            with self.subTest(name=name):
                self.assertEqual(detect_model(name), RGB_VIVID_II_MODEL)
        self.assertEqual(vivid2_curve_family("DYRGBV123"), "NewBleLed")
        self.assertEqual(vivid2_curve_family("DYNVVD123"), "SeaLed")
        self.assertEqual(vivid2_curve_family("DYNV123"), "SeaLed")

    def test_different_users_can_store_different_addresses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.json"
            second_path = Path(directory) / "second.json"
            first = configure_vivid2_device(first_path, "tank", "DYNVFIRST", "AA:BB:CC:DD:EE:01")
            second = configure_vivid2_device(second_path, "tank", "DYRGBVSECOND", "AA:BB:CC:DD:EE:02")
            self.assertNotEqual(first.address, second.address)
            self.assertEqual(load_devices(first_path)["tank"].address, "AA:BB:CC:DD:EE:01")
            self.assertEqual(load_devices(second_path)["tank"].address, "AA:BB:CC:DD:EE:02")

    def test_configure_rejects_unverified_model_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                configure_vivid2_device(
                    Path(directory) / "devices.json",
                    "tank",
                    "UNKNOWN123",
                    "AA:BB:CC:DD:EE:01",
                )

    def test_cli_has_no_raw_or_reset_firmware_dfu_commands(self) -> None:
        parser = chihirosctl.build_parser()
        for command in ("send-raw", "write-hex", "custom-command", "reset", "dfu", "firmware"):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args([command])

    def test_schedule_and_clock_mutations_require_explicit_gate(self) -> None:
        parser = chihirosctl.build_parser()
        schedule_args = [
            "schedule", "vivid2", "set", "--start", "15:30", "--end", "22:30",
            "--ramp-minutes", "30", "--red", "82", "--green", "82", "--blue", "82", "--days", "all",
        ]
        for arguments in (schedule_args, ["clock", "vivid2", "sync"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(arguments)

    def test_controller_exposes_no_live_schedule_mutation_method(self) -> None:
        self.assertFalse(hasattr(Vivid2Controller, "send_schedule_period"))

    def test_clock_show_is_read_capability_report_with_no_bleak_import(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = chihirosctl.main(["clock", "vivid2", "show"])
        self.assertEqual(result, 0)
        self.assertIn("write-only 5A/09", output.getvalue())
        self.assertIn("No Bluetooth connection", output.getvalue())


if __name__ == "__main__":
    unittest.main()
