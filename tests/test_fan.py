from __future__ import annotations

import asyncio
import contextlib
import io
import sys
import tempfile
import tkinter as tk
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from chihiros.constants import (
    BLACKLISTED_CHARACTERISTIC_UUIDS,
    BLACKLISTED_SERVICE_UUIDS,
    COOLING_FAN_MODEL,
    COOLING_FAN_PREFIXES,
    DFU_BUTTONLESS_UUID,
    DFU_SERVICE_UUID,
    FAN_CONFIGURATION_WRITE_DELAY_SECONDS,
    MAGNETIC_II_MODEL,
    MAGNETIC_LIGHT_MODEL,
)
from chihiros.fan_controller import CoolingFanTelemetry, parse_fan_telemetry
from chihiros.fan_protocol import (
    build_fan_automatic_plan,
    build_fan_manual_plan,
    build_fan_status_plan,
    validate_fan_automatic_plan,
    validate_fan_manual_plan,
)
from chihiros.models import A2_MAX_MODEL, detect_supported_model, supported_model
from chihiros.protocol import calculate_checksum
from chihiros.transport import ScanResult, TransportSafetyError, validate_phase4_command
from gui.app import ChihirosApplication
from gui.controller import (
    ApplicationController,
    CompatibleDevice,
    DiscoverySafetyError,
    FanSpeedValidationError,
    FanTemperatureValidationError,
    build_device_choices,
    controls_for_device,
    filter_compatible_devices,
    parse_fan_speed_input,
    parse_fan_temperature_inputs,
)
from tests.test_windows_gui import alternate_layout


# Clearly synthetic identities; no physical advertised name or address is embedded here.
FANS = tuple(
    CompatibleDevice("DYNFAN_SYNTHETIC", address, COOLING_FAN_MODEL)
    for address in ("02:00:00:10:00:01", "02:00:00:10:00:02")
)
TELEMETRY = bytes(
    (0x5B, 0x09, 0x0B, 0x00, 0x01, 0x25, 0x15, 0x9A, 0x0A, 0x5A, 0x01, 0x38, 0xE7)
)
# Synthetic frame with the physically confirmed decoded field values and an
# arbitrary byte 12. It is not the private captured packet.
SELECTABLE_TELEMETRY = bytes(
    (0x5B, 0x09, 0x0B, 0x00, 0x01, 0x25, 0x16, 0xA0, 0x0A, 0x38, 0x00, 0xF0, 0x7E)
)
SECOND_TELEMETRY = bytes(
    (0x5B, 0x09, 0x0B, 0x00, 0x01, 0x25, 0x14, 0xC8, 0x0A, 0x8C, 0x00, 0xF5, 0x19)
)
DUPLICATE_TELEMETRY = bytes(
    (0x5B, 0x09, 0x0B, 0x00, 0x01, 0x25, 0x12, 0x34, 0x0B, 0x54, 0x00, 0xFA, 0x99)
)
# Synthetic x[4] == 1 notification whose 0x0A subtype must not be decoded.
NON_TELEMETRY = bytes(
    (0x5B, 0x09, 0x0B, 0x00, 0x01, 0x0A, 0x16, 0xA0, 0x0A, 0x38, 0x00, 0xF0, 0x33)
)


