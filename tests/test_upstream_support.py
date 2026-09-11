"""Hardware-free behavioral tests; no real scanner/client is ever invoked."""

import asyncio
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from chihiros.constants import NUS_SERVICE_UUID, NUS_RX_UUID, NUS_TX_UUID
from chihiros.models import supported_model as local_model
from chihiros.protocol import calculate_checksum, create_command_encoding
from chihiros.transport import DeviceConfig, TransportSafetyError
from chihiros.upstream_profiles import (PROFILES, UNAVAILABLE, ACCESSORIES, Transport,
                                        profile_for, supported_model)
from chihiros.upstream_protocol import (Action, Request, build_plan, fan_wire,
                                       parse_telemetry, validate_request)
from chihiros.upstream_control import (FFE0, FFE1, OperationFailed, Receipt,
                                      UpstreamOperation, resolve_endpoint)
from gui.controller import ApplicationController, CompatibleDevice, filter_compatible_devices


STAMP = datetime(2026, 1, 2, 3, 4, 5)
LOCKED = ("DYRGBV", "DYNVVD", "DYNV", "DYNCMC", "DYCX", "DYMNC", "DYSSD", "DYNFAN")


def device(prefix="DYVVD3", address="00:11:22:33:44:55"):
    profile = profile_for(prefix)
    return DeviceConfig("synthetic", profile.name, prefix + "_TEST", address)


def topology(transport):
    service_uuid = NUS_SERVICE_UUID if transport is Transport.NUS else FFE0
    write_uuid = NUS_RX_UUID if transport is Transport.NUS else FFE1
    write = NS(uuid=write_uuid, service_uuid=service_uuid, properties=["write-without-response"])
    children = [write]
    if transport is Transport.NUS:
        children.append(NS(uuid=NUS_TX_UUID, service_uuid=service_uuid, properties=["notify"]))
    return [NS(uuid=service_uuid, characteristics=children)]


class FakeBle:
    def __init__(self, target, services=None):
        self.target = target
        self.services = services if services is not None else topology(profile_for(target.name).transport)
        self.address = target.address
        self.is_connected = False
        self.writes = []
        self.events = []
        self.frames = [bytes.fromhex("5B 1B 10 00 01 0B 02 58 19 00")]
        self.fail_write = None
        self.after_write = None
        self.wrong_sender = False
        self.fail_subscribe = False
        self.fail_unsubscribe = False

    async def discover(self, **kwargs):
        self.events.append("scan")
        return {"target": (NS(name=self.target.name, address=self.target.address),
                           NS(local_name=self.target.name))}

    def client(self, target, *, pair):
        assert pair is False
        assert target.address == self.target.address
        self.events.append("client")
        return self

    async def connect(self, **kwargs):
        self.events.append("connect")
        self.is_connected = True

    async def disconnect(self):
        self.events.append("disconnect")
        self.is_connected = False

    async def write_gatt_char(self, endpoint, packet, *, response):
        assert response is False
        assert endpoint is self.services[0].characteristics[0]
        self.writes.append(bytes(packet))
        self.events.append("write")
        if self.fail_write == len(self.writes):
            raise OSError("synthetic uncertain write")
        if self.after_write:
            self.after_write(self)

    async def start_notify(self, endpoint, callback):
        self.events.append("subscribe")
        if self.fail_subscribe:
            raise OSError("synthetic subscription error")
        for frame in self.frames:
            callback(object() if self.wrong_sender else endpoint, bytearray(frame))

    async def stop_notify(self, endpoint):
        self.events.append("unsubscribe")
        if self.fail_unsubscribe:
            raise OSError("synthetic unsubscribe error")

    def patched(self):
        return patch.dict(sys.modules, {"bleak": NS(BleakClient=self.client,
                                                   BleakScanner=NS(discover=self.discover))})


