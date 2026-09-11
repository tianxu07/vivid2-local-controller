"""GUI-facing application services built exclusively on the verified core."""

from __future__ import annotations

import json
import logging
import os
import platform
import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from chihiros.constants import (
    COOLING_FAN_MODEL,
    FAN_MANUAL_SPEED_MAX,
    FAN_MANUAL_SPEED_MIN,
    MAGNETIC_II_MODEL,
    MAGNETIC_LIGHT_MODEL,
    RGB_VIVID_II_MODEL,
    WINDOWS_APP_VERSION,
    Z_LIGHT_MODEL,
)
from chihiros.models import A2_MAX_MODEL
from chihiros.upstream_profiles import detect_supported_model, supported_model
from chihiros.upstream_control import UpstreamOperation
from chihiros.upstream_protocol import Request
from chihiros.a2max_controller import A2MaxController
from chihiros.a2max_protocol import validate_a2max_level
from chihiros.magnetic1_controller import Magnetic1Controller
from chihiros.magnetic1_protocol import validate_rg_levels
from chihiros.magnetic2_controller import Magnetic2Controller
from chihiros.magnetic2_protocol import validate_wrgb_levels
from chihiros.fan_controller import CoolingFanController, CoolingFanTelemetry
from chihiros.fan_protocol import validate_fan_speed, validate_fan_temperatures
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
from chihiros.zlight_controller import ZLightController
from chihiros.zlight_protocol import validate_white_levels


APP_NAME = "Vivid2Controller"
# Retain the existing data directory so saved Vivid II selections still work.
DISPLAY_NAME = "Chihiros Local Controller"
DEFAULT_SCAN_SECONDS = 8.0


class RgbValidationError(ValueError):
    """Raised before Bluetooth use when a GUI RGB value is invalid."""


class BrightnessValidationError(ValueError):
    """Raised before Bluetooth use when A2 Max brightness is invalid."""


class WrgbValidationError(RgbValidationError):
    """Raised before Bluetooth use when a Magnetic II WRGB value is invalid."""


class RgValidationError(RgbValidationError):
    """Raised before Bluetooth use when an original Magnetic Light RG value is invalid."""


class FanSpeedValidationError(ValueError):
    """Raised before Bluetooth use when manual Cooling Fan speed is invalid."""


class FanTemperatureValidationError(ValueError):
    """Raised before Bluetooth use when thermostat temperatures are invalid."""


class WhiteValidationError(ValueError):
    """Raised before Bluetooth use when a Z Light white value is invalid."""


class BusyOperationError(RuntimeError):
    """Raised when a second BLE operation is requested."""


class DiscoverySafetyError(RuntimeError):
    """Raised when advertisements cannot be resolved unambiguously."""


@dataclass(frozen=True)
class CompatibleDevice:
    """A scan result accepted by the development GUI's explicit model registry."""

    name: str
    address: str
    model: str = RGB_VIVID_II_MODEL
    rssi: int | None = None

    @property
    def identity(self) -> str:
        """Canonical Windows BLE address; never a model or presentation label."""
        return validate_address(self.address)

    def as_core_config(self) -> DeviceConfig:
        return DeviceConfig(
            alias={RGB_VIVID_II_MODEL: "selected_vivid2", A2_MAX_MODEL: "selected_a2max",
                   MAGNETIC_LIGHT_MODEL: "selected_magnetic1",
                   MAGNETIC_II_MODEL: "selected_magnetic2",
                   COOLING_FAN_MODEL: "selected_fan",
                   Z_LIGHT_MODEL: "selected_zlight"}.get(self.model, "selected_device"),
            model=self.model,
            name=self.name,
            address=self.identity,
        )


@dataclass(frozen=True)
class DeviceChoices:
    devices_by_address: dict[str, CompatibleDevice]
    addresses: tuple[str, ...]
    labels: tuple[str, ...]


@dataclass(frozen=True)
class FanSessionState:
    """Locally remembered values for exactly one canonical Cooling Fan address."""

    manual_speed: int = 0
    start_temperature: int = 24
    max_temperature: int = 28
    telemetry: CoolingFanTelemetry | None = None


