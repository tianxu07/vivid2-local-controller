from __future__ import annotations

import asyncio
import contextlib
import io
import sys
import tempfile
import tkinter as tk
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from chihiros.a2max_protocol import build_a2max_manual_plan
from chihiros.constants import (
    BLACKLISTED_CHARACTERISTIC_UUIDS,
    BLACKLISTED_SERVICE_UUIDS,
    DFU_BUTTONLESS_UUID,
    DFU_SERVICE_UUID,
    MAGNETIC_II_MODEL,
    MAGNETIC_LIGHT_MODEL,
    MAGNETIC_LIGHT_PREFIXES,
    RGB_VIVID_II_MODEL,
)
from chihiros.magnetic1_controller import Magnetic1ManualSession
from chihiros.magnetic1_protocol import (
    build_magnetic1_manual_plan,
    validate_magnetic1_manual_plan,
)
from chihiros.magnetic2_protocol import build_magnetic2_manual_plan
from chihiros.models import A2_MAX_MODEL, detect_supported_model, supported_model
from chihiros.protocol import Command, calculate_checksum, create_auto_mode
from chihiros.transport import (
    LocalSessionLog,
    TransportSafetyError,
    serialize_gatt,
    strict_resolve_nus,
)
from chihiros.vivid2 import build_manual_plan
from gui.app import ChihirosApplication
from gui.controller import (
    ApplicationController,
    CompatibleDevice,
    DiscoverySafetyError,
    RgValidationError,
    build_device_choices,
    controls_for_device,
    parse_rg_inputs,
)
from tests.test_windows_gui import alternate_layout


# Synthetic identities only. Matching names and suffixes exercise address routing.
LIGHTS = tuple(
    CompatibleDevice("DYCX_SYNTHETIC", address, MAGNETIC_LIGHT_MODEL)
    for address in ("02:00:00:1A:44:55", "02:00:00:2A:44:55")
)
LEVELS = (20, 40)
GOLDEN = tuple(
    bytes.fromhex(value)
    for value in (
        "5A 01 08 00 02 05 0B FF FF 05",
        "5A 01 07 00 03 07 00 14 16",
        "5A 01 07 00 04 07 01 28 2C",
    )
)


class MagneticLights:
    """Fake Bluetooth bound to canonical full addresses and NUS child objects."""

    def __init__(self) -> None:
        self.layouts = {device.identity: alternate_layout() for device in LIGHTS}
        self.discovery = {
            device.identity: (
                NS(address=device.identity, name=device.name),
                NS(local_name=device.name, rssi=-40),
            )
            for device in LIGHTS
        }
        self.events: list[tuple] = []
        self.fail_at: int | None = None
        self.wrong_client_address = False
        fixture = self

        class Scanner:
            @staticmethod
            async def discover(*, timeout, return_adv):
                assert return_adv
                fixture.events.append(("scan", timeout))
                return fixture.discovery

        class Client:
            def __init__(self, selected, *, pair):
                assert pair is False
                address = selected.address
                assert selected is fixture.discovery[address][0]
                self.address = LIGHTS[1].identity if fixture.wrong_client_address else address
                self.services, self.rx, self.tx = fixture.layouts[address]
                self.is_connected = False

            async def connect(self, *, timeout):
                self.is_connected = True
                fixture.events.append(("connect", self.address))

            async def write_gatt_char(self, characteristic, packet, *, response):
                assert characteristic is self.rx
                assert response is False
                fixture.events.append(("write", self.address, bytes(packet)))
                if len(fixture.writes) == fixture.fail_at:
                    raise OSError("uncertain synthetic write")

            async def disconnect(self):
                fixture.events.append(("disconnect", self.address))
                self.is_connected = False

        # No notification, pairing, read, handle lookup, or raw-write helper exists.
        self.module = NS(BleakScanner=Scanner, BleakClient=Client)

    @property
    def writes(self) -> list[tuple]:
        return [event for event in self.events if event[0] == "write"]


