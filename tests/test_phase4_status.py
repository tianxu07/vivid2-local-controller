from __future__ import annotations

import unittest

from chihiros.status import (
    RuntimeNotification,
    ScheduleSnapshotNotification,
    UnknownNotification,
    decode_notification,
    format_status_report,
)


RUNTIME = bytes.fromhex("5B 17 0A 00 01 0A 01 FF FF FF FF 13 88 8C")
SCHEDULE_1 = bytes.fromhex(
    "5B 17 30 00 01 FE 03 0D 06 00 00 00 00 00 00 00 00 00 00 00 00 00 03 0D 06 "
    "0F 1E 00 10 00 52 16 00 52 16 1E 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
)
SCHEDULE_2 = bytes.fromhex(
    "5B 17 30 00 01 FE 03 0D 07 0D 06 00 00 00 00 0D 07 00 00 00 00 00 03 0D 06 "
    "0F 1E 00 10 00 52 16 00 52 16 1E 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
)


class Phase4StatusTests(unittest.TestCase):
    def test_runtime_fixture(self) -> None:
        decoded = decode_notification(RUNTIME)
        self.assertIsInstance(decoded, RuntimeNotification)
        assert isinstance(decoded, RuntimeNotification)
        self.assertEqual(decoded.firmware_protocol_version, 0x17)
        self.assertEqual(decoded.runtime_minutes, 511)
        self.assertTrue(decoded.checksum_valid)
        self.assertEqual(decoded.unknown_header, bytes.fromhex("0A 00 01"))
        self.assertEqual(decoded.unknown_payload, bytes.fromhex("FF FF FF 13 88"))

    def test_both_schedule_fixtures_decode_only_known_curve(self) -> None:
        expected = [(15, 30, 0), (16, 0, 82), (22, 0, 82), (22, 30, 0)]
        decoded_items = []
        for fixture in (SCHEDULE_1, SCHEDULE_2):
            decoded = decode_notification(fixture)
            self.assertIsInstance(decoded, ScheduleSnapshotNotification)
            assert isinstance(decoded, ScheduleSnapshotNotification)
            self.assertEqual([(point.hour, point.minute, point.level) for point in decoded.points], expected)
            decoded_items.append(decoded)
        self.assertNotEqual(decoded_items[0].unknown_prefix, decoded_items[1].unknown_prefix)

    def test_report_labels_mode_and_unassigned_fields_conservatively(self) -> None:
        notifications = [decode_notification(RUNTIME), decode_notification(SCHEDULE_2)]
        report = format_status_report("RGB Vivid II", notifications, verbose=True)
        self.assertIn("Mode: Unknown", report)
        self.assertIn("Runtime: 511 minutes (8h 31m)", report)
        self.assertIn("15:30 -> 0%", report)
        self.assertIn("16:00 -> 82%", report)
        self.assertIn("22:00 -> 82%", report)
        self.assertIn("22:30 -> 0%", report)
        self.assertIn("Unknown/unassigned bytes:", report)
        self.assertIn(RUNTIME.hex(" ").upper(), report)

    def test_unknown_payload_stays_unknown(self) -> None:
        decoded = decode_notification(bytes.fromhex("5B 17 01 00 01 99 00"))
        self.assertIsInstance(decoded, UnknownNotification)


if __name__ == "__main__":
    unittest.main()