def build_device_choices(devices: Iterable[CompatibleDevice]) -> DeviceChoices:
    """Separate canonical identities, dropdown row order, and display-only text."""
    by_address: dict[str, CompatibleDevice] = {}
    for device in devices:
        ApplicationController._validate_selected_device(device)
        identity = device.identity
        existing = by_address.get(identity)
        if existing is not None:
            if (existing.name, existing.model) != (device.name, device.model):
                raise DiscoverySafetyError("Conflicting device identities for one BLE address")
            continue
        by_address[identity] = device if device.address == identity else replace(device, address=identity)

    addresses = tuple(by_address)
    compact = {address: address.replace(":", "") for address in addresses}
    widths = {address: 4 for address in addresses}
    # Extend only colliding suffixes. Full, distinct addresses guarantee termination.
    while True:
        suffixes = {address: compact[address][-widths[address]:] for address in addresses}
        counts = Counter(suffixes.values())
        collisions = [address for address in addresses if counts[suffixes[address]] > 1]
        if not collisions:
            break
        for address in collisions:
            widths[address] += 1
    labels = tuple(f"{by_address[address].model} — …{suffixes[address]}" for address in addresses)
    return DeviceChoices(by_address, addresses, labels)


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


def parse_brightness_input(value: object) -> int:
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        value = int(value.strip(), 10)
    try:
        return validate_a2max_level(value)
    except ValueError as exc:
        raise BrightnessValidationError("Brightness must be a whole number from 1 to 100.") from exc


def parse_wrgb_inputs(red: object, green: object, blue: object, white: object) -> tuple[int, int, int, int]:
    values = []
    for label, value in zip(("Red", "Green", "Blue", "White"), (red, green, blue, white)):
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            value = int(value.strip(), 10)
        try:
            values.append(validate_level(value))
        except ValueError as exc:
            raise WrgbValidationError(f"{label} must be a whole number from 0 to 100.") from exc
    return validate_wrgb_levels(*values)


def parse_rg_inputs(red: object, green: object) -> tuple[int, int]:
    values = []
    for label, value in zip(("Red", "Green"), (red, green)):
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            value = int(value.strip(), 10)
        try:
            values.append(validate_level(value))
        except ValueError as exc:
            raise RgValidationError(
                f"{label} must be a whole number from 0 to 100."
            ) from exc
    return validate_rg_levels(*values)


def parse_white_inputs(cool_white: object, warm_white: object) -> tuple[int, int]:
    values = []
    for label, value in zip(("Cool White", "Warm White"), (cool_white, warm_white)):
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            value = int(value.strip(), 10)
        try:
            values.append(validate_level(value))
        except ValueError as exc:
            raise WhiteValidationError(
                f"{label} must be a whole number from 0 to 100."
            ) from exc
    return validate_white_levels(*values)


def parse_fan_speed_input(value: object) -> int:
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        value = int(value.strip(), 10)
    try:
        return validate_fan_speed(value)
    except ValueError as exc:
        raise FanSpeedValidationError(
            f"Fan speed level must be a whole-number device level from "
            f"{FAN_MANUAL_SPEED_MIN} to {FAN_MANUAL_SPEED_MAX}."
        ) from exc


def parse_fan_temperature_inputs(
    start_temperature: object, max_temperature: object
) -> tuple[int, int]:
    parsed: list[int] = []
    for label, value in (
        ("Start Temperature", start_temperature),
        ("Max-Speed Temperature", max_temperature),
    ):
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            value = int(value.strip(), 10)
        if isinstance(value, bool) or not isinstance(value, int):
            raise FanTemperatureValidationError(
                f"{label} must be a whole-number Celsius value representable by one protocol byte."
            )
        parsed.append(value)
    try:
        return validate_fan_temperatures(*parsed)
    except ValueError as exc:
        raise FanTemperatureValidationError(str(exc)) from exc


def controls_for_device(device: CompatibleDevice | None) -> tuple[str, ...]:
    if device is None:
        return ()
    ApplicationController._validate_selected_device(device)
    return supported_model(device.name).controls


async def scan_supported_devices(seconds: float) -> list[ScanResult]:
    return await scan_known_chihiros(seconds, model_detector=detect_supported_model)


