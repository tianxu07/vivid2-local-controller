from __future__ import annotations

import json
import inspect
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from chihiros.constants import (
    DFU_BUTTONLESS_UUID,
    DFU_SERVICE_UUID,
    NUS_RX_UUID,
    NUS_SERVICE_UUID,
    NUS_TX_UUID,
)
from chihiros.transport import (
    ConfigurationError,
    TransportSafetyError,
    load_devices,
    serialize_gatt,
    strict_resolve_nus,
)
from chihiros import transport
from chihiros.transport import DeviceConfig, LocalSessionLog, NusSession


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


def actual_layout() -> tuple[list[FakeService], FakeService, FakeCharacteristic, FakeCharacteristic]:
    rx = FakeCharacteristic(NUS_RX_UUID, 15, ["write", "write-without-response"], NUS_SERVICE_UUID)
    tx = FakeCharacteristic(NUS_TX_UUID, 17, ["notify"], NUS_SERVICE_UUID)
    nus = FakeService(NUS_SERVICE_UUID, 14, [rx, tx])
    dfu_characteristic = FakeCharacteristic(
        DFU_BUTTONLESS_UUID, 22, ["write", "indicate"], DFU_SERVICE_UUID
    )
    dfu = FakeService(DFU_SERVICE_UUID, 21, [dfu_characteristic])
    return [nus, dfu], nus, rx, tx


class Phase4TransportTests(unittest.TestCase):
    def test_service_scoped_resolution_selects_only_nus_children(self) -> None:
        services, nus, rx, tx = actual_layout()
        resolved = strict_resolve_nus(services)
        self.assertIs(resolved.service, nus)
        self.assertIs(resolved.rx, rx)
        self.assertIs(resolved.tx, tx)
        self.assertEqual(len(resolved.dfu_services), 1)

    def test_entire_dfu_subtree_is_marked_blacklisted(self) -> None:
        inventory = serialize_gatt(actual_layout()[0])
        dfu = next(item for item in inventory if item["uuid"] == DFU_SERVICE_UUID)
        self.assertTrue(dfu["blacklisted"])
        self.assertTrue(dfu["characteristics"])
        self.assertTrue(all(item["blacklisted"] for item in dfu["characteristics"]))

    def test_buttonless_dfu_cannot_substitute_for_missing_nus_rx(self) -> None:
        services, nus, _rx, _tx = actual_layout()
        nus.characteristics = [item for item in nus.characteristics if item.uuid != NUS_RX_UUID]
        with self.assertRaises(TransportSafetyError):
            strict_resolve_nus(services)

    def test_duplicate_endpoint_outside_nus_aborts_as_ambiguous(self) -> None:
        services, _nus, _rx, _tx = actual_layout()
        services[1].characteristics.append(
            FakeCharacteristic(NUS_RX_UUID, 23, ["write-without-response"], DFU_SERVICE_UUID)
        )
        with self.assertRaises(TransportSafetyError):
            strict_resolve_nus(services)

    def test_observed_handles_are_diagnostics_not_universal_requirements(self) -> None:
        services, _nus, rx, _tx = actual_layout()
        rx.handle = 99
        resolved = strict_resolve_nus(services)
        self.assertIs(resolved.rx, rx)

    def test_configuration_loads_portable_synthetic_device(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "devices.json"
            config.write_text(
                json.dumps(
                    {
                        "vivid2": {
                            "model": "RGB Vivid II",
                            "name": "DYNVEXAMPLE",
                            "address": "AA:BB:CC:DD:EE:01",
                        }
                    }
                ),
                encoding="utf-8",
            )
            device = load_devices(config)["vivid2"]
        self.assertEqual(device.name, "DYNVEXAMPLE")
        self.assertEqual(device.address, "AA:BB:CC:DD:EE:01")
        self.assertEqual(device.model, "RGB Vivid II")

    def test_duplicate_configured_address_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            value = {
                "one": {"model": "RGB Vivid II", "name": "DYNVONE", "address": "AA:BB:CC:DD:EE:FF"},
                "two": {"model": "RGB Vivid II", "name": "DYNVTWO", "address": "AA:BB:CC:DD:EE:FF"},
            }
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_devices(path)

    def test_live_transport_has_one_scoped_write_site_and_no_global_lookup(self) -> None:
        source = inspect.getsource(transport.NusSession)
        self.assertEqual(source.count("write_gatt_char("), 1)
        self.assertIn("self.resolved.rx", source)
        self.assertIn("response=False", source)
        self.assertEqual(source.count("start_notify("), 1)
        self.assertIn("self.resolved.tx", source)
        self.assertNotIn("get_characteristic", source)
        self.assertIn("pair=False", source)


class Phase4CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_context_entry_closes_and_finishes_log(self) -> None:
        class FailingSession(NusSession):
            closed = False

            async def connect(self) -> None:
                raise TransportSafetyError("synthetic failure")

            async def close(self) -> None:
                self.closed = True

        device = DeviceConfig("vivid2", "RGB Vivid II", "DYNVTEST", "AA:BB:CC:DD:EE:FF")
        with tempfile.TemporaryDirectory() as directory:
            session_log = LocalSessionLog(Path(directory), device, "status")
            session = FailingSession(device, session_log)
            with self.assertRaisesRegex(TransportSafetyError, "synthetic failure"):
                async with session:
                    self.fail("unreachable")
            self.assertTrue(session.closed)
            logged = json.loads(session_log.path.read_text(encoding="utf-8"))
            self.assertIn("finished_at_utc", logged)
            self.assertEqual(logged["events"][-1]["event"], "error")


if __name__ == "__main__":
    unittest.main()
