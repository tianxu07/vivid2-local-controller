"""Manual-only A2 Max adapter for discovered candidate-prefix devices."""

import asyncio
from pathlib import Path
from typing import Any, Callable

from .a2max_protocol import build_a2max_manual_plan, validate_a2max_level
from .constants import BATCH_WRITE_DELAY_SECONDS, DEVELOPMENT_APP_VERSION
from .models import A2_MAX_MODEL, detect_supported_model
from .protocol import Command
from .transport import DeviceConfig, LocalSessionLog, NusSession, TransportSafetyError, validate_address


def validate_a2max_device(device: DeviceConfig) -> None:
    if (
        not isinstance(device, DeviceConfig)
        or device.model != A2_MAX_MODEL
        or detect_supported_model(device.name) != A2_MAX_MODEL
        or not isinstance(device.address, str)
        or device.address != validate_address(device.address)
    ):
        raise TransportSafetyError("Selected device is not an A2 Max candidate advertisement")


class A2MaxManualSession(NusSession):
    """Shared NUS transport restricted to one ordered, two-packet manual plan."""

    def __init__(self, device: DeviceConfig, session_log: LocalSessionLog, *, level: int) -> None:
        validate_a2max_device(device)
        self._plan = build_a2max_manual_plan(level)
        self._next_packet = 0
        self._failed = False
        super().__init__(device, session_log, scan_seconds=20.0, connect_timeout=30.0,
                         model_detector=detect_supported_model, subscribe_notifications=False)

    async def send(self, command: Command) -> None:
        if self._failed or self._next_packet >= len(self._plan) or command != self._plan[self._next_packet]:
            raise TransportSafetyError("Only the next A2 Max manual-brightness packet is permitted")
        # Never permit a second attempt or continuation after an uncertain write.
        self._next_packet += 1
        try:
            await super().send(command)
        except BaseException:
            self._failed = True
            raise


class A2MaxController:
    """No status, RTC, schedule, auto, off, or raw-command entry points."""

    def __init__(self, device: DeviceConfig, log_dir: Path, *,
                 session_factory: Callable[..., Any] = A2MaxManualSession) -> None:
        validate_a2max_device(device)
        self.device = device
        self.log_dir = log_dir
        self.session_factory = session_factory

    async def manual(self, level: int) -> Path:
        level = validate_a2max_level(level)
        validate_a2max_device(self.device)
        commands = build_a2max_manual_plan(level)
        log = LocalSessionLog(self.log_dir, self.device, "manual_brightness")
        attempted = completed = 0
        phase = "preparing"
        try:
            log.record("command_plan_created", development_app_version=DEVELOPMENT_APP_VERSION,
                       normalized_wire_level=level, ui_percentage_claimed=False,
                       candidate_prefix_physical_samples=1, retries=0,
                       commands=[{"packet_hex": c.packet_hex, "checksum": f"{c.packet[-1]:02X}"}
                                 for c in commands])
            phase = "connecting-and-resolving-NUS"
            async with self.session_factory(self.device, log, level=level) as session:
                for index, command in enumerate(commands):
                    phase = "writing-manual-mode" if index == 0 else "writing-brightness"
                    log.record("execution_phase", phase=phase)
                    attempted += 1
                    await session.send(command)
                    completed += 1
                    if index == 0:
                        phase = "waiting-30ms"
                        await asyncio.sleep(BATCH_WRITE_DELAY_SECONDS)
                phase = "disconnecting"
        except BaseException as exc:
            log.record("manual_brightness_failed", phase=phase,
                       exception_type=type(exc).__name__, exception_repr=repr(exc),
                       exception_str=str(exc))
            raise
        finally:
            log.record("manual_brightness_summary", phase=phase,
                       send_calls_attempted=attempted, send_calls_completed=completed)
            log.finish()
        return log.path
