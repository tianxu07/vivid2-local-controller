from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import chihirosctl
from chihiros import a2max
from chihiros.constants import (
    A2_MAX_CANDIDATE_PREFIX,
    A2_MAX_CANDIDATE_SAMPLE_COUNT,
    DFU_BUTTONLESS_UUID,
    DFU_SERVICE_UUID,
    NUS_RX_UUID,
    NUS_SERVICE_UUID,
    NUS_TX_UUID,
    detect_model,
)
from chihiros.protocol import Command, calculate_checksum, create_command_encoding
from chihiros.transport import TransportSafetyError

# Synthetic public test identity; never load a contributor's private device.
A2_MAX_STATUS_PROBE_NAME = "DYNCMC001122334455"
A2_MAX_STATUS_PROBE_ADDRESS = "00:11:22:33:44:55"


def setUpModule() -> None:
    patcher = mock.patch.object(a2max, "_private_probe_identity",
                               return_value=(A2_MAX_STATUS_PROBE_NAME, A2_MAX_STATUS_PROBE_ADDRESS))
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class A2MaxStatusProbeTests(unittest.TestCase):
    def test_single_unit_candidate_prefix_is_not_universal_model_detection(self) -> None:
        self.assertEqual(A2_MAX_CANDIDATE_PREFIX, "DYNCMC")
        self.assertEqual(A2_MAX_CANDIDATE_SAMPLE_COUNT, 1)
        self.assertTrue(A2_MAX_STATUS_PROBE_NAME.startswith(A2_MAX_CANDIDATE_PREFIX))
        self.assertEqual(A2_MAX_STATUS_PROBE_ADDRESS, "00:11:22:33:44:55")
        self.assertIsNone(detect_model(A2_MAX_STATUS_PROBE_NAME))

    def test_status_packet_is_exact_and_checksum_is_valid(self) -> None:
        command = a2max.build_a2max_status_command()
        self.assertEqual(command.message_id, (0, 2))
        self.assertEqual(command.packet, bytes.fromhex("5A 01 06 00 02 04 01 00"))
        self.assertEqual(calculate_checksum(command.packet[:-1]), command.packet[-1])
        self.assertFalse(command.changes_state)

    def test_probe_validator_rejects_non_status_packets(self) -> None:
        for family, mode, parameters in (
            (0x5A, 0x05, [0x0B, 0xFF, 0xFF]),
            (0x5A, 0x07, [0, 50]),
            (0x5A, 0x09, [26, 9, 3, 12, 0, 0]),
            (0xA5, 0x19, [0] * 14),
        ):
            with self.subTest(family=family, mode=mode):
                packet = create_command_encoding(family, mode, (0, 2), parameters)
                command = Command("forbidden", (packet[3], packet[4]), packet, True)
                with self.assertRaises(TransportSafetyError):
                    a2max.validate_a2max_status_command(command)

    def test_live_path_is_service_scoped_and_has_one_fixed_write_site(self) -> None:
        source = inspect.getsource(a2max.run_a2max_status_probe)
        self.assertEqual(source.count("write_gatt_char("), 1)
        self.assertIn("resolved.rx, command.packet, response=False", source)
        self.assertEqual(source.count("start_notify("), 1)
        self.assertIn("resolved.tx, on_notification", source)
        self.assertIn("BleakClient(ble_device, pair=False", source)
        self.assertNotIn("get_characteristic", source)
        self.assertNotIn("read_gatt_", source)

    def test_dry_run_does_not_load_bleak_or_access_live_entry_point(self) -> None:
        before = "bleak" in sys.modules
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = chihirosctl.main(["a2max-status-probe", "--dry-run"])
        self.assertEqual(result, 0)
        self.assertEqual("bleak" in sys.modules, before)
        rendered = output.getvalue()
        self.assertIn(f"{A2_MAX_STATUS_PROBE_NAME} / {A2_MAX_STATUS_PROBE_ADDRESS}", rendered)
        self.assertIn("5A 01 06 00 02 04 01 00", rendered)
        self.assertIn("01 XOR 06 XOR 00 XOR 02 XOR 04 XOR 01 = 00", rendered)
        self.assertIn("no scan, connection, subscription, or characteristic write", rendered)

