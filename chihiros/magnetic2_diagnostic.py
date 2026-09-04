"""Exact-unit GATT, status and fixed WRGB research; no GUI integration.

Device identity is supplied at runtime. No prefix implies model compatibility.
Reuses the controller's service-scoped NUS resolver and local event logger.
"""

from __future__ import annotations

import argparse
import asyncio
import math
from pathlib import Path
import sys
from typing import Any

from .constants import DFU_SERVICE_UUID, NUS_SERVICE_UUID, UPSTREAM_ROOT
from .protocol import (
    calculate_checksum, create_command_encoding, create_manual_mode, create_status_query,
)
from .transport import (
    DeviceConfig,
    LocalSessionLog,
    TransportSafetyError,
    normalize_address,
    serialize_gatt,
    strict_resolve_nus,
    validate_address,
    verify_resolved_nus,
)


STATUS_PACKET = bytes.fromhex("5A 01 06 00 02 04 01 00")
CHANNEL0_PACKETS = (
    bytes.fromhex("5A 01 08 00 02 05 0B FF FF 05"),
    bytes.fromhex("5A 01 07 00 03 07 00 14 16"),
)
ISOLATED_TEST_CHANNELS = {
    "isolated-channel1-test": 1,
    "isolated-channel2-test": 2,
    "isolated-channel3-test": 3,
}
ISOLATED_CLEAR_PACKETS = (
    CHANNEL0_PACKETS[0],
    bytes.fromhex("5A 01 07 00 03 07 00 00 02"),
    bytes.fromhex("5A 01 07 00 04 07 01 00 04"),
    bytes.fromhex("5A 01 07 00 05 07 02 00 06"),
    bytes.fromhex("5A 01 07 00 06 07 03 00 04"),
)
CHANNEL_TEST_CHANNELS = {
    "channel0-test": 0,
    "channel1-test": 1,
    "channel2-test": 2,
    "channel3-test": 3,
    **ISOLATED_TEST_CHANNELS,
}
WRGB_COMBINED_OPERATION = "wrgb-combined-test"
WRGB_COMBINED_LEVELS = ((0, 20), (1, 40), (2, 60), (3, 30))
WRGB_COMBINED_PACKETS = (
    CHANNEL0_PACKETS[0],
    bytes.fromhex("5A 01 07 00 03 07 00 14 16"),
    bytes.fromhex("5A 01 07 00 04 07 01 28 2C"),
    bytes.fromhex("5A 01 07 00 05 07 02 3C 3A"),
    bytes.fromhex("5A 01 07 00 06 07 03 1E 1A"),
)
CHANNEL_TEST_PACKETS = {
    "channel0-test": CHANNEL0_PACKETS,
    "channel1-test": (CHANNEL0_PACKETS[0], bytes.fromhex("5A 01 07 00 03 07 01 14 17")),
    "channel2-test": (CHANNEL0_PACKETS[0], bytes.fromhex("5A 01 07 00 03 07 02 14 14")),
    "channel3-test": (CHANNEL0_PACKETS[0], bytes.fromhex("5A 01 07 00 03 07 03 14 15")),
    "isolated-channel1-test": ISOLATED_CLEAR_PACKETS + (bytes.fromhex("5A 01 07 00 07 07 01 14 13"),),
    "isolated-channel2-test": ISOLATED_CLEAR_PACKETS + (bytes.fromhex("5A 01 07 00 07 07 02 14 10"),),
    "isolated-channel3-test": ISOLATED_CLEAR_PACKETS + (bytes.fromhex("5A 01 07 00 07 07 03 14 11"),),
    WRGB_COMBINED_OPERATION: WRGB_COMBINED_PACKETS,
}
CHANNEL_TEST_GAP_SECONDS = 0.030
LOCAL_LOG_DIR = Path(__file__).resolve().parents[1] / "logs" / "magnetic2"


def validate_status_packet(packet: bytes) -> None:
    """Accept only the reviewed packet, including its message ID and checksum."""
    if packet != STATUS_PACKET:
        raise TransportSafetyError("Only the fixed status query is permitted")
    if packet[2] != len(packet) - 2:
        raise TransportSafetyError("Invalid status frame length")
    if calculate_checksum(packet[:-1]) != packet[-1]:
        raise TransportSafetyError("Invalid status frame checksum")