class FanBluetooth:
    def __init__(self) -> None:
        self.layouts = {fan.identity: alternate_layout() for fan in FANS}
        self.discovery = {
            fan.identity: (
                NS(address=fan.identity, name=fan.name),
                NS(local_name=fan.name, rssi=-40),
            )
            for fan in FANS
        }
        self.events: list[tuple] = []
        self.notifications: list[bytes] = []
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
                self.selected_address = selected.address
                self.address = (
                    FANS[1].identity if fixture.wrong_client_address else selected.address
                )
                self.services, self.rx, self.tx = fixture.layouts[selected.address]
                self.is_connected = False
                self.callback = None

            async def connect(self, *, timeout):
                self.is_connected = True
                fixture.events.append(("connect", self.address))

            async def start_notify(self, characteristic, callback):
                assert characteristic is self.tx
                self.callback = callback
                fixture.events.append(("subscribe", self.address, characteristic.uuid))

            async def stop_notify(self, characteristic):
                assert characteristic is self.tx
                fixture.events.append(("unsubscribe", self.address, characteristic.uuid))
                self.callback = None

            async def write_gatt_char(self, characteristic, packet, *, response):
                assert characteristic is self.rx
                assert response is False
                wire = bytes(packet)
                fixture.events.append(("write", self.address, wire))
                if wire == build_fan_status_plan()[0].packet and self.callback is not None:
                    primary = (
                        SELECTABLE_TELEMETRY
                        if self.selected_address == FANS[0].identity
                        else SECOND_TELEMETRY
                    )
                    assert self.callback is not None
                    for notification in (
                        bytearray(NON_TELEMETRY),
                        bytearray(primary),
                        bytearray(DUPLICATE_TELEMETRY),
                    ):
                        fixture.notifications.append(bytes(notification))
                        self.callback(self.tx, notification)

            async def disconnect(self):
                fixture.events.append(("disconnect", self.address))
                self.is_connected = False

        self.module = NS(BleakScanner=Scanner, BleakClient=Client)

    @property
    def writes(self) -> list[tuple]:
        return [event for event in self.events if event[0] == "write"]


class FanIdentityAndStateTests(unittest.TestCase):
    def test_dynfan_identity_display_and_discovery_are_explicit(self) -> None:
        self.assertEqual(COOLING_FAN_PREFIXES, ("DYNFAN",))
        self.assertEqual(detect_supported_model(" dynfan_synthetic "), COOLING_FAN_MODEL)
        metadata = supported_model(FANS[0].name)
        self.assertEqual((metadata.name, metadata.controls), ("Cooling Fan", ("Fan",)))
        self.assertEqual((metadata.minimum, metadata.maximum), (0, 20))
        self.assertEqual(controls_for_device(FANS[0]), ("Fan",))
        for name in ("DYNFA", "DYNF", "FAN", "NORDIC_UART"):
            self.assertIsNone(detect_supported_model(name))

    def test_two_same_model_fans_route_by_full_canonical_address(self) -> None:
        lowercase = replace(FANS[0], address=FANS[0].address.lower().replace(":", "-"))
        choices = build_device_choices((lowercase, FANS[1]))
        self.assertEqual(set(choices.devices_by_address), {fan.identity for fan in FANS})
        self.assertEqual(len(set(choices.labels)), 2)
        self.assertTrue(all(label.startswith("Cooling Fan — …") for label in choices.labels))
        found = filter_compatible_devices(
            [ScanResult(fan.name, fan.address, fan.model, -40) for fan in FANS]
        )
        self.assertEqual({fan.identity for fan in found}, {fan.identity for fan in FANS})

    def test_per_address_manual_automatic_and_telemetry_state_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = FanBluetooth()
            controller = ApplicationController(Path(directory))
            try:
                with mock.patch.dict(sys.modules, {"bleak": fixture.module}), contextlib.redirect_stdout(
                    io.StringIO()
                ):
                    asyncio.run(controller.apply_fan_manual(FANS[0], 5))
                    asyncio.run(controller.apply_fan_manual(FANS[1], 20))
                    with mock.patch("chihiros.fan_controller.asyncio.sleep", new=mock.AsyncMock()):
                        asyncio.run(controller.apply_fan_automatic(FANS[0], 24, 29))
                        asyncio.run(controller.apply_fan_automatic(FANS[1], 26, 32))
                    asyncio.run(controller.refresh_fan_status(FANS[0]))
                    asyncio.run(controller.refresh_fan_status(FANS[1]))
                first, second = controller.fan_state(FANS[0]), controller.fan_state(FANS[1])
                self.assertEqual((first.manual_speed, first.start_temperature, first.max_temperature), (5, 24, 29))
                self.assertEqual((second.manual_speed, second.start_temperature, second.max_temperature), (20, 26, 32))
                self.assertEqual(first.telemetry.humidity_percent, 57.92)
                self.assertEqual(second.telemetry.humidity_percent, 53.2)
            finally:
                controller.close()