class A2MaxManualPlanTests(unittest.TestCase):
    def test_levels_have_exact_packets_and_checksums(self) -> None:
        expected = {
            1: "5A 01 07 00 03 07 00 01 03",
            20: "5A 01 07 00 03 07 00 14 16",
            60: "5A 01 07 00 03 07 00 3C 3E",
            100: "5A 01 07 00 03 07 00 64 66",
            88: "5A 01 07 00 04 07 00 58 5D",  # ID 0003 would give checksum 5A
            90: "5A 01 07 00 03 07 00 5A 58",  # literal normalized wire value
        }
        for level, packet_hex in expected.items():
            with self.subTest(level=level):
                manual, brightness = a2max.build_a2max_manual_plan(level)
                self.assertEqual(manual.message_id, (0, 2))
                self.assertEqual(manual.packet_hex, "5A 01 08 00 02 05 0B FF FF 05")
                self.assertEqual(brightness.message_id, (0, 4 if level == 88 else 3))
                self.assertEqual(brightness.packet_hex, packet_hex)
                for command in (manual, brightness):
                    self.assertTrue(command.changes_state)
                    self.assertEqual(calculate_checksum(command.packet[:-1]), command.packet[-1])

    def test_all_accepted_levels_are_literal_and_sequences_are_fresh(self) -> None:
        for level in range(1, 101):
            with self.subTest(level=level):
                commands = a2max.build_a2max_manual_plan(level)
                self.assertEqual(commands, a2max.build_a2max_manual_plan(level))
                self.assertEqual(commands[1].packet[6:-1], bytes([0, level]))
                for command in commands:
                    self.assertNotIn(0x5A, command.message_id)
                    self.assertNotEqual(command.packet[-1], 0x5A)
                a2max.validate_a2max_manual_plan(commands, level)

    def test_invalid_levels_are_rejected_without_coercion(self) -> None:
        for level in (0, -1, 101, 256, True, False, 1.0, 60.0, "20", None):
            with self.subTest(level=level):
                for function in (
                    a2max.validate_a2max_level,
                    a2max.build_a2max_manual_plan,
                    a2max.format_a2max_manual_plan,
                ):
                    with self.assertRaises(ValueError):
                        function(level)

    def test_manual_validator_rejects_any_plan_variation(self) -> None:
        manual, brightness = a2max.build_a2max_manual_plan(20)
        alternate_packet = create_command_encoding(0x5A, 0x07, (0, 3), [0, 21])
        alternate = Command("alternate", (0, 3), alternate_packet, True)
        for plan in (
            (),
            (brightness, manual),
            (manual,),
            (manual, alternate),
            (manual, brightness, brightness),
            (Command(manual.logical_name, manual.message_id, manual.packet, False), brightness),
            (manual, Command("wrong metadata", (0, 2), brightness.packet, True)),
            (manual, Command("bad checksum", (0, 3), brightness.packet[:-1] + b"\x00", True)),
        ):
            with self.subTest(plan=plan):
                with self.assertRaises(TransportSafetyError):
                    a2max.validate_a2max_manual_plan(plan, 20)
        for family, mode, parameters in (
            (0x5A, 0x04, [1]),  # no status prelude
            (0x5A, 0x05, [0x12, 0xFF, 0xFF]),  # no auto restore
            (0x5A, 0x05, [5, 0xFF, 0xFF]),  # no schedule reset
            (0x5A, 0x07, [1, 20]),  # white channel zero only
            (0x5A, 0x07, [0, 0]),  # no OFF
            (0x5A, 0x09, [26, 9, 3, 12, 0, 0]),  # no RTC
            (0xA5, 0x19, [0] * 14),  # no schedule
        ):
            packet = create_command_encoding(family, mode, (0, 3), parameters)
            with self.subTest(family=family, mode=mode, parameters=parameters):
                with self.assertRaises(TransportSafetyError):
                    a2max.validate_a2max_manual_plan(
                        (manual, Command("forbidden", (0, 3), packet, True)), 20
                    )

    def test_brightness_live_path_has_no_read_subscription_pair_or_raw_input(self) -> None:
        source = inspect.getsource(a2max.run_a2max_manual)
        self.assertEqual(source.count("write_gatt_char("), 1)
        self.assertIn("resolved.rx, command.packet, response=False", source)
        self.assertIn("BleakClient(ble_device, pair=False", source)
        self.assertNotIn("start_notify", source)
        self.assertNotIn("read_gatt_", source)
        signature = inspect.signature(a2max.run_a2max_manual)
        self.assertEqual(set(signature.parameters), {"log_dir", "level", "scan_seconds", "connect_timeout"})

    def test_manual_dry_run_is_ble_free_and_discloses_evidence_limits(self) -> None:
        before = "bleak" in sys.modules
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(chihirosctl, "run_a2max_manual") as live:
            result = chihirosctl.main(["a2max-manual", "--level", "60", "--dry-run"])
        live.assert_not_called()
        self.assertEqual(result, 0)
        self.assertEqual("bleak" in sys.modules, before)
        rendered = output.getvalue()
        self.assertIn(f"{A2_MAX_STATUS_PROBE_NAME} / {A2_MAX_STATUS_PROBE_ADDRESS}", rendered)
        self.assertIn("5A 01 08 00 02 05 0B FF FF 05", rendered)
        self.assertIn("5A 01 07 00 03 07 00 3C 3E", rendered)
        self.assertIn("01 XOR 07 XOR 00 XOR 03 XOR 07 XOR 00 XOR 3C = 3E", rendered)
        self.assertIn("max(1, floor(level * 100 / max_level[channel]))", rendered)
        self.assertIn("0x16 is firmware/protocol version 22", rendered)
        self.assertIn("not a universal claim of equivalence to the official-app UI percentage", rendered)
        self.assertIn("survived physical power loss", rendered)
        self.assertIn("power cycling is not a rollback", rendered)
        self.assertIn("FE59", rendered)
        self.assertIn("no scan, connection, subscription, read, or characteristic write", rendered)

    def test_cli_rejects_invalid_levels_missing_gates_and_extra_controls(self) -> None:
        invalid_argv = [
            ["a2max-manual", "--live", "--level", level]
            for level in ("0", "-1", "101", "1.5", "NaN", "true", "0x14")
        ]
        invalid_argv.extend([
            ["a2max-manual", "--level", "60"],
            ["a2max-manual", "--dry-run"],
            ["a2max-manual", "--level", "60", "--dry-run", "--live"],
            ["a2max-brightness-probe", "--live"],
        ])
        for option in ("--address", "--name", "--channel", "--packet", "--raw", "--rtc", "--auto"):
            invalid_argv.append(["a2max-manual", "--level", "60", "--live", option, "1"])
        with mock.patch.object(chihirosctl, "run_a2max_manual") as live:
            for argv in invalid_argv:
                with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        chihirosctl.main(argv)
                    self.assertEqual(error.exception.code, 2)
        live.assert_not_called()

    def test_cli_routes_level_to_mocked_live_entry_point(self) -> None:
        with mock.patch.object(
            chihirosctl, "run_a2max_manual", new_callable=mock.AsyncMock, return_value=Path("mock.json")
        ) as live, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(chihirosctl.main(["a2max-manual", "--level", "60", "--live"]), 0)
        live.assert_awaited_once_with(chihirosctl.DEFAULT_LOG_DIR.resolve(), level=60, scan_seconds=20.0, connect_timeout=30.0)


