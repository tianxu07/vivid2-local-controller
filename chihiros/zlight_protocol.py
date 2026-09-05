"""Z Light manual white-channel packets; no identity or Bluetooth access."""

from .protocol import (
    Command, RESERVED_BYTE, calculate_checksum, create_command_encoding,
    create_manual_mode, next_message_id, validate_level,
)
from .transport import TransportSafetyError


Z_LIGHT_CHANNELS = {"cool_white": 0, "warm_white": 1}


def validate_white_levels(cool_white: int, warm_white: int) -> tuple[int, int]:
    """Accept only normal-range integer levels; overclock values are unsupported."""
    return validate_level(cool_white), validate_level(warm_white)


def _channel_packet(message_id: tuple[int, int], channel: int, level: int) -> bytes:
    if channel not in Z_LIGHT_CHANNELS.values():
        raise TransportSafetyError("Z Light permits only Cool White channel 0 and Warm White channel 1")
    while True:
        packet = create_command_encoding(
            0x5A, 0x07, message_id, [channel, level], avoid_reserved_byte=False
        )
        if packet[-1] != RESERVED_BYTE:
            return packet
        message_id = next_message_id(message_id)


def _manual_commands(levels: tuple[int, int]) -> tuple[Command, ...]:
    current = next_message_id((0, 1))
    packet = create_manual_mode(current)
    commands = [Command("select manual mode", (packet[3], packet[4]), packet, True)]
    for channel, (label, level) in enumerate(zip(("Cool White", "Warm White"), levels)):
        current = next_message_id(commands[-1].message_id)
        packet = _channel_packet(current, channel, level)
        commands.append(Command(
            f"set {label} channel {channel} to {level}",
            (packet[3], packet[4]), packet, True,
        ))
    return tuple(commands)


def validate_zlight_manual_plan(
    commands: tuple[Command, ...], levels: tuple[int, int]
) -> None:
    expected = _manual_commands(validate_white_levels(*levels))
    if commands != expected:
        raise TransportSafetyError(
            "Only manual mode followed by the requested complete Z Light white state is permitted"
        )
    channels = tuple(command.packet[6] for command in commands[1:])
    if len(commands) != 3 or channels != (0, 1):
        raise TransportSafetyError("Z Light must write exactly channels 0 and 1")
    for command in commands:
        packet = command.packet
        if packet[2] != len(packet) - 2 or calculate_checksum(packet[:-1]) != packet[-1]:
            raise TransportSafetyError("Invalid Z Light frame length/checksum")


def build_zlight_manual_plan(cool_white: int, warm_white: int) -> tuple[Command, ...]:
    levels = validate_white_levels(cool_white, warm_white)
    commands = _manual_commands(levels)
    validate_zlight_manual_plan(commands, levels)
    return commands
