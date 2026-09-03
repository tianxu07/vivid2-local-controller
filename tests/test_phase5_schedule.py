from __future__ import annotations

import unittest
from datetime import datetime

from chihiros.protocol import Command
from chihiros.schedule import (
    ALL_WEEKDAYS,
    SchedulePeriod,
    Weekday,
    build_clock_sync_plan,
    build_curve_plan,
    build_schedule_period_plan,
    encode_rtc,
    encode_weekdays,
    parse_time,
    parse_weekdays,
)
from chihiros.transport import TransportSafetyError, validate_phase4_command


class Phase5ScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.period = SchedulePeriod(
            parse_time("15:30"),
            parse_time("22:30"),
            30,
            82,
            82,
            82,
            parse_weekdays("all"),
        )

    def test_known_period_has_exact_a5_19_packet(self) -> None:
        command = build_schedule_period_plan(self.period)[0]
        self.assertEqual(command.message_id, (0, 2))
        self.assertEqual(
            command.packet,
            bytes.fromhex(
                "A5 01 13 00 02 19 0F 1E 16 1E 1E 7F 52 52 52 FF FF FF FF FF DC"
            ),
        )
        validate_phase4_command(command)

    def test_schedule_builder_preserves_its_upstream_reserved_byte_policy(self) -> None:
        period = SchedulePeriod(60, 120, 90, 90, 20, 30, ALL_WEEKDAYS)
        command = build_schedule_period_plan(period)[0]
        # A5/19 uses create_command_encoding's default policy: each 0x5A
        # parameter becomes 0x59. Auto-curve points deliberately differ.
        self.assertEqual(command.packet[10], 89)
        self.assertEqual(command.packet[12], 89)
        self.assertIn("ramp_minutes 90->89", command.logical_name)
        self.assertIn("red 90->89", command.logical_name)

    def test_sea_led_curve_point_is_hour_minute_and_keeps_level_90(self) -> None:
        command = build_curve_plan([(2, 8 * 60 + 30, 90)], sea_led_family=True)[0]
        self.assertEqual(command.packet[0], 0x5A)
        self.assertEqual(command.packet[5], 0x06)
        self.assertEqual(command.packet[6:-1], bytes([2, 8, 30, 0x5A]))
        validate_phase4_command(command)

    def test_new_ble_led_curve_uses_rounded_thirty_minute_slot(self) -> None:
        command = build_curve_plan([(2, 8 * 60 + 15, 90)], sea_led_family=False)[0]
        self.assertEqual(command.packet[6:-1], bytes([2, 17, 0x5A]))
        validate_phase4_command(command)

    def test_weekday_masks_match_upstream(self) -> None:
        self.assertEqual(encode_weekdays(ALL_WEEKDAYS), 127)
        self.assertEqual(
            encode_weekdays((Weekday.MONDAY, Weekday.WEDNESDAY, Weekday.SUNDAY)),
            81,
        )
        self.assertEqual(parse_weekdays("monday,wednesday,sunday"), (
            Weekday.MONDAY,
            Weekday.WEDNESDAY,
            Weekday.SUNDAY,
        ))

    def test_time_validation(self) -> None:
        self.assertEqual(parse_time("00:00"), 0)
        self.assertEqual(parse_time("23:59"), 1439)
        for value in ("24:00", "12:60", "9:00", "noon"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_time(value)
        with self.assertRaisesRegex(ValueError, "before end"):
            SchedulePeriod(600, 600, 0, 10, 20, 30, ALL_WEEKDAYS)

    def test_schedule_rgb_and_ramp_validation(self) -> None:
        for values in ((-1, 20, 30), (10, 101, 30), (10, 20, 101)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                SchedulePeriod(60, 120, 0, *values, ALL_WEEKDAYS)
        for ramp in (-1, 256, True):
            with self.subTest(ramp=ramp), self.assertRaises(ValueError):
                SchedulePeriod(60, 120, ramp, 10, 20, 30, ALL_WEEKDAYS)  # type: ignore[arg-type]

    def test_rtc_encoding_and_exact_packet(self) -> None:
        timestamp = datetime(2026, 9, 2, 12, 34, 56)
        self.assertEqual(encode_rtc(timestamp), [26, 9, 3, 12, 34, 56])
        command = build_clock_sync_plan(timestamp)[0]
        self.assertEqual(command.packet, bytes.fromhex("5A 01 0B 00 02 09 1A 09 03 0C 22 38 07"))
        validate_phase4_command(command)

    def test_schedule_clear_reset_frame_remains_prohibited(self) -> None:
        from chihiros.protocol import create_command_encoding

        packet = create_command_encoding(0x5A, 0x05, (0, 2), [0x05, 0xFF, 0xFF])
        command = Command("prohibited auto-settings clear", (0, 2), packet, True)
        with self.assertRaises(TransportSafetyError):
            validate_phase4_command(command)


if __name__ == "__main__":
    unittest.main()