class _FakeCharacteristic:
    def __init__(
        self,
        uuid: str,
        handle: int,
        properties: tuple[str, ...],
        service_uuid: str,
    ) -> None:
        self.uuid = uuid
        self.handle = handle
        self.properties = properties
        self.service_uuid = service_uuid
        self.descriptors: tuple[object, ...] = ()


class _FakeService:
    def __init__(self, uuid: str, handle: int, characteristics: tuple[_FakeCharacteristic, ...]) -> None:
        self.uuid = uuid
        self.handle = handle
        self.characteristics = characteristics


def _confirmed_gatt() -> tuple[tuple[_FakeService, ...], _FakeCharacteristic, _FakeCharacteristic]:
    rx = _FakeCharacteristic(NUS_RX_UUID, 15, ("write", "write-without-response"), NUS_SERVICE_UUID)
    tx = _FakeCharacteristic(NUS_TX_UUID, 17, ("notify",), NUS_SERVICE_UUID)
    dfu = _FakeCharacteristic(DFU_BUTTONLESS_UUID, 21, ("write", "indicate"), DFU_SERVICE_UUID)
    return (
        (
            _FakeService(NUS_SERVICE_UUID, 14, (rx, tx)),
            _FakeService(DFU_SERVICE_UUID, 20, (dfu,)),
        ),
        rx,
        tx,
    )


class A2MaxStatusProbeLiveBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_fake_device_gets_one_exact_nus_rx_write(self) -> None:
        services, rx, tx = _confirmed_gatt()
        test_case = self
        device = types.SimpleNamespace(
            name=A2_MAX_STATUS_PROBE_NAME,
            address=A2_MAX_STATUS_PROBE_ADDRESS,
        )
        advertisement = types.SimpleNamespace(local_name=A2_MAX_STATUS_PROBE_NAME)
        clients: list[object] = []

        class FakeScanner:
            @staticmethod
            async def discover(*, timeout: float, return_adv: bool) -> dict[str, tuple[object, object]]:
                self.assertEqual(timeout, 1.0)
                self.assertTrue(return_adv)
                return {"confirmed": (device, advertisement)}

        class FakeClient:
            def __init__(self, selected: object, *, pair: bool, timeout: float) -> None:
                self.selected = selected
                self.pair = pair
                self.timeout = timeout
                self.is_connected = False
                self.services = services
                self.writes: list[tuple[object, bytes, bool]] = []
                self.notify_target: object | None = None
                self.callback: object | None = None
                clients.append(self)

            async def connect(self) -> None:
                self.is_connected = True

            async def start_notify(self, characteristic: object, callback: object) -> None:
                self.notify_target = characteristic
                self.callback = callback

            async def write_gatt_char(
                self,
                characteristic: object,
                packet: bytes,
                *,
                response: bool,
            ) -> None:
                self.writes.append((characteristic, packet, response))
                assert callable(self.callback)
                self.callback(tx, bytearray.fromhex("5B 17 0A 00 01 0A 01 FF FF FF FF 13 88 8C"))

            async def stop_notify(self, characteristic: object) -> None:
                test_case.assertIs(characteristic, tx)

            async def disconnect(self) -> None:
                self.is_connected = False

        fake_bleak = types.ModuleType("bleak")
        fake_bleak.BleakScanner = FakeScanner
        fake_bleak.BleakClient = FakeClient
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(sys.modules, {"bleak": fake_bleak}):
                notifications, log_path = await a2max.run_a2max_status_probe(
                    Path(temp_dir),
                    scan_seconds=1.0,
                    connect_timeout=2.0,
                    notification_seconds=0.001,
                )

        self.assertEqual(len(clients), 1)
        client = clients[0]
        self.assertIs(client.selected, device)
        self.assertFalse(client.pair)
        self.assertEqual(client.timeout, 2.0)
        self.assertIs(client.notify_target, tx)
        self.assertEqual(client.writes, [(rx, a2max.A2_MAX_STATUS_PACKET, False)])
        self.assertEqual(notifications, [bytes.fromhex("5B 17 0A 00 01 0A 01 FF FF FF FF 13 88 8C")])
        self.assertTrue(log_path.name.endswith(".json"))

    async def test_identity_mismatch_aborts_before_client_construction(self) -> None:
        test_case = self
        device = types.SimpleNamespace(name="WRONG", address=A2_MAX_STATUS_PROBE_ADDRESS)
        advertisement = types.SimpleNamespace(local_name="WRONG")

        class FakeScanner:
            @staticmethod
            async def discover(*, timeout: float, return_adv: bool) -> dict[str, tuple[object, object]]:
                return {"wrong-name": (device, advertisement)}

        class ForbiddenClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                test_case.fail("BLE client must not be constructed after an identity mismatch")

        fake_bleak = types.ModuleType("bleak")
        fake_bleak.BleakScanner = FakeScanner
        fake_bleak.BleakClient = ForbiddenClient
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(sys.modules, {"bleak": fake_bleak}):
                with self.assertRaisesRegex(TransportSafetyError, f"expected '{A2_MAX_STATUS_PROBE_NAME}'"):
                    await a2max.run_a2max_status_probe(
                        Path(temp_dir),
                        scan_seconds=1.0,
                        connect_timeout=2.0,
                        notification_seconds=0.001,
                    )


class _ManualBleakFixture:
    """Only scan/connect/write/disconnect exist; all other BLE calls fail tests."""

    def __init__(
        self,
        *,
        name: str = A2_MAX_STATUS_PROBE_NAME,
        address: str = A2_MAX_STATUS_PROBE_ADDRESS,
        fail_write: int | None = None,
        change_gatt_after_first_write: bool = False,
        fail_phase: str | None = None,
        failure: BaseException | None = None,
    ) -> None:
        self.services, self.rx, self.tx = _confirmed_gatt()
        self.scan_count = 0
        self.clients: list[object] = []
        self.writes: list[tuple[object, bytes, bool]] = []
        self.disconnect_count = 0
        self.output = io.StringIO()
        self.error_output = io.StringIO()
        fixture = self
        device = types.SimpleNamespace(name=name, address=address)
        advertisement = types.SimpleNamespace(local_name=name)

        def fail_at(phase: str) -> None:
            if fail_phase == phase:
                raise failure if failure is not None else TimeoutError()

        class FakeScanner:
            @staticmethod
            async def discover(*, timeout: float, return_adv: bool) -> dict[str, tuple[object, object]]:
                fixture.scan_count += 1
                # The full exact transaction must be printed BEFORE Bluetooth.
                assert "Checksum:" in fixture.output.getvalue()
                assert "1. Manual-mode packet:" in fixture.output.getvalue()
                assert "2. White channel 0" in fixture.output.getvalue()
                assert timeout == 1.0 and return_adv is True
                fail_at("scanning")
                return {"confirmed": (device, advertisement)}

        class FakeClient:
            def __init__(self, selected: object, *, pair: bool, timeout: float) -> None:
                self.selected = selected
                self.pair = pair
                self.timeout = timeout
                self.is_connected = False
                self._services = fixture.services
                fixture.clients.append(self)

            @property
            def services(self) -> tuple[_FakeService, ...]:
                fail_at("resolving-NUS")
                return self._services

            @services.setter
            def services(self, value: tuple[_FakeService, ...]) -> None:
                self._services = value

            async def connect(self) -> None:
                fail_at("connecting")
                self.is_connected = True

            async def write_gatt_char(
                self,
                characteristic: object,
                packet: bytes,
                *,
                response: bool,
            ) -> None:
                fixture.writes.append((characteristic, packet, response))
                fail_at("writing-manual-mode" if len(fixture.writes) == 1 else "writing-brightness")
                if len(fixture.writes) == fail_write:
                    raise OSError("injected uncertain write failure")
                if change_gatt_after_first_write and len(fixture.writes) == 1:
                    self.services = (fixture.services[1],)  # NUS disappears; only DFU remains

            async def disconnect(self) -> None:
                fixture.disconnect_count += 1
                fail_at("disconnecting")
                self.is_connected = False

        self.module = types.ModuleType("bleak")
        self.module.BleakScanner = FakeScanner
        self.module.BleakClient = FakeClient


