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

from chihiros.constants import (
    BLACKLISTED_CHARACTERISTIC_UUIDS, BLACKLISTED_SERVICE_UUIDS,
    DFU_BUTTONLESS_UUID, DFU_SERVICE_UUID, Z_LIGHT_MODEL, Z_LIGHT_PREFIXES,
)
from chihiros.models import detect_supported_model, supported_model
from chihiros.transport import TransportSafetyError
from chihiros.zlight_protocol import (
    Z_LIGHT_CHANNELS, build_zlight_manual_plan, validate_zlight_manual_plan,
)
from gui.app import ChihirosApplication
from gui.controller import (
    ApplicationController, CompatibleDevice, WhiteValidationError,
    build_device_choices, controls_for_device, parse_white_inputs,
)
from tests.test_windows_gui import alternate_layout


LIGHTS = tuple(
    CompatibleDevice("DYSSD_SYNTHETIC", address, Z_LIGHT_MODEL)
    for address in ("02:00:00:1B:55:66", "02:00:00:2B:55:66")
)
LEVELS = (20, 40)
GOLDEN = tuple(bytes.fromhex(value) for value in (
    "5A 01 08 00 02 05 0B FF FF 05",
    "5A 01 07 00 03 07 00 14 16",
    "5A 01 07 00 04 07 01 28 2C",
))


class SyntheticZLights:
    def __init__(self) -> None:
        self.layouts = {device.identity: alternate_layout() for device in LIGHTS}
        self.discovery = {
            device.identity: (
                NS(address=device.identity, name=device.name),
                NS(local_name=device.name, rssi=-40),
            ) for device in LIGHTS
        }
        self.events: list[tuple] = []
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
                self.address = selected.address
                self.services, self.rx, self.tx = fixture.layouts[self.address]
                self.is_connected = False

            async def connect(self, *, timeout):
                self.is_connected = True
                fixture.events.append(("connect", self.address))

            async def write_gatt_char(self, characteristic, packet, *, response):
                assert characteristic is self.rx
                assert response is False
                fixture.events.append(("write", self.address, bytes(packet)))

            async def disconnect(self):
                fixture.events.append(("disconnect", self.address))
                self.is_connected = False

        # Deliberately no read, notify, subscribe, DFU, handle-based, or raw API.
        self.module = NS(BleakScanner=Scanner, BleakClient=Client)

    @property
    def writes(self):
        return [event for event in self.events if event[0] == "write"]


class ZLightProtocolTests(unittest.TestCase):
    def test_dyssd_classifies_as_z_light_with_exact_capabilities(self) -> None:
        self.assertEqual(Z_LIGHT_PREFIXES, ("DYSSD",))
        self.assertEqual(detect_supported_model(" dyssd_SYNTHETIC "), Z_LIGHT_MODEL)
        metadata = supported_model(LIGHTS[0].name)
        self.assertEqual(metadata.name, "Z Light")
        self.assertEqual((metadata.controls, metadata.minimum, metadata.maximum),
                         (("Cool White", "Warm White"), 0, 100))
        self.assertEqual(controls_for_device(LIGHTS[0]), ("Cool White", "Warm White"))

    def test_exact_apply_sequence_and_channel_mapping(self) -> None:
        plan = build_zlight_manual_plan(*LEVELS)
        self.assertEqual(tuple(command.packet for command in plan), GOLDEN)
        self.assertEqual(Z_LIGHT_CHANNELS, {"cool_white": 0, "warm_white": 1})
        self.assertEqual(tuple(command.packet[6] for command in plan[1:]), (0, 1))
        self.assertTrue(all(command.packet[5] in (0x05, 0x07) for command in plan))
        self.assertNotIn(2, (command.packet[6] for command in plan[1:]))
        self.assertNotIn(3, (command.packet[6] for command in plan[1:]))

    def test_range_and_invalid_values(self) -> None:
        self.assertEqual(parse_white_inputs("0", 100), (0, 100))
        for invalid in (-1, 101, True, 20.0, "1.2", "", None):
            with self.subTest(invalid=invalid), self.assertRaises(WhiteValidationError):
                parse_white_inputs(20, invalid)

    def test_incomplete_reordered_or_different_plan_is_rejected(self) -> None:
        plan = build_zlight_manual_plan(*LEVELS)
        for altered in (plan[:-1], plan + plan[-1:], (plan[0], plan[2], plan[1]),
                        build_zlight_manual_plan(21, 40)):
            with self.subTest(altered=altered), self.assertRaises(TransportSafetyError):
                validate_zlight_manual_plan(altered, LEVELS)

    def test_dfu_protections_remain_permanent(self) -> None:
        self.assertIn(DFU_SERVICE_UUID, BLACKLISTED_SERVICE_UUIDS)
        self.assertIn(DFU_BUTTONLESS_UUID, BLACKLISTED_CHARACTERISTIC_UUIDS)


class ZLightAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = ApplicationController(Path(self.temp.name))
        self.addCleanup(self.controller.close)
        self.fixture = SyntheticZLights()
        self.patch = mock.patch.dict(sys.modules, {"bleak": self.fixture.module})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def apply(self, device=LIGHTS[0]):
        with contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(self.controller.apply_white(device, *LEVELS))

    def test_service_scoped_rx_order_delay_disconnect_and_no_dfu_interaction(self) -> None:
        async def gap(seconds):
            self.fixture.events.append(("gap", seconds))

        with mock.patch("chihiros.zlight_controller.asyncio.sleep", side_effect=gap):
            self.apply()
        self.assertEqual([event[0] for event in self.fixture.events],
                         ["scan", "connect", "write", "gap", "write", "gap", "write", "disconnect"])
        self.assertEqual([event[2] for event in self.fixture.writes], list(GOLDEN))
        self.assertEqual([event[1] for event in self.fixture.events if event[0] == "gap"], [0.03, 0.03])
        services, rx, _tx = self.fixture.layouts[LIGHTS[0].identity]
        self.assertTrue(all(write[1] == LIGHTS[0].identity for write in self.fixture.writes))
        self.assertTrue(all(rx is services[0].characteristics[0] for _ in self.fixture.writes))

    def test_multiple_units_route_by_canonical_full_address(self) -> None:
        choices = build_device_choices(LIGHTS)
        self.assertEqual(set(choices.addresses), {device.identity for device in LIGHTS})
        self.assertEqual(len(set(choices.labels)), 2)
        for selected in LIGHTS:
            self.fixture.events.clear()
            self.apply(selected)
            self.assertEqual(self.fixture.writes,
                             [("write", selected.identity, packet) for packet in GOLDEN])

    def test_noncanonical_selected_address_is_normalized_before_connect(self) -> None:
        selected = replace(LIGHTS[0], address=LIGHTS[0].address.lower().replace(":", "-"))
        self.apply(selected)
        self.assertEqual({write[1] for write in self.fixture.writes}, {LIGHTS[0].identity})

    def test_dfu_parent_for_rx_fails_before_any_write(self) -> None:
        _services, rx, _tx = self.fixture.layouts[LIGHTS[0].identity]
        rx.service_uuid = DFU_SERVICE_UUID
        with self.assertRaises(TransportSafetyError):
            self.apply()
        self.assertEqual(self.fixture.writes, [])


class ZLightWidgetTests(unittest.TestCase):
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

    def select(self, device):
        self.app.device_combo.current(self.app.device_addresses.index(device.identity))
        self.app._device_selected()
        self.root.update()

    def test_only_white_ui_shows_and_values_are_isolated_per_address(self) -> None:
        vivid = CompatibleDevice("DYNV_SYNTHETIC", "02:00:00:00:00:01")
        self.app._scan_completed((*LIGHTS, vivid))
        self.select(LIGHTS[0])
        self.assertTrue(self.app.white_frame.winfo_ismapped())
        self.assertTrue(self.app.apply_white_button.winfo_ismapped())
        for frame in (self.app.rgb_frame, self.app.brightness_frame, self.app.rg_frame,
                      self.app.wrgb_frame, self.app.fan_frame):
            self.assertFalse(frame.winfo_ismapped())
        self.assertEqual(len(self.app.white_scales), 2)
        self.assertTrue(all((float(s.cget("from")), float(s.cget("to"))) == (0, 100)
                            for s in self.app.white_scales))
        self.app.white_vars[0].set(75)
        self.select(LIGHTS[1])
        self.assertEqual(tuple(v.get() for v in self.app.white_vars), (20, 20))
        self.app.white_vars[1].set(35)
        self.select(vivid)
        self.assertTrue(self.app.rgb_frame.winfo_ismapped())
        self.assertFalse(self.app.white_frame.winfo_ismapped())
        self.select(LIGHTS[0])
        self.assertEqual(tuple(v.get() for v in self.app.white_vars), (75, 20))
        self.select(LIGHTS[1])
        self.assertEqual(tuple(v.get() for v in self.app.white_vars), (20, 35))


if __name__ == "__main__":
    unittest.main()
