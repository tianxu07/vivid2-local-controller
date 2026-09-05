"""Strict service-scoped NUS transport with local JSON session logging."""

from __future__ import annotations

import asyncio
import json
import platform
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .constants import (
    BLACKLISTED_CHARACTERISTIC_UUIDS,
    BLACKLISTED_SERVICE_UUIDS,
    DFU_SERVICE_UUID,
    CONTROLLER_VERSION,
    FAN_MANUAL_SPEED_MAX,
    NUS_RX_UUID,
    NUS_SERVICE_UUID,
    NUS_TX_UUID,
    RGB_VIVID_II_MODEL,
    WINDOWS_APP_VERSION,
    ORIGINAL_DEVICE_NUS_RX_HANDLE,
    ORIGINAL_DEVICE_NUS_SERVICE_HANDLE,
    ORIGINAL_DEVICE_NUS_TX_HANDLE,
    detect_model,
)
from .protocol import Command, calculate_checksum
from .status import ParsedNotification, decode_notification, decoded_to_dict


class TransportSafetyError(RuntimeError):
    """Raised before a write when GATT identity is missing or ambiguous."""


class ConfigurationError(ValueError):
    """Raised for invalid local device configuration."""


@dataclass(frozen=True)
class DeviceConfig:
    alias: str
    model: str
    name: str
    address: str


@dataclass(frozen=True)
class ResolvedNus:
    service: Any
    rx: Any
    tx: Any
    dfu_services: tuple[Any, ...]


@dataclass(frozen=True)
class ScanResult:
    name: str
    address: str
    model: str
    rssi: int | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def normalize_uuid(value: Any) -> str:
    return str(value).strip().lower()


def normalize_address(value: str) -> str:
    return value.strip().replace("-", ":").upper()


def object_handle(value: Any) -> int | None:
    handle = getattr(value, "handle", None)
    return handle if isinstance(handle, int) else None


def service_characteristics(service: Any) -> tuple[Any, ...]:
    return tuple(getattr(service, "characteristics", ()))


def characteristic_properties(characteristic: Any) -> set[str]:
    return {str(item).strip().lower() for item in getattr(characteristic, "properties", ())}


def validate_address(address: str) -> str:
    normalized = normalize_address(address)
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", normalized):
        raise ConfigurationError(f"Invalid Bluetooth address: {address!r}")
    return normalized


