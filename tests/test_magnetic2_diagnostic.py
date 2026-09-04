from __future__ import annotations

import asyncio
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock

from chihiros import magnetic2_diagnostic as diagnostic
from chihiros.constants import (
    DFU_BUTTONLESS_UUID, DFU_SERVICE_UUID, NUS_RX_UUID, NUS_SERVICE_UUID,
    NUS_TX_UUID, detect_model,
)
from chihiros.protocol import create_command_encoding
from chihiros.transport import TransportSafetyError


# Synthetic identity only. No local configuration or physical device is read.
ADDRESS = "00:11:22:33:44:55"
NAME = "DYMNC001122334455"
NEW_CHANNEL_CASES = (
    ("channel1-test", 1, bytes.fromhex("5A 01 07 00 03 07 01 14 17")),
    ("channel2-test", 2, bytes.fromhex("5A 01 07 00 03 07 02 14 14")),
    ("channel3-test", 3, bytes.fromhex("5A 01 07 00 03 07 03 14 15")),
)
ISOLATED_COMMON_GOLDEN = tuple(bytes.fromhex(packet) for packet in (
    "5A 01 08 00 02 05 0B FF FF 05",
    "5A 01 07 00 03 07 00 00 02",
    "5A 01 07 00 04 07 01 00 04",
    "5A 01 07 00 05 07 02 00 06",
    "5A 01 07 00 06 07 03 00 04",
))
ISOLATED_CASES = (
    ("isolated-channel1-test", 1, bytes.fromhex("5A 01 07 00 07 07 01 14 13")),
    ("isolated-channel2-test", 2, bytes.fromhex("5A 01 07 00 07 07 02 14 10")),
    ("isolated-channel3-test", 3, bytes.fromhex("5A 01 07 00 07 07 03 14 11")),
)
COMBINED_GOLDEN = tuple(bytes.fromhex(packet) for packet in (
    "5A 01 08 00 02 05 0B FF FF 05",
    "5A 01 07 00 03 07 00 14 16",
    "5A 01 07 00 04 07 01 28 2C",
    "5A 01 07 00 05 07 02 3C 3A",
    "5A 01 07 00 06 07 03 1E 1A",
))


def layout():
    rx = NS(uuid=NUS_RX_UUID, handle=105, properties=["write-without-response"],
            service_uuid=NUS_SERVICE_UUID, descriptors=[])
    tx = NS(uuid=NUS_TX_UUID, handle=108, properties=["notify"],
            service_uuid=NUS_SERVICE_UUID, descriptors=[])
    nus = NS(uuid=NUS_SERVICE_UUID, handle=100, characteristics=[rx, tx])
    dfu_char = NS(uuid=DFU_BUTTONLESS_UUID, handle=205, properties=["write", "indicate"],
                  service_uuid=DFU_SERVICE_UUID, descriptors=[])
    dfu = NS(uuid=DFU_SERVICE_UUID, handle=200, characteristics=[dfu_char])
    return [nus, dfu], rx, tx


class MagneticDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.events = []
        self.services, self.rx, self.tx = layout()
        self.device = NS(address=ADDRESS, name=NAME)
        self.advertisement = NS(local_name=NAME)
        self.discoveries = {ADDRESS: (self.device, self.advertisement)}
        self.client_address = ADDRESS
        self.after_subscribe = lambda: None
        self.after_write = lambda: None
        self.write_error = False
        self.fail_write_index = None
        self.subscribe_error = False
        self.notifications = [bytes.fromhex("5B 01 02"), bytes.fromhex("B6 02 03 04")]
        outer = self

        class FakeScanner:
            @staticmethod
            async def discover(**kwargs):
                outer.events.append(("scan", kwargs))
                return outer.discoveries

        class FakeClient:
            def __init__(self, device, *, pair, timeout):
                outer.events.append(("client", device, pair, timeout))
                outer.assertIs(device, outer.device)
                outer.assertIs(pair, False)
                self.address = outer.client_address
                self.services = outer.services
                self.is_connected = False
                outer.client = self

            async def connect(self):
                outer.events.append(("connect",))
                self.is_connected = True

            async def start_notify(self, characteristic, callback):
                outer.assertIs(characteristic, outer.tx)
                outer.events.append(("subscribe", characteristic))
                if outer.subscribe_error:
                    raise OSError("synthetic subscription failure")
                self.callback = callback
                outer.after_subscribe()

            async def write_gatt_char(self, characteristic, packet, *, response):
                outer.assertIs(characteristic, outer.rx)
                outer.assertIs(response, False)
                outer.events.append(("write", characteristic, packet))
                if outer.write_error or outer.actions().count("write") == outer.fail_write_index:
                    raise OSError("synthetic uncertain write failure")
                if hasattr(self, "callback"):
                    for raw in outer.notifications:
                        self.callback(outer.tx, bytearray(raw))
                outer.after_write()

            async def stop_notify(self, characteristic):
                outer.assertIs(characteristic, outer.tx)
                outer.events.append(("unsubscribe", characteristic))

            async def disconnect(self):
                outer.events.append(("disconnect",))
                self.is_connected = False

        self.bleak_patch = mock.patch.dict(sys.modules, {
            "bleak": NS(BleakClient=FakeClient, BleakScanner=FakeScanner),
        })
        self.bleak_patch.start()
        self.addCleanup(self.bleak_patch.stop)

    def run_probe(self, operation="status", **kwargs):
        options = dict(address=ADDRESS, name=NAME, log_dir=Path(self.tmp.name),
                       notification_seconds=0.001)
        options.update(kwargs)
        with contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(diagnostic.run_diagnostic(operation, **options))

    def actions(self):
        return [e[0] for e in self.events]

    def test_gatt_inventory_without_subscription_or_write(self):
        path = self.run_probe("gatt")
        self.assertEqual(self.actions(), ["scan", "client", "connect", "disconnect"])
        events = json.loads(path.read_text())["events"]
        inventory = next(e["services"] for e in events if e["event"] == "gatt_enumerated")
        self.assertTrue(inventory[1]["blacklisted"])
        self.assertTrue(inventory[1]["characteristics"][0]["blacklisted"])

    def test_one_fixed_write_raw_order_and_cleanup(self):
        path = self.run_probe()
        self.assertEqual(self.actions(), ["scan", "client", "connect", "subscribe", "write",
                                          "unsubscribe", "disconnect"])
        self.assertEqual(next(e[2] for e in self.events if e[0] == "write"), diagnostic.STATUS_PACKET)
        events = json.loads(path.read_text())["events"]
        received = [e for e in events if e["event"] == "nus_tx_notification"]
        self.assertEqual([e["raw_hex"] for e in received], [b.hex(" ").upper() for b in self.notifications])
        self.assertEqual([e["index"] for e in received], [1, 2])
        self.assertEqual(events[-1]["application_write_attempts"], 1)
        self.assertTrue(events[-1]["application_write_completed"])

    def test_no_device_or_ambiguous_identity_never_connects(self):
        for discoveries in ({}, {"a": (self.device, self.advertisement),
                                "b": (self.device, self.advertisement)}):
            with self.subTest(count=len(discoveries)):
                self.events.clear()
                self.discoveries = discoveries
                with self.assertRaises(TransportSafetyError):
                    self.run_probe()
                self.assertEqual(self.actions(), ["scan"])

    def test_wrong_advertised_name_never_connects(self):
        self.advertisement.local_name = "different unit"
        with self.assertRaises(TransportSafetyError):
            self.run_probe()
        self.assertEqual(self.actions(), ["scan"])

    def test_client_address_mismatch_never_subscribes(self):
        self.client_address = "AA:BB:CC:DD:EE:FF"
        with self.assertRaises(TransportSafetyError):
            self.run_probe()
        self.assertNotIn("subscribe", self.actions())
        self.assertEqual(self.actions()[-1], "disconnect")

    def test_unsafe_topologies_never_subscribe_or_write(self):
        for case in ("no_nus", "duplicate_nus", "outside_duplicate", "missing_rx", "wrong_parent",
                     "dfu_rx", "no_write_without_response", "no_notify"):
            with self.subTest(case=case):
                self.events.clear()
                self.services, self.rx, self.tx = layout()
                if case == "no_nus":
                    self.services.pop(0)
                elif case == "duplicate_nus":
                    self.services.append(layout()[0][0])
                elif case == "outside_duplicate":
                    self.services[1].characteristics.append(layout()[1])
                elif case == "missing_rx":
                    self.services[0].characteristics.remove(self.rx)
                elif case == "wrong_parent":
                    self.rx.service_uuid = DFU_SERVICE_UUID
                elif case == "dfu_rx":
                    self.rx.uuid = DFU_BUTTONLESS_UUID
                elif case == "no_write_without_response":
                    self.rx.properties = ["write"]
                elif case == "no_notify":
                    self.tx.properties = ["indicate"]
                with self.assertRaises(TransportSafetyError):
                    self.run_probe()
                self.assertNotIn("subscribe", self.actions())
                self.assertNotIn("write", self.actions())
                self.assertEqual(self.actions()[-1], "disconnect")

    def test_topology_change_after_subscription_blocks_write(self):
        self.after_subscribe = lambda: self.services[0].characteristics.remove(self.rx)
        with self.assertRaises(TransportSafetyError):
            self.run_probe()
        self.assertNotIn("write", self.actions())
        self.assertNotIn("unsubscribe", self.actions())
        self.assertEqual(self.actions()[-1], "disconnect")

    def test_identity_change_after_subscription_blocks_write(self):
        self.after_subscribe = lambda: setattr(self.client, "address", "AA:BB:CC:DD:EE:FF")
        with self.assertRaises(TransportSafetyError):
            self.run_probe()
        self.assertNotIn("write", self.actions())
        self.assertEqual(self.actions()[-1], "disconnect")

    def test_failed_subscription_does_not_write(self):
        self.subscribe_error = True
        with self.assertRaises(OSError):
            self.run_probe()
        self.assertNotIn("write", self.actions())
        self.assertEqual(self.actions()[-1], "disconnect")

    def test_uncertain_write_is_never_retried_or_restored(self):
        self.write_error = True
        with self.assertRaises(OSError):
            self.run_probe()
        self.assertEqual(self.actions().count("write"), 1)
        self.assertEqual(self.actions()[-2:], ["unsubscribe", "disconnect"])
        report = json.loads(next(Path(self.tmp.name).glob("*.json")).read_text())
        summary = report["events"][-1]
        self.assertEqual(summary["application_write_attempts"], 1)
        self.assertFalse(summary["application_write_completed"])

    def test_silent_device_gets_no_second_query(self):
        self.notifications = []
        self.run_probe()
        self.assertEqual(self.actions().count("write"), 1)

    def test_non_status_packets_rejected_before_ble(self):
        for packet in (diagnostic.STATUS_PACKET[:-1] + b"\x01",
                       create_command_encoding(0x5A, 0x05, (0, 2), [11, 255, 255]),
                       create_command_encoding(0x5A, 0x04, (0, 3), [1])):
            with self.subTest(packet=packet), mock.patch.object(diagnostic, "create_status_query", return_value=packet):
                with self.assertRaises(TransportSafetyError):
                    self.run_probe()
        self.assertEqual(self.events, [])

    def test_unbounded_or_invalid_options_rejected_before_ble(self):
        for key in ("scan_seconds", "connect_timeout", "notification_seconds"):
            for value in (0, -1, float("inf"), float("nan"), 61):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.run_probe(**{key: value})
        self.assertEqual(self.events, [])

    def test_dry_run_is_offline_and_no_prefix_is_registered(self):
        for operation in ("gatt", "status"):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(diagnostic.main([operation, "--address", ADDRESS, "--name", NAME, "--dry-run"]), 0)
        self.assertEqual(self.events, [])
        self.assertIsNone(detect_model(NAME))

    def test_cli_has_no_arbitrary_write_surface_and_requires_explicit_live(self):
        base = ["status", "--address", ADDRESS, "--name", NAME]
        for suffix in ([], ["--live", "--dry-run"], ["--live", "--raw", "00"],
                       ["--live", "--brightness", "50"], ["--live", "--packet", "00"]):
            with self.subTest(suffix=suffix), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    diagnostic.main(base + suffix)
        self.assertEqual(self.events, [])


    def test_channel0_fixed_frames_and_independent_checksum(self):
        packets = diagnostic.build_channel0_plan()
        self.assertEqual(packets, (
            bytes.fromhex("5A 01 08 00 02 05 0B FF FF 05"),
            bytes.fromhex("5A 01 07 00 03 07 00 14 16"),
        ))
        for packet in packets:
            self.assertEqual(packet[2], len(packet) - 2)
            checksum = 0
            for value in packet[1:-1]:
                checksum ^= value
            self.assertEqual(checksum, packet[-1])

    def test_channel0_rejects_other_commands_levels_order_and_count(self):
        manual, channel = diagnostic.build_channel0_plan()
        alternatives = (
            (), (manual,), (channel,), (channel, manual), (manual, channel, channel),
            (diagnostic.STATUS_PACKET, channel),
            (create_command_encoding(0x5A, 0x05, (0, 2), [0x12, 255, 255]), channel),
        )
        for plan in alternatives:
            with self.subTest(plan=plan), self.assertRaises(TransportSafetyError):
                diagnostic.validate_channel0_plan(plan)
        bad_channel_packets = (
            create_command_encoding(0x5A, 0x07, (0, 3), [1, 20]),
            create_command_encoding(0x5A, 0x07, (0, 3), [0, 21]),
            create_command_encoding(0x5A, 0x07, (0, 4), [0, 20]),
            create_command_encoding(0x5A, 0x08, (0, 3), [0, 20]),
            create_command_encoding(0xA5, 0x07, (0, 3), [0, 20]),
            channel[:-1] + bytes([channel[-1] ^ 1]),
            channel[:2] + b"\x08" + channel[3:],
        )
        for packet in bad_channel_packets:
            with self.subTest(packet=packet), mock.patch.object(
                diagnostic, "create_command_encoding", return_value=packet
            ), self.assertRaises(TransportSafetyError):
                self.run_probe("channel0-test")
        with mock.patch.object(diagnostic, "create_manual_mode", return_value=diagnostic.STATUS_PACKET):
            with self.assertRaises(TransportSafetyError):
                self.run_probe("channel0-test")
        self.assertEqual(self.events, [])

    def test_channel0_two_writes_30ms_gap_then_immediate_disconnect(self):
        async def record_sleep(seconds):
            self.events.append(("gap", seconds))

        with mock.patch.object(diagnostic.asyncio, "sleep", side_effect=record_sleep):
            path = self.run_probe("channel0-test")
        self.assertEqual(self.actions(), ["scan", "client", "connect", "write", "gap", "write", "disconnect"])
        self.assertEqual([e[2] for e in self.events if e[0] == "write"], list(diagnostic.CHANNEL0_PACKETS))
        self.assertEqual(next(e[1] for e in self.events if e[0] == "gap"), 0.030)
        events = json.loads(path.read_text())["events"]
        self.assertEqual(events[0]["application_write_limit"], 2)
        plan_index = next(i for i, e in enumerate(events) if e["event"] == "channel0_plan")
        attempt_index = next(i for i, e in enumerate(events) if e["event"] == "channel0_write_attempted")
        self.assertLess(plan_index, attempt_index)
        self.assertEqual(events[plan_index]["packets_hex"], [p.hex(" ").upper() for p in diagnostic.CHANNEL0_PACKETS])
        self.assertEqual(events[-1]["application_write_attempts"], 2)
        self.assertTrue(events[-1]["application_write_completed"])
        self.assertEqual(events[-1]["notification_count"], 0)

    def test_channel0_each_uncertain_write_aborts_without_retry_or_restore(self):
        for failure in (1, 2):
            with self.subTest(failure=failure):
                self.events.clear()
                self.fail_write_index = failure
                with self.assertRaises(OSError):
                    self.run_probe("channel0-test")
                writes = [e[2] for e in self.events if e[0] == "write"]
                self.assertEqual(writes, list(diagnostic.CHANNEL0_PACKETS[:failure]))
                self.assertNotIn("subscribe", self.actions())
                self.assertEqual(self.actions()[-1], "disconnect")
        reports = [json.loads(p.read_text()) for p in Path(self.tmp.name).glob("*.json")]
        self.assertTrue(all(not r["events"][-1]["application_write_completed"] for r in reports))

    def test_channel0_revalidates_identity_and_nus_between_writes(self):
        for case in ("address", "name", "disconnected", "removed_rx", "dfu_parent", "replaced_nus"):
            with self.subTest(case=case):
                self.events.clear()
                self.services, self.rx, self.tx = layout()
                self.advertisement.local_name = NAME

                def change():
                    if case == "address":
                        self.client.address = "AA:BB:CC:DD:EE:FF"
                    elif case == "name":
                        self.advertisement.local_name = "different unit"
                    elif case == "disconnected":
                        self.client.is_connected = False
                    elif case == "removed_rx":
                        self.services[0].characteristics.remove(self.rx)
                    elif case == "dfu_parent":
                        self.rx.service_uuid = DFU_SERVICE_UUID
                    else:
                        self.client.services = layout()[0]

                self.after_write = change
                with self.assertRaises(TransportSafetyError):
                    self.run_probe("channel0-test")
                self.assertEqual([e[2] for e in self.events if e[0] == "write"], [diagnostic.CHANNEL0_PACKETS[0]])
                self.assertEqual(self.actions()[-1], "disconnect")

    def test_channel0_cancelled_gap_disconnects_without_second_write(self):
        with mock.patch.object(diagnostic.asyncio, "sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                self.run_probe("channel0-test")
        self.assertEqual(self.actions(), ["scan", "client", "connect", "write", "disconnect"])

    def test_channel0_bad_topology_blocks_manual_mode(self):
        for case in ("missing_nus", "duplicate_nus", "outside_duplicate", "dfu_parent", "dfu_rx",
                     "no_write_without_response", "no_notify"):
            with self.subTest(case=case):
                self.events.clear()
                self.services, self.rx, self.tx = layout()
                if case == "missing_nus":
                    self.services.pop(0)
                elif case == "duplicate_nus":
                    self.services.append(layout()[0][0])
                elif case == "outside_duplicate":
                    self.services[1].characteristics.append(layout()[1])
                elif case == "dfu_parent":
                    self.rx.service_uuid = DFU_SERVICE_UUID
                elif case == "dfu_rx":
                    self.rx.uuid = DFU_BUTTONLESS_UUID
                elif case == "no_write_without_response":
                    self.rx.properties = ["write"]
                else:
                    self.tx.properties = ["indicate"]
                with self.assertRaises(TransportSafetyError):
                    self.run_probe("channel0-test")
                self.assertEqual(self.actions(), ["scan", "client", "connect", "disconnect"])

    def test_channel0_exact_runtime_identity_required_before_manual_mode(self):
        for case in ("missing", "ambiguous", "wrong_name", "wrong_client_address", "invalid_address", "empty_name"):
            with self.subTest(case=case):
                self.events.clear()
                self.discoveries = {ADDRESS: (self.device, self.advertisement)}
                self.advertisement.local_name = NAME
                self.client_address = ADDRESS
                options = {}
                if case == "missing":
                    self.discoveries = {}
                elif case == "ambiguous":
                    self.discoveries["duplicate"] = (self.device, self.advertisement)
                elif case == "wrong_name":
                    self.advertisement.local_name = "different unit"
                elif case == "wrong_client_address":
                    self.client_address = "AA:BB:CC:DD:EE:FF"
                elif case == "invalid_address":
                    options["address"] = "invalid"
                else:
                    options["name"] = ""
                with self.assertRaises((TransportSafetyError, ValueError)):
                    self.run_probe("channel0-test", **options)
                self.assertNotIn("write", self.actions())
                self.assertNotIn("subscribe", self.actions())
                if case == "wrong_client_address":
                    self.assertEqual(self.actions()[-1], "disconnect")
                else:
                    self.assertNotIn("client", self.actions())

    def test_channel0_dry_run_is_offline_and_limits_physical_claims(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(diagnostic, "LocalSessionLog") as logger:
            self.assertEqual(diagnostic.main([
                "channel0-test", "--address", ADDRESS, "--name", NAME, "--dry-run",
            ]), 0)
        self.assertEqual(self.events, [])
        logger.assert_not_called()
        for packet in diagnostic.CHANNEL0_PACKETS:
            self.assertIn(packet.hex(" ").upper(), output.getvalue())
        self.assertIn("User-confirmed", output.getvalue())
        self.assertIn("does not establish photometric equivalence or linearity", output.getvalue())
        self.assertIsNone(detect_model(NAME))

    def test_channel0_cli_requires_explicit_gate_and_forbids_overrides(self):
        base = ["channel0-test", "--address", ADDRESS, "--name", NAME]
        for suffix in ([], ["--live", "--dry-run"], ["--live", "--raw", "00"],
                       ["--live", "--channel", "1"], ["--live", "--level", "21"],
                       ["--live", "--brightness", "20"], ["--live", "--auto"],
                       ["--live", "--retry"], ["--live", "--delay", "0"]):
            with self.subTest(suffix=suffix), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    diagnostic.main(base + suffix)
        self.assertEqual(self.events, [])
        # Exercise the --live dispatch with the BLE coroutine replaced entirely.
        with mock.patch.object(diagnostic, "run_diagnostic", new_callable=mock.AsyncMock) as runner:
            self.assertEqual(diagnostic.main(base + ["--live"]), 0)
        runner.assert_awaited_once_with("channel0-test", address=ADDRESS, name=NAME,
                                      scan_seconds=20, connect_timeout=30, notification_seconds=5)
        self.assertEqual(self.events, [])


    def test_new_channel_packets_have_exact_ids_levels_and_checksums(self):
        for operation, channel, expected in NEW_CHANNEL_CASES:
            with self.subTest(operation=operation):
                plan = diagnostic.build_channel_plan(operation)
                self.assertEqual(plan, (bytes.fromhex("5A 01 08 00 02 05 0B FF FF 05"), expected))
                self.assertEqual(plan[1][6:8], bytes([channel, 20]))
                for packet in plan:
                    self.assertEqual(packet[2], len(packet) - 2)
                    checksum = 0
                    for value in packet[1:-1]:
                        checksum ^= value
                    self.assertEqual(checksum, packet[-1])

    def test_each_named_channel_rejects_other_valid_plans_and_altered_packets(self):
        operations = ("channel0-test", *(case[0] for case in NEW_CHANNEL_CASES))
        for operation in operations:
            plan = diagnostic.build_channel_plan(operation)
            for other in operations:
                if other != operation:
                    with self.subTest(operation=operation, other=other), self.assertRaises(TransportSafetyError):
                        diagnostic.validate_channel_plan(operation, diagnostic.build_channel_plan(other))
            for bad_plan in ((), plan[:1], plan[::-1], plan + plan[1:],
                             (diagnostic.STATUS_PACKET, plan[1])):
                with self.subTest(operation=operation, bad_plan=bad_plan), self.assertRaises(TransportSafetyError):
                    diagnostic.validate_channel_plan(operation, bad_plan)
        for operation, channel, expected in NEW_CHANNEL_CASES:
            for packet in (
                create_command_encoding(0x5A, 0x07, (0, 3), [channel, 21]),
                create_command_encoding(0x5A, 0x07, (0, 4), [channel, 20]),
                create_command_encoding(0x5A, 0x08, (0, 3), [channel, 20]),
                expected[:-1] + bytes([expected[-1] ^ 1]),
            ):
                with self.subTest(operation=operation, packet=packet), mock.patch.object(
                    diagnostic, "create_command_encoding", return_value=packet
                ), self.assertRaises(TransportSafetyError):
                    self.run_probe(operation)
        self.assertEqual(self.events, [])

    def test_each_new_channel_has_only_two_writes_one_gap_and_disconnect(self):
        async def record_sleep(seconds):
            self.events.append(("gap", seconds))

        for operation, channel, expected in NEW_CHANNEL_CASES:
            with self.subTest(operation=operation), mock.patch.object(
                diagnostic.asyncio, "sleep", side_effect=record_sleep
            ):
                self.events.clear()
                path = self.run_probe(operation)
                self.assertEqual(self.actions(), ["scan", "client", "connect", "write", "gap", "write", "disconnect"])
                self.assertEqual([e[2] for e in self.events if e[0] == "write"],
                                 [diagnostic.CHANNEL0_PACKETS[0], expected])
                self.assertEqual(next(e[1] for e in self.events if e[0] == "gap"), 0.030)
                events = json.loads(path.read_text())["events"]
                self.assertEqual(events[0]["application_write_limit"], 2)
                plan_index = next(i for i, e in enumerate(events) if e["event"] == f"channel{channel}_plan")
                attempt_index = next(i for i, e in enumerate(events) if e["event"] == f"channel{channel}_write_attempted")
                self.assertLess(plan_index, attempt_index)
                self.assertEqual(events[plan_index]["channel"], channel)
                self.assertTrue(events[plan_index]["channel_mapping_confirmed_on_test_unit"])
                self.assertFalse(events[plan_index]["photometric_scaling_verified"])
                self.assertEqual(events[-1]["application_write_attempts"], 2)
                self.assertTrue(events[-1]["application_write_completed"])
                self.assertEqual(events[-1]["notification_count"], 0)

    def test_each_new_channel_aborts_uncertain_writes_without_retry(self):
        for operation, _, expected in NEW_CHANNEL_CASES:
            for failure in (1, 2):
                with self.subTest(operation=operation, failure=failure):
                    self.events.clear()
                    self.fail_write_index = failure
                    with self.assertRaises(OSError):
                        self.run_probe(operation)
                    self.assertEqual([e[2] for e in self.events if e[0] == "write"],
                                     [diagnostic.CHANNEL0_PACKETS[0], expected][:failure])
                    self.assertNotIn("subscribe", self.actions())
                    self.assertEqual(self.actions()[-1], "disconnect")

    def test_new_channels_reject_target_and_topology_changes_before_each_write(self):
        for operation, _, _ in NEW_CHANNEL_CASES:
            for case in ("wrong_name", "dfu_parent", "changed_address", "changed_nus"):
                with self.subTest(operation=operation, case=case):
                    self.events.clear()
                    self.services, self.rx, self.tx = layout()
                    self.advertisement.local_name = NAME
                    self.after_write = lambda: None
                    if case == "wrong_name":
                        self.advertisement.local_name = "different unit"
                    elif case == "dfu_parent":
                        self.rx.service_uuid = DFU_SERVICE_UUID
                    elif case == "changed_address":
                        self.after_write = lambda: setattr(self.client, "address", "AA:BB:CC:DD:EE:FF")
                    else:
                        self.after_write = lambda: self.services[0].characteristics.remove(self.rx)
                    with self.assertRaises(TransportSafetyError):
                        self.run_probe(operation)
                    expected = [diagnostic.CHANNEL0_PACKETS[0]] if case.startswith("changed_") else []
                    self.assertEqual([e[2] for e in self.events if e[0] == "write"], expected)
                    self.assertNotIn("subscribe", self.actions())
                    if case == "wrong_name":
                        self.assertNotIn("connect", self.actions())
                    else:
                        self.assertEqual(self.actions()[-1], "disconnect")

    def test_new_channel_cli_is_offline_by_dry_run_and_live_is_explicit(self):
        for operation, _, expected in NEW_CHANNEL_CASES:
            base = [operation, "--address", ADDRESS, "--name", NAME]
            output = io.StringIO()
            with self.subTest(operation=operation), contextlib.redirect_stdout(output), mock.patch.object(
                diagnostic, "LocalSessionLog"
            ) as logger:
                self.assertEqual(diagnostic.main(base + ["--dry-run"]), 0)
                logger.assert_not_called()
            self.assertIn(expected.hex(" ").upper(), output.getvalue())
            self.assertIn("User-confirmed", output.getvalue())
            self.assertIn("0=Red, 1=Green, 2=Blue, 3=White", output.getvalue())
            self.assertIn("does not establish photometric equivalence or linearity", output.getvalue())
            self.assertEqual(self.events, [])
            for suffix in ([], ["--live", "--dry-run"], ["--live", "--channel", "0"],
                           ["--live", "--level", "21"], ["--live", "--raw", "00"],
                           ["--live", "--retry"], ["--live", "channel0-test"]):
                with self.subTest(operation=operation, suffix=suffix), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        diagnostic.main(base + suffix)
            with mock.patch.object(diagnostic, "run_diagnostic", new_callable=mock.AsyncMock) as runner:
                self.assertEqual(diagnostic.main(base + ["--live"]), 0)
            runner.assert_awaited_once_with(operation, address=ADDRESS, name=NAME,
                                          scan_seconds=20, connect_timeout=30, notification_seconds=5)
        for operation in ("channel4-test", "channel-1-test", "all-channels-test", "channel-test"):
            with self.subTest(operation=operation), self.assertRaises(TransportSafetyError):
                diagnostic.build_channel_plan(operation)
        self.assertEqual(self.events, [])
        self.assertIsNone(detect_model(NAME))


    def test_isolated_exact_six_packets_true_zero_and_independent_checksums(self):
        for operation, target, last in ISOLATED_CASES:
            with self.subTest(operation=operation):
                plan = diagnostic.build_channel_plan(operation)
                self.assertEqual(plan, ISOLATED_COMMON_GOLDEN + (last,))
                self.assertEqual([p[6:8] for p in plan[1:5]], [bytes([c, 0]) for c in range(4)])
                self.assertEqual(plan[-1][6:8], bytes([target, 20]))
                for packet in plan:
                    self.assertEqual(packet[2], len(packet) - 2)
                    checksum = 0
                    for value in packet[1:-1]:
                        checksum ^= value
                    self.assertEqual(checksum, packet[-1])

    def test_isolated_clamped_zero_builder_aborts_before_ble(self):
        original_encoder = diagnostic.create_command_encoding

        def clamp_zero(family, mode, message_id, parameters):
            channel, level = parameters
            return original_encoder(family, mode, message_id, [channel, max(1, level)])

        for operation, _, _ in ISOLATED_CASES:
            with self.subTest(operation=operation), mock.patch.object(
                diagnostic, "create_command_encoding", side_effect=clamp_zero
            ), self.assertRaises(TransportSafetyError):
                self.run_probe(operation)
        self.assertEqual(self.events, [])

    def test_isolated_rejects_missing_clears_reordering_extra_and_wrong_target(self):
        for operation, target, _ in ISOLATED_CASES:
            plan = diagnostic.build_channel_plan(operation)
            invalid_plans = [
                plan[:index] + plan[index + 1:] for index in range(6)
            ] + [
                plan + plan[-1:],
                (plan[0], plan[2], plan[1], *plan[3:]),
                (plan[0], plan[-1], *plan[1:5]),
                (diagnostic.STATUS_PACKET, *plan[1:]),
                diagnostic.build_channel_plan(f"channel{target}-test"),
                (*plan[:-1], create_command_encoding(0x5A, 0x07, (0, 7), [target, 21])),
                (*plan[:-1], plan[-1][:-1] + bytes([plan[-1][-1] ^ 1])),
            ]
            invalid_plans.extend(diagnostic.build_channel_plan(other) for other, _, _ in ISOLATED_CASES
                                 if other != operation)
            for invalid in invalid_plans:
                with self.subTest(operation=operation, invalid=invalid), self.assertRaises(TransportSafetyError):
                    diagnostic.validate_channel_plan(operation, invalid)

    def test_isolated_six_writes_five_gaps_then_disconnect_and_complete_log(self):
        async def record_sleep(seconds):
            self.events.append(("gap", seconds))

        for operation, target, last in ISOLATED_CASES:
            with self.subTest(operation=operation), mock.patch.object(
                diagnostic.asyncio, "sleep", side_effect=record_sleep
            ):
                self.events.clear()
                path = self.run_probe(operation)
                self.assertEqual(self.actions(), ["scan", "client", "connect", "write"]
                                 + ["gap", "write"] * 5 + ["disconnect"])
                self.assertEqual([e[2] for e in self.events if e[0] == "write"],
                                 list(ISOLATED_COMMON_GOLDEN + (last,)))
                self.assertEqual([e[1] for e in self.events if e[0] == "gap"], [0.030] * 5)
                events = json.loads(path.read_text())["events"]
                self.assertEqual(events[0]["application_write_limit"], 6)
                plan_index = next(i for i, e in enumerate(events) if e["event"] == f"isolated_channel{target}_plan")
                attempt_index = next(i for i, e in enumerate(events) if e["event"] == f"isolated_channel{target}_write_attempted")
                self.assertLess(plan_index, attempt_index)
                self.assertEqual(events[plan_index]["clear_channels"], [0, 1, 2, 3])
                self.assertEqual(events[plan_index]["packets_hex"],
                                 [p.hex(" ").upper() for p in ISOLATED_COMMON_GOLDEN + (last,)])
                self.assertEqual(events[-1]["application_write_attempts"], 6)
                self.assertTrue(events[-1]["application_write_completed"])
                self.assertEqual(events[-1]["notification_count"], 0)

    def test_isolated_each_failed_write_aborts_without_retry_or_later_writes(self):
        for operation, _, last in ISOLATED_CASES:
            for failure in range(1, 7):
                with self.subTest(operation=operation, failure=failure):
                    self.events.clear()
                    self.fail_write_index = failure
                    with self.assertRaises(OSError):
                        self.run_probe(operation)
                    self.assertEqual([e[2] for e in self.events if e[0] == "write"],
                                     list((ISOLATED_COMMON_GOLDEN + (last,))[:failure]))
                    self.assertNotIn("subscribe", self.actions())
                    self.assertEqual(self.actions()[-1], "disconnect")

    def test_isolated_revalidates_identity_and_dfu_exclusion_after_every_write(self):
        for operation, _, _ in ISOLATED_CASES:
            for completed in range(1, 6):
                for change in ("identity", "dfu_parent"):
                    with self.subTest(operation=operation, completed=completed, change=change):
                        self.events.clear()
                        self.services, self.rx, self.tx = layout()

                        def alter():
                            if self.actions().count("write") == completed:
                                if change == "identity":
                                    self.client.address = "AA:BB:CC:DD:EE:FF"
                                else:
                                    self.rx.service_uuid = DFU_SERVICE_UUID

                        self.after_write = alter
                        with self.assertRaises(TransportSafetyError):
                            self.run_probe(operation)
                        self.assertEqual([e[2] for e in self.events if e[0] == "write"],
                                         list(ISOLATED_COMMON_GOLDEN[:completed]))
                        self.assertEqual(self.actions()[-1], "disconnect")

    def test_isolated_bad_identity_or_topology_blocks_first_write(self):
        for operation, _, _ in ISOLATED_CASES:
            for case in ("wrong_name", "missing_nus", "duplicate_nus", "dfu_parent"):
                with self.subTest(operation=operation, case=case):
                    self.events.clear()
                    self.services, self.rx, self.tx = layout()
                    self.advertisement.local_name = NAME
                    if case == "wrong_name":
                        self.advertisement.local_name = "different unit"
                    elif case == "missing_nus":
                        self.services.pop(0)
                    elif case == "duplicate_nus":
                        self.services.append(layout()[0][0])
                    else:
                        self.rx.service_uuid = DFU_SERVICE_UUID
                    with self.assertRaises(TransportSafetyError):
                        self.run_probe(operation)
                    self.assertNotIn("write", self.actions())
                    self.assertNotIn("subscribe", self.actions())
                    if case == "wrong_name":
                        self.assertNotIn("connect", self.actions())
                    else:
                        self.assertEqual(self.actions()[-1], "disconnect")

    def test_isolated_cancelled_gap_never_continues_the_plan(self):
        for operation, _, _ in ISOLATED_CASES:
            for cancelled_gap in range(1, 6):
                with self.subTest(operation=operation, cancelled_gap=cancelled_gap):
                    self.events.clear()

                    async def cancel_sleep(seconds):
                        if self.actions().count("write") == cancelled_gap:
                            raise asyncio.CancelledError

                    with mock.patch.object(diagnostic.asyncio, "sleep", side_effect=cancel_sleep):
                        with self.assertRaises(asyncio.CancelledError):
                            self.run_probe(operation)
                    self.assertEqual([e[2] for e in self.events if e[0] == "write"],
                                     list(ISOLATED_COMMON_GOLDEN[:cancelled_gap]))
                    self.assertEqual(self.actions()[-1], "disconnect")

    def test_isolated_cli_offline_preview_and_explicit_single_live_operation(self):
        for operation, _, last in ISOLATED_CASES:
            base = [operation, "--address", ADDRESS, "--name", NAME]
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(diagnostic, "LocalSessionLog") as logger:
                self.assertEqual(diagnostic.main(base + ["--dry-run"]), 0)
                logger.assert_not_called()
            for packet in ISOLATED_COMMON_GOLDEN + (last,):
                self.assertIn(packet.hex(" ").upper(), output.getvalue())
            self.assertIn("remain wire byte 00; no clamp", output.getvalue())
            self.assertIn("Exactly 6 application writes", output.getvalue())
            self.assertEqual(self.events, [])
            for suffix in ([], ["--live", "--dry-run"], ["--live", "--raw", "00"],
                           ["--live", "--level", "21"], ["--live", "--skip-clear"],
                           ["--live", "--channel", "0"], ["--live", "--retry"],
                           ["--live", "channel0-test"]):
                with self.subTest(operation=operation, suffix=suffix), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        diagnostic.main(base + suffix)
            with mock.patch.object(diagnostic, "run_diagnostic", new_callable=mock.AsyncMock) as runner:
                self.assertEqual(diagnostic.main(base + ["--live"]), 0)
            runner.assert_awaited_once_with(operation, address=ADDRESS, name=NAME,
                                          scan_seconds=20, connect_timeout=30, notification_seconds=5)
        self.assertEqual(self.events, [])
        self.assertIsNone(detect_model(NAME))


    def test_combined_fixed_five_packets_direct_levels_and_checksums(self):
        plan = diagnostic.build_channel_plan("wrgb-combined-test")
        self.assertEqual(plan, COMBINED_GOLDEN)
        self.assertEqual([p[6:8] for p in plan[1:]],
                         [bytes([0, 20]), bytes([1, 40]), bytes([2, 60]), bytes([3, 30])])
        for packet in plan:
            self.assertEqual(packet[2], len(packet) - 2)
            checksum = 0
            for value in packet[1:-1]:
                checksum ^= value
            self.assertEqual(checksum, packet[-1])

    def test_combined_rejects_missing_extra_reordered_changed_or_scaled_writes(self):
        operation = "wrgb-combined-test"
        plan = diagnostic.build_channel_plan(operation)
        invalid = [plan[:i] + plan[i + 1:] for i in range(5)] + [
            plan + plan[-1:], (plan[0], plan[2], plan[1], *plan[3:]),
            (diagnostic.STATUS_PACKET, *plan[1:]),
            (plan[0], *ISOLATED_COMMON_GOLDEN[1:], *plan[1:]),
            diagnostic.build_channel_plan("channel0-test"),
            diagnostic.build_channel_plan("isolated-channel1-test"),
        ]
        for index in range(1, 5):
            packet = plan[index]
            altered = create_command_encoding(0x5A, 0x07, (packet[3], packet[4]), [packet[6], packet[7] + 1])
            invalid.append((*plan[:index], altered, *plan[index + 1:]))
            invalid.append((*plan[:index], packet[:-1] + bytes([packet[-1] ^ 1]), *plan[index + 1:]))
        for bad_plan in invalid:
            with self.subTest(plan=bad_plan), self.assertRaises(TransportSafetyError):
                diagnostic.validate_channel_plan(operation, bad_plan)
        with self.assertRaises(TransportSafetyError):
            diagnostic.validate_channel_plan("channel0-test", plan)
        original_encoder = diagnostic.create_command_encoding

        def scaled_encoder(family, mode, message_id, parameters):
            channel, level = parameters
            return original_encoder(family, mode, message_id, [channel, level * 100 // 120])

        with mock.patch.object(diagnostic, "create_command_encoding", side_effect=scaled_encoder):
            with self.assertRaises(TransportSafetyError):
                self.run_probe(operation)
        self.assertEqual(self.events, [])

    def test_combined_only_five_writes_four_gaps_then_disconnect(self):
        async def record_sleep(seconds):
            self.events.append(("gap", seconds))

        with mock.patch.object(diagnostic.asyncio, "sleep", side_effect=record_sleep):
            path = self.run_probe("wrgb-combined-test")
        self.assertEqual(self.actions(), ["scan", "client", "connect", "write"]
                         + ["gap", "write"] * 4 + ["disconnect"])
        self.assertEqual([e[2] for e in self.events if e[0] == "write"], list(COMBINED_GOLDEN))
        self.assertEqual([e[1] for e in self.events if e[0] == "gap"], [0.030] * 4)
        events = json.loads(path.read_text())["events"]
        self.assertEqual(events[0]["application_write_limit"], 5)
        plan_index = next(i for i, e in enumerate(events) if e["event"] == "wrgb_combined_plan")
        attempt_index = next(i for i, e in enumerate(events) if e["event"] == "wrgb_combined_write_attempted")
        self.assertLess(plan_index, attempt_index)
        self.assertEqual(events[plan_index]["packets_hex"], [p.hex(" ").upper() for p in COMBINED_GOLDEN])
        self.assertEqual(events[plan_index]["channel_writes"], [
            {"channel": 0, "wire_level": 20}, {"channel": 1, "wire_level": 40},
            {"channel": 2, "wire_level": 60}, {"channel": 3, "wire_level": 30},
        ])
        self.assertEqual(events[plan_index]["clear_channels"], [])
        self.assertEqual(events[plan_index]["level_convention"], "direct_0_100")
        self.assertFalse(events[plan_index]["photometric_scaling_verified"])
        self.assertEqual(events[-1]["application_write_attempts"], 5)
        self.assertTrue(events[-1]["application_write_completed"])
        self.assertEqual(events[-1]["notification_count"], 0)

    def test_combined_failure_at_any_write_never_retries_or_restores(self):
        for failure in range(1, 6):
            with self.subTest(failure=failure):
                self.events.clear()
                self.fail_write_index = failure
                with self.assertRaises(OSError):
                    self.run_probe("wrgb-combined-test")
                self.assertEqual([e[2] for e in self.events if e[0] == "write"], list(COMBINED_GOLDEN[:failure]))
                self.assertNotIn("subscribe", self.actions())
                self.assertEqual(self.actions()[-1], "disconnect")

    def test_combined_revalidates_target_and_dfu_exclusion_before_every_write(self):
        for completed in range(1, 5):
            for change in ("identity", "dfu_parent"):
                with self.subTest(completed=completed, change=change):
                    self.events.clear()
                    self.services, self.rx, self.tx = layout()

                    def alter():
                        if self.actions().count("write") == completed:
                            if change == "identity":
                                self.client.address = "AA:BB:CC:DD:EE:FF"
                            else:
                                self.rx.service_uuid = DFU_SERVICE_UUID

                    self.after_write = alter
                    with self.assertRaises(TransportSafetyError):
                        self.run_probe("wrgb-combined-test")
                    self.assertEqual([e[2] for e in self.events if e[0] == "write"], list(COMBINED_GOLDEN[:completed]))
                    self.assertEqual(self.actions()[-1], "disconnect")

    def test_combined_wrong_identity_or_unsafe_topology_prevents_manual_mode(self):
        for case in ("wrong_name", "missing_nus", "duplicate_nus", "dfu_parent"):
            with self.subTest(case=case):
                self.events.clear()
                self.services, self.rx, self.tx = layout()
                self.advertisement.local_name = NAME
                if case == "wrong_name":
                    self.advertisement.local_name = "different unit"
                elif case == "missing_nus":
                    self.services.pop(0)
                elif case == "duplicate_nus":
                    self.services.append(layout()[0][0])
                else:
                    self.rx.service_uuid = DFU_SERVICE_UUID
                with self.assertRaises(TransportSafetyError):
                    self.run_probe("wrgb-combined-test")
                self.assertNotIn("write", self.actions())
                self.assertNotIn("subscribe", self.actions())
                if case == "wrong_name":
                    self.assertNotIn("connect", self.actions())
                else:
                    self.assertEqual(self.actions()[-1], "disconnect")

    def test_combined_cancellation_at_each_gap_disconnects_without_more_writes(self):
        for cancelled_gap in range(1, 5):
            with self.subTest(cancelled_gap=cancelled_gap):
                self.events.clear()

                async def cancel_sleep(seconds):
                    if self.actions().count("write") == cancelled_gap:
                        raise asyncio.CancelledError

                with mock.patch.object(diagnostic.asyncio, "sleep", side_effect=cancel_sleep):
                    with self.assertRaises(asyncio.CancelledError):
                        self.run_probe("wrgb-combined-test")
                self.assertEqual([e[2] for e in self.events if e[0] == "write"], list(COMBINED_GOLDEN[:cancelled_gap]))
                self.assertEqual(self.actions()[-1], "disconnect")

    def test_combined_cli_offline_preview_fixed_values_and_explicit_live_gate(self):
        base = ["wrgb-combined-test", "--address", ADDRESS, "--name", NAME]
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(diagnostic, "LocalSessionLog") as logger:
            self.assertEqual(diagnostic.main(base + ["--dry-run"]), 0)
            logger.assert_not_called()
        for packet in COMBINED_GOLDEN:
            self.assertIn(packet.hex(" ").upper(), output.getvalue())
        self.assertIn("Exactly 5 application writes", output.getvalue())
        self.assertIn("direct 0..100 convention", output.getvalue())
        self.assertIn("does not establish photometric equivalence or linearity", output.getvalue())
        for suffix in ([], ["--live", "--dry-run"], ["--live", "--raw", "00"],
                       ["--live", "--red", "60"], ["--live", "--level", "20"],
                       ["--live", "--channel", "0"], ["--live", "--retry"],
                       ["--live", "--delay", "0"], ["--live", "isolated-channel1-test"]):
            with self.subTest(suffix=suffix), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    diagnostic.main(base + suffix)
        with mock.patch.object(diagnostic, "run_diagnostic", new_callable=mock.AsyncMock) as runner:
            self.assertEqual(diagnostic.main(base + ["--live"]), 0)
        runner.assert_awaited_once_with("wrgb-combined-test", address=ADDRESS, name=NAME,
                                      scan_seconds=20, connect_timeout=30, notification_seconds=5)
        self.assertEqual(self.events, [])
        self.assertIsNone(detect_model(NAME))


if __name__ == "__main__":
    unittest.main()