class RegistryTests(unittest.TestCase):
    def test_every_registered_prefix_and_semantic_channel(self):
        self.assertEqual(len(PROFILES), 16)
        seen = set()
        for profile in PROFILES:
            for prefix in profile.prefixes:
                with self.subTest(prefix=prefix):
                    self.assertNotIn(prefix, seen)
                    seen.add(prefix)
                    self.assertIs(profile_for(" " + prefix.lower() + "_TEST "), profile)
                    self.assertIn(profile.transport, (Transport.NUS, Transport.HM10))
                    self.assertTrue(profile.evidence)
                    self.assertTrue(set(profile.controls) <= {"Red", "Green", "Blue", "White", "Warm White"})

    def test_locked_precedence_and_local_metadata_identity(self):
        for prefix in LOCKED:
            for name in (prefix, prefix + "_TEST", prefix.lower(), " \t" + prefix.lower() + "_test \n"):
                with self.subTest(name=name):
                    local = local_model(name)
                    self.assertIsNotNone(local)
                    self.assertIsNone(profile_for(name))
                    self.assertIs(supported_model(name), local)

    def test_insufficient_evidence_and_accessories_never_become_profiles(self):
        for name in (*ACCESSORIES, *(p for item in UNAVAILABLE for p in item.prefixes),
                     "UNKNOWN", "6e400001-b5a3-f393-e0a9-e50e24dcca9e", "", None):
            with self.subTest(name=name):
                self.assertIsNone(profile_for(name))

    def test_longest_prefix_and_broad_prefix_exclusions(self):
        for prefix, expected in (("DYARGB", "RGB+APLUS (legacy)"), ("DYNC2N", "C II"),
                                 ("DYC", "New C (legacy)"), ("DYA", "A Series")):
            self.assertEqual(profile_for(prefix).name, expected)
        for prefix in ("DYCOM", "DYCX", "DYCO2", "DYCHIL", "DYAPRCO2"):
            self.assertIsNone(profile_for(prefix))

    def test_forged_profile_cannot_authorize_packet_building(self):
        for profile in (replace(PROFILES[0]), replace(PROFILES[0], transport=Transport.HM10)):
            with self.assertRaises(ValueError):
                build_plan(profile, Request(Action.LIGHT, (20,)), STAMP)