def build_status_packet() -> bytes:
    packet = create_status_query((0, 2))
    validate_status_packet(packet)
    return packet


def validate_channel_plan(operation: str, packets: tuple[bytes, ...]) -> None:
    """Bind the complete ordered packet allowlist to one named experiment.

    Isolated plans include literal zero levels on all four channels. A builder
    that clamps zero, omits a clear, or changes any packet is rejected before BLE.
    """
    if operation not in CHANNEL_TEST_PACKETS or packets != CHANNEL_TEST_PACKETS[operation]:
        raise TransportSafetyError("Only the selected fixed manual/channel packet plan is permitted")
    for packet in packets:
        if packet[2] != len(packet) - 2:
            raise TransportSafetyError("Invalid channel experiment frame length")
        if calculate_checksum(packet[:-1]) != packet[-1]:
            raise TransportSafetyError("Invalid channel experiment frame checksum")


def build_channel_plan(operation: str) -> tuple[bytes, ...]:
    """Build one named experiment; no caller-selected level or arbitrary channel."""
    if operation not in CHANNEL_TEST_PACKETS:
        raise TransportSafetyError("Unknown channel experiment")
    packets = (create_manual_mode((0, 2)),)
    if operation == WRGB_COMBINED_OPERATION:
        packets += tuple(
            create_command_encoding(0x5A, 0x07, (0, channel + 3), [channel, level])
            for channel, level in WRGB_COMBINED_LEVELS
        )
        validate_channel_plan(operation, packets)
        return packets
    if operation in ISOLATED_TEST_CHANNELS:
        # Use the same generic encoder as the existing channel tests. It
        # preserves 0; no A2 Max adapter or min-nonzero clamp is involved.
        packets += tuple(
            create_command_encoding(0x5A, 0x07, (0, channel + 3), [channel, 0])
            for channel in range(4)
        )
        target_message_id = (0, 7)
    else:
        target_message_id = (0, 3)
    packets += (create_command_encoding(
        0x5A, 0x07, target_message_id, [CHANNEL_TEST_CHANNELS[operation], 20]
    ),)
    validate_channel_plan(operation, packets)
    return packets


def validate_channel0_plan(packets: tuple[bytes, ...]) -> None:
    """Preserve the original channel-0 helper and its exact packet allowlist."""
    validate_channel_plan("channel0-test", packets)


def build_channel0_plan() -> tuple[bytes, bytes]:
    return build_channel_plan("channel0-test")


def print_channel_plan(operation: str, packets: tuple[bytes, ...]) -> None:
    validate_channel_plan(operation, packets)
    channel = CHANNEL_TEST_CHANNELS.get(operation)
    combined = operation == WRGB_COMBINED_OPERATION
    print("Pinned generic builders: " + UPSTREAM_ROOT + "/src/chihiros_led_control/commands.py")
    print("User-confirmed on the verified unit: 0=Red, 1=Green, 2=Blue, 3=White; zero turns a channel off.")
    print("The legacy manual + 5A/07 path works on that unit.")
    print("Wire level 20 matched apparent output of another Magnetic II at 20% in the user's comparison.")
    print("Use the direct 0..100 convention; this does not establish photometric equivalence or linearity.")
    isolated = operation in ISOLATED_TEST_CHANNELS
    print(f"Selected experiment: {operation}; {len(packets)} application writes.")
    labels = ["Enter manual mode"]
    if combined:
        labels.extend(f"Set {color}/channel {number} to wire level {level}"
                      for color, (number, level) in zip(("Red", "Green", "Blue", "White"), WRGB_COMBINED_LEVELS))
    elif isolated:
        labels.extend(f"Clear channel {number} to wire level 0" for number in range(4))
        print("Zero check: all four clearing levels remain wire byte 00; no clamp or scaling.")
    if not combined:
        labels.append(f"Set channel {channel} to wire level 20")
    for label, packet in zip(labels, packets):
        checksum_terms = " XOR ".join(f"{value:02X}" for value in packet[1:-1])
        print(f"{label}: {packet.hex(' ').upper()}")
        print(f"  Length: {packet[2]:02X} = {len(packet)} - 2; "
              f"checksum: {checksum_terms} = {packet[-1]:02X} (valid)")
    print(f"Exactly {len(packets)} application writes, 30 ms apart, then disconnect; no retries or restoration.")
    print("No reads, subscriptions, status query or application writes outside this plan.")
    print("Manual mode affects the device operating mode; other output may follow stored manual levels.")
    print("The experiment leaves manual mode selected; no automatic return to the schedule.")
    if isolated:
        print("All four channels receive zero before only the target receives 20; verify visible isolation.")
    elif not combined:
        print("Existing levels on other channels are not cleared; observe the incremental color change.")