class A2MaxManualLiveBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_levels_send_exactly_two_writes_to_scoped_rx_and_log_level(self) -> None:
        for level in (1, 20, 60, 100, 88, 90):
            with self.subTest(level=level):
                fixture = _ManualBleakFixture()
                # Different handles must be accepted; UUID topology is authoritative.
                fixture.services[0].handle = 50
                fixture.rx.handle = 51
                fixture.tx.handle = 52
                with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
                    sys.modules, {"bleak": fixture.module}
                ), contextlib.redirect_stdout(fixture.output):
                    log_path = await a2max.run_a2max_manual(
                        Path(temp_dir), level=level, scan_seconds=1.0, connect_timeout=2.0
                    )
                    document = json.loads(log_path.read_text(encoding="utf-8"))
                self.assertEqual(fixture.scan_count, 1)
                self.assertEqual(len(fixture.clients), 1)
                client = fixture.clients[0]
                self.assertEqual(client.selected.address, A2_MAX_STATUS_PROBE_ADDRESS)
                self.assertFalse(client.pair)
                self.assertEqual(client.timeout, 2.0)
                self.assertFalse(client.is_connected)
                self.assertEqual(fixture.disconnect_count, 1)
                self.assertEqual(
                    fixture.writes,
                    [(fixture.rx, command.packet, False) for command in a2max.build_a2max_manual_plan(level)],
                )
                policy = document["events"][0]
                self.assertEqual(policy["normalized_wire_level"], level)
                self.assertEqual(policy["retries"], 0)
                self.assertFalse(policy["ui_percentage_claimed"])
                self.assertEqual(document["logical_command"], f"manual_wire_level_{level}")
                self.assertIn("finished_at_utc", document)

    async def test_invalid_level_aborts_before_bleak_import_or_log_creation(self) -> None:
        original_import = __import__

        def forbid_bleak(name: str, *args: object, **kwargs: object) -> object:
            if name == "bleak":
                self.fail("Invalid input must fail before loading Bleak")
            return original_import(name, *args, **kwargs)

        for level in (0, -1, 101, True, False, 60.0, "60", None):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as temp_dir:
                with mock.patch("builtins.__import__", side_effect=forbid_bleak):
                    with self.assertRaises(ValueError):
                        await a2max.run_a2max_manual(Path(temp_dir), level=level)
                self.assertEqual(list(Path(temp_dir).iterdir()), [])

    async def test_identity_mismatches_abort_before_client_construction(self) -> None:
        for identity in (
            {"name": "WRONG"},
            {"address": "AA:BB:CC:DD:EE:FF"},
        ):
            with self.subTest(identity=identity):
                fixture = _ManualBleakFixture(**identity)
                with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
                    sys.modules, {"bleak": fixture.module}
                ), contextlib.redirect_stdout(fixture.output):
                    with self.assertRaises(TransportSafetyError):
                        await a2max.run_a2max_manual(
                            Path(temp_dir), level=60, scan_seconds=1.0, connect_timeout=2.0
                        )
                self.assertEqual(fixture.clients, [])
                self.assertEqual(fixture.writes, [])

    async def test_dfu_never_substitutes_for_nus_and_duplicates_abort(self) -> None:
        for variant in ("missing_nus", "duplicate_rx_in_dfu", "wrong_rx_property"):
            with self.subTest(variant=variant):
                fixture = _ManualBleakFixture()
                if variant == "missing_nus":
                    fixture.services = (fixture.services[1],)
                elif variant == "duplicate_rx_in_dfu":
                    fixture.services[1].characteristics += (
                        _FakeCharacteristic(NUS_RX_UUID, 22, ("write-without-response",), DFU_SERVICE_UUID),
                    )
                else:
                    fixture.rx.properties = ("read",)
                with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
                    sys.modules, {"bleak": fixture.module}
                ), contextlib.redirect_stdout(fixture.output):
                    with self.assertRaises(TransportSafetyError):
                        await a2max.run_a2max_manual(
                            Path(temp_dir), level=60, scan_seconds=1.0, connect_timeout=2.0
                        )
                self.assertEqual(fixture.writes, [])
                self.assertEqual(fixture.disconnect_count, 1)

    async def test_endpoints_are_rechecked_before_second_write(self) -> None:
        fixture = _ManualBleakFixture(change_gatt_after_first_write=True)
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            sys.modules, {"bleak": fixture.module}
        ), contextlib.redirect_stdout(fixture.output):
            with self.assertRaises(TransportSafetyError):
                await a2max.run_a2max_manual(
                    Path(temp_dir),
                    level=60,
                    scan_seconds=1.0,
                    connect_timeout=2.0,
                )
        self.assertEqual(len(fixture.writes), 1)
        self.assertEqual(fixture.disconnect_count, 1)

    async def test_failed_write_is_not_retried_or_followed_by_restore(self) -> None:
        for fail_write in (1, 2):
            with self.subTest(fail_write=fail_write):
                fixture = _ManualBleakFixture(fail_write=fail_write)
                with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
                    sys.modules, {"bleak": fixture.module}
                ), contextlib.redirect_stdout(fixture.output):
                    with self.assertRaisesRegex(OSError, "uncertain write failure"):
                        await a2max.run_a2max_manual(
                            Path(temp_dir), level=60, scan_seconds=1.0, connect_timeout=2.0
                        )
                    logs = list(Path(temp_dir).glob("*.json"))
                    self.assertEqual(len(logs), 1)
                    document = json.loads(logs[0].read_text(encoding="utf-8"))
                self.assertEqual(fixture.scan_count, 1)
                self.assertEqual(len(fixture.clients), 1)
                self.assertEqual(len(fixture.writes), fail_write)
                self.assertEqual(fixture.disconnect_count, 1)
                self.assertIn("may have reached the device", fixture.output.getvalue())
                self.assertIn("finished_at_utc", document)
                error = next(event for event in document["events"] if event["event"] == "error")
                self.assertEqual(error["writes_attempted"], fail_write)
                self.assertEqual(error["writes_completed"], fail_write - 1)


