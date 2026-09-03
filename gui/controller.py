"""GUI-facing application services built exclusively on the verified core."""

from __future__ import annotations

import json
import logging
import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from chihiros.constants import RGB_VIVID_II_MODEL, WINDOWS_APP_VERSION, detect_model
from chihiros.protocol import validate_level
from chihiros.transport import (
    DeviceConfig,
    ScanResult,
    TransportSafetyError,
    normalize_address,
    scan_known_chihiros,
    validate_address,
)
from chihiros.vivid2 import Vivid2Controller


APP_NAME = "Vivid2Controller"
DISPLAY_NAME = "RGB Vivid II Local Controller"
DEFAULT_SCAN_SECONDS = 8.0


class RgbValidationError(ValueError):
    """Raised before Bluetooth use when a GUI RGB value is invalid."""


class BusyOperationError(RuntimeError):
    """Raised when a second BLE operation is requested."""


class DiscoverySafetyError(RuntimeError):
    """Raised when advertisements cannot be resolved unambiguously."""


@dataclass(frozen=True)
class CompatibleDevice:
    """A scan result whose advertised name proves RGB Vivid II support."""

    name: str
    address: str
    model: str = RGB_VIVID_II_MODEL
    rssi: int | None = None

    @property
    def label(self) -> str:
        return f"{self.model} — {self.name} — {self.address}"

    def as_core_config(self) -> DeviceConfig:
        return DeviceConfig(
            alias="selected_vivid2",
            model=self.model,
            name=self.name,
            address=self.address,
        )


def default_data_dir() -> Path:
    """Return a per-user writable location for preferences and diagnostics."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def parse_rgb_inputs(red: object, green: object, blue: object) -> tuple[int, int, int]:
    """Parse whole-number GUI values and enforce the core's 0..100 boundary."""

    def parse_one(label: str, value: object) -> int:
        if isinstance(value, bool):
            raise RgbValidationError("RGB values must be whole numbers from 0 to 100.")
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            parsed = int(value.strip(), 10)
        else:
            raise RgbValidationError(
                f"{label} must be a whole number from 0 to 100."
            )
        try:
            return validate_level(parsed)
        except ValueError as exc:
            raise RgbValidationError("RGB values must be between 0 and 100.") from exc

    return (
        parse_one("Red", red),
        parse_one("Green", green),
        parse_one("Blue", blue),
    )


def filter_compatible_devices(results: Iterable[ScanResult]) -> tuple[CompatibleDevice, ...]:
    """Keep only unambiguous, prefix-proven Vivid II advertisements."""
    by_address: dict[str, CompatibleDevice] = {}
    for result in results:
        name = (result.name or "").strip()
        if detect_model(name) != RGB_VIVID_II_MODEL:
            continue
        try:
            address = validate_address(result.address)
        except (AttributeError, TypeError, ValueError):
            continue
        candidate = CompatibleDevice(name, address, RGB_VIVID_II_MODEL, result.rssi)
        existing = by_address.get(address)
        if existing is not None and existing.name != candidate.name:
            raise DiscoverySafetyError(
                f"Address {address} advertised conflicting supported names; selection was refused"
            )
        candidate_strength = candidate.rssi if candidate.rssi is not None else -9999
        existing_strength = (
            existing.rssi if existing is not None and existing.rssi is not None else -9999
        )
        if existing is None or candidate_strength > existing_strength:
            by_address[address] = candidate
    return tuple(
        sorted(by_address.values(), key=lambda item: (item.name.upper(), item.address))
    )


def preferred_device(
    devices: tuple[CompatibleDevice, ...],
    saved: CompatibleDevice | None,
) -> CompatibleDevice | None:
    """Select one discovery safely; discovery never triggers a control operation."""
    if saved is not None:
        for device in devices:
            if device.address == saved.address and device.name == saved.name:
                return device
    if len(devices) == 1:
        return devices[0]
    return None