def runtime_identity(address: str, name: str) -> DeviceConfig:
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise ValueError("Supply the exact non-empty advertised name")
    return DeviceConfig(
        "magnetic2-research", "Magnetic Light II (user-identified unit)",
        name, validate_address(address),
    )


def validate_options(operation: str, scan_seconds: float, connect_timeout: float,
                     notification_seconds: float) -> None:
    if operation not in {"gatt", "status", *CHANNEL_TEST_PACKETS}:
        raise ValueError("Operation must be gatt, status or a named channel experiment")
    for label, value, maximum in (
        ("scan seconds", scan_seconds, 60),
        ("connection timeout", connect_timeout, 60),
        ("notification seconds", notification_seconds, 10),
    ):
        if not math.isfinite(value) or not 0 < value <= maximum:
            raise ValueError(f"{label} must be finite, greater than zero and <= {maximum}")


def print_status_evidence() -> None:
    packet = build_status_packet()
    print("Pinned builder: " + UPSTREAM_ROOT + "/src/chihiros_led_control/commands.py#L15-L17")
    print("The builder names 5A/04/[01] as the app's getDeviceInfo/auth/status query.")
    print("Its payload contains no lighting, mode, clock or schedule setting.")
    print("Magnetic Light II semantics remain a compatibility experiment.")
    print("Exact status packet: " + packet.hex(" ").upper())
    print("Checksum: 01 XOR 06 XOR 00 XOR 02 XOR 04 XOR 01 = 00")
    print("At most one application write; no retries, startup commands or restoration commands.")


def print_topology(inventory: list[dict[str, Any]]) -> None:
    print("GATT inventory (handles are diagnostics only):")
    for service in inventory:
        label = " [BLACKLISTED SUBTREE]" if service["blacklisted"] else ""
        print(f"Service {service['uuid']} handle={service['handle']}{label}")
        for characteristic in service["characteristics"]:
            label = " [BLACKLISTED]" if characteristic["blacklisted"] else ""
            print(f"  Characteristic {characteristic['uuid']} handle={characteristic['handle']} "
                  f"properties={','.join(characteristic['properties'])}{label}")
            for descriptor in characteristic["descriptors"]:
                print(f"    Descriptor {descriptor['uuid']} handle={descriptor['handle']}")
    print(f"NUS present: {any(s['uuid'] == NUS_SERVICE_UUID for s in inventory)}")
    print(f"FE59 present: {any(s['uuid'] == DFU_SERVICE_UUID for s in inventory)}; permanently blacklisted")


def verify_target(client: Any, ble_device: Any, advertisement: Any,
                  identity: DeviceConfig) -> None:
    if not client.is_connected:
        raise TransportSafetyError("Exact-unit connection is not active")
    for address in (ble_device.address, client.address):
        if normalize_address(str(address)) != identity.address:
            raise TransportSafetyError("Connected target differs from runtime address")
    found_name = getattr(advertisement, "local_name", None) or getattr(ble_device, "name", None)
    if found_name != identity.name:
        raise TransportSafetyError("Target name differs from runtime advertised name")


