"""Manual WRGB adapter for physically validated Magnetic Light II advertisements."""

import asyncio
from pathlib import Path
from typing import Any, Callable

from .constants import BATCH_WRITE_DELAY_SECONDS, MAGNETIC_II_MODEL, NUS_RX_UUID
from .magnetic2_protocol import build_magnetic2_manual_plan, validate_wrgb_levels
from .models import detect_supported_model
from .protocol import Command
from .transport import (
    DeviceConfig, LocalSessionLog, NusSession, TransportSafetyError,
    normalize_address, validate_address, verify_resolved_nus,
)


def validate_magnetic2_device(device: DeviceConfig) -> None:
    if (not isinstance(device, DeviceConfig) or device.model != MAGNETIC_II_MODEL
            or detect_supported_model(device.name) != MAGNETIC_II_MODEL
            or not isinstance(device.address, str)
            or device.address != validate_address(device.address)):
        raise TransportSafetyError("Selected device is not a Magnetic Light II advertisement with a canonical address")


class Magnetic2ManualSession(NusSession):
    """Reuse NUS connection/cleanup, with a separate five-command write guard.

    The legacy Vivid II transport command whitelist is left unchanged. No status,
    subscription, restoration or other application command is sent here.
    """

    def __init__(self, device: DeviceConfig, session_log: LocalSessionLog, *,
                 levels: tuple[int, int, int, int]) -> None:
        validate_magnetic2_device(device)
        self._target = device
        self._plan = build_magnetic2_manual_plan(*levels)
        self._next_packet = 0
        self._failed = False
        super().__init__(device, session_log, scan_seconds=20, connect_timeout=30,
                         model_detector=detect_supported_model, subscribe_notifications=False)

    def _verify_target(self) -> None:
        if (self.device != self._target or self.client is None or not self.client.is_connected
                or normalize_address(str(self.client.address)) != self._target.address):
            raise TransportSafetyError("Active connection differs from the selected Magnetic Light II address")

    async def connect(self) -> None:
        await super().connect()
        self._verify_target()

    async def send(self, command: Command) -> None:
        try:
            if self._failed or self._next_packet >= len(self._plan) or command != self._plan[self._next_packet]:
                raise TransportSafetyError("Only the next selected Magnetic Light II WRGB packet is permitted")
            self._verify_target()
            if self.resolved is None:
                raise TransportSafetyError("NUS has not been resolved")
            verify_resolved_nus(self.resolved, tuple(self.client.services))
            self.log.record("command_write_started", logical_command=command.logical_name,
                            message_id=f"{command.packet[3]:02X}{command.packet[4]:02X}",
                            packet_hex=command.packet_hex, write_target_uuid=NUS_RX_UUID,
                            target_address=self._target.address, response=False, changes_state=True)
            self._next_packet += 1  # An uncertain write may never be attempted again.
            await self.client.write_gatt_char(self.resolved.rx, command.packet, response=False)
            self.log.record("command_write_completed", packet_hex=command.packet_hex)
        except BaseException:
            self._failed = True
            raise


class Magnetic2Controller:
    """Only the complete manual WRGB transaction; no other device operations."""

    def __init__(self, device: DeviceConfig, log_dir: Path, *,
                 session_factory: Callable[..., Any] = Magnetic2ManualSession) -> None:
        validate_magnetic2_device(device)
        self.device, self.log_dir, self.session_factory = device, log_dir, session_factory

    async def manual(self, red: int, green: int, blue: int, white: int) -> Path:
        levels = validate_wrgb_levels(red, green, blue, white)
        validate_magnetic2_device(self.device)
        commands = build_magnetic2_manual_plan(*levels)
        log = LocalSessionLog(self.log_dir, self.device, "manual_wrgb")
        attempted = completed = 0
        try:
            log.record("command_plan_created", requested_wrgb=dict(zip(("red", "green", "blue", "white"), levels)),
                       level_convention="direct_0_100", physical_validation="practical visual comparison",
                       photometric_linearity_claimed=False, retries=0,
                       commands=[{"packet_hex": c.packet_hex} for c in commands])
            async with self.session_factory(self.device, log, levels=levels) as session:
                for index, command in enumerate(commands):
                    if index:
                        await asyncio.sleep(BATCH_WRITE_DELAY_SECONDS)
                    attempted += 1
                    await session.send(command)
                    completed += 1
        except BaseException as exc:
            log.record("manual_wrgb_failed", exception_type=type(exc).__name__, message=str(exc))
            raise
        finally:
            log.record("manual_wrgb_summary", send_calls_attempted=attempted, send_calls_completed=completed)
            log.finish()
        return log.path