class DevicePreferences:
    """Account-free storage for the one explicitly selected local lamp."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> CompatibleDevice | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return None
            name = value.get("name")
            address = value.get("address")
            model = value.get("model")
            if not all(isinstance(item, str) for item in (name, address, model)):
                return None
            if model != RGB_VIVID_II_MODEL or detect_model(name) != RGB_VIVID_II_MODEL:
                return None
            return CompatibleDevice(name.strip(), validate_address(address), model)
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
            return None

    def save(self, device: CompatibleDevice) -> None:
        if detect_model(device.name) != RGB_VIVID_II_MODEL:
            raise DiscoverySafetyError("Only a prefix-verified RGB Vivid II can be remembered")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"name": device.name, "address": device.address, "model": device.model},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def forget(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def create_technical_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"{APP_NAME}.{id(log_dir)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.FileHandler(log_dir / "application.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


class ApplicationController:
    """Serialize scan/control operations and delegate packets to the core library."""

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        scan_func: Callable[[float], Awaitable[list[ScanResult]]] = scan_known_chihiros,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.log_dir = self.data_dir / "logs"
        self.preferences = DevicePreferences(self.data_dir / "selected_device.json")
        self._scan_func = scan_func
        self._session_factory = session_factory
        self._busy = False
        self.logger = create_technical_logger(self.log_dir)
        self.logger.info(
            "application_started version=%s windows=%s account_or_cloud_used=false",
            WINDOWS_APP_VERSION,
            platform.platform(),
        )

    @property
    def busy(self) -> bool:
        return self._busy

    def _begin(self, operation: str) -> None:
        if self._busy:
            raise BusyOperationError("Wait for the current Bluetooth operation to finish.")
        self._busy = True
        self.logger.info("operation_started operation=%s", operation)

    def _finish(self, operation: str) -> None:
        self.logger.info("operation_finished operation=%s", operation)
        self._busy = False

    def _core_controller(self, device: CompatibleDevice) -> Vivid2Controller:
        kwargs: dict[str, Any] = {}
        if self._session_factory is not None:
            kwargs["session_factory"] = self._session_factory
        return Vivid2Controller(device.as_core_config(), self.log_dir, **kwargs)

    async def scan(self, seconds: float = DEFAULT_SCAN_SECONDS) -> tuple[CompatibleDevice, ...]:
        self._begin("scan")
        try:
            results = filter_compatible_devices(await self._scan_func(seconds))
            self.logger.info("scan_completed compatible_devices=%d", len(results))
            for device in results:
                self.logger.info(
                    "device_detected model=%s name=%s address=%s rssi=%s",
                    device.model,
                    device.name,
                    device.address,
                    device.rssi,
                )
            return results
        except BaseException:
            self.logger.exception("scan_failed")
            raise
        finally:
            self._finish("scan")

    async def apply_rgb(
        self,
        device: CompatibleDevice,
        red: object,
        green: object,
        blue: object,
    ) -> Path:
        values = parse_rgb_inputs(red, green, blue)
        self._validate_selected_device(device)
        self._begin("apply_rgb")
        try:
            self.logger.info(
                "rgb_requested name=%s address=%s red=%d green=%d blue=%d",
                device.name,
                device.address,
                *values,
            )
            path = await self._core_controller(device).manual(*values)
            self.logger.info("rgb_applied session_log=%s", path)
            return path
        except BaseException:
            self.logger.exception("rgb_apply_failed name=%s address=%s", device.name, device.address)
            raise
        finally:
            self._finish("apply_rgb")

    @staticmethod
    def _validate_selected_device(device: CompatibleDevice) -> None:
        if (
            not isinstance(device, CompatibleDevice)
            or device.model != RGB_VIVID_II_MODEL
            or detect_model(device.name) != RGB_VIVID_II_MODEL
            or normalize_address(device.address) != validate_address(device.address)
        ):
            raise DiscoverySafetyError("The selected device is not a verified RGB Vivid II")

    def close(self) -> None:
        self.logger.info("application_stopped")
        for handler in tuple(self.logger.handlers):
            handler.flush()
            handler.close()
            self.logger.removeHandler(handler)


def friendly_error(error: BaseException, operation: str) -> str:
    """Translate technical failures for aquarium hobbyists; details remain in logs."""
    if isinstance(error, RgbValidationError):
        return str(error)
    if isinstance(error, BusyOperationError):
        return str(error)
    if isinstance(error, DiscoverySafetyError):
        return "The detected device identity was ambiguous. No command was sent."
    message = str(error).lower()
    if isinstance(error, TransportSafetyError):
        if "advertisement" in message or "advertised as" in message:
            return "Could not find the selected Vivid II. Click Scan and try again."
        return "Unexpected BLE service layout. No command was sent."
    if any(
        marker in message
        for marker in ("in use", "access denied", "unreachable", "0x800700aa", "resource busy")
    ):
        return (
            "The lamp is currently in use by another Bluetooth device. "
            "Close My Chihiros and try again."
        )
    if operation == "scan":
        return "Could not scan for Bluetooth lights. Check that Windows Bluetooth is on."
    if operation == "save_selection":
        return "Could not remember the selected device. You can still scan and try again."
    return "Could not connect to the selected Vivid II. Close My Chihiros and try again."


__all__ = [
    "APP_NAME",
    "ApplicationController",
    "BusyOperationError",
    "CompatibleDevice",
    "DevicePreferences",
    "DiscoverySafetyError",
    "DISPLAY_NAME",
    "RgbValidationError",
    "filter_compatible_devices",
    "friendly_error",
    "parse_rgb_inputs",
    "preferred_device",
]