class Magnetic1ProtocolTests(unittest.TestCase):
    def test_dycx_registry_name_and_two_controls_are_explicit(self) -> None:
        self.assertEqual(MAGNETIC_LIGHT_PREFIXES, ("DYCX",))
        self.assertEqual(detect_supported_model(" dycx_SYNTHETIC "), MAGNETIC_LIGHT_MODEL)
        metadata = supported_model(LIGHTS[0].name)
        self.assertEqual(metadata.name, "Magnetic Light")
        self.assertEqual((metadata.controls, metadata.minimum, metadata.maximum),
                         (("Red", "Green"), 0, 100))
        self.assertEqual(controls_for_device(LIGHTS[0]), ("Red", "Green"))
        for name in ("DYC", "DYCY", "Magnetic Light", "NORDIC_UART"):
            self.assertIsNone(detect_supported_model(name))

    def test_exact_apply_rg_packets_and_channel_mapping(self) -> None:
        plan = build_magnetic1_manual_plan(*LEVELS)
        self.assertEqual(tuple(command.packet for command in plan), GOLDEN)
        self.assertEqual(tuple(command.packet[6] for command in plan[1:]), (0, 1))
        self.assertNotIn(2, (command.packet[6] for command in plan[1:]))
        self.assertNotIn(3, (command.packet[6] for command in plan[1:]))

    def test_every_normal_level_is_literal_and_overclock_is_rejected(self) -> None:
        for value in range(101):
            with self.subTest(value=value):
                plan = build_magnetic1_manual_plan(value, value)
                self.assertEqual(
                    [command.packet[6:8] for command in plan[1:]],
                    [bytes((0, value)), bytes((1, value))],
                )
                for command in plan:
                    self.assertEqual(calculate_checksum(command.packet[:-1]), command.packet[-1])
                    self.assertNotEqual(command.packet[-1], 0x5A)
        for invalid in (-1, 101, 255, True, False, 20.0, "20", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_magnetic1_manual_plan(invalid, 40)

    def test_input_parsing_accepts_only_whole_numbers_zero_through_one_hundred(self) -> None:
        self.assertEqual(parse_rg_inputs("0", "100"), (0, 100))
        for invalid in (-1, 101, True, 20.0, "1.2", "", None):
            with self.subTest(invalid=invalid), self.assertRaises(RgValidationError):
                parse_rg_inputs(20, invalid)

    def test_incomplete_reordered_or_other_model_plans_are_rejected(self) -> None:
        plan = build_magnetic1_manual_plan(*LEVELS)
        auto = Command("auto", (0, 2), create_auto_mode((0, 2)), True)
        altered_plans = (
            plan[:-1],
            plan + plan[-1:],
            (plan[0], plan[2], plan[1]),
            (auto, *plan[1:]),
            build_magnetic1_manual_plan(21, 40),
        )
        for altered in altered_plans:
            with self.subTest(altered=altered), self.assertRaises(TransportSafetyError):
                validate_magnetic1_manual_plan(altered, LEVELS)

    def test_existing_model_capabilities_and_packet_plans_are_unchanged(self) -> None:
        cases = (
            ("DYNV_SYNTHETIC", RGB_VIVID_II_MODEL, ("Red", "Green", "Blue")),
            ("DYNCMC_SYNTHETIC", A2_MAX_MODEL, ("Brightness",)),
            ("DYMNC_SYNTHETIC", MAGNETIC_II_MODEL, ("Red", "Green", "Blue", "White")),
        )
        for name, model, controls in cases:
            with self.subTest(model=model):
                self.assertEqual(detect_supported_model(name), model)
                self.assertEqual(supported_model(name).controls, controls)
        self.assertEqual([command.packet[6] for command in build_manual_plan(20, 40, 60)[1:]],
                         [0, 1, 2])
        self.assertEqual(len(build_a2max_manual_plan(60)), 2)
        self.assertEqual([command.packet[6] for command in build_magnetic2_manual_plan(20, 40, 60, 30)[1:]],
                         [0, 1, 2, 3])

    def test_fe59_and_buttonless_dfu_remain_blacklisted(self) -> None:
        self.assertIn(DFU_SERVICE_UUID, BLACKLISTED_SERVICE_UUIDS)
        self.assertIn(DFU_BUTTONLESS_UUID, BLACKLISTED_CHARACTERISTIC_UUIDS)
        services, rx, tx = alternate_layout()
        resolved = strict_resolve_nus(services)
        self.assertIs(resolved.rx, rx)
        self.assertIs(resolved.tx, tx)
        dfu = next(item for item in serialize_gatt(services) if item["uuid"] == DFU_SERVICE_UUID)
        self.assertTrue(dfu["blacklisted"])
        self.assertTrue(all(item["blacklisted"] for item in dfu["characteristics"]))


class Magnetic1AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = ApplicationController(Path(self.temp.name))
        self.addCleanup(self.controller.close)
        self.fixture = MagneticLights()
        self.patch = mock.patch.dict(sys.modules, {"bleak": self.fixture.module})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def apply(self, device=LIGHTS[0], levels=LEVELS):
        with contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(self.controller.apply_rg(device, *levels))

    def test_each_same_model_device_routes_three_writes_to_only_its_full_address(self) -> None:
        choices = build_device_choices(LIGHTS)
        self.assertEqual(len(set(choices.labels)), 2)
        for selected in LIGHTS:
            with self.subTest(address=selected.identity):
                self.fixture.events.clear()

                async def gap(seconds):
                    self.fixture.events.append(("gap", seconds))

                with mock.patch("chihiros.magnetic1_controller.asyncio.sleep", side_effect=gap):
                    self.apply(selected)
                self.assertEqual(
                    [event[0] for event in self.fixture.events],
                    ["scan", "connect", "write", "gap", "write", "gap", "write", "disconnect"],
                )
                self.assertEqual(
                    self.fixture.writes,
                    [("write", selected.identity, packet) for packet in GOLDEN],
                )
                self.assertEqual(
                    [event[1] for event in self.fixture.events if event[0] == "gap"],
                    [0.03, 0.03],
                )

    def test_noncanonical_input_normalizes_before_connect_and_model_mismatch_is_blocked(self) -> None:
        device = replace(LIGHTS[0], address=LIGHTS[0].address.lower().replace(":", "-"))
        self.apply(device)
        self.assertEqual({write[1] for write in self.fixture.writes}, {LIGHTS[0].identity})
        self.fixture.events.clear()
        wrong = CompatibleDevice("DYMNC_SYNTHETIC", LIGHTS[0].identity, MAGNETIC_II_MODEL)
        with self.assertRaises(DiscoverySafetyError):
            self.apply(wrong)
        self.assertEqual(self.fixture.events, [])

    def test_wrong_connected_address_or_dfu_parent_never_writes(self) -> None:
        self.fixture.wrong_client_address = True
        with self.assertRaises(TransportSafetyError):
            self.apply()
        self.assertEqual(self.fixture.writes, [])
        self.fixture.events.clear()
        self.fixture.wrong_client_address = False
        _services, rx, _tx = self.fixture.layouts[LIGHTS[0].identity] = alternate_layout()
        rx.service_uuid = DFU_SERVICE_UUID
        with self.assertRaises(TransportSafetyError):
            self.apply()
        self.assertEqual(self.fixture.writes, [])

    def test_uncertain_write_never_retries_continues_or_restores(self) -> None:
        for failure in range(1, 4):
            with self.subTest(failure=failure):
                self.fixture.events.clear()
                self.fixture.fail_at = failure
                with self.assertRaises(OSError):
                    self.apply()
                self.assertEqual([write[2] for write in self.fixture.writes], list(GOLDEN[:failure]))
                self.assertEqual(self.fixture.events[-1][0], "disconnect")

    def test_session_rejects_out_of_order_packet_and_stays_poisoned(self) -> None:
        async def exercise() -> None:
            log = LocalSessionLog(Path(self.temp.name), LIGHTS[0].as_core_config(), "guard")
            session = Magnetic1ManualSession(LIGHTS[0].as_core_config(), log, levels=LEVELS)
            async with session:
                plan = build_magnetic1_manual_plan(*LEVELS)
                with self.assertRaises(TransportSafetyError):
                    await session.send(plan[1])
                with self.assertRaises(TransportSafetyError):
                    await session.send(plan[0])

        with contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(exercise())
        self.assertEqual(self.fixture.writes, [])
        self.assertEqual(self.fixture.events[-1][0], "disconnect")


class Magnetic1WidgetTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.attributes("-alpha", 0.0)
        self.temp = tempfile.TemporaryDirectory()
        self.controller = ApplicationController(Path(self.temp.name))
        self.app = ChihirosApplication(self.root, self.controller)

    def tearDown(self) -> None:
        if hasattr(self, "app"):
            self.app.close()
            self.temp.cleanup()

    def select(self, device: CompatibleDevice) -> None:
        self.app.device_combo.current(self.app.device_addresses.index(device.identity))
        self.app._device_selected()
        self.root.update()

    def test_only_rg_controls_show_and_values_are_isolated_by_address(self) -> None:
        vivid = CompatibleDevice("DYNV_SYNTHETIC", "02:00:00:00:00:01")
        magnetic2 = CompatibleDevice(
            "DYMNC_SYNTHETIC", "02:00:00:00:00:02", MAGNETIC_II_MODEL
        )
        self.app._scan_completed((*LIGHTS, vivid, magnetic2))

        self.select(LIGHTS[0])
        self.assertTrue(self.app.rg_frame.winfo_ismapped())
        self.assertTrue(self.app.apply_rg_button.winfo_ismapped())
        self.assertFalse(self.app.rgb_frame.winfo_ismapped())
        self.assertFalse(self.app.wrgb_frame.winfo_ismapped())
        self.assertEqual(len(self.app.rg_scales), 2)
        for scale in self.app.rg_scales:
            self.assertEqual(
                (float(scale.cget("from")), float(scale.cget("to")), float(scale.cget("resolution"))),
                (0, 100, 1),
            )

        for variable, value in zip(self.app.rg_vars, LEVELS):
            variable.set(value)
        self.select(LIGHTS[1])
        self.assertEqual(tuple(variable.get() for variable in self.app.rg_vars), (20, 20))
        self.app.rg_vars[0].set(75)
        self.select(magnetic2)
        self.assertTrue(self.app.wrgb_frame.winfo_ismapped())
        self.assertFalse(self.app.rg_frame.winfo_ismapped())
        self.select(vivid)
        self.assertTrue(self.app.rgb_frame.winfo_ismapped())
        self.select(LIGHTS[0])
        self.assertEqual(tuple(variable.get() for variable in self.app.rg_vars), LEVELS)
        self.select(LIGHTS[1])
        self.assertEqual(tuple(variable.get() for variable in self.app.rg_vars), (75, 20))

    def test_busy_state_includes_rg_action_and_scales(self) -> None:
        self.app._scan_completed((LIGHTS[0],))
        self.app._set_busy(True)
        self.assertEqual(str(self.app.apply_rg_button.cget("state")), "disabled")
        self.assertTrue(all(str(scale.cget("state")) == "disabled" for scale in self.app.rg_scales))
        self.app._set_busy(False)
        self.assertEqual(str(self.app.apply_rg_button.cget("state")), "normal")


if __name__ == "__main__":
    unittest.main()
