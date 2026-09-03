"""Conservative, source-backed decoding of Vivid II NUS notifications."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, TypeAlias

from .protocol import calculate_checksum


SCHEDULE_POINT_SIZE = 3
SCHEDULE_POINTS_START = 25


@dataclass(frozen=True)
class SchedulePoint:
    hour: int
    minute: int
    level: int

    @property
    def time_text(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


@dataclass(frozen=True)
class RuntimeNotification:
    firmware_protocol_version: int
    runtime_minutes: int
    checksum_valid: bool | None
    unknown_header: bytes
    unknown_payload: bytes
    raw: bytes


@dataclass(frozen=True)
class ScheduleSnapshotNotification:
    firmware_protocol_version: int
    points: tuple[SchedulePoint, ...]
    unknown_header: bytes
    unknown_prefix: bytes
    unknown_trailer: bytes
    raw: bytes


@dataclass(frozen=True)
class UnknownNotification:
    reason: str
    raw: bytes


ParsedNotification: TypeAlias = (
    RuntimeNotification | ScheduleSnapshotNotification | UnknownNotification
)


def runtime_notification(notifications: list[ParsedNotification]) -> RuntimeNotification | None:
    return next(
        (item for item in reversed(notifications) if isinstance(item, RuntimeNotification)),
        None,
    )


def schedule_notification(notifications: list[ParsedNotification]) -> ScheduleSnapshotNotification | None:
    return next(
        (item for item in reversed(notifications) if isinstance(item, ScheduleSnapshotNotification)),
        None,
    )


def decode_notification(data: bytes | bytearray) -> ParsedNotification:
    """Decode only fields established by upstream and the real captures."""
    raw = bytes(data)
    if len(raw) < 7:
        return UnknownNotification("shorter than the known notification header", raw)
    if raw[0] != 0x5B:
        return UnknownNotification("unrecognized notification family", raw)

    firmware_protocol_version = raw[1]
    mode = raw[5]
    unknown_header = raw[2:5]

    if mode == 0x0A and len(raw) >= 8:
        runtime_minutes = (raw[6] << 8) | raw[7]
        checksum_valid: bool | None = None
        unknown_payload = raw[8:]
        if len(raw) >= 9:
            checksum_valid = calculate_checksum(raw[:-1]) == raw[-1]
            unknown_payload = raw[8:-1]
        return RuntimeNotification(
            firmware_protocol_version,
            runtime_minutes,
            checksum_valid,
            unknown_header,
            unknown_payload,
            raw,
        )

    if mode == 0xFE:
        unknown_prefix = raw[6:min(SCHEDULE_POINTS_START, len(raw))]
        point_region = raw[SCHEDULE_POINTS_START:]
        complete_length = (len(point_region) // SCHEDULE_POINT_SIZE) * SCHEDULE_POINT_SIZE
        complete_region = point_region[:complete_length]
        unknown_trailer = point_region[complete_length:]
        points: list[SchedulePoint] = []
        for offset in range(0, len(complete_region), SCHEDULE_POINT_SIZE):
            hour, minute, level = complete_region[offset : offset + SCHEDULE_POINT_SIZE]
            if hour == 0 and minute == 0 and level == 0:
                continue
            if hour > 23 or minute > 59 or level > 100:
                continue
            points.append(SchedulePoint(hour, minute, level))
        return ScheduleSnapshotNotification(
            firmware_protocol_version,
            tuple(points),
            unknown_header,
            unknown_prefix,
            unknown_trailer,
            raw,
        )

    return UnknownNotification(f"unrecognized 0x5B mode 0x{mode:02X}", raw)


def bytes_hex(data: bytes) -> str:
    return data.hex(" ").upper() if data else "(none)"


def decoded_to_dict(notification: ParsedNotification) -> dict[str, Any]:
    """Convert a decoded notification to JSON-safe logging data."""
    if isinstance(notification, RuntimeNotification):
        return {
            "type": "runtime_status",
            "firmware_protocol_version": notification.firmware_protocol_version,
            "runtime_minutes": notification.runtime_minutes,
            "checksum_valid": notification.checksum_valid,
            "unknown_header_hex": bytes_hex(notification.unknown_header),
            "unknown_payload_hex": bytes_hex(notification.unknown_payload),
            "raw_hex": bytes_hex(notification.raw),
        }
    if isinstance(notification, ScheduleSnapshotNotification):
        return {
            "type": "schedule_snapshot",
            "firmware_protocol_version": notification.firmware_protocol_version,
            "points": [asdict(point) for point in notification.points],
            "unknown_header_hex": bytes_hex(notification.unknown_header),
            "unknown_prefix_hex": bytes_hex(notification.unknown_prefix),
            "unknown_trailer_hex": bytes_hex(notification.unknown_trailer),
            "raw_hex": bytes_hex(notification.raw),
        }
    return {
        "type": "unknown",
        "reason": notification.reason,
        "raw_hex": bytes_hex(notification.raw),
    }


def format_runtime(minutes: int) -> str:
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{minutes} minutes ({hours}h {remaining_minutes}m)"


def format_status_report(
    model: str,
    notifications: list[ParsedNotification],
    *,
    verbose: bool = False,
) -> str:
    """Render status without assigning semantics to unconfirmed bytes."""
    runtime = runtime_notification(notifications)
    schedule = schedule_notification(notifications)

    lines = [
        f"Device: {model}",
        "Mode: Unknown (no confirmed mode field exists in these notifications)",
        f"Runtime: {format_runtime(runtime.runtime_minutes) if runtime else 'not received'}",
        "Stored schedule:",
    ]
    if schedule and schedule.points:
        lines.extend(f"  {point.time_text} -> {point.level}%" for point in schedule.points)
    elif schedule:
        lines.append("  (snapshot received; no valid nonzero time slots decoded)")
    else:
        lines.append("  (schedule snapshot not received)")

    lines.append("Unknown/unassigned bytes:")
    if runtime:
        lines.append(f"  runtime header bytes[2:5]: {bytes_hex(runtime.unknown_header)}")
        lines.append(f"  runtime payload bytes[8:-1]: {bytes_hex(runtime.unknown_payload)}")
        checksum_text = (
            "not evaluated" if runtime.checksum_valid is None else str(runtime.checksum_valid).lower()
        )
        lines.append(f"  runtime XOR checksum valid: {checksum_text}")
    if schedule:
        lines.append(f"  schedule header bytes[2:5]: {bytes_hex(schedule.unknown_header)}")
        lines.append(f"  schedule pre-curve bytes[6:25]: {bytes_hex(schedule.unknown_prefix)}")
        lines.append(f"  schedule trailing incomplete bytes: {bytes_hex(schedule.unknown_trailer)}")
        lines.append("  schedule snapshot checksum/trailer semantics: not established")
    if not runtime and not schedule:
        lines.append("  no recognized status payload received")

    unknown = [item for item in notifications if isinstance(item, UnknownNotification)]
    for index, item in enumerate(unknown, start=1):
        lines.append(f"  unclassified notification {index}: {item.reason}")

    if verbose:
        lines.append("Raw NUS TX notifications:")
        if notifications:
            lines.extend(f"  {index}: {bytes_hex(item.raw)}" for index, item in enumerate(notifications, 1))
        else:
            lines.append("  (none)")
    return "\n".join(lines)
