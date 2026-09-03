"""Source-derived Chihiros framing and the deliberately small Phase 4 command set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


RESERVED_BYTE = 0x5A
RESERVED_MESSAGE_ID_BYTES = (RESERVED_BYTE,)


def next_message_id(current_msg_id: tuple[int, int] = (0, 0)) -> tuple[int, int]:
    """Mirror upstream two-byte ID increment, wrap, and reserved-ID skipping."""
    high, low = current_msg_id
    while True:
        if high == 0xFF and low == 0xFF:
            high, low = 0, 1
        elif low == 0xFF:
            high = (high + 1) % 256
            low = 0
        else:
            low += 1
        if high not in RESERVED_MESSAGE_ID_BYTES and low not in RESERVED_MESSAGE_ID_BYTES:
            return high, low


def calculate_checksum(input_bytes: bytes | bytearray) -> int:
    """XOR bytes 1 through the final payload byte, excluding family byte 0."""
    if len(input_bytes) < 7:
        raise ValueError("Commands must contain at least 7 bytes")
    checksum = input_bytes[1]
    for value in input_bytes[2:]:
        checksum ^= value
    return checksum


def create_command_encoding(
    command_family: int,
    command_mode: int,
    message_id: tuple[int, int],
    parameters: list[int],
    *,
    avoid_reserved_byte: bool = True,
) -> bytes:
    """Mirror upstream framing while preserving each builder's explicit policy."""
    safe_id = message_id
    if avoid_reserved_byte and (
        safe_id[0] in RESERVED_MESSAGE_ID_BYTES or safe_id[1] in RESERVED_MESSAGE_ID_BYTES
    ):
        safe_id = next_message_id(safe_id)
    safe_parameters = [
        value if not avoid_reserved_byte or value != RESERVED_BYTE else RESERVED_BYTE - 1
        for value in parameters
    ]
    command = bytes(
        [
            command_family,
            0x01,
            len(safe_parameters) + 5,
            safe_id[0],
            safe_id[1],
            command_mode,
            *safe_parameters,
        ]
    )
    checksum = calculate_checksum(command)
    if avoid_reserved_byte and checksum == RESERVED_BYTE:
        return create_command_encoding(
            command_family,
            command_mode,
            next_message_id(safe_id),
            safe_parameters,
            avoid_reserved_byte=avoid_reserved_byte,
        )
    return command + bytes([checksum])


def validate_level(value: int) -> int:
    """Validate an RGB percentage without accepting bool as integer input."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError("RGB values must be integers from 0 through 100")
    return value


def create_status_query(message_id: tuple[int, int]) -> bytes:
    """Upstream 0x5A/0x04 [0x01], using this builder's default byte policy."""
    return create_command_encoding(0x5A, 0x04, message_id, [0x01])


def create_manual_mode(message_id: tuple[int, int]) -> bytes:
    """Upstream 0x5A/0x05 [0x0B, 0xFF, 0xFF]."""
    return create_command_encoding(0x5A, 0x05, message_id, [0x0B, 0xFF, 0xFF])


def create_brightness(message_id: tuple[int, int], channel: int, level: int) -> bytes:
    """Upstream 0x5A/0x07 [channel, level] for Vivid II channels 0..2."""
    if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 2:
        raise ValueError("RGB Vivid II channel must be 0, 1, or 2")
    return create_command_encoding(0x5A, 0x07, message_id, [channel, validate_level(level)])


def create_auto_mode(message_id: tuple[int, int]) -> bytes:
    """Upstream 0x5A/0x05 [0x12, 0xFF, 0xFF]; no schedule write."""
    return create_command_encoding(0x5A, 0x05, message_id, [0x12, 0xFF, 0xFF])


@dataclass(frozen=True)
class Command:
    logical_name: str
    message_id: tuple[int, int]
    packet: bytes
    changes_state: bool

    @property
    def packet_hex(self) -> str:
        return self.packet.hex(" ").upper()


class MessageIdSequence:
    """Allocate IDs exactly like a fresh upstream ChihirosDevice."""

    def __init__(self) -> None:
        self._current = next_message_id()  # stored 00 01; first command is 00 02

    @property
    def current(self) -> tuple[int, int]:
        return self._current

    def build(
        self,
        logical_name: str,
        builder: Callable[[tuple[int, int]], bytes],
        *,
        changes_state: bool,
    ) -> Command:
        self._current = next_message_id(self._current)
        packet = builder(self._current)
        # A builder with reserved-byte avoidance may rebuild with a later wire
        # ID if its checksum is 0x5A. Keep upstream's stored-ID progression,
        # while reporting the ID actually encoded in the packet.
        wire_message_id = (packet[3], packet[4])
        return Command(logical_name, wire_message_id, packet, changes_state)
