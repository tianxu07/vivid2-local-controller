"""Shared A2 Max manual-brightness packets; no device identity or live BLE."""

from .protocol import (
    Command, MessageIdSequence, RESERVED_BYTE, calculate_checksum,
    create_command_encoding, create_manual_mode, next_message_id,
)
from .transport import TransportSafetyError


def validate_a2max_level(level: int) -> int:
    """Accept integer normalized wire levels 1..100, never bool or coercion."""
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 100:
        raise ValueError("A2 Max normalized wire level must be an integer from 1 through 100")
    return level


def _create_a2max_wire_brightness(message_id: tuple[int, int], level: int) -> bytes:
    """Preserve the literal channel-0 wire level, including 90 (0x5A).

    Unlike the legacy Vivid II percentage builder, this wire-level API must not
    silently substitute 89 for 90. ID/checksum avoidance is retained separately;
    rebuilding here is offline packet generation, not a BLE retry. Literal 90
    remains hardware-unverified on this unit and is disclosed in the preview.
    """
    level = validate_a2max_level(level)
    safe_id = message_id
    if RESERVED_BYTE in safe_id:
        safe_id = next_message_id(safe_id)
    while True:
        packet = create_command_encoding(
            0x5A, 0x07, safe_id, [0x00, level], avoid_reserved_byte=False
        )
        if packet[-1] != RESERVED_BYTE:
            return packet
        safe_id = next_message_id(safe_id)


def _a2max_manual_commands(level: int) -> tuple[Command, Command]:
    """Allocate a fresh upstream-compatible sequence for this transaction."""
    level = validate_a2max_level(level)
    sequence = MessageIdSequence()
    return (
        sequence.build(
            "select manual mode without modifying stored schedule data",
            create_manual_mode,
            changes_state=True,
        ),
        sequence.build(
            f"set white channel 0 to normalized wire level {level}",
            lambda message_id: _create_a2max_wire_brightness(message_id, level),
            changes_state=True,
        ),
    )


def build_a2max_manual_plan(level: int) -> tuple[Command, Command]:
    """Build manual mode then white-channel brightness; no prelude or restore."""
    plan = _a2max_manual_commands(level)
    validate_a2max_manual_plan(plan, level)
    return plan


def validate_a2max_manual_plan(commands: tuple[Command, ...], level: int) -> None:
    """Require the two ordered packets, requested level, fresh IDs and checksum."""
    expected = _a2max_manual_commands(level)
    if len(commands) != len(expected):
        raise TransportSafetyError("A2 Max manual brightness requires exactly two commands")
    for command, expected_command in zip(commands, expected):
        packet = command.packet
        if packet != expected_command.packet or command.message_id != expected_command.message_id:
            raise TransportSafetyError("Command is outside the A2 Max manual-brightness plan")
        if command.changes_state is not True:
            raise TransportSafetyError("Manual-brightness commands must be marked state-changing")
        if calculate_checksum(packet[:-1]) != packet[-1]:
            raise TransportSafetyError("A2 Max manual-brightness checksum is invalid")