def filter_compatible_devices(results: Iterable[ScanResult]) -> tuple[CompatibleDevice, ...]:
    """Keep only unambiguous, explicitly supported advertisement prefixes."""
    by_address: dict[str, CompatibleDevice] = {}
    for result in results:
        name = (result.name or "").strip()
        model = detect_supported_model(name)
        if model is None:
            continue
        try:
            address = validate_address(result.address)
        except (AttributeError, TypeError, ValueError):
            continue
        candidate = CompatibleDevice(name, address, model, result.rssi)
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
            if device.identity == saved.identity and device.name == saved.name and device.model == saved.model:
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
            if detect_supported_model(name) is None or detect_supported_model(name) != model:
                return None
            return CompatibleDevice(name.strip(), validate_address(address), model)
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
            return None

    def save(self, device: CompatibleDevice) -> None:
        ApplicationController._validate_selected_device(device)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"name": device.name, "address": device.identity, "model": device.model},
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
        scan_func: Callable[[float], Awaitable[list[ScanResult]]] = scan_supported_devices,
        session_factory: Callable[..., Any] | None = None,
        a2max_session_factory: Callable[..., Any] | None = None,
        magnetic1_session_factory: Callable[..., Any] | None = None,
        magnetic2_session_factory: Callable[..., Any] | None = None,
        zlight_session_factory: Callable[..., Any] | None = None,
        fan_status_session_factory: Callable[..., Any] | None = None,
        fan_manual_session_factory: Callable[..., Any] | None = None,
        fan_automatic_session_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.log_dir = self.data_dir / "logs"
        self.preferences = DevicePreferences(self.data_dir / "selected_device.json")
        self._scan_func = scan_func
        self._session_factory = session_factory
        self._a2max_session_factory = a2max_session_factory
        self._magnetic1_session_factory = magnetic1_session_factory
        self._magnetic2_session_factory = magnetic2_session_factory
        self._zlight_session_factory = zlight_session_factory
        self._fan_status_session_factory = fan_status_session_factory
        self._fan_manual_session_factory = fan_manual_session_factory
        self._fan_automatic_session_factory = fan_automatic_session_factory
        self._fan_state_by_address: dict[str, FanSessionState] = {}
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

    def _fan_controller(self, device: CompatibleDevice) -> CoolingFanController:
        kwargs: dict[str, Any] = {}
        if self._fan_status_session_factory is not None:
            kwargs["status_session_factory"] = self._fan_status_session_factory
        if self._fan_manual_session_factory is not None:
            kwargs["manual_session_factory"] = self._fan_manual_session_factory
        if self._fan_automatic_session_factory is not None:
            kwargs["automatic_session_factory"] = self._fan_automatic_session_factory
        return CoolingFanController(device.as_core_config(), self.log_dir, **kwargs)

    def fan_state(self, device: CompatibleDevice) -> FanSessionState:
        self._validate_selected_device(device)
        if device.model != COOLING_FAN_MODEL:
            raise DiscoverySafetyError("Cooling Fan state requires a DYNFAN device")
        return self._fan_state_by_address.setdefault(device.identity, FanSessionState())

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
        if device.model != RGB_VIVID_II_MODEL:
            raise DiscoverySafetyError("RGB controls require RGB Vivid II")
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

    async def apply_brightness(self, device: CompatibleDevice, brightness: object) -> Path:
        level = parse_brightness_input(brightness)
        self._validate_selected_device(device)
        if device.model != A2_MAX_MODEL:
            raise DiscoverySafetyError("Brightness control requires A2 Max")
        self._begin("apply_brightness")
        try:
            kwargs: dict[str, Any] = {}
            if self._a2max_session_factory is not None:
                kwargs["session_factory"] = self._a2max_session_factory
            self.logger.info("brightness_requested name=%s address=%s normalized_wire_level=%d",
                             device.name, device.address, level)
            path = await A2MaxController(device.as_core_config(), self.log_dir, **kwargs).manual(level)
            self.logger.info("brightness_applied session_log=%s", path)
            return path
        except BaseException:
            self.logger.exception("brightness_apply_failed name=%s address=%s", device.name, device.address)
            raise
        finally:
            self._finish("apply_brightness")


    async def apply_wrgb(self, device: CompatibleDevice, red: object, green: object,
                         blue: object, white: object) -> Path:
        levels = parse_wrgb_inputs(red, green, blue, white)
        self._validate_selected_device(device)
        if device.model != MAGNETIC_II_MODEL:
            raise DiscoverySafetyError("WRGB controls require Magnetic Light II")
        self._begin("apply_wrgb")
        try:
            kwargs: dict[str, Any] = {}
            if self._magnetic2_session_factory is not None:
                kwargs["session_factory"] = self._magnetic2_session_factory
            self.logger.info("wrgb_requested address=%s red=%d green=%d blue=%d white=%d",
                             device.identity, *levels)
            path = await Magnetic2Controller(device.as_core_config(), self.log_dir, **kwargs).manual(*levels)
            self.logger.info("wrgb_applied session_log=%s", path)
            return path
        except BaseException:
            self.logger.exception("wrgb_apply_failed address=%s", device.identity)
            raise
        finally:
            self._finish("apply_wrgb")

    async def apply_rg(
        self, device: CompatibleDevice, red: object, green: object
    ) -> Path:
        levels = parse_rg_inputs(red, green)
        self._validate_selected_device(device)
        if device.model != MAGNETIC_LIGHT_MODEL:
            raise DiscoverySafetyError("RG controls require Magnetic Light")
        self._begin("apply_rg")
        try:
            kwargs: dict[str, Any] = {}
            if self._magnetic1_session_factory is not None:
                kwargs["session_factory"] = self._magnetic1_session_factory
            self.logger.info(
                "rg_requested address=%s red=%d green=%d", device.identity, *levels
            )
            path = await Magnetic1Controller(
                device.as_core_config(), self.log_dir, **kwargs
            ).manual(*levels)
            self.logger.info("rg_applied session_log=%s", path)
            return path
        except BaseException:
            self.logger.exception("rg_apply_failed address=%s", device.identity)
            raise
        finally:
            self._finish("apply_rg")

    async def apply_white(
        self, device: CompatibleDevice, cool_white: object, warm_white: object
    ) -> Path:
        levels = parse_white_inputs(cool_white, warm_white)
        self._validate_selected_device(device)
        if device.model != Z_LIGHT_MODEL:
            raise DiscoverySafetyError("White controls require Z Light")
        self._begin("apply_white")
        try:
            kwargs: dict[str, Any] = {}
            if self._zlight_session_factory is not None:
                kwargs["session_factory"] = self._zlight_session_factory
            self.logger.info(
                "white_requested address=%s cool_white=%d warm_white=%d",
                device.identity, *levels,
            )
            path = await ZLightController(
                device.as_core_config(), self.log_dir, **kwargs
            ).manual(*levels)
            self.logger.info("white_applied session_log=%s", path)
            return path
        except BaseException:
            self.logger.exception("white_apply_failed address=%s", device.identity)
            raise
        finally:
            self._finish("apply_white")

    async def execute_upstream(self, device: CompatibleDevice, request: Request):
        """New devices only; existing six Apply paths retain their model guards."""
        self._validate_selected_device(device)
        operation = UpstreamOperation(device.as_core_config(), request, self.log_dir)
        self._begin("upstream")
        try:
            return await operation.run()
        finally:
            self._finish("upstream")

    async def refresh_fan_status(
        self, device: CompatibleDevice
    ) -> tuple[CoolingFanTelemetry, Path]:
        self._validate_selected_device(device)
        if device.model != COOLING_FAN_MODEL:
            raise DiscoverySafetyError("Refresh Status requires a Cooling Fan")
        self._begin("refresh_fan_status")
        try:
            telemetry, path = await self._fan_controller(device).refresh_status()
            self._fan_state_by_address[device.identity] = replace(
                self.fan_state(device), telemetry=telemetry
            )
            self.logger.info("fan_status_refreshed address=%s session_log=%s", device.identity, path)
            return telemetry, path
        except BaseException:
            self.logger.exception("fan_status_refresh_failed address=%s", device.identity)
            raise
        finally:
            self._finish("refresh_fan_status")

    async def apply_fan_manual(self, device: CompatibleDevice, speed: object) -> Path:
        speed = parse_fan_speed_input(speed)
        self._validate_selected_device(device)
        if device.model != COOLING_FAN_MODEL:
            raise DiscoverySafetyError("Manual fan speed level requires a Cooling Fan")
        self._begin("apply_fan_manual")
        try:
            path = await self._fan_controller(device).manual(speed)
            self._fan_state_by_address[device.identity] = replace(
                self.fan_state(device), manual_speed=speed
            )
            self.logger.info(
                "fan_manual_applied address=%s speed=%d session_log=%s",
                device.identity,
                speed,
                path,
            )
            return path
        except BaseException:
            self.logger.exception("fan_manual_apply_failed address=%s", device.identity)
            raise
        finally:
            self._finish("apply_fan_manual")

    async def apply_fan_automatic(
        self,
        device: CompatibleDevice,
        start_temperature: object,
        max_temperature: object,
    ) -> Path:
        start_temperature, max_temperature = parse_fan_temperature_inputs(
            start_temperature, max_temperature
        )
        self._validate_selected_device(device)
        if device.model != COOLING_FAN_MODEL:
            raise DiscoverySafetyError("Automatic thermostat configuration requires a Cooling Fan")
        self._begin("apply_fan_automatic")
        try:
            path = await self._fan_controller(device).automatic(
                start_temperature, max_temperature
            )
            self._fan_state_by_address[device.identity] = replace(
                self.fan_state(device),
                start_temperature=start_temperature,
                max_temperature=max_temperature,
            )
            self.logger.info(
                "fan_automatic_applied address=%s start=%d max=%d session_log=%s",
                device.identity,
                start_temperature,
                max_temperature,
                path,
            )
            return path
        except BaseException:
            self.logger.exception("fan_automatic_apply_failed address=%s", device.identity)
            raise
        finally:
            self._finish("apply_fan_automatic")

    @staticmethod
    def _validate_selected_device(device: CompatibleDevice) -> None:
        if (
            not isinstance(device, CompatibleDevice)
            or detect_supported_model(device.name) is None
            or detect_supported_model(device.name) != device.model
            or not isinstance(device.address, str)
        ):
            raise DiscoverySafetyError("The selected device is not a supported model advertisement")
        try:
            if normalize_address(device.address) != validate_address(device.address):
                raise ValueError("Invalid address")
        except ValueError as exc:
            raise DiscoverySafetyError("The selected device has an invalid Bluetooth address") from exc

    def close(self) -> None:
        self.logger.info("application_stopped")
        for handler in tuple(self.logger.handlers):
            handler.flush()
            handler.close()
            self.logger.removeHandler(handler)


