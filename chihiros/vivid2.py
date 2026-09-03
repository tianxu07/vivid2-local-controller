"""Reusable RGB Vivid II command plans and conservative live operations."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Callable

from .backup import create_backup_document, save_backup_document
from .constants import (
    BATCH_WRITE_DELAY_SECONDS,
    RGB_CHANNELS,
    RGB_VIVID_II_MODEL,
    STATUS_RESPONSE_WAIT_SECONDS,
)
from .protocol import (
    Command,
    MessageIdSequence,
    create_auto_mode,
    create_brightness,
    create_manual_mode,
    create_status_query,
    validate_level,
)
from .schedule import build_clock_sync_plan
from .status import ParsedNotification, format_status_report
from .transport import DeviceConfig, LocalSessionLog, NusSession


def build_status_plan() -> tuple[Command, ...]:
    sequence = MessageIdSequence()
    return (
        sequence.build("Query runtime and stored schedule", create_status_query, changes_state=False),
    )


def build_manual_plan(red: int, green: int, blue: int) -> tuple[Command, ...]:
    levels = {
        "red": validate_level(red),
        "green": validate_level(green),
        "blue": validate_level(blue),
    }
    sequence = MessageIdSequence()
    commands = [
        sequence.build("Enter manual mode", create_manual_mode, changes_state=True),
    ]
    for name in ("red", "green", "blue"):
        channel = RGB_CHANNELS[name]
        level = levels[name]
        command = sequence.build(
            f"Set {name} channel {channel} to {level}%",
            lambda message_id, channel=channel, level=level: create_brightness(
                message_id, channel, level
            ),
            changes_state=True,
        )
        wire_level = command.packet[7]
        if wire_level != level:
            command = Command(
                f"{command.logical_name} (wire level {wire_level}% from upstream reserved-byte avoidance)",
                command.message_id,
                command.packet,
                command.changes_state,
            )
        commands.append(command)
    return tuple(commands)


def build_auto_plan() -> tuple[Command, ...]:
    sequence = MessageIdSequence()
    return (
        sequence.build(
            "Activate existing stored auto schedule",
            create_auto_mode,
            changes_state=True,
        ),
    )


def build_off_plan() -> tuple[Command, ...]:
    return build_manual_plan(0, 0, 0)


def checksum_equation(command: Command) -> str:
    return " XOR ".join(f"{value:02X}" for value in command.packet[1:-1])


def format_plan(title: str, commands: tuple[Command, ...]) -> str:
    lines = [title, "Rendering this packet plan does not connect to or write to Bluetooth."]
    for index, command in enumerate(commands, start=1):
        state = "yes" if command.changes_state else "no"
        lines.extend(
            (
                "",
                f"{index}. {command.logical_name}",
                f"   message ID: {command.message_id[0]:02X} {command.message_id[1]:02X}",
                f"   packet: {command.packet_hex}",
                f"   checksum: {checksum_equation(command)} = {command.packet[-1]:02X}",
                f"   changes state: {state}",
            )
        )
    contains_rtc = any(item.packet[0] == 0x5A and item.packet[5] == 0x09 for item in commands)
    contains_schedule_write = any(
        (item.packet[0] == 0xA5 and item.packet[5] == 0x19)
        or (item.packet[0] == 0x5A and item.packet[5] == 0x06)
        for item in commands
    )
    excluded = ["reset", "pairing", "firmware", "bootloader", "DFU"]
    if not contains_rtc:
        excluded.insert(0, "RTC")
    if not contains_schedule_write:
        excluded.insert(1 if not contains_rtc else 0, "schedule writes")
    lines.extend(
        (
            "",
            f"Excluded: {', '.join(excluded[:-1])}, and {excluded[-1]}.",
            "Live writes, when requested, target only service-scoped NUS RX with response=False.",
        )
    )
    return "\n".join(lines)


class Vivid2Controller:
    """High-level controller for the one configured RGB Vivid II."""

    def __init__(
        self,
        device: DeviceConfig,
        log_dir: Path,
        *,
        session_factory: Callable[..., NusSession] = NusSession,
    ) -> None:
        if device.model != RGB_VIVID_II_MODEL:
            raise ValueError(
                f"Alias {device.alias!r} is {device.model!r}; this controller supports only "
                f"{RGB_VIVID_II_MODEL!r}"
            )
        self.device = device
        self.log_dir = log_dir
        self.session_factory = session_factory

    async def status(self, *, verbose: bool = False) -> tuple[str, Path]:
        notifications, log_path = await self.query_status("status")
        report = format_status_report(self.device.model, notifications, verbose=verbose)
        return report, log_path

    async def query_status(self, logical_command: str) -> tuple[list[ParsedNotification], Path]:
        command = build_status_plan()[0]
        session_log = LocalSessionLog(self.log_dir, self.device, logical_command)
        async with self.session_factory(self.device, session_log) as session:
            await session.send(command)
            await session.wait_for_notifications(STATUS_RESPONSE_WAIT_SECONDS)
            notifications = list(session.notifications)
        return notifications, session_log.path

    async def backup_schedule(
        self, backup_root: Path
    ) -> tuple[Path, str, dict[str, object], Path]:
        notifications, log_path = await self.query_status("schedule_backup")
        document = create_backup_document(self.device, notifications)
        backup_path = save_backup_document(backup_root, self.device, document)
        report = format_status_report(self.device.model, notifications, verbose=True)
        return backup_path, report, document, log_path

    async def sync_clock(self, timestamp: datetime) -> Path:
        if not isinstance(timestamp, datetime):
            raise ValueError("Clock timestamp must be a datetime")
        return await self._send_plan("clock_sync", build_clock_sync_plan(timestamp))

    async def manual(self, red: int, green: int, blue: int) -> Path:
        commands = build_manual_plan(red, green, blue)
        return await self._send_plan(
            "manual",
            commands,
            requested_rgb={"red": red, "green": green, "blue": blue},
        )

    async def auto(self) -> Path:
        return await self._send_plan("auto", build_auto_plan())

    async def off(self) -> Path:
        return await self._send_plan(
            "off",
            build_off_plan(),
            requested_rgb={"red": 0, "green": 0, "blue": 0},
        )

    async def _send_plan(
        self,
        logical_command: str,
        commands: tuple[Command, ...],
        *,
        requested_rgb: dict[str, int] | None = None,
    ) -> Path:
        session_log = LocalSessionLog(self.log_dir, self.device, logical_command)
        session_log.record(
            "command_plan_created",
            requested_rgb=requested_rgb,
            commands=[
                {
                    "logical_command": command.logical_name,
                    "message_id": f"{command.packet[3]:02X}{command.packet[4]:02X}",
                    "packet_hex": command.packet_hex,
                    "changes_state": command.changes_state,
                }
                for command in commands
            ],
        )
        async with self.session_factory(self.device, session_log) as session:
            for index, command in enumerate(commands):
                await session.send(command)
                if index + 1 < len(commands):
                    await asyncio.sleep(BATCH_WRITE_DELAY_SECONDS)
        return session_log.path


__all__ = [
    "DeviceConfig",
    "Vivid2Controller",
    "build_auto_plan",
    "build_manual_plan",
    "build_off_plan",
    "build_status_plan",
    "format_plan",
]