class A2MaxManualDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_phase_order_and_packet_timing_are_unchanged(self) -> None:
        fixture = _ManualBleakFixture()
        expected_phases = [
            "initializing-session-log", "loading-Bleak", "scanning", "exact-device-found",
            "connecting", "connected", "resolving-NUS", "writing-manual-mode",
            "manual-mode-write-complete", "waiting-30ms", "writing-brightness",
            "brightness-write-complete", "disconnecting",
        ]
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            sys.modules, {"bleak": fixture.module}
        ), contextlib.redirect_stdout(fixture.output), mock.patch.object(
            a2max.asyncio, "sleep", new_callable=mock.AsyncMock
        ) as sleep:
            log_path = await a2max.run_a2max_manual(
                Path(temp_dir), level=60, scan_seconds=1.0, connect_timeout=2.0
            )
            document = json.loads(log_path.read_text(encoding="utf-8"))
        sleep.assert_awaited_once_with(0.03)
        self.assertEqual(
            [line.removeprefix("A2 Max phase: ") for line in fixture.output.getvalue().splitlines()
             if line.startswith("A2 Max phase: ")],
            expected_phases,
        )
        self.assertEqual(
            [event["phase"] for event in document["events"] if event["event"] == "execution_phase"],
            expected_phases[1:],
        )
        self.assertEqual(fixture.writes, [
            (fixture.rx, bytes.fromhex("5A 01 08 00 02 05 0B FF FF 05"), False),
            (fixture.rx, bytes.fromhex("5A 01 07 00 03 07 00 3C 3E"), False),
        ])
        summary = document["events"][-1]
        self.assertEqual(summary["event"], "manual_diagnostic_summary")
        self.assertEqual(summary["packet_attempt_state"], "both-packets-attempted")
        self.assertEqual(summary["writes_completed"], 2)
        self.assertEqual(summary["outcome"], "completed")
        self.assertIsNone(summary["failure_phase"])

    async def test_empty_exception_reports_abort_phase_counts_and_log_path(self) -> None:
        cases = (
            ("scanning", 0, 0),
            ("connecting", 0, 0),
            ("resolving-NUS", 0, 0),
            ("writing-manual-mode", 1, 0),
            ("writing-brightness", 2, 1),
        )
        for phase, attempted, completed in cases:
            with self.subTest(phase=phase):
                failure = TimeoutError()
                fixture = _ManualBleakFixture(fail_phase=phase, failure=failure)
                with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
                    sys.modules, {"bleak": fixture.module}
                ), contextlib.redirect_stdout(fixture.output), contextlib.redirect_stderr(fixture.error_output):
                    with self.assertRaises(TimeoutError) as raised:
                        await a2max.run_a2max_manual(
                            Path(temp_dir), level=60, scan_seconds=1.0, connect_timeout=2.0
                        )
                    logs = list(Path(temp_dir).glob("*.json"))
                    self.assertEqual(len(logs), 1)
                    document = json.loads(logs[0].read_text(encoding="utf-8"))
                self.assertIs(raised.exception, failure)
                self.assertIn(f"phase: {phase}", fixture.error_output.getvalue())
                self.assertIn("exception type: builtins.TimeoutError", fixture.error_output.getvalue())
                self.assertIn("repr(exception): TimeoutError()", fixture.error_output.getvalue())
                self.assertIn("str(exception): ''", fixture.error_output.getvalue())
                self.assertIn(f"Session log: {logs[0]}", fixture.output.getvalue())
                self.assertIn(f"A2 Max failure phase: {phase}", fixture.output.getvalue())
                error = next(event for event in document["events"] if event["event"] == "error")
                self.assertEqual(error["phase"], phase)
                self.assertEqual(error["error_type"], "TimeoutError")
                self.assertEqual(error["exception_repr"], "TimeoutError()")
                self.assertEqual(error["exception_str"], "")
                summary = document["events"][-1]
                self.assertEqual(summary["failure_phase"], phase)
                self.assertEqual(summary["outcome"], "failed")
                self.assertEqual(summary["writes_attempted"], attempted)
                self.assertEqual(summary["writes_completed"], completed)
                state = ("zero-packets-attempted", "manual-mode-only-attempted", "both-packets-attempted")[attempted]
                self.assertEqual(summary["packet_attempt_state"], state)
                self.assertEqual(summary["manual_mode_attempted"], attempted >= 1)
                self.assertEqual(summary["brightness_attempted"], attempted >= 2)
                self.assertIn(state, fixture.output.getvalue())
                self.assertEqual(len(fixture.writes), attempted)
                self.assertEqual(fixture.scan_count, 1)
                self.assertEqual(fixture.disconnect_count, int(phase not in {"scanning", "connecting"}))
                self.assertIn("finished_at_utc", document)

    async def test_cancellation_during_30ms_wait_logs_manual_only_and_cleans_up(self) -> None:
        fixture = _ManualBleakFixture()
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            sys.modules, {"bleak": fixture.module}
        ), contextlib.redirect_stdout(fixture.output), contextlib.redirect_stderr(fixture.error_output), mock.patch.object(
            a2max.asyncio, "sleep", new_callable=mock.AsyncMock, side_effect=asyncio.CancelledError()
        ) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await a2max.run_a2max_manual(
                    Path(temp_dir), level=60, scan_seconds=1.0, connect_timeout=2.0
                )
            document = json.loads(next(Path(temp_dir).glob("*.json")).read_text(encoding="utf-8"))
        sleep.assert_awaited_once_with(0.03)
        self.assertEqual(len(fixture.writes), 1)
        self.assertEqual(fixture.disconnect_count, 1)
        self.assertEqual(document["events"][-1]["failure_phase"], "waiting-30ms")
        self.assertEqual(document["events"][-1]["packet_attempt_state"], "manual-mode-only-attempted")
        self.assertIn("CancelledError()", fixture.error_output.getvalue())

    async def test_disconnect_exception_reports_both_attempts_without_retry(self) -> None:
        fixture = _ManualBleakFixture(fail_phase="disconnecting")
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            sys.modules, {"bleak": fixture.module}
        ), contextlib.redirect_stdout(fixture.output), contextlib.redirect_stderr(fixture.error_output):
            log_path = await a2max.run_a2max_manual(
                Path(temp_dir), level=60, scan_seconds=1.0, connect_timeout=2.0
            )
            document = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(fixture.disconnect_count, 1)
        self.assertEqual(len(fixture.writes), 2)
        error = next(event for event in document["events"] if event["event"] == "disconnect_error")
        self.assertEqual(error["phase"], "disconnecting")
        self.assertEqual(error["exception_repr"], "TimeoutError()")
        self.assertEqual(document["events"][-1]["failure_phase"], "disconnecting")
        self.assertEqual(document["events"][-1]["writes_completed"], 2)
        self.assertIn(f"Session log: {log_path}", fixture.output.getvalue())

    async def test_cleanup_and_log_failures_do_not_mask_original_write_exception(self) -> None:
        original = TimeoutError()
        fixture = _ManualBleakFixture(fail_phase="writing-manual-mode", failure=original)
        original_record = a2max.LocalSessionLog.record

        def broken_error_record(log: object, event: str, **details: object) -> None:
            # Simulate a flush error after retaining the diagnostic event.
            original_record(log, event, **details)
            if event == "error":
                raise OSError("diagnostic flush failure")

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            sys.modules, {"bleak": fixture.module}
        ), contextlib.redirect_stdout(fixture.output), contextlib.redirect_stderr(fixture.error_output), mock.patch.object(
            fixture.module.BleakClient, "disconnect", new_callable=mock.AsyncMock, side_effect=OSError("cleanup failure")
        ) as disconnect, mock.patch.object(
            a2max.LocalSessionLog, "record", new=broken_error_record
        ), mock.patch.object(a2max.LocalSessionLog, "finish", side_effect=OSError("final flush failure")):
            with self.assertRaises(TimeoutError) as raised:
                await a2max.run_a2max_manual(
                    Path(temp_dir), level=60, scan_seconds=1.0, connect_timeout=2.0
                )
            log_path = next(Path(temp_dir).glob("*.json"))
            document = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertIs(raised.exception, original)
        disconnect.assert_awaited_once()
        self.assertEqual(len(fixture.writes), 1)
        self.assertEqual(document["events"][-1]["failure_phase"], "writing-manual-mode")
        self.assertIn("Diagnostic log write failed", fixture.error_output.getvalue())
        self.assertIn("Diagnostic log finalization failed", fixture.error_output.getvalue())
        self.assertIn("may be incomplete", fixture.error_output.getvalue())
        self.assertIn(f"Session log: {log_path}", fixture.output.getvalue())

    async def test_disconnect_cancellation_is_reported_and_propagated(self) -> None:
        failure = asyncio.CancelledError()
        fixture = _ManualBleakFixture(fail_phase="disconnecting", failure=failure)
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            sys.modules, {"bleak": fixture.module}
        ), contextlib.redirect_stdout(fixture.output), contextlib.redirect_stderr(fixture.error_output):
            with self.assertRaises(asyncio.CancelledError) as raised:
                await a2max.run_a2max_manual(
                    Path(temp_dir), level=60, scan_seconds=1.0, connect_timeout=2.0
                )
            log_path = next(Path(temp_dir).glob("*.json"))
            document = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertIs(raised.exception, failure)
        self.assertEqual(fixture.disconnect_count, 1)
        self.assertEqual(len(fixture.writes), 2)
        self.assertEqual(document["events"][-1]["failure_phase"], "disconnecting")
        self.assertEqual(document["events"][-1]["outcome"], "failed")
        self.assertIn("phase: disconnecting", fixture.error_output.getvalue())
        self.assertIn("CancelledError()", fixture.error_output.getvalue())
        self.assertIn(f"Session log: {log_path}", fixture.output.getvalue())
        self.assertIn("finished_at_utc", document)

    async def test_log_creation_failure_reports_zero_attempts_without_bluetooth(self) -> None:
        fixture = _ManualBleakFixture()
        failure = PermissionError("cannot create diagnostic log")
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            sys.modules, {"bleak": fixture.module}
        ), contextlib.redirect_stdout(fixture.output), contextlib.redirect_stderr(fixture.error_output), mock.patch.object(
            a2max, "LocalSessionLog", side_effect=failure
        ):
            with self.assertRaises(PermissionError) as raised:
                await a2max.run_a2max_manual(Path(temp_dir), level=60)
        self.assertIs(raised.exception, failure)
        self.assertEqual(fixture.scan_count, 0)
        self.assertEqual(fixture.writes, [])
        self.assertIn("phase: initializing-session-log", fixture.error_output.getvalue())
        self.assertIn("Session log unavailable", fixture.error_output.getvalue())
        self.assertIn("zero-packets-attempted", fixture.output.getvalue())

    async def test_bleak_import_failure_still_writes_diagnostic_log(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            sys.modules, {"bleak": None}
        ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            with self.assertRaises(ModuleNotFoundError):
                await a2max.run_a2max_manual(Path(temp_dir), level=60)
            log_path = next(Path(temp_dir).glob("*.json"))
            document = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(document["events"][-1]["failure_phase"], "loading-Bleak")
        self.assertEqual(document["events"][-1]["writes_attempted"], 0)
        self.assertIn(f"Session log: {log_path}", output.getvalue())
        self.assertIn("finished_at_utc", document)


class A2MaxManualDiagnosticCliTests(unittest.TestCase):
    def test_empty_connection_timeout_cli_is_not_an_empty_error(self) -> None:
        fixture = _ManualBleakFixture(fail_phase="connecting", failure=TimeoutError())
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            sys.modules, {"bleak": fixture.module}
        ), contextlib.redirect_stdout(fixture.output), contextlib.redirect_stderr(fixture.error_output):
            result = chihirosctl.main([
                "--log-dir", temp_dir, "a2max-manual", "--level", "60", "--live",
                "--scan-seconds", "1", "--connect-timeout", "2",
            ])
            log_path = next(Path(temp_dir).glob("*.json"))
        self.assertEqual(result, 2)
        self.assertIn("Error: TimeoutError: TimeoutError()", fixture.error_output.getvalue())
        self.assertIn("phase: connecting", fixture.error_output.getvalue())
        self.assertIn(f"Session log: {log_path}", fixture.output.getvalue())
        self.assertIn("zero-packets-attempted", fixture.output.getvalue())
        self.assertNotIn("writes submitted", fixture.output.getvalue())
        self.assertEqual(fixture.writes, [])


if __name__ == "__main__":
    unittest.main()