def load_devices(path: Path) -> dict[str, DeviceConfig]:
    """Load and validate account-free local device configuration."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Device configuration not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise ConfigurationError("Device configuration must be a non-empty JSON object")

    devices: dict[str, DeviceConfig] = {}
    seen_addresses: set[str] = set()
    for alias, value in raw.items():
        if not isinstance(alias, str) or not alias or not isinstance(value, dict):
            raise ConfigurationError("Every device entry requires a non-empty alias and object value")
        required = {key: value.get(key) for key in ("model", "name", "address")}
        if any(not isinstance(item, str) or not item.strip() for item in required.values()):
            raise ConfigurationError(f"Device {alias!r} requires string model, name, and address fields")
        address = validate_address(required["address"])
        if address in seen_addresses:
            raise ConfigurationError(f"Duplicate configured Bluetooth address: {address}")
        seen_addresses.add(address)
        devices[alias] = DeviceConfig(
            alias,
            required["model"].strip(),
            required["name"].strip(),
            address,
        )
    return devices


def configure_vivid2_device(
    path: Path,
    alias: str,
    name: str,
    address: str,
    *,
    replace: bool = False,
) -> DeviceConfig:
    """Store one explicitly selected, prefix-verified Vivid II in local config."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", alias):
        raise ConfigurationError("Alias may contain only letters, numbers, dot, underscore, and hyphen")
    name = name.strip()
    if detect_model(name) != RGB_VIVID_II_MODEL:
        raise ConfigurationError(
            f"Advertised name {name!r} is not a source-verified RGB Vivid II prefix"
        )
    normalized_address = validate_address(address)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigurationError("Device configuration must be a JSON object")
    else:
        raw = {}
    if alias in raw and not replace:
        raise ConfigurationError(f"Alias {alias!r} already exists; use --replace to update it")
    for existing_alias, value in raw.items():
        if existing_alias == alias or not isinstance(value, dict):
            continue
        existing_address = value.get("address")
        if isinstance(existing_address, str) and normalize_address(existing_address) == normalized_address:
            raise ConfigurationError(
                f"Address {normalized_address} is already configured as {existing_alias!r}"
            )
    raw[alias] = {
        "model": RGB_VIVID_II_MODEL,
        "name": name,
        "address": normalized_address,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return DeviceConfig(alias, RGB_VIVID_II_MODEL, name, normalized_address)


def handle_diagnostics(resolved: ResolvedNus) -> dict[str, dict[str, Any]]:
    """Compare actual handles with one observed lamp without treating them as identity."""
    return {
        label: {
            "actual": object_handle(value),
            "reference_from_original_lamp": expected,
            "matches_reference": object_handle(value) == expected if object_handle(value) is not None else None,
        }
        for label, value, expected in (
            ("service", resolved.service, ORIGINAL_DEVICE_NUS_SERVICE_HANDLE),
            ("rx", resolved.rx, ORIGINAL_DEVICE_NUS_RX_HANDLE),
            ("tx", resolved.tx, ORIGINAL_DEVICE_NUS_TX_HANDLE),
        )
    }


def strict_resolve_nus(services: Iterable[Any]) -> ResolvedNus:
    """Select RX/TX only from the unique NUS service's own children."""
    service_list = tuple(services)
    nus_services = tuple(
        service
        for service in service_list
        if normalize_uuid(getattr(service, "uuid", "")) == NUS_SERVICE_UUID
    )
    if len(nus_services) != 1:
        raise TransportSafetyError(f"Expected exactly one NUS service; found {len(nus_services)}")
    nus = nus_services[0]
    children = service_characteristics(nus)
    rx_matches = tuple(
        item for item in children if normalize_uuid(getattr(item, "uuid", "")) == NUS_RX_UUID
    )
    tx_matches = tuple(
        item for item in children if normalize_uuid(getattr(item, "uuid", "")) == NUS_TX_UUID
    )
    if len(rx_matches) != 1 or len(tx_matches) != 1:
        raise TransportSafetyError(
            f"NUS requires exactly one RX and one TX; found RX={len(rx_matches)}, TX={len(tx_matches)}"
        )
    rx, tx = rx_matches[0], tx_matches[0]
    if rx is tx:
        raise TransportSafetyError("NUS RX and TX resolved to the same object")

    for label, characteristic, expected_uuid in (
        ("RX", rx, NUS_RX_UUID),
        ("TX", tx, NUS_TX_UUID),
    ):
        if id(characteristic) not in {id(child) for child in children}:
            raise TransportSafetyError(f"Selected {label} is not a NUS child")
        if normalize_uuid(getattr(characteristic, "uuid", "")) != expected_uuid:
            raise TransportSafetyError(f"Selected {label} UUID changed during resolution")
        parent = getattr(characteristic, "service_uuid", None)
        if parent is not None and normalize_uuid(parent) != NUS_SERVICE_UUID:
            raise TransportSafetyError(f"Selected {label} reports non-NUS parent {parent}")
        if normalize_uuid(getattr(characteristic, "uuid", "")) in BLACKLISTED_CHARACTERISTIC_UUIDS:
            raise TransportSafetyError(f"Selected {label} is explicitly blacklisted")

    if "write-without-response" not in characteristic_properties(rx):
        raise TransportSafetyError("NUS RX lacks write-without-response")
    if "notify" not in characteristic_properties(tx):
        raise TransportSafetyError("NUS TX lacks notify")

    # This loop detects ambiguity only. It never selects an endpoint globally.
    for service in service_list:
        if service is nus:
            continue
        for characteristic in service_characteristics(service):
            uuid = normalize_uuid(getattr(characteristic, "uuid", ""))
            if uuid in {NUS_RX_UUID, NUS_TX_UUID}:
                raise TransportSafetyError(f"Duplicate endpoint UUID {uuid} exists outside NUS")

    dfu_services = tuple(
        service
        for service in service_list
        if normalize_uuid(getattr(service, "uuid", "")) in BLACKLISTED_SERVICE_UUIDS
    )
    dfu_children = tuple(
        characteristic
        for service in dfu_services
        for characteristic in service_characteristics(service)
    )
    if id(rx) in {id(item) for item in dfu_children} or id(tx) in {id(item) for item in dfu_children}:
        raise TransportSafetyError("A selected endpoint is also in the DFU subtree")
    selected_handles = {object_handle(rx), object_handle(tx)} - {None}
    dfu_handles = {object_handle(item) for item in dfu_children} - {None}
    if selected_handles & dfu_handles:
        raise TransportSafetyError("A selected endpoint handle collides with a DFU characteristic")

    return ResolvedNus(nus, rx, tx, dfu_services)


def verify_resolved_nus(resolved: ResolvedNus, services: Iterable[Any]) -> None:
    """Repeat all resolution and handle checks immediately before GATT writes."""
    repeated = strict_resolve_nus(tuple(services))
    if repeated.service is not resolved.service or repeated.rx is not resolved.rx or repeated.tx is not resolved.tx:
        raise TransportSafetyError("NUS endpoint objects changed during the session")


def _descriptor_dict(descriptor: Any) -> dict[str, Any]:
    return {
        "uuid": normalize_uuid(getattr(descriptor, "uuid", "")),
        "handle": object_handle(descriptor),
    }


def _characteristic_dict(characteristic: Any, *, blacklisted: bool) -> dict[str, Any]:
    blacklisted = blacklisted or normalize_uuid(getattr(characteristic, "uuid", "")) in BLACKLISTED_CHARACTERISTIC_UUIDS
    return {
        "uuid": normalize_uuid(getattr(characteristic, "uuid", "")),
        "handle": object_handle(characteristic),
        "properties": sorted(characteristic_properties(characteristic)),
        "blacklisted": blacklisted,
        "descriptors": [
            {**_descriptor_dict(item), "blacklisted": blacklisted}
            for item in getattr(characteristic, "descriptors", ())
        ],
    }


def serialize_gatt(services: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for service in services:
        service_uuid = normalize_uuid(getattr(service, "uuid", ""))
        blacklisted = service_uuid in BLACKLISTED_SERVICE_UUIDS
        result.append(
            {
                "uuid": service_uuid,
                "handle": object_handle(service),
                "blacklisted": blacklisted,
                "characteristics": [
                    _characteristic_dict(item, blacklisted=blacklisted)
                    for item in service_characteristics(service)
                ],
            }
        )
    return result


def print_dfu_subtrees(services: Iterable[Any]) -> None:
    print("DFU subtree: BLACKLISTED")
    found = False
    for service in services:
        if normalize_uuid(getattr(service, "uuid", "")) != DFU_SERVICE_UUID:
            continue
        found = True
        print(f"  service {DFU_SERVICE_UUID}, handle {object_handle(service)}")
        for characteristic in service_characteristics(service):
            print(
                f"    characteristic {normalize_uuid(getattr(characteristic, 'uuid', ''))}, "
                f"handle {object_handle(characteristic)}, properties "
                f"{', '.join(sorted(characteristic_properties(characteristic)))} - BLACKLISTED"
            )
            for descriptor in getattr(characteristic, "descriptors", ()):
                print(
                    f"      descriptor {normalize_uuid(getattr(descriptor, 'uuid', ''))}, "
                    f"handle {object_handle(descriptor)} - BLACKLISTED"
                )
    if not found:
        print("  not present; UUID remains blacklisted")


def validate_phase4_command(command: Command) -> None:
    """Enforce the complete public high-level packet whitelist at transport time.

    The historical function name remains for compatibility with Phase 4 tests;
    Phase 5 adds only source-derived RTC, A5/19 period, and SeaLed curve frames.
    The destructive auto-settings clear frame remains prohibited.
    """
    packet = command.packet
    if len(packet) < 8 or packet[0] != 0x5A or packet[1] != 0x01:
        if len(packet) < 8 or packet[0] != 0xA5 or packet[1] != 0x01:
            raise TransportSafetyError("Packet is outside the public LED command families")
    if packet[2] != len(packet) - 2:
        raise TransportSafetyError("Packet length field is invalid")
    if calculate_checksum(packet[:-1]) != packet[-1]:
        raise TransportSafetyError("Packet checksum is invalid")
    mode = packet[5]
    parameters = packet[6:-1]
    allowed = False
    if packet[0] == 0x5A and mode == 0x04:
        allowed = parameters == bytes([0x01])
    elif packet[0] == 0x5A and mode == 0x05:
        allowed = parameters in {
            bytes([0x0B, 0xFF, 0xFF]),
            bytes([0x12, 0xFF, 0xFF]),
            bytes([0x22, 0xFF, 0xFF]),
            bytes([0x23, 0xFF, 0xFF]),
        }
    elif packet[0] == 0x5A and mode == 0x07 and len(parameters) == 2:
        target, level = parameters
        allowed = (
            (target in {0, 1, 2} and level <= 100)
            or (target == 0xFF and level <= FAN_MANUAL_SPEED_MAX)
        )
    elif packet[0] == 0x5A and mode == 0x06 and len(parameters) == 4:
        channel, hour, minute, level = parameters
        allowed = (
            channel in {0, 1, 2}
            and minute <= 59
            and hour * 60 + minute <= 2880
            and level <= 100
        )
    elif packet[0] == 0x5A and mode == 0x06 and len(parameters) == 3:
        channel, time_index, level = parameters
        allowed = channel in {0, 1, 2} and time_index <= 96 and level <= 100
    elif packet[0] == 0x5A and mode == 0x09 and len(parameters) == 6:
        year, month, weekday, hour, minute, second = parameters
        allowed = (
            month in range(1, 13)
            and weekday in range(1, 8)
            and hour <= 23
            and minute <= 59
            and second <= 59
            and year <= 0xFF
        )
    elif packet[0] == 0xA5 and mode == 0x19 and len(parameters) == 14:
        start_hour, start_minute, end_hour, end_minute, ramp, weekdays = parameters[:6]
        red, green, blue = parameters[6:9]
        allowed = (
            start_hour <= 23
            and start_minute <= 59
            and end_hour <= 23
            and end_minute <= 59
            and start_hour * 60 + start_minute < end_hour * 60 + end_minute
            and ramp <= 0xFF
            and 1 <= weekdays <= 0x7F
            and red <= 100
            and green <= 100
            and blue <= 100
            and parameters[9:] == bytes([0xFF] * 5)
        )
    elif packet[0] == 0xA5 and mode == 0x04:
        allowed = parameters in {bytes([0x06]), bytes([0x08])}
    elif packet[0] == 0xA5 and mode == 0x21 and len(parameters) == 3:
        start_temperature, max_temperature, trailing = parameters
        allowed = max_temperature > start_temperature and trailing == 0xFF
    if not allowed:
        raise TransportSafetyError(
            f"Packet 0x{packet[0]:02X}/0x{mode:02X} is outside the public command whitelist"
        )


class LocalSessionLog:
    """Durable local JSON event log; no account or credential fields exist."""

    def __init__(self, log_dir: Path, device: DeviceConfig, logical_command: str) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        safe_alias = re.sub(r"[^A-Za-z0-9_.-]+", "_", device.alias)
        safe_command = re.sub(r"[^A-Za-z0-9_.-]+", "_", logical_command)
        self.path = log_dir / f"{stamp}_{safe_alias}_{safe_command}.json"
        self._data: dict[str, Any] = {
            "started_at_utc": utc_now(),
            "application": {
                "name": "ChihirosLocalController",
                "application_version": WINDOWS_APP_VERSION,
                "controller_version": CONTROLLER_VERSION,
                "windows_version": platform.platform(),
            },
            "device": {
                "alias": device.alias,
                "model": device.model,
                "name": device.name,
                "address": device.address,
            },
            "logical_command": logical_command,
            "account_or_cloud_used": False,
            "events": [],
        }
        self._flush()

    def record(self, event: str, **details: Any) -> None:
        self._data["events"].append({"timestamp_utc": utc_now(), "event": event, **details})
        self._flush()

    def finish(self) -> None:
        self._data["finished_at_utc"] = utc_now()
        self._flush()

    def _flush(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)


async def scan_known_chihiros(
    seconds: float, *, model_detector: Callable[[str | None], str | None] = detect_model,
) -> list[ScanResult]:
    """Scan without connecting and return devices with source-known prefixes."""
    from bleak import BleakScanner

    discovered = await BleakScanner.discover(timeout=seconds, return_adv=True)
    results: list[ScanResult] = []
    for device, advertisement in discovered.values():
        name = getattr(advertisement, "local_name", None) or getattr(device, "name", None)
        model = model_detector(name)
        if model is None:
            continue
        results.append(
            ScanResult(
                name=name,
                address=str(device.address),
                model=model,
                rssi=getattr(advertisement, "rssi", None),
            )
        )
    return sorted(results, key=lambda item: item.rssi if item.rssi is not None else -9999, reverse=True)


class NusSession:
    """One short-lived, non-pairing, no-retry NUS connection."""

    def __init__(
        self,
        device: DeviceConfig,
        session_log: LocalSessionLog,
        *,
        scan_seconds: float = 10.0,
        connect_timeout: float = 15.0,
        model_detector: Callable[[str | None], str | None] = detect_model,
        subscribe_notifications: bool = True,
    ) -> None:
        self.device = device
        self.log = session_log
        self.scan_seconds = scan_seconds
        self.connect_timeout = connect_timeout
        self._model_detector = model_detector
        self._subscribe_notifications = subscribe_notifications
        self.client: Any | None = None
        self.resolved: ResolvedNus | None = None
        self.notifications: list[ParsedNotification] = []
        self._subscribed = False

    async def __aenter__(self) -> "NusSession":
        try:
            await self.connect()
            return self
        except BaseException as exc:
            self.log.record("error", error_type=type(exc).__name__, message=str(exc))
            await self.close()
            self.log.finish()
            raise

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> bool:
        if exc is not None:
            self.log.record("error", error_type=type(exc).__name__, message=str(exc))
        await self.close()
        self.log.finish()
        return False

    async def connect(self) -> None:
        from bleak import BleakClient, BleakScanner

        self.log.record("scan_started", seconds=self.scan_seconds)
        discovered = await BleakScanner.discover(timeout=self.scan_seconds, return_adv=True)
        matches: list[tuple[Any, Any]] = []
        for device, advertisement in discovered.values():
            if normalize_address(str(device.address)) == self.device.address:
                matches.append((device, advertisement))
        if len(matches) != 1:
            raise TransportSafetyError(
                f"Expected one advertisement for {self.device.address}; found {len(matches)}"
            )
        ble_device, advertisement = matches[0]
        found_name = getattr(advertisement, "local_name", None) or getattr(ble_device, "name", None)
        if found_name != self.device.name:
            raise TransportSafetyError(
                f"Configured address advertised as {found_name!r}, expected {self.device.name!r}"
            )
        found_model = self._model_detector(found_name)
        if found_model != self.device.model:
            raise TransportSafetyError(
                f"Advertisement resolves to {found_model!r}, configured model is {self.device.model!r}"
            )
        self.log.record("device_found", name=found_name, address=str(ble_device.address), model=found_model)

        self.client = BleakClient(ble_device, pair=False)
        self.log.record("connection_started", pairing=False)
        await self.client.connect(timeout=self.connect_timeout)
        if not self.client.is_connected:
            raise TransportSafetyError("BLE client did not establish a connection")
        self.log.record("connected")

        services = tuple(self.client.services)
        self.log.record("gatt_enumerated", services=serialize_gatt(services))
        print_dfu_subtrees(services)
        self.resolved = strict_resolve_nus(services)
        diagnostics = handle_diagnostics(self.resolved)
        self.log.record(
            "nus_resolved",
            service_uuid=NUS_SERVICE_UUID,
            service_handle=object_handle(self.resolved.service),
            rx_uuid=NUS_RX_UUID,
            rx_handle=object_handle(self.resolved.rx),
            tx_uuid=NUS_TX_UUID,
            tx_handle=object_handle(self.resolved.tx),
            handle_diagnostics=diagnostics,
        )
        print(
            f"NUS selected: service {NUS_SERVICE_UUID} handle {object_handle(self.resolved.service)}, "
            f"RX handle {object_handle(self.resolved.rx)}, TX handle {object_handle(self.resolved.tx)}"
        )
        for label, diagnostic in diagnostics.items():
            actual = diagnostic["actual"]
            reference = diagnostic["reference_from_original_lamp"]
            if actual is None:
                print(f"  {label} handle unavailable; original-lamp reference was {reference}")
            elif actual == reference:
                print(f"  {label} handle {actual} matches the original-lamp reference")
            else:
                print(
                    f"  {label} handle {actual} differs from original-lamp reference {reference}; "
                    "UUID/service topology remains authoritative"
                )
        verify_resolved_nus(self.resolved, tuple(self.client.services))
        if self._subscribe_notifications:
            await self.client.start_notify(self.resolved.tx, self._on_notification)
            self._subscribed = True
            self.log.record("notification_subscribed", uuid=NUS_TX_UUID, handle=object_handle(self.resolved.tx))

    def _on_notification(self, sender: Any, data: bytearray) -> None:
        raw = bytes(data)
        decoded = decode_notification(raw)
        self.notifications.append(decoded)
        self.log.record(
            "notification_received",
            uuid=normalize_uuid(getattr(sender, "uuid", "")),
            handle=object_handle(sender),
            raw_hex=raw.hex(" ").upper(),
            decoded=decoded_to_dict(decoded),
        )

    async def send(self, command: Command) -> None:
        if self.client is None or not self.client.is_connected or self.resolved is None:
            raise TransportSafetyError("Cannot write without an active validated NUS session")
        validate_phase4_command(command)
        verify_resolved_nus(self.resolved, tuple(self.client.services))
        self.log.record(
            "command_write_started",
            logical_command=command.logical_name,
            message_id=f"{command.packet[3]:02X}{command.packet[4]:02X}",
            packet_hex=command.packet_hex,
            write_target_uuid=NUS_RX_UUID,
            write_target_handle=object_handle(self.resolved.rx),
            response=False,
            changes_state=command.changes_state,
        )
        await self.client.write_gatt_char(self.resolved.rx, command.packet, response=False)
        self.log.record(
            "command_write_completed",
            logical_command=command.logical_name,
            packet_hex=command.packet_hex,
        )

    async def wait_for_notifications(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def close(self) -> None:
        if self.client is None:
            return
        if self._subscribed and self.resolved is not None and self.client.is_connected:
            try:
                await self.client.stop_notify(self.resolved.tx)
                self.log.record("notification_unsubscribed", uuid=NUS_TX_UUID)
            except Exception as exc:
                self.log.record("unsubscribe_error", error_type=type(exc).__name__, message=str(exc))
                print(f"Warning: NUS TX unsubscribe failed: {type(exc).__name__}: {exc}")
        self._subscribed = False
        if self.client.is_connected:
            try:
                await self.client.disconnect()
                self.log.record("disconnected")
            except Exception as exc:
                self.log.record("disconnect_error", error_type=type(exc).__name__, message=str(exc))
                print(f"Warning: disconnect failed: {type(exc).__name__}: {exc}")
