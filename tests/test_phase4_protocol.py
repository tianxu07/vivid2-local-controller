from __future__ import annotations

import unittest

from chihiros.protocol import (
    Command,
    calculate_checksum,
    create_command_encoding,
    next_message_id,
    validate_level,
)
from chihiros.transport import TransportSafetyError, validate_phase4_command
from chihiros.vivid2 import build_auto_plan, build_manual_plan, build_off_plan, build_status_plan


class Phase4ProtocolTests(unittest.TestCase):
    def test_message_id_increment_wrap_and_reserved_byte_skip(self) -> None:
        self.assertEqual(next_message_id((0x00, 0x59)), (0x00, 0x5B))
        self.assertEqual(next_message_id((0x59, 0xFF)), (0x5B, 0x00))
        self.assertEqual(next_message_id((0xFF, 0xFF)), (0x00, 0x01))

    def test_status_packet_is_exact(self) -> None:
        command = build_status_plan()[0]
        self.assertEqual(command.message_id, (0, 2))
        self.assertEqual(command.packet, bytes.fromhex("5A 01 06 00 02 04 01 00"))
        self.assertFalse(command.changes_state)

    def test_manual_10_20_30_packets_are_exact(self) -> None:
        commands = build_manual_plan(10, 20, 30)
        self.assertEqual(
            [command.packet.hex(" ").upper() for command in commands],
            [
                "5A 01 08 00 02 05 0B FF FF 05",
                "5A 01 07 00 03 07 00 0A 08",
                "5A 01 07 00 04 07 01 14 10",
                "5A 01 07 00 05 07 02 1E 18",
            ],
        )
        self.assertEqual([item.message_id for item in commands], [(0, 2), (0, 3), (0, 4), (0, 5)])
        self.assertTrue(all(item.changes_state for item in commands))

    def test_auto_packet_is_exact(self) -> None:
        command = build_auto_plan()[0]
        self.assertEqual(command.message_id, (0, 2))
        self.assertEqual(command.packet, bytes.fromhex("5A 01 08 00 02 05 12 FF FF 1C"))

    def test_off_is_manual_plus_three_zero_channels(self) -> None:
        commands = build_off_plan()
        self.assertEqual(
            [command.packet.hex(" ").upper() for command in commands],
            [
                "5A 01 08 00 02 05 0B FF FF 05",
                "5A 01 07 00 03 07 00 00 02",
                "5A 01 07 00 04 07 01 00 04",
                "5A 01 07 00 05 07 02 00 06",
            ],
        )

    def test_every_exposed_packet_has_valid_checksum_and_whitelist(self) -> None:
        commands = (
            *build_status_plan(),
            *build_manual_plan(10, 20, 30),
            *build_auto_plan(),
            *build_off_plan(),
        )
        for command in commands:
            with self.subTest(command=command.logical_name):
                self.assertEqual(calculate_checksum(command.packet[:-1]), command.packet[-1])
                validate_phase4_command(command)

    def test_out_of_range_and_non_integer_levels_are_rejected(self) -> None:
        for value in (-1, 101, True, 1.5, "10"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_level(value)  # type: ignore[arg-type]

    def test_reserved_level_90_preserves_upstream_per_builder_policy(self) -> None:
        red = build_manual_plan(90, 0, 0)[1]
        self.assertEqual(red.packet[7], 89)
        self.assertIn("wire level 89%", red.logical_name)

    def test_explicit_source_derived_rtc_is_now_allowed_at_transport_boundary(self) -> None:
        packet = create_command_encoding(0x5A, 0x09, (0, 2), [26, 9, 3, 12, 0, 0])
        command = Command("forbidden RTC", (packet[3], packet[4]), packet, True)
        validate_phase4_command(command)

    def test_malformed_a5_19_schedule_packet_is_rejected_at_transport_boundary(self) -> None:
        packet = create_command_encoding(0xA5, 0x19, (0, 2), [0, 0, 0])
        command = Command("malformed schedule write", (packet[3], packet[4]), packet, True)
        with self.assertRaises(TransportSafetyError):
            validate_phase4_command(command)


if __name__ == "__main__":
    unittest.main()
