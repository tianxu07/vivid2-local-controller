"""Typed transactions derived from pinned upstream commands.py/client.py.

No raw-command or schedule interface. Requested 90 is encoded as payload 89
(0x59) for upstream compatibility, not a claim of measured device output.
This policy never affects the locked local controllers.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .protocol import Command, MessageIdSequence, create_command_encoding, validate_level
from .schedule import encode_rtc
from .upstream_profiles import Profile, PROFILES


class Action(str, Enum):
    LIGHT = "light"
    FAN_MANUAL = "fan_manual"
    FAN_AUTO = "fan_auto"
    FAN_THRESHOLDS = "fan_thresholds"
    TELEMETRY = "telemetry"


@dataclass(frozen=True)
class Request:
    action: Action
    values: tuple[int, ...] = ()


def validate_request(profile: Profile, request: Request) -> None:
    if not any(profile is item for item in PROFILES):
        raise ValueError("An authorized registry profile is required")
    if not isinstance(request, Request) or not isinstance(request.action, Action):
        raise ValueError("A typed upstream action is required")
    if not isinstance(request.values, tuple):
        raise ValueError("Request values must be an immutable tuple")
    for value in request.values:
        validate_level(value)
    if request.action is Action.LIGHT:
        count = len(profile.controls)
    else:
        if not profile.vivid3:
            raise ValueError("Integrated fan and telemetry require VIVID III")
        count = {Action.FAN_MANUAL: 1, Action.FAN_AUTO: 0,
                 Action.FAN_THRESHOLDS: 2, Action.TELEMETRY: 0}[request.action]
    if len(request.values) != count:
        raise ValueError(f"{request.action.value} requires exactly {count} values")
    if request.action is Action.FAN_THRESHOLDS:
        start, stop = request.values
        if not (15 <= stop <= 60 and 15 <= start <= 60 and start - stop >= 2):
            raise ValueError("Thresholds require 15–60 °C and start at least 2 °C above stop")


def fan_wire(value: int) -> int:
    value = validate_level(value)
    clamped = max(25, value) if value else 0
    return 89 if clamped == 90 else clamped


def build_plan(profile: Profile, request: Request, timestamp: datetime) -> tuple[Command, ...]:
    validate_request(profile, request)
    if not isinstance(timestamp, datetime):
        raise ValueError("A valid clock timestamp is required")
    if request.action is Action.TELEMETRY:
        # Passive refresh never sets the RTC or sends an auth/status query.
        return ()
    sequence = MessageIdSequence()

    def command(label, family, mode, values, *, preserve=False):
        return sequence.build(label, lambda mid: create_command_encoding(
            family, mode, mid, list(values), avoid_reserved_byte=not preserve),
            changes_state=mode != 4)

    # Upstream allocates action IDs before it allocates the connection prelude.
    if request.action is Action.LIGHT:
        actions = [command("manual light mode", 0x5A, 5, (11, 255, 255))]
        actions.extend(command(f"{label} requested {value}", 0x5A, 7, (index, value))
                       for index, (label, value) in enumerate(zip(profile.controls, request.values)))
    elif request.action is Action.FAN_MANUAL:
        value = request.values[0]
        actions = [command("manual integrated fan", 0x5A, 15, (max(25, value) if value else 0,))]
    elif request.action is Action.FAN_AUTO:
        actions = [command("automatic integrated fan", 0x5A, 5, (17, 255, 255))]
    else:
        actions = [command("integrated fan thresholds", 0xA5, 45, request.values, preserve=True)]
    rtc = encode_rtc(timestamp)
    prelude = [command("auth/status", 0x5A, 4, (1,)),
               command("time first pass", 0x5A, 9, rtc),
               command("time second pass", 0x5A, 9, rtc)]
    return tuple(prelude + actions)


@dataclass(frozen=True)
class Telemetry:
    rpm: int
    temperature_c: int
    source: str


def parse_telemetry(data: bytes) -> Telemetry | None:
    if len(data) < 9 or (data[0], data[5]) not in ((0x5B, 0x0B), (0xB6, 0x16)):
        return None
    # Neither known form has a confirmed XOR trailer. No inferred switch state.
    return Telemetry((data[6] << 8) | data[7], data[8], f"{data[0]:02X}/{data[5]:02X}")