def friendly_error(error: BaseException, operation: str) -> str:
    """Translate technical failures for aquarium hobbyists; details remain in logs."""
    if isinstance(
        error,
        (
            RgbValidationError,
            BrightnessValidationError,
            FanSpeedValidationError,
            FanTemperatureValidationError,
            WhiteValidationError,
        ),
    ):
        return str(error)
    if isinstance(error, BusyOperationError):
        return str(error)
    if isinstance(error, DiscoverySafetyError):
        return "The detected device identity was ambiguous. No command was sent."
    message = str(error).lower()
    if isinstance(error, TransportSafetyError):
        if "advertisement" in message or "advertised as" in message:
            return "Could not find the selected device. Click Scan and try again."
        return "BLE safety check failed; the operation stopped. Check the local log before trying again."
    if any(
        marker in message
        for marker in ("in use", "access denied", "unreachable", "0x800700aa", "resource busy")
    ):
        return (
            "The device is currently in use by another Bluetooth client. "
            "Close My Chihiros and try again."
        )
    if operation == "scan":
        return "Could not scan for Bluetooth devices. Check that Windows Bluetooth is on."
    if operation == "save_selection":
        return "Could not remember the selected device. You can still scan and try again."
    return "Could not complete the operation. Close My Chihiros and check the local diagnostic log."


__all__ = [
    "APP_NAME",
    "ApplicationController",
    "BusyOperationError",
    "CompatibleDevice",
    "DeviceChoices",
    "build_device_choices",
    "DevicePreferences",
    "DiscoverySafetyError",
    "DISPLAY_NAME",
    "RgbValidationError",
    "RgValidationError",
    "BrightnessValidationError",
    "FanSessionState",
    "FanSpeedValidationError",
    "FanTemperatureValidationError",
    "WrgbValidationError",
    "WhiteValidationError",
    "controls_for_device",
    "parse_brightness_input",
    "parse_fan_speed_input",
    "parse_fan_temperature_inputs",
    "parse_rg_inputs",
    "parse_wrgb_inputs",
    "parse_white_inputs",
    "filter_compatible_devices",
    "friendly_error",
    "parse_rgb_inputs",
    "preferred_device",
]
