"""Original Magnetic Light manual RG packets; no identity or Bluetooth access."""

from .protocol import (
    Command, RESERVED_BYTE, calculate_checksum, create_command_encoding,
    create_manual_mode, next_message_id, validate_level,
)
from .transport import TransportSafetyError


def validate_rg_levels(red: int, green: int) -> tuple[int, int]:
    """Accept only normal-range integer levels; overclock values are unsupported."""
    return validate_level(red), validate_level(green)


def _channel_packet(message_id: tuple[int, int], channel: int, level: int) -> bytes:
    if channel not in (0, 1):
        raise TransportSafetyError("Magnetic Light permits only Red channel 0 and Green channel 1")
    # Preserve literal level 90 (0x5A), as physically validated direct levels are
    # not the legacy Vivid II percentage-substitution convention.
    while True:
        packet = create_command_encoding(
            0x5A, 0x07, message_id, [channel, level], avoid_reserved_byte=False
        )
        if packet[-1] != RESERVED_BYTE:
            return packet
        message_id = next_message_id(message_id)


def _manual_commands(levels: tuple[int, int]) -> tuple[Command, ...]:
    current = next_message_id((0, 1))  # Fresh-session first wire ID: 00 02.
    packet = create_manual_mode(current)
    commands = [Command("select manual mode", (packet[3], packet[4]), packet, True)]
    for channel, (color, level) in enumerate(zip(("Red", "Green"), levels)):
        current = next_message_id(commands[-1].message_id)
        packet = _channel_packet(current, channel, level)
        commands.append(
            Command(
                f"set {color} channel {channel} to {level}",
                (packet[3], packet[4]),
                packet,
                True,
            )
        )
    return tuple(commands)


def validate_magnetic1_manual_plan(
    commands: tuple[Command, ...], levels: tuple[int, int]
) -> None:
    expected = _manual_commands(validate_rg_levels(*levels))
    if commands != expected:
        raise TransportSafetyError(
            "Only manual mode followed by the requested complete Magnetic Light RG state is permitted"
        )
    if len(commands) != 3 or tuple(command.packet[6] for command in commands[1:]) != (0, 1):
        raise TransportSafetyError("Magnetic Light must write exactly channels 0 and 1")
    for command in commands:
        packet = command.packet
        if packet[2] != len(packet) - 2 or calculate_checksum(packet[:-1]) != packet[-1]:
            raise TransportSafetyError("Invalid Magnetic Light frame length/checksum")


def build_magnetic1_manual_plan(red: int, green: int) -> tuple[Command, ...]:
    levels = validate_rg_levels(red, green)
    commands = _manual_commands(levels)
    validate_magnetic1_manual_plan(commands, levels)
    return commands