async def run_diagnostic(
    operation: str, *, address: str, name: str,
    scan_seconds: float = 20, connect_timeout: float = 30,
    notification_seconds: float = 5, log_dir: Path = LOCAL_LOG_DIR,
) -> Path:
    """One exact-address connection. GATT mode never subscribes or writes.

    Status mode revalidates topology and identity before TX subscription and
    its only RX write. Cleanup never sends an application command.
    Channel tests send only their fixed single-channel, isolated or combined plan,
    without subscribing, and revalidate the exact target and NUS before every write.
    """
    identity = runtime_identity(address, name)
    validate_options(operation, scan_seconds, connect_timeout, notification_seconds)
    packet = build_status_packet()
    channel_plan = build_channel_plan(operation) if operation in CHANNEL_TEST_PACKETS else None
    from bleak import BleakClient, BleakScanner

    log = LocalSessionLog(log_dir, identity, f"magnetic2_{operation}")
    print(f"Private local log: {log.path}", flush=True)
    client: Any = None
    resolved = None
    subscribed = False
    write_attempted = False
    application_write_attempts = 0
    write_completed = False
    notification_count = 0
    try:
        log.record("diagnostic_policy", operation=operation, pairing=False,
                   arbitrary_reads=False, dfu_subtree_blacklisted=True,
                   application_write_limit=len(channel_plan) if channel_plan else int(operation == "status"),
                   permitted_packet_hex=packet.hex(" ").upper() if operation == "status" else None,
                   retries=0, notification_seconds=notification_seconds if operation == "status" else 0)
        log.record("scan_started", seconds=scan_seconds)
        discovered = await BleakScanner.discover(timeout=scan_seconds, return_adv=True)
        matches = [(device, adv) for device, adv in discovered.values()
                   if normalize_address(str(device.address)) == identity.address]
        if len(matches) != 1:
            raise TransportSafetyError(f"Expected one exact-address advertisement; found {len(matches)}")
        ble_device, advertisement = matches[0]
        found_name = getattr(advertisement, "local_name", None) or getattr(ble_device, "name", None)
        if found_name != identity.name:
            raise TransportSafetyError("Exact-address advertisement did not match runtime name")
        log.record("exact_identity_confirmed", address=identity.address, name=found_name)
        print("Exact runtime address and name matched; connecting without pairing.", flush=True)
        client = BleakClient(ble_device, pair=False, timeout=connect_timeout)
        await client.connect()
        verify_target(client, ble_device, advertisement, identity)
        services = tuple(client.services)
        inventory = serialize_gatt(services)
        log.record("gatt_enumerated", services=inventory)
        print_topology(inventory)
        resolved = strict_resolve_nus(services)
        verify_resolved_nus(resolved, services)
        log.record("nus_validated", service_uuid=NUS_SERVICE_UUID)
        print("Service-scoped NUS topology validated.", flush=True)
        if operation == "gatt":
            log.record("gatt_only_completed")
            return log.path

        if channel_plan is not None:
            channel = CHANNEL_TEST_CHANNELS.get(operation)
            isolated = operation in ISOLATED_TEST_CHANNELS
            combined = operation == WRGB_COMBINED_OPERATION
            event_prefix = ("wrgb_combined" if combined else
                            f"isolated_channel{channel}" if isolated else f"channel{channel}")
            print_channel_plan(operation, channel_plan)
            log.record(f"{event_prefix}_plan", packets_hex=[p.hex(" ").upper() for p in channel_plan],
                       operation=operation, clear_channels=[0, 1, 2, 3] if isolated else [],
                       channel=channel, wire_level=None if combined else 20,
                       channel_writes=[{"channel": p[6], "wire_level": p[7]} for p in channel_plan[1:]],
                       channel_mapping_confirmed_on_test_unit=True,
                       level_convention="direct_0_100", practical_level20_comparison_confirmed=True,
                       photometric_scaling_verified=False, inter_write_seconds=CHANNEL_TEST_GAP_SECONDS)
            for index, control_packet in enumerate(channel_plan, start=1):
                if index > 1:
                    await asyncio.sleep(CHANNEL_TEST_GAP_SECONDS)
                verify_target(client, ble_device, advertisement, identity)
                verify_resolved_nus(resolved, tuple(client.services))
                validate_channel_plan(operation, channel_plan)
                # Record before BLE; an uncertain/failed write aborts the plan.
                log.record(f"{event_prefix}_write_attempted", index=index,
                           packet_hex=control_packet.hex(" ").upper(), response=False)
                application_write_attempts += 1
                print(f"Sending experiment packet {index}/{len(channel_plan)}: {control_packet.hex(' ').upper()}", flush=True)
                await client.write_gatt_char(resolved.rx, control_packet, response=False)
                log.record(f"{event_prefix}_write_completed", index=index)
            write_completed = True
            return log.path

        print_status_evidence()

        def on_notification(sender: Any, data: bytearray) -> None:
            nonlocal notification_count
            # Bleak callbacks supply the characteristic object registered below.
            # Unknown senders are retained as an anomaly, never parsed as NUS TX.
            if sender is not resolved.tx:
                log.record("unexpected_notification_sender")
                return
            notification_count += 1
            raw_hex = bytes(data).hex(" ").upper()
            log.record("nus_tx_notification", index=notification_count, raw_hex=raw_hex,
                       length=len(data), after_write_attempt=write_attempted)
            print(f"NUS TX [{notification_count}] {raw_hex}", flush=True)

        verify_target(client, ble_device, advertisement, identity)
        verify_resolved_nus(resolved, tuple(client.services))
        await client.start_notify(resolved.tx, on_notification)
        subscribed = True
        log.record("nus_tx_subscribed")
        verify_target(client, ble_device, advertisement, identity)
        verify_resolved_nus(resolved, tuple(client.services))
        validate_status_packet(packet)
        # Persist the attempt before touching BLE. Failure is never retried.
        log.record("status_write_attempted", packet_hex=packet.hex(" ").upper(), response=False)
        write_attempted = True
        application_write_attempts += 1
        print("Sending the one allowed status query.", flush=True)
        await client.write_gatt_char(resolved.rx, packet, response=False)
        write_completed = True
        log.record("status_write_completed")
        await asyncio.sleep(notification_seconds)
        log.record("notification_window_completed", seconds=notification_seconds)
        return log.path
    except BaseException as exc:
        log.record("diagnostic_error", error_type=type(exc).__name__, message=str(exc))
        raise
    finally:
        if client is not None:
            try:
                if subscribed and client.is_connected:
                    # Never stop a subscription through a changed/ambiguous endpoint.
                    verify_target(client, ble_device, advertisement, identity)
                    verify_resolved_nus(resolved, tuple(client.services))
                    await client.stop_notify(resolved.tx)
                    log.record("nus_tx_unsubscribed")
            except Exception as exc:
                log.record("unsubscribe_error", message=str(exc))
                print(f"Unsubscribe failed; disconnecting: {exc}", flush=True)
            finally:
                try:
                    await client.disconnect()
                    log.record("disconnected")
                except Exception as exc:
                    log.record("disconnect_error", message=str(exc))
                    print(f"Disconnect error: {exc}", flush=True)
        log.record("diagnostic_summary", application_write_attempts=application_write_attempts,
                   application_write_completed=write_completed, notification_count=notification_count)
        log.finish()
        print(f"Application write attempts: {application_write_attempts}; "
              f"write completed: {write_completed}; NUS notifications: {notification_count}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("operation", choices=("gatt", "status", *CHANNEL_TEST_PACKETS),
                        help="Fixed channel/isolated experiments; wrgb-combined-test: manual + R20/G40/B60/W30")
    parser.add_argument("--address", required=True, help="Exact physically identified BLE address")
    parser.add_argument("--name", required=True, help="Exact advertised name; runtime-only private data")
    gate = parser.add_mutually_exclusive_group(required=True)
    gate.add_argument("--live", action="store_true")
    gate.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scan-seconds", type=float, default=20)
    parser.add_argument("--connect-timeout", type=float, default=30)
    parser.add_argument("--notification-seconds", type=float, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runtime_identity(args.address, args.name)
        validate_options(args.operation, args.scan_seconds, args.connect_timeout, args.notification_seconds)
        build_status_packet()
        channel_plan = build_channel_plan(args.operation) if args.operation in CHANNEL_TEST_PACKETS else None
        if args.dry_run:
            print("Dry run: no Bluetooth access. Identity must be supplied at runtime.")
            if args.operation == "status":
                print_status_evidence()
            elif channel_plan is not None:
                print_channel_plan(args.operation, channel_plan)
            else:
                print("GATT inventory only; no reads, subscriptions or application writes.")
            return 0
        asyncio.run(run_diagnostic(args.operation, address=args.address, name=args.name,
                                  scan_seconds=args.scan_seconds, connect_timeout=args.connect_timeout,
                                  notification_seconds=args.notification_seconds))
        return 0
    except KeyboardInterrupt:
        print("Interrupted; no application retry or restoration is attempted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Diagnostic stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
