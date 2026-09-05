"""Manual RG adapter for physically validated original Magnetic Light devices."""

import asyncio
from pathlib import Path
from typing import Any, Callable

from .constants import BATCH_WRITE_DELAY_SECONDS, MAGNETIC_LIGHT_MODEL, NUS_RX_UUID
from .magnetic1_protocol import build_magnetic1_manual_plan, validate_rg_levels
from .models import detect_supported_model
from .protocol import Command
from .transport import (
    DeviceConfig, LocalSessionLog, NusSession, TransportSafetyError,
    normalize_address, validate_address, verify_resolved_nus,
)


def validate_magnetic1_device(device: DeviceConfig) -> None:
    if (
        not isinstance(device, DeviceConfig)
        or device.model != MAGNETIC_LIGHT_MODEL
        or detect_supported_model(device.name) != MAGNETIC_LIGHT_MODEL
        or not isinstance(device.address, str)
        or device.address != validate_address(device.address)
    ):
        raise TransportSafetyError(
            "Selected device is not a Magnetic Light advertisement with a canonical address"
        )


class Magnetic1ManualSession(NusSession):
    """NUS transport restricted to one ordered three-command RG transaction."""

    def __init__(
        self,
        device: DeviceConfig,
        session_log: LocalSessionLog,
        *,
        levels: tuple[int, int],
    ) -> None:
        validate_magnetic1_device(device)
        self._target = device
        self._plan = build_magnetic1_manual_plan(*levels)
        self._next_packet = 0
        self._failed = False
        super().__init__(
            device,
            session_log,
            scan_seconds=20,
            connect_timeout=30,
            model_detector=detect_supported_model,
            subscribe_notifications=False,
        )

    def _verify_target(self) -> None:
        if (
            self.device != self._target
            or self.client is None
            or not self.client.is_connected
            or normalize_address(str(self.client.address)) != self._target.address
        ):
            raise TransportSafetyError(
                "Active connection differs from the selected Magnetic Light address"
            )

    async def connect(self) -> None:
        await super().connect()
        self._verify_target()

    async def send(self, command: Command) -> None:
        try:
            if (
                self._failed
                or self._next_packet >= len(self._plan)
                or command != self._plan[self._next_packet]
            ):
                raise TransportSafetyError(
                    "Only the next selected Magnetic Light RG packet is permitted"
                )
            self._verify_target()
            if self.resolved is None:
                raise TransportSafetyError("NUS has not been resolved")
            verify_resolved_nus(self.resolved, tuple(self.client.services))
            self.log.record(
                "command_write_started",
                logical_command=command.logical_name,
                message_id=f"{command.packet[3]:02X}{command.packet[4]:02X}",
                packet_hex=command.packet_hex,
                write_target_uuid=NUS_RX_UUID,
                target_address=self._target.address,
                response=False,
                changes_state=True,
            )
            self._next_packet += 1  # Never retry an uncertain write.
            await self.client.write_gatt_char(self.resolved.rx, command.packet, response=False)
            self.log.record("command_write_completed", packet_hex=command.packet_hex)
        except BaseException:
            self._failed = True
            raise


class Magnetic1Controller:
    """Only complete manual RG; no status, scheduling, reset, DFU or raw writes."""

    def __init__(
        self,
        device: DeviceConfig,
        log_dir: Path,
        *,
        session_factory: Callable[..., Any] = Magnetic1ManualSession,
    ) -> None:
        validate_magnetic1_device(device)
        self.device = device
        self.log_dir = log_dir
        self.session_factory = session_factory

    async def manual(self, red: int, green: int) -> Path:
        levels = validate_rg_levels(red, green)
        validate_magnetic1_device(self.device)
        commands = build_magnetic1_manual_plan(*levels)
        log = LocalSessionLog(self.log_dir, self.device, "manual_rg")
        attempted = completed = 0
        try:
            log.record(
                "command_plan_created",
                requested_rg=dict(zip(("red", "green"), levels)),
                level_convention="direct_0_100_no_overclock",
                physical_validation="real device practical comparison",
                hardware_revision_coverage_claimed=False,
                retries=0,
                commands=[{"packet_hex": command.packet_hex} for command in commands],
            )
            async with self.session_factory(self.device, log, levels=levels) as session:
                for index, command in enumerate(commands):
                    if index:
                        await asyncio.sleep(BATCH_WRITE_DELAY_SECONDS)
                    attempted += 1
                    await session.send(command)
                    completed += 1
        except BaseException as exc:
            log.record(
                "manual_rg_failed", exception_type=type(exc).__name__, message=str(exc)
            )
            raise
        finally:
            log.record(
                "manual_rg_summary",
                send_calls_attempted=attempted,
                send_calls_completed=completed,
            )
            log.finish()
        return log.path
