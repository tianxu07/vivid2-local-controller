from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from chihiros.backup import (
    BACKUP_SCHEMA,
    BackupError,
    create_backup_document,
    definition_from_backup,
    load_backup_document,
    save_backup_document,
)
from chihiros.schedule import SchedulePeriod, build_schedule_period_plan, parse_time, parse_weekdays
from chihiros.status import decode_notification
from chihiros.transport import DeviceConfig


RUNTIME = bytes.fromhex("5B 17 0A 00 01 0A 01 FF FF FF FF 13 88 8C")
SCHEDULE = bytes.fromhex(
    "5B 17 30 00 01 FE 03 0D 07 0D 06 00 00 00 00 0D 07 00 00 00 00 00 03 0D 06 "
    "0F 1E 00 10 00 52 16 00 52 16 1E 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
)


class Phase5BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.device = DeviceConfig("vivid2", "RGB Vivid II", "DYNVTEST123", "AA:BB:CC:DD:EE:01")
        self.notifications = [decode_notification(RUNTIME), decode_notification(SCHEDULE)]
        self.created = datetime(2026, 9, 2, 20, 0, 0, tzinfo=timezone.utc)

    def test_backup_preserves_raw_decoded_unknown_and_versions(self) -> None:
        document = create_backup_document(self.device, self.notifications, created_at=self.created)
        self.assertEqual(document["schema"], BACKUP_SCHEMA)
        self.assertEqual(document["device"]["advertised_name"], "DYNVTEST123")
        self.assertEqual(document["raw"]["runtime_status_responses"], [RUNTIME.hex(" ").upper()])
        self.assertEqual(document["raw"]["schedule_snapshot_responses"], [SCHEDULE.hex(" ").upper()])
        decoded_schedule = document["decoded"]["schedule"]
        self.assertIn("unknown_prefix_hex", decoded_schedule)
        self.assertEqual(
            [(point["hour"], point["minute"], point["level"]) for point in decoded_schedule["points"]],
            [(15, 30, 0), (16, 0, 82), (22, 0, 82), (22, 30, 0)],
        )
        self.assertFalse(document["rebuild"]["supported"])
        self.assertIn("Raw packets are never replayed", document["rebuild"]["reason"])

    def test_backup_files_are_append_only(self) -> None:
        document = create_backup_document(self.device, self.notifications, created_at=self.created)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = save_backup_document(root, self.device, document)
            second = save_backup_document(root, self.device, document)
            self.assertNotEqual(first, second)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertEqual(load_backup_document(first)["schema"], BACKUP_SCHEMA)

    def test_raw_snapshot_backup_cannot_be_restored_by_guessing(self) -> None:
        document = create_backup_document(self.device, self.notifications, created_at=self.created)
        with self.assertRaisesRegex(BackupError, "not safely rebuildable"):
            definition_from_backup(document, self.device)

    def test_typed_backup_rebuilds_current_message_ids_and_checksums(self) -> None:
        period = SchedulePeriod(
            parse_time("15:30"), parse_time("22:30"), 30, 82, 82, 82, parse_weekdays("all")
        )
        document = create_backup_document(
            self.device,
            self.notifications,
            created_at=self.created,
            known_definition=period,
        )
        rebuilt = definition_from_backup(document, self.device)
        command = build_schedule_period_plan(rebuilt)[0]
        self.assertEqual(command.message_id, (0, 2))
        self.assertEqual(command.packet[-1], 0xDC)

    def test_restore_rejects_a_different_device(self) -> None:
        period = SchedulePeriod(60, 120, 0, 10, 20, 30, parse_weekdays("all"))
        document = create_backup_document(
            self.device, self.notifications, created_at=self.created, known_definition=period
        )
        other = DeviceConfig("other", "RGB Vivid II", "DYNVOTHER", "AA:BB:CC:DD:EE:02")
        with self.assertRaisesRegex(BackupError, "does not match"):
            definition_from_backup(document, other)

    def test_restore_rejects_a_different_protocol_revision(self) -> None:
        period = SchedulePeriod(60, 120, 0, 10, 20, 30, parse_weekdays("all"))
        document = create_backup_document(
            self.device, self.notifications, created_at=self.created, known_definition=period
        )
        document["protocol_source_commit"] = "different"
        with self.assertRaisesRegex(BackupError, "source commit"):
            definition_from_backup(document, self.device)

    def test_no_snapshot_means_no_backup(self) -> None:
        with self.assertRaisesRegex(BackupError, "No schedule snapshot"):
            create_backup_document(self.device, [decode_notification(RUNTIME)], created_at=self.created)


if __name__ == "__main__":
    unittest.main()
