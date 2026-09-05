"""Cooling Fan packet plans built from the shared Chihiros frame encoder."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .constants import FAN_MANUAL_SPEED_MAX, FAN_MANUAL_SPEED_MIN
from .protocol import (
    Command,
    RESERVED_BYTE,
    create_command_encoding,
    create_status_query,
    next_message_id,
)
from .schedule import create_clock_sync_command
from .transport import TransportSafetyError


def validate_fan_speed(speed: int) -> int:
    if (
        isinstance(speed, bool)
        or not isinstance(speed, int)
        or not FAN_MANUAL_SPEED_MIN <= speed <= FAN_MANUAL_SPEED_MAX
    ):
        raise ValueError(
            f"Fan speed must be an integer from {FAN_MANUAL_SPEED_MIN} "
            f"through {FAN_MANUAL_SPEED_MAX}"
        )
    return speed


def validate_fan_temperatures(
    start_temperature: int, max_temperature: int
) -> tuple[int, int]:
    for label, value in (
        ("Start Temperature", start_temperature),
        ("Max-Speed Temperature", max_temperature),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
            raise ValueError(f"{label} must be an integer representable by one protocol byte")
    if max_temperature <= start_temperature:
        raise ValueError("Max-Speed Temperature must be greater than Start Temperature")
    return start_temperature, max_temperature


def _exact_packet(
    message_id: tuple[int, int], command_family: int, command_mode: int, parameters: list[int]
) -> bytes:
    """Preserve protocol data bytes while retaining safe message IDs/checksums."""
    safe_id = message_id
    if RESERVED_BYTE in safe_id:
        safe_id = next_message_id(safe_id)
    while True:
        packet = create_command_encoding(
            command_family,
            command_mode,
            safe_id,
            parameters,
            avoid_reserved_byte=False,
        )
        if packet[-1] != RESERVED_BYTE:
            return packet
        safe_id = next_message_id(safe_id)


def create_fan_speed(message_id: tuple[int, int], speed: int) -> bytes:
    return _exact_packet(message_id, 0x5A, 0x07, [0xFF, validate_fan_speed(speed)])


def create_device_auth(message_id: tuple[int, int], extension: int) -> bytes:
    if extension not in (0x06, 0x08):
        raise ValueError("Cooling Fan extended authentication must be 0x06 or 0x08")
    return _exact_packet(message_id, 0xA5, 0x04, [extension])


def create_fan_temperature_thresholds(
    message_id: tuple[int, int], start_temperature: int, max_temperature: int
) -> bytes:
    start_temperature, max_temperature = validate_fan_temperatures(
        start_temperature, max_temperature
    )
    return _exact_packet(
        message_id, 0xA5, 0x21, [start_temperature, max_temperature, 0xFF]
    )


def create_fan_mode(message_id: tuple[int, int], mode: int) -> bytes:
    if mode not in (0x22, 0x23):
        raise ValueError("Cooling Fan automatic mode byte must be 0x22 or 0x23")
    return _exact_packet(message_id, 0x5A, 0x05, [mode, 0xFF, 0xFF])


def _append_command(
    commands: list[Command],
    current_id: tuple[int, int],
    logical_name: str,
    builder: Callable[[tuple[int, int]], bytes],
    *,
    changes_state: bool,
) -> tuple[int, int]:
    packet = builder(next_message_id(current_id))
    wire_id = (packet[3], packet[4])
    commands.append(Command(logical_name, wire_id, packet, changes_state))
    return wire_id


def build_fan_status_plan() -> tuple[Command, ...]:
    commands: list[Command] = []
    _append_command(
        commands,
        next_message_id(),
        "Refresh Cooling Fan telemetry",
        create_status_query,
        changes_state=False,
    )
    return tuple(commands)


def _manual_commands(speed: int) -> tuple[Command, ...]:
    speed = validate_fan_speed(speed)
    commands: list[Command] = []
    _append_command(
        commands,
        next_message_id(),
        f"Set manual fan speed to device level {speed}",
        lambda message_id: create_fan_speed(message_id, speed),
        changes_state=True,
    )
    return tuple(commands)


def validate_fan_manual_plan(commands: tuple[Command, ...], speed: int) -> None:
    expected = _manual_commands(validate_fan_speed(speed))
    if commands != expected or len(commands) != 1:
        raise TransportSafetyError("Manual Cooling Fan control permits exactly one FAN_SPEED command")


def build_fan_manual_plan(speed: int) -> tuple[Command, ...]:
    commands = _manual_commands(speed)
    validate_fan_manual_plan(commands, speed)
    return commands


def _automatic_commands(
    start_temperature: int, max_temperature: int, timestamp: datetime
) -> tuple[Command, ...]:
    start_temperature, max_temperature = validate_fan_temperatures(
        start_temperature, max_temperature
    )
    if not isinstance(timestamp, datetime):
        raise ValueError("Cooling Fan RTC timestamp must be a datetime")

    commands: list[Command] = []
    current_id = next_message_id()

    def add(logical_name: str, builder: Callable[[tuple[int, int]], bytes]) -> None:
        nonlocal current_id
        current_id = _append_command(
            commands, current_id, logical_name, builder, changes_state=True
        )

    add("BASE AUTH", create_status_query)
    add("RTC", lambda message_id: create_clock_sync_command(message_id, timestamp))
    add("RTC second write", lambda message_id: create_clock_sync_command(message_id, timestamp))
    add("DEVICE AUTH_EXT1", lambda message_id: create_device_auth(message_id, 0x06))
    add("DEVICE AUTH_EXT2", lambda message_id: create_device_auth(message_id, 0x08))
    add(
        f"DEVICE TEMP_THRESH {start_temperature}C to {max_temperature}C",
        lambda message_id: create_fan_temperature_thresholds(
            message_id, start_temperature, max_temperature
        ),
    )
    add("BASE MODE 0x23", lambda message_id: create_fan_mode(message_id, 0x23))
    add("BASE MODE 0x22", lambda message_id: create_fan_mode(message_id, 0x22))
    add("DEVICE AUTH_EXT1 final", lambda message_id: create_device_auth(message_id, 0x06))
    add("DEVICE AUTH_EXT2 final", lambda message_id: create_device_auth(message_id, 0x08))
    return tuple(commands)


def validate_fan_automatic_plan(
    commands: tuple[Command, ...],
    start_temperature: int,
    max_temperature: int,
    timestamp: datetime,
) -> None:
    expected = _automatic_commands(start_temperature, max_temperature, timestamp)
    if commands != expected or len(commands) != 10:
        raise TransportSafetyError("Cooling Fan automatic configuration plan is incomplete or reordered")
    if any(command.packet[0] == 0x5A and command.packet[5] == 0x07 for command in commands):
        raise TransportSafetyError("Cooling Fan automatic mode must never send FAN_SPEED")


def build_fan_automatic_plan(
    start_temperature: int, max_temperature: int, timestamp: datetime
) -> tuple[Command, ...]:
    commands = _automatic_commands(start_temperature, max_temperature, timestamp)
    validate_fan_automatic_plan(commands, start_temperature, max_temperature, timestamp)
    return commands


__all__ = [
    "build_fan_automatic_plan",
    "build_fan_manual_plan",
    "build_fan_status_plan",
    "create_device_auth",
    "create_fan_mode",
    "create_fan_speed",
    "create_fan_temperature_thresholds",
    "validate_fan_automatic_plan",
    "validate_fan_manual_plan",
    "validate_fan_speed",
    "validate_fan_temperatures",
]