class FanProtocolTests(unittest.TestCase):
    def test_safe_status_query_is_exactly_one_known_packet(self) -> None:
        plan = build_fan_status_plan()
        self.assertEqual(len(plan), 1)
        self.assertFalse(plan[0].changes_state)
        self.assertEqual(plan[0].packet, bytes.fromhex("5A 01 06 00 02 04 01 00"))
        validate_phase4_command(plan[0])

    def test_manual_speed_accepts_every_device_level_zero_through_twenty_literally(self) -> None:
        for speed in range(21):
            with self.subTest(speed=speed):
                plan = build_fan_manual_plan(speed)
                self.assertEqual(len(plan), 1)
                packet = plan[0].packet
                self.assertEqual((packet[0], packet[5], packet[6], packet[7]), (0x5A, 0x07, 0xFF, speed))
                self.assertEqual(calculate_checksum(packet[:-1]), packet[-1])
                validate_phase4_command(plan[0])
        for invalid in (-1, 21, 100, True, False, 1.2, "20", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_fan_manual_plan(invalid)

    def test_manual_plan_rejects_any_automatic_or_extra_command(self) -> None:
        manual = build_fan_manual_plan(10)
        automatic = build_fan_automatic_plan(24, 28, datetime(2026, 9, 4, 12, 34, 56))
        for altered in ((), manual + manual, automatic[:1], build_fan_manual_plan(11)):
            with self.subTest(altered=altered), self.assertRaises(TransportSafetyError):
                validate_fan_manual_plan(altered, 10)

    def test_automatic_sequence_is_complete_ordered_and_has_no_fan_speed(self) -> None:
        timestamp = datetime(2026, 9, 4, 12, 34, 56)
        plan = build_fan_automatic_plan(24, 30, timestamp)
        self.assertEqual(len(plan), 10)
        self.assertEqual(
            [(command.packet[0], command.packet[5], bytes(command.packet[6:-1])) for command in plan],
            [
                (0x5A, 0x04, bytes((0x01,))),
                (0x5A, 0x09, bytes((26, 9, 5, 12, 34, 56))),
                (0x5A, 0x09, bytes((26, 9, 5, 12, 34, 56))),
                (0xA5, 0x04, bytes((0x06,))),
                (0xA5, 0x04, bytes((0x08,))),
                (0xA5, 0x21, bytes((24, 30, 0xFF))),
                (0x5A, 0x05, bytes((0x23, 0xFF, 0xFF))),
                (0x5A, 0x05, bytes((0x22, 0xFF, 0xFF))),
                (0xA5, 0x04, bytes((0x06,))),
                (0xA5, 0x04, bytes((0x08,))),
            ],
        )
        self.assertFalse(any(command.packet[0] == 0x5A and command.packet[5] == 0x07 for command in plan))
        for command in plan:
            validate_phase4_command(command)

    def test_automatic_temperature_validation_and_plan_guard(self) -> None:
        timestamp = datetime(2026, 9, 4, 12, 34, 56)
        self.assertEqual(parse_fan_temperature_inputs("0", "255"), (0, 255))
        for values in ((24, 24), (25, 24), (-1, 24), (24, 256), (24.0, 28), (True, 28), ("24.0", "28")):
            with self.subTest(values=values), self.assertRaises((ValueError, FanTemperatureValidationError)):
                parse_fan_temperature_inputs(*values)
        plan = build_fan_automatic_plan(24, 30, timestamp)
        for altered in (plan[:-1], plan + plan[-1:], (plan[0], plan[2], plan[1], *plan[3:])):
            with self.subTest(altered=altered), self.assertRaises(TransportSafetyError):
                validate_fan_automatic_plan(altered, 24, 30, timestamp)

    def test_gui_manual_speed_parsing_rejects_invalid_values(self) -> None:
        self.assertEqual(parse_fan_speed_input("0"), 0)
        self.assertEqual(parse_fan_speed_input("20"), 20)
        for invalid in (-1, 21, 100, True, 20.0, "1.2", "", None):
            with self.subTest(invalid=invalid), self.assertRaises(FanSpeedValidationError):
                parse_fan_speed_input(invalid)


class FanTelemetryTests(unittest.TestCase):
    def test_all_fields_decode_and_water_temperature_uses_both_bytes(self) -> None:
        telemetry = parse_fan_telemetry(TELEMETRY)
        self.assertEqual(telemetry.humidity_percent, 0x159A / 100.0)
        self.assertEqual(telemetry.room_temperature_c, 26.5)
        self.assertEqual(telemetry.water_temperature_c, 31.2)
        self.assertGreater(telemetry.water_temperature_c, 25.5)
        self.assertFalse(hasattr(telemetry, "current_fan_speed"))

    def test_synthetic_physical_field_equivalent_decodes_expected_values(self) -> None:
        telemetry = parse_fan_telemetry(SELECTABLE_TELEMETRY)
        self.assertEqual(telemetry.humidity_percent, 57.92)
        self.assertEqual(telemetry.room_temperature_c, 26.16)
        self.assertEqual(telemetry.water_temperature_c, 24.0)

    def test_byte_twelve_is_unknown_and_does_not_affect_decoding(self) -> None:
        changed_unknown = SELECTABLE_TELEMETRY[:-1] + b"\x01"
        self.assertEqual(
            parse_fan_telemetry(changed_unknown),
            parse_fan_telemetry(SELECTABLE_TELEMETRY),
        )

    def test_short_malformed_or_unrelated_notifications_are_ignored(self) -> None:
        bad_length_field = TELEMETRY[:2] + b"\x0A" + TELEMETRY[3:]
        bad_humidity = TELEMETRY[:6] + b"\x27\x11" + TELEMETRY[8:]
        implausible_room = TELEMETRY[:8] + b"\x00\x00" + TELEMETRY[10:]
        sentinel_water = TELEMETRY[:10] + b"\xFF\xFF" + TELEMETRY[12:]
        for payload in (
            b"",
            TELEMETRY[:12],
            bytes((0x5A,)) + TELEMETRY[1:],
            TELEMETRY[:4] + b"\x02" + TELEMETRY[5:],
            bad_length_field,
            bad_humidity,
            implausible_room,
            sentinel_water,
            NON_TELEMETRY,
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(parse_fan_telemetry(payload))


class FanTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = ApplicationController(Path(self.temp.name))
        self.addCleanup(self.controller.close)
        self.fixture = FanBluetooth()
        self.patch = mock.patch.dict(sys.modules, {"bleak": self.fixture.module})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_refresh_subscribes_only_tx_queries_once_then_unsubscribes_and_disconnects(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            telemetry, _path = asyncio.run(self.controller.refresh_fan_status(FANS[0]))
        self.assertEqual(telemetry.water_temperature_c, 24.0)
        self.assertEqual(
            [event[0] for event in self.fixture.events],
            ["scan", "connect", "subscribe", "write", "unsubscribe", "disconnect"],
        )
        self.assertEqual(len(self.fixture.writes), 1)
        self.assertEqual(self.fixture.writes[0][2], build_fan_status_plan()[0].packet)
        self.assertEqual(self.fixture.events[2][2].lower(), "6e400003-b5a3-f393-e0a9-e50e24dcca9e")

    def test_refresh_rejects_0x0a_and_keeps_first_of_duplicate_0x25_frames(self) -> None:
        self.assertIsNone(parse_fan_telemetry(NON_TELEMETRY))
        with contextlib.redirect_stdout(io.StringIO()):
            telemetry, _path = asyncio.run(self.controller.refresh_fan_status(FANS[0]))
        self.assertEqual(
            self.fixture.notifications,
            [NON_TELEMETRY, SELECTABLE_TELEMETRY, DUPLICATE_TELEMETRY],
        )
        self.assertEqual(telemetry, parse_fan_telemetry(SELECTABLE_TELEMETRY))
        self.assertNotEqual(telemetry, parse_fan_telemetry(DUPLICATE_TELEMETRY))

    def test_manual_sends_only_fan_speed_without_subscription_or_automatic_commands(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(self.controller.apply_fan_manual(FANS[0], 12))
        self.assertEqual([event[0] for event in self.fixture.events], ["scan", "connect", "write", "disconnect"])
        self.assertEqual([write[2] for write in self.fixture.writes], [build_fan_manual_plan(12)[0].packet])

    def test_automatic_uses_200ms_gaps_disconnects_and_never_sends_fan_speed(self) -> None:
        gaps: list[float] = []

        async def gap(seconds: float) -> None:
            gaps.append(seconds)

        with contextlib.redirect_stdout(io.StringIO()), mock.patch(
            "chihiros.fan_controller.asyncio.sleep", side_effect=gap
        ):
            asyncio.run(self.controller.apply_fan_automatic(FANS[0], 24, 30))
        self.assertEqual(len(self.fixture.writes), 10)
        self.assertEqual(gaps, [FAN_CONFIGURATION_WRITE_DELAY_SECONDS] * 9)
        self.assertEqual(self.fixture.events[-1], ("disconnect", FANS[0].identity))
        self.assertNotIn("subscribe", [event[0] for event in self.fixture.events])
        self.assertFalse(any(write[2][0] == 0x5A and write[2][5] == 0x07 for write in self.fixture.writes))

    def test_automatic_leaves_no_background_polling_task(self) -> None:
        async def exercise() -> None:
            current = asyncio.current_task()
            with contextlib.redirect_stdout(io.StringIO()), mock.patch(
                "chihiros.fan_controller.asyncio.sleep", new=mock.AsyncMock()
            ):
                await self.controller.apply_fan_automatic(FANS[0], 24, 30)
            pending = [
                task for task in asyncio.all_tasks() if task is not current and not task.done()
            ]
            self.assertEqual(pending, [])

        asyncio.run(exercise())

    def test_wrong_connected_address_and_dfu_topology_prevent_writes(self) -> None:
        self.fixture.wrong_client_address = True
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(TransportSafetyError):
            asyncio.run(self.controller.apply_fan_manual(FANS[0], 10))
        self.assertEqual(self.fixture.writes, [])
        self.fixture.events.clear()
        self.fixture.wrong_client_address = False
        _services, rx, _tx = self.fixture.layouts[FANS[0].identity] = alternate_layout()
        rx.service_uuid = DFU_SERVICE_UUID
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(TransportSafetyError):
            asyncio.run(self.controller.apply_fan_manual(FANS[0], 10))
        self.assertEqual(self.fixture.writes, [])
        self.assertIn(DFU_SERVICE_UUID, BLACKLISTED_SERVICE_UUIDS)
        self.assertIn(DFU_BUTTONLESS_UUID, BLACKLISTED_CHARACTERISTIC_UUIDS)

    def test_other_models_cannot_enter_fan_operations(self) -> None:
        wrong = CompatibleDevice("DYNV_SYNTHETIC", FANS[0].identity)
        for coroutine in (
            self.controller.refresh_fan_status(wrong),
            self.controller.apply_fan_manual(wrong, 10),
            self.controller.apply_fan_automatic(wrong, 24, 30),
        ):
            with self.subTest(coroutine=coroutine), self.assertRaises(DiscoverySafetyError):
                asyncio.run(coroutine)
        self.assertEqual(self.fixture.events, [])


class FanWidgetTests(unittest.TestCase):
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

    def test_fan_ui_is_model_specific_and_per_address_values_and_telemetry_are_isolated(self) -> None:
        vivid = CompatibleDevice("DYNV_SYNTHETIC", "02:00:00:20:00:01")
        a2max = CompatibleDevice("DYNCMC_SYNTHETIC", "02:00:00:20:00:02", A2_MAX_MODEL)
        magnetic1 = CompatibleDevice("DYCX_SYNTHETIC", "02:00:00:20:00:03", MAGNETIC_LIGHT_MODEL)
        magnetic2 = CompatibleDevice("DYMNC_SYNTHETIC", "02:00:00:20:00:04", MAGNETIC_II_MODEL)
        self.controller._fan_state_by_address[FANS[0].identity] = replace(
            self.controller.fan_state(FANS[0]),
            telemetry=CoolingFanTelemetry(
                room_temperature_c=26.5,
                water_temperature_c=31.2,
                humidity_percent=55.25,
            ),
        )
        self.app._scan_completed((*FANS, vivid, a2max, magnetic1, magnetic2))

        self.select(FANS[0])
        self.assertTrue(self.app.fan_frame.winfo_ismapped())
        for light_frame in (self.app.rgb_frame, self.app.brightness_frame, self.app.rg_frame, self.app.wrgb_frame):
            self.assertFalse(light_frame.winfo_ismapped())
        self.assertEqual(self.app.fan_water_temperature_var.get(), "31.2 °C")
        self.assertEqual(self.app.fan_room_temperature_var.get(), "26.50 °C")
        self.assertEqual(self.app.fan_humidity_var.get(), "55.25%")
        self.app.fan_speed_var.set(5)
        self.app.fan_start_temperature_var.set("25")
        self.app.fan_max_temperature_var.set("31")

        self.select(FANS[1])
        self.assertEqual(self.app.fan_speed_var.get(), 0)
        self.assertEqual(self.app.fan_water_temperature_var.get(), "Not read")
        self.app.fan_speed_var.set(20)
        self.app.fan_start_temperature_var.set("27")
        self.app.fan_max_temperature_var.set("33")
        for light in (vivid, a2max, magnetic1, magnetic2):
            self.select(light)
            self.assertFalse(self.app.fan_frame.winfo_ismapped())
        self.select(FANS[0])
        self.assertEqual(
            (
                self.app.fan_speed_var.get(),
                self.app.fan_start_temperature_var.get(),
                self.app.fan_max_temperature_var.get(),
            ),
            (5, "25", "31"),
        )
        self.select(FANS[1])
        self.assertEqual(
            (
                self.app.fan_speed_var.get(),
                self.app.fan_start_temperature_var.get(),
                self.app.fan_max_temperature_var.get(),
            ),
            (20, "27", "33"),
        )

    def test_fan_controls_have_no_silent_mode_and_busy_state_covers_every_action(self) -> None:
        self.app._scan_completed((FANS[0],))
        self.root.update()
        texts: list[str] = []

        def collect(widget) -> None:
            try:
                texts.append(str(widget.cget("text")))
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                collect(child)

        collect(self.app.fan_frame)
        self.assertNotIn("Silent Mode", texts)
        self.assertNotIn("Current Fan Speed:", texts)
        self.assertIn("Speed Level: 0–20", texts)
        self.assertEqual(
            (float(self.app.fan_speed_scale.cget("from")), float(self.app.fan_speed_scale.cget("to"))),
            (0, 20),
        )
        self.app._set_busy(True)
        for button in (
            self.app.refresh_fan_status_button,
            self.app.apply_fan_manual_button,
            self.app.apply_fan_automatic_button,
        ):
            self.assertEqual(str(button.cget("state")), "disabled")
        self.app._set_busy(False)
        self.assertEqual(str(self.app.apply_fan_automatic_button.cget("state")), "normal")


if __name__ == "__main__":
    unittest.main()
