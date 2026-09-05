"""Short-lived, address-routed Cooling Fan BLE operations."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .constants import (
    COOLING_FAN_MODEL,
    FAN_CONFIGURATION_WRITE_DELAY_SECONDS,
    FAN_STATUS_RESPONSE_WAIT_SECONDS,
    NUS_TX_UUID,
)
from .fan_protocol import (
    build_fan_automatic_plan,
    build_fan_manual_plan,
    build_fan_status_plan,
    validate_fan_speed,
    validate_fan_temperatures,
)
from .models import detect_supported_model
from .protocol import Command
from .transport import (
    DeviceConfig,
    LocalSessionLog,
    NusSession,
    TransportSafetyError,
    normalize_address,
    normalize_uuid,
    object_handle,
    validate_address,
)


@dataclass(frozen=True)
class CoolingFanTelemetry:
    room_temperature_c: float
    water_temperature_c: float
    humidity_percent: float


def parse_fan_telemetry(data: bytes | bytearray) -> CoolingFanTelemetry | None:
    """Decode a structurally valid, plausible DYNFAN telemetry notification."""
    raw = bytes(data)
    if (
        len(raw) != 13
        or raw[0] != 0x5B
        or raw[1] != 0x09
        or raw[2] != len(raw) - 2
        or raw[3] != 0x00
        or raw[4] != 0x01
        or raw[5] != 0x25
    ):
        return None

    humidity_percent = ((raw[6] << 8) | raw[7]) / 100.0
    room_temperature_c = ((raw[8] << 8) | raw[9]) / 100.0
    water_temperature_raw = (raw[10] << 8) | raw[11]

    # Byte 12 has no independently verified meaning and is intentionally ignored.
    # The explicit 0x25 subtype distinguishes telemetry from preceding 0x0A
    # notifications that otherwise share the same outer frame structure.
    if water_temperature_raw == 0xFFFF:
        return None
    water_temperature_c = water_temperature_raw / 10.0
    if (
        not 0.0 <= humidity_percent <= 100.0
        or not 0.0 < room_temperature_c < 50.0
        or not 0.0 < water_temperature_c < 50.0
    ):
        return None

    return CoolingFanTelemetry(
        room_temperature_c=room_temperature_c,
        water_temperature_c=water_temperature_c,
        humidity_percent=humidity_percent,
    )


def validate_fan_device(device: DeviceConfig) -> None:
    if (
        not isinstance(device, DeviceConfig)
        or device.model != COOLING_FAN_MODEL
        or detect_supported_model(device.name) != COOLING_FAN_MODEL
        or not isinstance(device.address, str)
        or device.address != validate_address(device.address)
    ):
        raise TransportSafetyError(
            "Selected device is not a DYNFAN Cooling Fan advertisement with a canonical address"
        )


class _CoolingFanSession(NusSession):
    def _verify_target(self) -> None:
        if (
            self.client is None
            or not self.client.is_connected
            or normalize_address(str(self.client.address)) != self.device.address
        ):
            raise TransportSafetyError("Active connection differs from the selected Cooling Fan address")

    async def connect(self) -> None:
        await super().connect()
        self._verify_target()


class CoolingFanStatusSession(_CoolingFanSession):
    """Subscribe only to NUS TX and accept one known-safe status query."""

    def __init__(self, device: DeviceConfig, session_log: LocalSessionLog) -> None:
        validate_fan_device(device)
        self._query = build_fan_status_plan()[0]
        self._query_attempted = False
        self._failed = False
        self._telemetry: CoolingFanTelemetry | None = None
        self._telemetry_ready = asyncio.Event()
        super().__init__(
            device,
            session_log,
            scan_seconds=20.0,
            connect_timeout=30.0,
            model_detector=detect_supported_model,
            subscribe_notifications=True,
        )

    def _on_notification(self, sender: Any, data: bytearray) -> None:
        if self.resolved is None or normalize_uuid(getattr(sender, "uuid", "")) != NUS_TX_UUID:
            self.log.record("notification_ignored", reason="not selected NUS TX")
            return
        raw = bytes(data)
        telemetry = parse_fan_telemetry(raw)
        self.log.record(
            "notification_received",
            uuid=NUS_TX_UUID,
            handle=object_handle(sender),
            raw_hex=raw.hex(" ").upper(),
            decoded=asdict(telemetry) if telemetry is not None else {"type": "unrecognized"},
        )
        if telemetry is not None and self._telemetry is None:
            self._telemetry = telemetry
            self._telemetry_ready.set()

    async def send(self, command: Command) -> None:
        if self._failed or self._query_attempted or command != self._query:
            self._failed = True
            raise TransportSafetyError("Refresh Status permits the known safe query exactly once")
        self._verify_target()
        self._query_attempted = True
        try:
            await super().send(command)
        except BaseException:
            self._failed = True
            raise

    async def wait_for_telemetry(
        self, timeout: float = FAN_STATUS_RESPONSE_WAIT_SECONDS
    ) -> CoolingFanTelemetry:
        try:
            await asyncio.wait_for(self._telemetry_ready.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("Cooling Fan telemetry was not received before timeout") from exc
        if self._telemetry is None:
            raise TransportSafetyError("Cooling Fan telemetry notification was not retained")
        return self._telemetry


class _CoolingFanWriteSession(_CoolingFanSession):
    def __init__(
        self, device: DeviceConfig, session_log: LocalSessionLog, plan: tuple[Command, ...]
    ) -> None:
        validate_fan_device(device)
        self._plan = plan
        self._next_packet = 0
        self._failed = False
        super().__init__(
            device,
            session_log,
            scan_seconds=20.0,
            connect_timeout=30.0,
            model_detector=detect_supported_model,
            subscribe_notifications=False,
        )

    async def send(self, command: Command) -> None:
        if (
            self._failed
            or self._next_packet >= len(self._plan)
            or command != self._plan[self._next_packet]
        ):
            self._failed = True
            raise TransportSafetyError("Only the next Cooling Fan packet in the selected plan is permitted")
        self._verify_target()
        self._next_packet += 1
        try:
            await super().send(command)
        except BaseException:
            self._failed = True
            raise


class CoolingFanManualSession(_CoolingFanWriteSession):
    def __init__(self, device: DeviceConfig, session_log: LocalSessionLog, *, speed: int) -> None:
        super().__init__(device, session_log, build_fan_manual_plan(speed))


class CoolingFanAutomaticSession(_CoolingFanWriteSession):
    def __init__(
        self,
        device: DeviceConfig,
        session_log: LocalSessionLog,
        *,
        start_temperature: int,
        max_temperature: int,
        timestamp: datetime,
    ) -> None:
        super().__init__(
            device,
            session_log,
            build_fan_automatic_plan(start_temperature, max_temperature, timestamp),
        )


class CoolingFanController:
    """Explicit refresh, manual speed, and one-shot device-side thermostat setup."""

    def __init__(
        self,
        device: DeviceConfig,
        log_dir: Path,
        *,
        status_session_factory: Callable[..., Any] = CoolingFanStatusSession,
        manual_session_factory: Callable[..., Any] = CoolingFanManualSession,
        automatic_session_factory: Callable[..., Any] = CoolingFanAutomaticSession,
    ) -> None:
        validate_fan_device(device)
        self.device = device
        self.log_dir = log_dir
        self.status_session_factory = status_session_factory
        self.manual_session_factory = manual_session_factory
        self.automatic_session_factory = automatic_session_factory

    async def refresh_status(self) -> tuple[CoolingFanTelemetry, Path]:
        command = build_fan_status_plan()[0]
        log = LocalSessionLog(self.log_dir, self.device, "fan_status")
        async with self.status_session_factory(self.device, log) as session:
            await session.send(command)
            telemetry = await session.wait_for_telemetry(FAN_STATUS_RESPONSE_WAIT_SECONDS)
        return telemetry, log.path

    async def manual(self, speed: int) -> Path:
        speed = validate_fan_speed(speed)
        plan = build_fan_manual_plan(speed)
        log = LocalSessionLog(self.log_dir, self.device, "fan_manual")
        log.record(
            "command_plan_created",
            requested_speed=speed,
            automatic_configuration_included=False,
            commands=[{"packet_hex": command.packet_hex} for command in plan],
        )
        async with self.manual_session_factory(self.device, log, speed=speed) as session:
            await session.send(plan[0])
        return log.path

    async def automatic(
        self,
        start_temperature: int,
        max_temperature: int,
        *,
        timestamp: datetime | None = None,
    ) -> Path:
        start_temperature, max_temperature = validate_fan_temperatures(
            start_temperature, max_temperature
        )
        local_timestamp = timestamp or datetime.now()
        plan = build_fan_automatic_plan(
            start_temperature, max_temperature, local_timestamp
        )
        log = LocalSessionLog(self.log_dir, self.device, "fan_automatic")
        log.record(
            "command_plan_created",
            requested_start_temperature=start_temperature,
            requested_max_temperature=max_temperature,
            thermostat_control="device-side after disconnect",
            fan_speed_command_included=False,
            inter_command_delay_seconds=FAN_CONFIGURATION_WRITE_DELAY_SECONDS,
            commands=[{"packet_hex": command.packet_hex} for command in plan],
        )
        async with self.automatic_session_factory(
            self.device,
            log,
            start_temperature=start_temperature,
            max_temperature=max_temperature,
            timestamp=local_timestamp,
        ) as session:
            for index, command in enumerate(plan):
                if index:
                    await asyncio.sleep(FAN_CONFIGURATION_WRITE_DELAY_SECONDS)
                await session.send(command)
        return log.path


__all__ = [
    "CoolingFanAutomaticSession",
    "CoolingFanController",
    "CoolingFanManualSession",
    "CoolingFanStatusSession",
    "CoolingFanTelemetry",
    "parse_fan_telemetry",
    "validate_fan_device",
]