class PacketTests(unittest.TestCase):
    def test_all_profiles_have_complete_prelude_manual_and_channels(self):
        for profile in PROFILES:
            with self.subTest(profile=profile.name):
                values = tuple(90 for _ in profile.controls)
                plan = build_plan(profile, Request(Action.LIGHT, values), STAMP)
                self.assertEqual([c.packet[5] for c in plan], [4, 9, 9, 5] + [7] * len(values))
                self.assertEqual([c.packet[6] for c in plan[4:]], list(range(len(values))))
                self.assertEqual([c.packet[7] for c in plan[4:]], [89] * len(values))
                for c in plan:
                    self.assertEqual(calculate_checksum(c.packet[:-1]), c.packet[-1])
                    self.assertEqual(c.packet[2], len(c.packet) - 2)

    def test_external_manual_vectors(self):
        # Fixed upstream capture vectors; independent of our plan builder.
        self.assertEqual(create_command_encoding(0x5A, 15, (0, 0xC7), [100]).hex(),
                         "5a010600c70f64ab")
        self.assertEqual(create_command_encoding(0x5A, 15, (0, 0xC5), [53]).hex(),
                         "5a010600c50f35f8")
        self.assertEqual(create_command_encoding(0x5A, 7, (0, 0x20), [0, 100]).hex(),
                         "5a0107002007006445")

    def test_fixed_white_plan(self):
        plan = build_plan(PROFILES[0], Request(Action.LIGHT, (90,)), STAMP)
        self.assertEqual([c.packet.hex() for c in plan], [
            "5a01060004040106", "5a010b0005091a01050304051a",
            "5a010b0006091a010503040519", "5a01080002050bffff05", "5a010700030700595b"])

    def test_manual_fan_entire_range_and_only_vivid3(self):
        profile = profile_for("DYVVD3")
        for value in range(101):
            plan = build_plan(profile, Request(Action.FAN_MANUAL, (value,)), STAMP)
            self.assertEqual([c.packet[5] for c in plan], [4, 9, 9, 15])
            expected = 0 if not value else max(25, value)
            self.assertEqual(plan[-1].packet[6], 89 if expected == 90 else expected)
            self.assertEqual(plan[-1].packet[6], fan_wire(value))
        for profile in PROFILES[:-1]:
            with self.assertRaises(ValueError):
                build_plan(profile, Request(Action.FAN_MANUAL, (20,)), STAMP)

    def test_auto_and_threshold_vectors(self):
        profile = profile_for("DYVVD3")
        self.assertEqual(build_plan(profile, Request(Action.FAN_AUTO), STAMP)[-1].packet.hex(),
                         "5a010800020511ffff1f")
        self.assertEqual(build_plan(profile, Request(Action.FAN_THRESHOLDS, (38, 33)), STAMP)[-1].packet.hex(),
                         "a5010700022d26212e")

    def test_threshold_boundaries(self):
        profile = profile_for("DYVVD3")
        for start in range(14, 62):
            for stop in range(14, 62):
                request = Request(Action.FAN_THRESHOLDS, (start, stop))
                if 15 <= start <= 60 and 15 <= stop <= 60 and start - stop >= 2:
                    validate_request(profile, request)
                else:
                    with self.assertRaises(ValueError):
                        validate_request(profile, request)

    def test_invalid_types_ranges_counts_and_actions(self):
        for value in (-1, 101, True, False, 1.2, "20", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_plan(PROFILES[0], Request(Action.LIGHT, (value,)), STAMP)
        for request in (Request(Action.LIGHT), Request(Action.LIGHT, (1, 2)),
                        Request("fan_auto"), Request(Action.LIGHT, [20])):
            with self.assertRaises(ValueError):
                build_plan(PROFILES[0], request, STAMP)

    def test_switch_collision_and_no_typed_route(self):
        protection_on = create_command_encoding(0x5A, 5, (0, 1), [0x31, 255, 255])
        indicator_off = create_command_encoding(0x5A, 5, (0, 1), [0x31, 255, 255])
        self.assertEqual(protection_on.hex(), "5a010800010531ffff3c")
        self.assertEqual(protection_on, indicator_off)
        profile = profile_for("DYVVD3")
        for request in (Request(Action.FAN_AUTO), Request(Action.FAN_MANUAL, (31,)),
                        Request(Action.FAN_THRESHOLDS, (38, 33)), Request(Action.LIGHT, (48, 49, 50, 90))):
            for command in build_plan(profile, request, STAMP):
                self.assertFalse(command.packet[5] == 5 and command.packet[6] in (0x30, 0x31, 0x32))
        for action in ("temperature_protection", "indicator_led", "raw"):
            with self.assertRaises(ValueError):
                build_plan(profile, Request(action), STAMP)

    def test_telemetry_shapes_and_unknowns(self):
        for prefix, mode in ((0x5B, 0x0B), (0xB6, 0x16)):
            frame = bytes([prefix, 27, 0, 0, 1, mode, 2, 88, 25, 255])
            parsed = parse_telemetry(frame)
            self.assertEqual((parsed.rpm, parsed.temperature_c), (600, 25))
            for length in range(9):
                self.assertIsNone(parse_telemetry(frame[:length]))
        for mode in range(256):
            if mode != 0x0B:
                self.assertIsNone(parse_telemetry(bytes([0x5B, 0, 0, 0, 0, mode, 0, 0, 0])))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    async def run_fake(self, fixture, request, **kwargs):
        operation = UpstreamOperation(fixture.target, request, self.path, now=lambda: STAMP, **kwargs)
        with fixture.patched():
            result = await operation.run()
        return operation, result

    async def test_all_profiles_actual_executor_topology_and_packets(self):
        for profile in PROFILES:
            fixture = FakeBle(device(profile.prefixes[0]))
            request = Request(Action.LIGHT, tuple(90 for _ in profile.controls))
            with self.subTest(profile=profile.name):
                operation, result = await self.run_fake(fixture, request)
                self.assertEqual(fixture.writes, [c.packet for c in build_plan(profile, request, STAMP)])
                self.assertEqual(result.address, fixture.target.address)
                self.assertNotIn("subscribe", fixture.events)
                self.assertEqual(fixture.events[-1], "disconnect")
                with self.assertRaises(TransportSafetyError):
                    await operation.run()

    async def test_no_transport_fallback_even_with_valid_other_service(self):
        for prefix, other in (("DYNA2", Transport.HM10), ("DYA", Transport.NUS)):
            fixture = FakeBle(device(prefix), topology(other))
            with self.assertRaises(OperationFailed) as failure:
                await self.run_fake(fixture, Request(Action.LIGHT, (20,)))
            self.assertEqual(failure.exception.attempted, 0)
            self.assertEqual(fixture.writes, [])
            self.assertEqual(fixture.events[-1], "disconnect")

    async def test_both_services_do_not_change_selected_transport(self):
        for prefix in ("DYNA2", "DYA"):
            fixture = FakeBle(device(prefix))
            other = Transport.HM10 if prefix == "DYNA2" else Transport.NUS
            fixture.services += topology(other)
            await self.run_fake(fixture, Request(Action.LIGHT, (20,)))
            self.assertEqual(len(fixture.writes), 5)

    async def test_fail_at_every_packet_no_retry(self):
        for prefix in ("DYNA2", "DYA", "DYVVD3"):
            count = len(profile_for(prefix).controls)
            for failure_index in range(1, 5 + count):
                fixture = FakeBle(device(prefix))
                fixture.fail_write = failure_index
                operation = UpstreamOperation(fixture.target, Request(Action.LIGHT, (20,) * count), self.path)
                with fixture.patched(), self.assertRaises(OperationFailed) as failure:
                    await operation.run()
                self.assertEqual(failure.exception.attempted, failure_index)
                self.assertEqual(len(fixture.writes), failure_index)
                self.assertEqual(fixture.events.count("connect"), 1)
                with self.assertRaises(TransportSafetyError):
                    await operation.run()

    async def test_identity_and_topology_mutation_stop_next_write(self):
        for prefix in ("DYNA2", "DYA"):
            for mutate in (lambda f: setattr(f, "address", "00:11:22:33:44:66"),
                           lambda f: setattr(f, "services", topology(profile_for(f.target.name).transport))):
                fixture = FakeBle(device(prefix))
                fixture.after_write = mutate
                with self.assertRaises(OperationFailed):
                    await self.run_fake(fixture, Request(Action.LIGHT, (20,)))
                self.assertEqual(len(fixture.writes), 1)
                self.assertEqual(fixture.events[-1], "disconnect")

    async def test_scan_identity_mismatch_has_zero_writes(self):
        fixture = FakeBle(device("DYNA2"))
        operation = UpstreamOperation(fixture.target, Request(Action.LIGHT, (20,)), self.path)
        fixture.target = replace(fixture.target, name="DYNA2_CHANGED")
        with fixture.patched(), self.assertRaises(OperationFailed):
            await operation.run()
        self.assertNotIn("client", fixture.events)

    async def test_telemetry_passive_no_write_and_cleanup(self):
        for header, mode in ((0x5B, 11), (0xB6, 22)):
            fixture = FakeBle(device())
            fixture.frames = [bytes([header, 0, 0, 0, 0, mode, 2, 88, 25])]
            _, receipt = await self.run_fake(fixture, Request(Action.TELEMETRY))
            self.assertEqual(receipt.telemetry.rpm, 600)
            self.assertEqual(fixture.writes, [])
            self.assertEqual(fixture.events[-2:], ["unsubscribe", "disconnect"])

    async def test_telemetry_timeout_wrong_sender_and_subscribe_failure(self):
        for problem in ("silent", "wrong_sender", "fail_subscribe", "unknown36"):
            fixture = FakeBle(device())
            if problem == "silent":
                fixture.frames = []
            elif problem == "unknown36":
                fixture.frames = [bytes.fromhex("5B 00 00 00 00 36 00 00 00")]
            else:
                setattr(fixture, problem, True)
            with self.assertRaises(OperationFailed):
                await self.run_fake(fixture, Request(Action.TELEMETRY), telemetry_seconds=0.01)
            self.assertEqual(fixture.writes, [])
            self.assertEqual(fixture.events[-2:], ["unsubscribe", "disconnect"])

    async def test_unsubscribe_failure_still_disconnects(self):
        fixture = FakeBle(device())
        fixture.fail_unsubscribe = True
        await self.run_fake(fixture, Request(Action.TELEMETRY))
        self.assertFalse(fixture.is_connected)

    async def test_cancellation_consumes_operation_and_disconnects(self):
        fixture = FakeBle(device())
        fixture.frames = []
        operation = UpstreamOperation(fixture.target, Request(Action.TELEMETRY), self.path)
        with fixture.patched():
            task = asyncio.create_task(operation.run())
            for _ in range(100):
                if "subscribe" in fixture.events:
                    break
                await asyncio.sleep(0.001)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(fixture.is_connected)
        with self.assertRaises(TransportSafetyError):
            await operation.run()

    async def test_locked_devices_cannot_construct_upstream_operation(self):
        for prefix in LOCKED:
            core = DeviceConfig("test", local_model(prefix).name, prefix, "00:11:22:33:44:55")
            with self.assertRaises(TransportSafetyError):
                UpstreamOperation(core, Request(Action.LIGHT, (20,)), self.path)

    async def test_every_vivid3_write_uses_pinned_prelude_without_light_mode(self):
        for request in (Request(Action.FAN_MANUAL, (24,)), Request(Action.FAN_AUTO),
                        Request(Action.FAN_THRESHOLDS, (38, 33))):
            fixture = FakeBle(device())
            await self.run_fake(fixture, request)
            self.assertEqual(len(fixture.writes), 4)
            self.assertEqual([p[5] for p in fixture.writes[:3]], [4, 9, 9])
            self.assertFalse(any(p[5] == 7 or (p[5] == 5 and p[6] == 11) for p in fixture.writes))

    async def test_gui_controller_serializes_and_routes_full_addresses(self):
        controller = ApplicationController(self.path)
        self.addCleanup(controller.close)
        for address in ("00:11:22:33:44:55", "00:11:22:33:44:66"):
            target = device("DYNA2", address)
            fixture = FakeBle(target)
            with fixture.patched():
                receipt = await controller.execute_upstream(CompatibleDevice(target.name, address, target.model),
                                                           Request(Action.LIGHT, (20,)))
            self.assertEqual(receipt.address, address)
            self.assertFalse(controller.busy)


class TopologyTests(unittest.TestCase):
    def test_missing_duplicate_parent_and_properties(self):
        for transport in Transport:
            for problem in ("empty", "duplicate_service", "duplicate_child", "wrong_parent", "no_write", "outside"):
                services = topology(transport)
                if problem == "empty":
                    services = []
                elif problem == "duplicate_service":
                    services += topology(transport)
                elif problem == "duplicate_child":
                    services[0].characteristics.append(services[0].characteristics[0])
                elif problem == "wrong_parent":
                    services[0].characteristics[0].service_uuid = "wrong"
                elif problem == "no_write":
                    services[0].characteristics[0].properties = ["write"]
                else:
                    services.append(NS(uuid="0000fe59-0000-1000-8000-00805f9b34fb",
                                       characteristics=[services[0].characteristics[0]]))
                with self.subTest(transport=transport, problem=problem), self.assertRaises(TransportSafetyError):
                    resolve_endpoint(services, transport)

    def test_nus_missing_notify_and_unrelated_uuid_do_not_authorize(self):
        services = topology(Transport.NUS)
        services[0].characteristics.pop()
        with self.assertRaises(TransportSafetyError):
            resolve_endpoint(services, Transport.NUS)
        services = topology(Transport.HM10)
        services[0].uuid = "unrelated"
        with self.assertRaises(TransportSafetyError):
            resolve_endpoint(services, Transport.HM10)

    def test_handles_are_not_required_or_selected(self):
        for transport in Transport:
            services = topology(transport)
            self.assertIs(resolve_endpoint(services, transport)[1], services[0].characteristics[0])


class LockedSourceTests(unittest.TestCase):
    def test_locked_modules_are_byte_identical_to_release(self):
        root = Path(__file__).resolve().parents[1]
        modules = ("models", "constants", "protocol", "transport", "vivid2", "a2max",
                   "a2max_controller", "a2max_protocol", "magnetic1_controller", "magnetic1_protocol",
                   "magnetic2_controller", "magnetic2_protocol", "zlight_controller", "zlight_protocol",
                   "fan_controller", "fan_protocol")
        for name in modules:
            relative = f"chihiros/{name}.py"
            baseline = subprocess.check_output(["git", "show", f"v1.3.0:{relative}"], cwd=root)
            current = (root / relative).read_bytes()
            self.assertEqual(current.replace(b"\r\n", b"\n"), baseline.replace(b"\r\n", b"\n"), relative)


if __name__ == "__main__":
    unittest.main()
