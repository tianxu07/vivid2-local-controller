"""Source-derived RGB Vivid II schedule and RTC data models.

This module only builds typed high-level operations. It intentionally exposes
no arbitrary family/mode or raw-packet interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Sequence

from .protocol import Command, MessageIdSequence, create_command_encoding, validate_level


class Weekday(str, Enum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


WEEKDAY_BITS = {
    Weekday.MONDAY: 64,
    Weekday.TUESDAY: 32,
    Weekday.WEDNESDAY: 16,
    Weekday.THURSDAY: 8,
    Weekday.FRIDAY: 4,
    Weekday.SATURDAY: 2,
    Weekday.SUNDAY: 1,
}
ALL_WEEKDAYS = tuple(Weekday)
MAX_RAMP_MINUTES = 255
MAX_CURVE_MINUTES = 2880


def parse_time(value: str) -> int:
    """Parse strict 24-hour HH:MM into minutes since midnight."""
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError(f"Invalid time {value!r}; expected HH:MM")
    try:
        hour = int(value[:2])
        minute = int(value[3:])
    except ValueError as exc:
        raise ValueError(f"Invalid time {value!r}; expected HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Invalid time {value!r}; hour must be 0-23 and minute 0-59")
    return hour * 60 + minute


def format_time(minutes: int) -> str:
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def parse_weekdays(value: str | Iterable[str]) -> tuple[Weekday, ...]:
    """Parse `all` or a comma-separated/iterable weekday selection."""
    values = [value] if isinstance(value, str) else list(value)
    tokens = [token.strip().lower() for item in values for token in item.split(",") if token.strip()]
    if not tokens:
        raise ValueError("At least one weekday is required")
    if "all" in tokens or "everyday" in tokens:
        if len(tokens) != 1:
            raise ValueError("'all' cannot be combined with individual weekdays")
        return ALL_WEEKDAYS
    try:
        selected = tuple(Weekday(token) for token in tokens)
    except ValueError as exc:
        valid = ", ".join(day.value for day in ALL_WEEKDAYS)
        raise ValueError(f"Unknown weekday; use all or: {valid}") from exc
    if len(set(selected)) != len(selected):
        raise ValueError("Weekdays must not be repeated")
    return tuple(day for day in ALL_WEEKDAYS if day in selected)


def encode_weekdays(days: Sequence[Weekday]) -> int:
    if not days:
        raise ValueError("At least one weekday is required")
    if len(set(days)) != len(days) or any(day not in WEEKDAY_BITS for day in days):
        raise ValueError("Weekday selection is invalid")
    return sum(WEEKDAY_BITS[day] for day in days)


@dataclass(frozen=True)
class SchedulePeriod:
    start_minutes: int
    end_minutes: int
    ramp_minutes: int
    red: int
    green: int
    blue: int
    days: tuple[Weekday, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.start_minutes < 24 * 60 or not 0 <= self.end_minutes < 24 * 60:
            raise ValueError("Schedule start/end must be within one day")
        if self.start_minutes >= self.end_minutes:
            raise ValueError("Schedule start time must be before end time")
        if isinstance(self.ramp_minutes, bool) or not isinstance(self.ramp_minutes, int):
            raise ValueError("Ramp duration must be an integer")
        if not 0 <= self.ramp_minutes <= MAX_RAMP_MINUTES:
            raise ValueError(f"Ramp duration must be 0-{MAX_RAMP_MINUTES} minutes")
        validate_level(self.red)
        validate_level(self.green)
        validate_level(self.blue)
        encode_weekdays(self.days)

    @property
    def start(self) -> str:
        return format_time(self.start_minutes)

    @property
    def end(self) -> str:
        return format_time(self.end_minutes)

    @property
    def weekday_mask(self) -> int:
        return encode_weekdays(self.days)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "a5_19_period",
            "start": self.start,
            "end": self.end,
            "ramp_minutes": self.ramp_minutes,
            "red": self.red,
            "green": self.green,
            "blue": self.blue,
            "days": [day.value for day in self.days],
            "weekday_mask": self.weekday_mask,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SchedulePeriod":
        if value.get("kind") != "a5_19_period":
            raise ValueError("Backup does not contain an A5/19 schedule period")
        return cls(
            parse_time(value["start"]),
            parse_time(value["end"]),
            int(value["ramp_minutes"]),
            int(value["red"]),
            int(value["green"]),
            int(value["blue"]),
            parse_weekdays(value["days"]),
        )


def create_schedule_period_command(message_id: tuple[int, int], period: SchedulePeriod) -> bytes:
    """Build upstream A5/19 with its default reserved-byte policy."""
    start_hour, start_minute = divmod(period.start_minutes, 60)
    end_hour, end_minute = divmod(period.end_minutes, 60)
    parameters = [
        start_hour,
        start_minute,
        end_hour,
        end_minute,
        period.ramp_minutes,
        period.weekday_mask,
        period.red,
        period.green,
        period.blue,
        0xFF,
        0xFF,
        0xFF,
        0xFF,
        0xFF,
    ]
    return create_command_encoding(0xA5, 0x19, message_id, parameters)


def build_schedule_period_plan(period: SchedulePeriod) -> tuple[Command, ...]:
    sequence = MessageIdSequence()
    expected = (
        *divmod(period.start_minutes, 60),
        *divmod(period.end_minutes, 60),
        period.ramp_minutes,
        period.weekday_mask,
        period.red,
        period.green,
        period.blue,
        *([0xFF] * 5),
    )
    command = sequence.build(
        f"Add/update A5/19 period {period.start}-{period.end}, mask 0x{period.weekday_mask:02X}",
        lambda message_id: create_schedule_period_command(message_id, period),
        changes_state=True,
    )
    wire_parameters = command.packet[6:-1]
    labels = (
        "start_hour",
        "start_minute",
        "end_hour",
        "end_minute",
        "ramp_minutes",
        "weekday_mask",
        "red",
        "green",
        "blue",
        "padding_1",
        "padding_2",
        "padding_3",
        "padding_4",
        "padding_5",
    )
    substitutions = [
        f"{label} {source}->{wire}"
        for label, source, wire in zip(labels, expected, wire_parameters, strict=True)
        if source != wire
    ]
    if substitutions:
        command = Command(
            f"{command.logical_name} (upstream reserved-byte substitutions: {', '.join(substitutions)})",
            command.message_id,
            command.packet,
            command.changes_state,
        )
    return (command,)


def create_auto_curve_point_command(
    message_id: tuple[int, int],
    channel: int,
    minutes: int,
    level: int,
    *,
    sea_led_family: bool,
) -> bytes:
    """Build one family-specific 5A/06 point; payload/checksum stay verbatim."""
    if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 2:
        raise ValueError("RGB Vivid II curve channel must be 0, 1, or 2")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= MAX_CURVE_MINUTES:
        raise ValueError(f"Curve minutes must be 0-{MAX_CURVE_MINUTES}")
    level = validate_level(level)
    if sea_led_family:
        hour, minute = divmod(minutes, 60)
        parameters = [channel, hour, minute, level]
    else:
        time_index, remainder = divmod(minutes, 30)
        if remainder > 14:
            time_index += 1
        parameters = [channel, time_index, level]
    return create_command_encoding(
        0x5A,
        0x06,
        message_id,
        parameters,
        avoid_reserved_byte=False,
    )


def build_curve_plan(
    points: Sequence[tuple[int, int, int]], *, sea_led_family: bool
) -> tuple[Command, ...]:
    if not points:
        raise ValueError("Curve must contain at least one point")
    sequence = MessageIdSequence()
    commands: list[Command] = []
    for channel, minutes, level in points:
        family = "SeaLed" if sea_led_family else "NewBleLed"
        commands.append(
            sequence.build(
                f"{family} curve channel {channel} at {format_time(minutes)} -> {level}%",
                lambda message_id, channel=channel, minutes=minutes, level=level: (
                    create_auto_curve_point_command(
                        message_id,
                        channel,
                        minutes,
                        level,
                        sea_led_family=sea_led_family,
                    )
                ),
                changes_state=True,
            )
        )
    return tuple(commands)


def encode_rtc(timestamp: datetime) -> list[int]:
    if not 2000 <= timestamp.year <= 2255:
        raise ValueError("RTC year must be 2000-2255")
    return [
        timestamp.year - 2000,
        timestamp.month,
        timestamp.isoweekday(),
        timestamp.hour,
        timestamp.minute,
        timestamp.second,
    ]


def create_clock_sync_command(message_id: tuple[int, int], timestamp: datetime) -> bytes:
    return create_command_encoding(0x5A, 0x09, message_id, encode_rtc(timestamp))


def build_clock_sync_plan(timestamp: datetime) -> tuple[Command, ...]:
    sequence = MessageIdSequence()
    return (
        sequence.build(
            f"Synchronize RTC to local time {timestamp.isoformat(timespec='seconds')}",
            lambda message_id: create_clock_sync_command(message_id, timestamp),
            changes_state=True,
        ),
    )


__all__ = [
    "ALL_WEEKDAYS",
    "MAX_CURVE_MINUTES",
    "MAX_RAMP_MINUTES",
    "SchedulePeriod",
    "Weekday",
    "build_clock_sync_plan",
    "build_curve_plan",
    "build_schedule_period_plan",
    "create_clock_sync_command",
    "create_schedule_period_command",
    "create_auto_curve_point_command",
    "encode_rtc",
    "encode_weekdays",
    "parse_time",
    "parse_weekdays",
]
