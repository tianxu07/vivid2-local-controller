"""Identity-locked status and manual-brightness control for one A2 Max.

This exact-unit CLI remains separate from the development GUI adapter in
a2max_controller.py. Both reuse a2max_protocol.py. Every live operation has its
own narrow packet allowlist; there is deliberately no raw-command surface.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from .constants import (
    BATCH_WRITE_DELAY_SECONDS,
    BLACKLISTED_CHARACTERISTIC_UUIDS,
    BLACKLISTED_SERVICE_UUIDS,
    DFU_SERVICE_UUID,
    NUS_RX_UUID,
    NUS_SERVICE_UUID,
    NUS_TX_UUID,
    UPSTREAM_ROOT,
)
from .protocol import (
    Command,
    MessageIdSequence,
    RESERVED_BYTE,
    calculate_checksum,
    create_status_query,
)
from .a2max_protocol import (
    build_a2max_manual_plan, validate_a2max_level, validate_a2max_manual_plan,
)
from .status import decode_notification, decoded_to_dict
from .transport import (
    DeviceConfig,
    LocalSessionLog,
    ResolvedNus,
    TransportSafetyError,
    normalize_address,
    normalize_uuid,
    object_handle,
    print_dfu_subtrees,
    serialize_gatt,
    service_characteristics,
    strict_resolve_nus,
    verify_resolved_nus,
    validate_address,
)


# Private exact-unit CLI configuration is never imported by the public GUI.
PRIVATE_PROBE_CONFIG = Path(__file__).resolve().parents[1] / "config" / "a2max-private-unit.json"


@lru_cache(maxsize=1)
def _private_probe_identity() -> tuple[str, str]:
    """Load one private identity once per process; absent/invalid config fails closed."""
    try:
        value = json.loads(PRIVATE_PROBE_CONFIG.read_text(encoding="utf-8"))
        name, address = value["name"], validate_address(value["address"])
        if not isinstance(name, str) or not name.startswith("DYNCMC"):
            raise ValueError("Invalid private A2 Max name")
        return name, address
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError("The private A2 Max CLI requires valid local config/a2max-private-unit.json") from exc

A2_MAX_STATUS_QUERY_SOURCE = (
    f"{UPSTREAM_ROOT}/src/chihiros_led_control/commands.py#L15-L17"
)
A2_MAX_STATUS_CLIENT_SOURCE = (
    f"{UPSTREAM_ROOT}/src/chihiros_led_control/client.py#L306-L310"
)
A2_MAX_STATUS_RESPONSE_SOURCE = f"{UPSTREAM_ROOT}/docs/protocol.md#runtime-and-status-responses"
A2_MAX_STATUS_WAIT_SECONDS = 2.0
A2_MAX_STATUS_PACKET = bytes.fromhex("5A 01 06 00 02 04 01 00")

A2_MAX_BRIGHTNESS_BUILDER_SOURCE = (
    f"{UPSTREAM_ROOT}/src/chihiros_led_control/commands.py#L56-L58"
)
A2_MAX_MANUAL_BUILDER_SOURCE = (
    f"{UPSTREAM_ROOT}/src/chihiros_led_control/commands.py#L172-L174"
)
A2_MAX_MANUAL_CALL_SITE_SOURCE = (
    f"{UPSTREAM_ROOT}/src/chihiros_led_control/client.py#L245-L261"
)
A2_MAX_MODEL_SOURCE = f"{UPSTREAM_ROOT}/src/chihiros_led_control/models.py"
A2_MAX_SCALING_SOURCE = f"{UPSTREAM_ROOT}/docs/protocol.md#max-brightness-per-channel"
A2_MAX_RESPONSE_VERSION_SOURCE = f"{UPSTREAM_ROOT}/docs/protocol.md#runtime-and-status-responses"
# User-observed on the locked unit only, without BLE/app action during the
# physical power cycle. This is not a claim of UI scaling or all-level testing.
A2_MAX_VERIFIED_PERSISTENT_WIRE_LEVEL = 20


def build_a2max_status_command() -> Command:
    """Build the one packet permitted by the A2 Max compatibility probe."""
    sequence = MessageIdSequence()
    command = sequence.build(
        "A2 Max generic runtime/status compatibility query",
        create_status_query,
        changes_state=False,
    )
    validate_a2max_status_command(command)
    return command


def validate_a2max_status_command(command: Command) -> None:
    """Reject every packet except the fixed 5A/04/[01] status query."""
    if command.changes_state:
        raise TransportSafetyError("A2 Max status probe cannot send a state-changing command")
    if command.packet != A2_MAX_STATUS_PACKET:
        raise TransportSafetyError("Packet is not the fixed A2 Max status-only probe packet")
    if calculate_checksum(command.packet[:-1]) != command.packet[-1]:
        raise TransportSafetyError("A2 Max status probe checksum is invalid")


def checksum_equation(packet: bytes = A2_MAX_STATUS_PACKET) -> str:
    """Render the XOR inputs used by the protocol checksum."""
    return " XOR ".join(f"{value:02X}" for value in packet[1:-1])




def format_a2max_manual_plan(level: int) -> str:
    """Describe exact generated packets for both dry-run and pre-live review."""
    manual, brightness = build_a2max_manual_plan(level)
    A2_MAX_STATUS_PROBE_NAME, A2_MAX_STATUS_PROBE_ADDRESS = _private_probe_identity()
    return "\n".join(
        (
            "A2 Max manual brightness / normalized wire level",
            "Rendering this plan does not load Bleak or access Bluetooth.",
            f"Exact identity lock: {A2_MAX_STATUS_PROBE_NAME} / {A2_MAX_STATUS_PROBE_ADDRESS}",
            "Candidate-prefix scope: DYNCMC is based on one confirmed physical A2 Max only.",
            f"Requested normalized wire level: {level} (literal byte {level:02X}); white channel 0.",
            "This is not a universal claim of equivalence to the official-app UI percentage.",
            f"User-confirmed on this unit: manual wire level {A2_MAX_VERIFIED_PERSISTENT_WIRE_LEVEL}",
            "survived physical power loss without any BLE command or official-app action.",
            "Other wire levels remain controlled hardware tests, not established measurements.",
            "NewA2Led scaling: max(1, floor(level * 100 / max_level[channel])).",
            "The pinned A II model entry recognizes only DYNA2/DYNA2N, not DYNCMC.",
            "No inspected registry maps DYNCMC to NewA2Led or supplies its max_level, so the",
            "DYNCMC UI-to-wire mapping remains unresolved.",
            "The observed 5B response byte 0x16 is firmware/protocol version 22, not a model-class",
            "tag, and no inspected source maps version 22 to either brightness path.",
            "Only --level 1..100 is accepted; no address override, channel choice or raw packet input.",
            "Fresh transaction IDs start at 0002; reserved ID/checksum values are skipped offline.",
            (
                "CAUTION: literal wire level 90 (0x5A) is hardware-unverified; it is NOT rewritten to 89."
                if level == RESERVED_BYTE
                else "Payload levels are sent literally, without scaling or silent substitution."
            ),
            "",
            "Execution order (one attempt each; no retries):",
            f"1. Manual-mode packet: {manual.packet_hex}",
            f"   Checksum: {checksum_equation(manual.packet)} = {manual.packet[-1]:02X}",
            f"2. White channel 0, normalized wire level {level}: {brightness.packet_hex}",
            f"   Checksum: {checksum_equation(brightness.packet)} = {brightness.packet[-1]:02X}",
            f"Inter-packet pacing: {BATCH_WRITE_DELAY_SECONDS:.2f} seconds.",
            "",
            f"Write target for both: NUS RX {NUS_RX_UUID}, resolved only as a direct child of {NUS_SERVICE_UUID}",
            f"NUS topology check also requires TX {NUS_TX_UUID} under the same service; no subscription is made.",
            f"Permanently blacklisted: FE59 service {DFU_SERVICE_UUID} and its entire subtree",
            "Handles are diagnostic only and are never used to select an endpoint.",
            "",
            f"Upstream brightness builder: {A2_MAX_BRIGHTNESS_BUILDER_SOURCE}",
            f"Upstream manual-mode builder: {A2_MAX_MANUAL_BUILDER_SOURCE}",
            f"Upstream manual-before-slider call site: {A2_MAX_MANUAL_CALL_SITE_SOURCE}",
            f"Upstream one-white-channel A II model entry: {A2_MAX_MODEL_SOURCE}",
            f"Vendor-path scaling analysis: {A2_MAX_SCALING_SOURCE}",
            f"0x5B response byte-1 interpretation: {A2_MAX_RESPONSE_VERSION_SOURCE}",
            "Manual mode is included because the source client sends it before brightness writes",
            "so a running auto schedule cannot immediately race the requested manual level.",
            "",
            "State effects: selects manual mode and requests a new white-channel output level.",
            "Stored schedule and RTC data are not written or erased. Manual mode overrides auto operation.",
            "Treat this as a persistent change; disconnecting or power cycling is not a rollback.",
            "Recovery: if unexpected, disconnect, use the official app to restore the previously desired",
            "manual level or re-enable the existing auto schedule. No automatic rollback is attempted",
            "because the pre-probe mode and level cannot be read reliably from the known responses.",
            "Excluded: status queries, auto-mode writes, RTC, schedule, power, firmware, pairing,",
            "rename, notification subscription, characteristic reads, raw commands, and retries.",
        )
    )


def format_a2max_status_dry_run() -> str:
    """Describe the exact identity, transport target, and packet without BLE."""
    command = build_a2max_status_command()
    A2_MAX_STATUS_PROBE_NAME, A2_MAX_STATUS_PROBE_ADDRESS = _private_probe_identity()
    return "\n".join(
        (
            "A2 Max status-only compatibility probe",
            "Rendering this plan does not load Bleak or access Bluetooth.",
            f"Exact identity lock: {A2_MAX_STATUS_PROBE_NAME} / {A2_MAX_STATUS_PROBE_ADDRESS}",
            "Candidate-prefix scope: DYNCMC is based on one confirmed physical A2 Max only.",
            "Observed result: this query is confirmed on this one physical A2 Max and returned",
            "the known 0x0A runtime/status and 0xFE snapshot modes.",
            "Evidence limit: status compatibility does not establish brightness encoding.",
            "Source-derived operation: family 5A, mode 04, parameters [01]",
            f"Message ID: {command.message_id[0]:02X} {command.message_id[1]:02X}",
            f"Exact packet: {command.packet_hex}",
            f"Checksum: {checksum_equation(command.packet)} = {command.packet[-1]:02X}",
            f"Write target: NUS RX {NUS_RX_UUID}, resolved only as a direct child of {NUS_SERVICE_UUID}",
            f"Notification target: NUS TX {NUS_TX_UUID}, resolved only as a direct child of {NUS_SERVICE_UUID}",
            f"Permanently blacklisted: FE59 service {DFU_SERVICE_UUID} and its entire subtree",
            f"Upstream query builder: {A2_MAX_STATUS_QUERY_SOURCE}",
            f"Upstream query call site: {A2_MAX_STATUS_CLIENT_SOURCE}",
            f"Upstream response notes: {A2_MAX_STATUS_RESPONSE_SOURCE}",
            "Confirmed response family on this unit: raw NUS TX 0x5B notifications with 0x0A",
            "runtime/status and 0xFE schedule-snapshot modes.",
            "Excluded: brightness, manual mode, auto mode, RTC, schedule, power, firmware,",
            "pairing, rename, raw command entry, retries, and every other application command.",
        )
    )


def _verify_probe_endpoints(resolved: ResolvedNus, services: tuple[Any, ...]) -> None:
    """Recheck service-scoped NUS identity and exclusion from all DFU objects."""
    verify_resolved_nus(resolved, services)
    if normalize_uuid(getattr(resolved.service, "uuid", "")) != NUS_SERVICE_UUID:
        raise TransportSafetyError("Selected service is not NUS")
    if normalize_uuid(getattr(resolved.rx, "uuid", "")) != NUS_RX_UUID:
        raise TransportSafetyError("Selected write target is not NUS RX")
    if normalize_uuid(getattr(resolved.tx, "uuid", "")) != NUS_TX_UUID:
        raise TransportSafetyError("Selected notification target is not NUS TX")

    dfu_objects = {
        id(item)
        for service in services
        if normalize_uuid(getattr(service, "uuid", "")) in BLACKLISTED_SERVICE_UUIDS
        for item in (service, *service_characteristics(service))
    }
    if id(resolved.service) in dfu_objects or id(resolved.rx) in dfu_objects or id(resolved.tx) in dfu_objects:
        raise TransportSafetyError("A selected status-probe endpoint belongs to the DFU subtree")
    if normalize_uuid(getattr(resolved.rx, "uuid", "")) in BLACKLISTED_CHARACTERISTIC_UUIDS:
        raise TransportSafetyError("Selected write endpoint is blacklisted")
    if normalize_uuid(getattr(resolved.tx, "uuid", "")) in BLACKLISTED_CHARACTERISTIC_UUIDS:
        raise TransportSafetyError("Selected notification endpoint is blacklisted")


async def run_a2max_status_probe(
    log_dir: Path,
    *,
    scan_seconds: float = 20.0,
    connect_timeout: float = 30.0,
    notification_seconds: float = A2_MAX_STATUS_WAIT_SECONDS,
) -> tuple[list[bytes], Path]:
    """Send exactly one status query to the one locked unit, then disconnect.

    Bleak is imported only inside this live entry point. There is no pairing,
    application prelude, retry, characteristic read, or non-status write.
    """
    from bleak import BleakClient, BleakScanner

    if scan_seconds <= 0 or connect_timeout <= 0 or notification_seconds <= 0:
        raise ValueError("Probe timeouts must be greater than zero")
    A2_MAX_STATUS_PROBE_NAME, A2_MAX_STATUS_PROBE_ADDRESS = _private_probe_identity()

    device_config = DeviceConfig(
        "a2max-status-probe",
        "A2 Max (one confirmed physical unit)",
        A2_MAX_STATUS_PROBE_NAME,
        A2_MAX_STATUS_PROBE_ADDRESS,
    )
    session_log = LocalSessionLog(log_dir, device_config, "status_only_probe")
    client: Any | None = None
    resolved: ResolvedNus | None = None
    subscribed = False
    notifications: list[bytes] = []

    try:
        session_log.record(
            "probe_policy",
            exact_name=A2_MAX_STATUS_PROBE_NAME,
            exact_address=A2_MAX_STATUS_PROBE_ADDRESS,
            permitted_packet_hex=A2_MAX_STATUS_PACKET.hex(" ").upper(),
            state_changing_commands_allowed=False,
            pairing=False,
            retries=0,
            dfu_service_uuid=DFU_SERVICE_UUID,
            dfu_subtree_blacklisted=True,
        )
        session_log.record("scan_started", seconds=scan_seconds)
        discovered = await BleakScanner.discover(timeout=scan_seconds, return_adv=True)
        matches: list[tuple[Any, Any]] = []
        for ble_device, advertisement in discovered.values():
            if normalize_address(str(ble_device.address)) == A2_MAX_STATUS_PROBE_ADDRESS:
                matches.append((ble_device, advertisement))
        if len(matches) != 1:
            raise TransportSafetyError(
                f"Expected exactly one advertisement for {A2_MAX_STATUS_PROBE_ADDRESS}; found {len(matches)}"
            )

        ble_device, advertisement = matches[0]
        found_name = getattr(advertisement, "local_name", None) or getattr(ble_device, "name", None)
        if found_name != A2_MAX_STATUS_PROBE_NAME:
            raise TransportSafetyError(
                f"Locked address advertised as {found_name!r}, expected {A2_MAX_STATUS_PROBE_NAME!r}"
            )
        session_log.record(
            "exact_identity_confirmed",
            name=found_name,
            address=normalize_address(str(ble_device.address)),
        )

        client = BleakClient(ble_device, pair=False, timeout=connect_timeout)
        session_log.record("connection_started", pairing=False)
        await client.connect()
        if not client.is_connected:
            raise TransportSafetyError("BLE client did not establish a connection")
        session_log.record("connected")

        services = tuple(client.services)
        session_log.record("gatt_enumerated", services=serialize_gatt(services))
        print_dfu_subtrees(services)
        resolved = strict_resolve_nus(services)
        _verify_probe_endpoints(resolved, services)
        session_log.record(
            "status_probe_endpoints_resolved",
            service_uuid=NUS_SERVICE_UUID,
            service_handle=object_handle(resolved.service),
            rx_uuid=NUS_RX_UUID,
            rx_handle=object_handle(resolved.rx),
            tx_uuid=NUS_TX_UUID,
            tx_handle=object_handle(resolved.tx),
            handles_are_diagnostic_only=True,
            dfu_subtree_selected=False,
        )
        print(
            f"NUS selected by service-scoped UUID: service {NUS_SERVICE_UUID}, "
            f"RX {NUS_RX_UUID}, TX {NUS_TX_UUID}"
        )
        print("GATT handles are logged for diagnostics only and are not identity checks.")

        def on_notification(sender: Any, data: bytearray) -> None:
            raw = bytes(data)
            notifications.append(raw)
            decoded = decoded_to_dict(decode_notification(raw))
            session_log.record(
                "status_notification_received",
                uuid=normalize_uuid(getattr(sender, "uuid", "")),
                handle=object_handle(sender),
                raw_hex=raw.hex(" ").upper(),
                tentative_source_derived_decode=decoded,
            )
            print(f"NUS TX notification: {raw.hex(' ').upper()}")

        _verify_probe_endpoints(resolved, tuple(client.services))
        await client.start_notify(resolved.tx, on_notification)
        subscribed = True
        session_log.record("notification_subscribed", uuid=NUS_TX_UUID, handle=object_handle(resolved.tx))

        command = build_a2max_status_command()
        validate_a2max_status_command(command)
        _verify_probe_endpoints(resolved, tuple(client.services))
        session_log.record(
            "status_query_write_started",
            packet_hex=command.packet_hex,
            write_target_uuid=NUS_RX_UUID,
            write_target_handle=object_handle(resolved.rx),
            response=False,
            changes_state=False,
        )
        await client.write_gatt_char(resolved.rx, command.packet, response=False)
        session_log.record("status_query_write_completed", packet_hex=command.packet_hex)
        await asyncio.sleep(notification_seconds)
        session_log.record("notification_wait_completed", count=len(notifications))
    except BaseException as exc:
        session_log.record("error", error_type=type(exc).__name__, message=str(exc))
        raise
    finally:
        if subscribed and resolved is not None and client is not None and client.is_connected:
            try:
                await client.stop_notify(resolved.tx)
                session_log.record("notification_unsubscribed", uuid=NUS_TX_UUID)
            except Exception as exc:
                session_log.record(
                    "unsubscribe_error", error_type=type(exc).__name__, message=str(exc)
                )
                print(f"Warning: NUS TX unsubscribe failed: {type(exc).__name__}: {exc}")
        if client is not None and client.is_connected:
            try:
                await client.disconnect()
                session_log.record("disconnected")
            except Exception as exc:
                session_log.record(
                    "disconnect_error", error_type=type(exc).__name__, message=str(exc)
                )
                print(f"Warning: disconnect failed: {type(exc).__name__}: {exc}")
        session_log.finish()

    return notifications, session_log.path


async def run_a2max_manual(
    log_dir: Path,
    *,
    level: int,
    scan_seconds: float = 20.0,
    connect_timeout: float = 30.0,
) -> Path:
    """Send only manual mode then channel-0 brightness to the locked A2 Max.

    Validate and print the exact plan before loading Bleak. The only control
    input is an integer normalized wire level, 1..100. No pairing, subscription,
    characteristic read, retry, auto restore, connection prelude or DFU access.
    """
    commands = build_a2max_manual_plan(level)
    validate_a2max_manual_plan(commands, level)
    if any(not math.isfinite(value) or value <= 0 for value in (scan_seconds, connect_timeout)):
        raise ValueError("A2 Max timeouts must be finite and greater than zero")
    print(format_a2max_manual_plan(level), flush=True)
    A2_MAX_STATUS_PROBE_NAME, A2_MAX_STATUS_PROBE_ADDRESS = _private_probe_identity()

    device_config = DeviceConfig(
        "a2max-manual",
        "A2 Max (one confirmed physical unit)",
        A2_MAX_STATUS_PROBE_NAME,
        A2_MAX_STATUS_PROBE_ADDRESS,
    )
    session_log: LocalSessionLog | None = None
    client: Any | None = None
    phase = "initializing-session-log"
    failure_phase: str | None = None
    writes_attempted = 0
    writes_completed = 0

    def packet_attempts() -> dict[str, Any]:
        return {
            "writes_attempted": writes_attempted,
            "writes_completed": writes_completed,
            "packet_attempt_state": (
                "zero-packets-attempted",
                "manual-mode-only-attempted",
                "both-packets-attempted",
            )[writes_attempted],
            "manual_mode_attempted": writes_attempted >= 1,
            "brightness_attempted": writes_attempted >= 2,
        }

    def print_exception(exc: BaseException, error_phase: str, *, label: str = "A2 Max exception") -> None:
        print(
            f"{label}\n"
            f"  phase: {error_phase}\n"
            f"  exception type: {type(exc).__module__}.{type(exc).__name__}\n"
            f"  repr(exception): {exc!r}\n"
            f"  str(exception): {str(exc)!r}",  # quotes make an empty str visible
            file=sys.stderr,
            flush=True,
        )

    def best_effort_record(event: str, **details: Any) -> None:
        # Error reporting must not mask the original exception or prevent
        # disconnect. Normal-operation log failures still abort before more BLE.
        if session_log is not None:
            try:
                session_log.record(event, **details)
            except Exception as log_exc:
                print_exception(log_exc, phase, label="Diagnostic log write failed")

    def set_phase(new_phase: str, *, cleanup: bool = False) -> None:
        nonlocal phase
        phase = new_phase
        print(f"A2 Max phase: {phase}", flush=True)
        if session_log is not None:
            if cleanup:
                best_effort_record("execution_phase", phase=phase, **packet_attempts())
            else:
                session_log.record("execution_phase", phase=phase, **packet_attempts())

    def report_exception(exc: BaseException, error_phase: str, *, event: str = "error") -> None:
        print_exception(exc, error_phase)
        best_effort_record(
            event,
            phase=error_phase,
            error_type=type(exc).__name__,
            exception_type=f"{type(exc).__module__}.{type(exc).__name__}",
            exception_repr=repr(exc),
            exception_str=str(exc),
            message=str(exc),
            **packet_attempts(),
        )

    try:
        set_phase("initializing-session-log")
        session_log = LocalSessionLog(log_dir, device_config, f"manual_wire_level_{level}")
        print(f"Diagnostic session log: {session_log.path}", flush=True)
        session_log.record(
            "manual_brightness_policy",
            exact_name=A2_MAX_STATUS_PROBE_NAME,
            exact_address=A2_MAX_STATUS_PROBE_ADDRESS,
            permitted_packets_hex=[command.packet_hex for command in commands],
            ui_percentage_claimed=False,
            normalized_wire_level=level,
            power_cycle_persistence_observed_on_locked_unit_at_level=A2_MAX_VERIFIED_PERSISTENT_WIRE_LEVEL,
            pairing=False,
            notifications=False,
            reads=False,
            retries=0,
            dfu_service_uuid=DFU_SERVICE_UUID,
            dfu_subtree_blacklisted=True,
        )
        set_phase("loading-Bleak")
        from bleak import BleakClient, BleakScanner

        set_phase("scanning")
        session_log.record("scan_started", seconds=scan_seconds)
        discovered = await BleakScanner.discover(timeout=scan_seconds, return_adv=True)
        matches: list[tuple[Any, Any]] = []
        for ble_device, advertisement in discovered.values():
            if normalize_address(str(ble_device.address)) == A2_MAX_STATUS_PROBE_ADDRESS:
                matches.append((ble_device, advertisement))
        if len(matches) != 1:
            raise TransportSafetyError(
                f"Expected exactly one advertisement for {A2_MAX_STATUS_PROBE_ADDRESS}; found {len(matches)}"
            )

        ble_device, advertisement = matches[0]
        found_name = getattr(advertisement, "local_name", None) or getattr(ble_device, "name", None)
        if found_name != A2_MAX_STATUS_PROBE_NAME:
            raise TransportSafetyError(
                f"Locked address advertised as {found_name!r}, expected {A2_MAX_STATUS_PROBE_NAME!r}"
            )
        set_phase("exact-device-found")
        session_log.record(
            "exact_identity_confirmed",
            name=found_name,
            address=normalize_address(str(ble_device.address)),
        )

        set_phase("connecting")
        client = BleakClient(ble_device, pair=False, timeout=connect_timeout)
        session_log.record("connection_started", pairing=False)
        await client.connect()
        if not client.is_connected:
            raise TransportSafetyError("BLE client did not establish a connection")
        set_phase("connected")
        session_log.record("connected")

        set_phase("resolving-NUS")
        services = tuple(client.services)
        session_log.record("gatt_enumerated", services=serialize_gatt(services))
        print_dfu_subtrees(services)
        resolved = strict_resolve_nus(services)
        _verify_probe_endpoints(resolved, services)
        session_log.record(
            "manual_brightness_endpoints_resolved",
            service_uuid=NUS_SERVICE_UUID,
            service_handle=object_handle(resolved.service),
            rx_uuid=NUS_RX_UUID,
            rx_handle=object_handle(resolved.rx),
            tx_uuid=NUS_TX_UUID,
            tx_handle=object_handle(resolved.tx),
            tx_required_for_topology_check_only=True,
            notifications_subscribed=False,
            handles_are_diagnostic_only=True,
            dfu_subtree_selected=False,
        )
        print(
            f"NUS selected by service-scoped UUID: service {NUS_SERVICE_UUID}, "
            f"RX {NUS_RX_UUID}; TX {NUS_TX_UUID} verified but not subscribed."
        )
        print("GATT handles are logged for diagnostics only and are not identity checks.")

        # Revalidate the complete allowlist before the first state-changing write.
        validate_a2max_manual_plan(commands, level)
        for index, command in enumerate(commands, start=1):
            if index > 1:
                set_phase("waiting-30ms")
                await asyncio.sleep(BATCH_WRITE_DELAY_SECONDS)
            set_phase("writing-manual-mode" if index == 1 else "writing-brightness")
            if not client.is_connected:
                raise TransportSafetyError("Cannot write without an active A2 Max NUS connection")
            _verify_probe_endpoints(resolved, tuple(client.services))
            session_log.record(
                "manual_brightness_write_started",
                sequence=index,
                logical_name=command.logical_name,
                packet_hex=command.packet_hex,
                write_target_uuid=NUS_RX_UUID,
                write_target_handle=object_handle(resolved.rx),
                response=False,
                changes_state=True,
            )
            writes_attempted += 1
            await client.write_gatt_char(resolved.rx, command.packet, response=False)
            writes_completed += 1
            set_phase("manual-mode-write-complete" if index == 1 else "brightness-write-complete")
            session_log.record(
                "manual_brightness_write_completed",
                sequence=index,
                packet_hex=command.packet_hex,
            )
        session_log.record("manual_brightness_completed", writes_completed=writes_completed)
    except BaseException as exc:
        failure_phase = phase  # Preserve the abort phase across disconnect/log cleanup.
        report_exception(exc, failure_phase)
        if writes_attempted:
            print(
                "Warning: a state-changing packet may have reached the device before the error. "
                "No retry or automatic restore will be attempted. "
                "Use the official app to restore the desired manual level or auto mode."
            )
        raise
    finally:
        try:
            if client is not None and client.is_connected:
                set_phase("disconnecting", cleanup=True)
                await client.disconnect()
                best_effort_record("disconnected")
        except BaseException as exc:
            failure_phase = failure_phase or phase
            report_exception(exc, phase, event="disconnect_error")
            if not isinstance(exc, Exception):
                # Report cancellation too, without swallowing it during cleanup.
                raise
        finally:
            attempts = packet_attempts()
            best_effort_record(
                "manual_diagnostic_summary",
                phase=phase,
                failure_phase=failure_phase,
                outcome="failed" if failure_phase is not None else "completed",
                **attempts,
            )
            print(
                f"Packet attempts: {attempts['packet_attempt_state']} ({writes_attempted}/2); "
                f"write calls completed: {writes_completed}/2.",
                flush=True,
            )
            if failure_phase is not None:
                print(f"A2 Max failure phase: {failure_phase}", flush=True)
            if session_log is not None:
                try:
                    session_log.finish()
                except Exception as exc:
                    print_exception(exc, "finalizing-session-log", label="Diagnostic log finalization failed")
                    print("Warning: the diagnostic log may be incomplete.", file=sys.stderr, flush=True)
                finally:
                    print(f"Session log: {session_log.path}", flush=True)
            else:
                print(f"Session log unavailable; requested directory: {log_dir}", file=sys.stderr, flush=True)

    return session_log.path


__all__ = [
    "A2_MAX_VERIFIED_PERSISTENT_WIRE_LEVEL",
    "A2_MAX_STATUS_PACKET",
    "build_a2max_manual_plan",
    "build_a2max_status_command",
    "format_a2max_manual_plan",
    "format_a2max_status_dry_run",
    "run_a2max_manual",
    "run_a2max_status_probe",
    "validate_a2max_level",
    "validate_a2max_manual_plan",
    "validate_a2max_status_command",
]
