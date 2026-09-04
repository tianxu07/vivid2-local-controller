#!/usr/bin/env python3
"""Account-free local CLI for the Chihiros RGB Vivid II."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from chihiros.a2max import (
    format_a2max_manual_plan,
    format_a2max_status_dry_run,
    run_a2max_manual,
    run_a2max_status_probe,
    validate_a2max_level,
)
from chihiros.backup import BackupError, definition_from_backup, load_backup_document
from chihiros.constants import RGB_VIVID_II_MODEL
from chihiros.schedule import (
    SchedulePeriod,
    build_clock_sync_plan,
    build_schedule_period_plan,
    parse_time,
    parse_weekdays,
)
from chihiros.transport import (
    ConfigurationError,
    TransportSafetyError,
    configure_vivid2_device,
    load_devices,
    scan_known_chihiros,
)
from chihiros.vivid2 import (
    Vivid2Controller,
    build_auto_plan,
    build_manual_plan,
    build_off_plan,
    build_status_plan,
    format_plan,
)


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_DIR / "config" / "devices.json"
DEFAULT_LOG_DIR = PROJECT_DIR / "logs"
DEFAULT_BACKUP_DIR = PROJECT_DIR / "backups"
OFF_WARNING = "Manual OFF overrides automatic schedule until Auto mode is restored."


def rgb_value(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer from 0 through 100") from exc
    if not 0 <= value <= 100:
        raise argparse.ArgumentTypeError("must be an integer from 0 through 100")
    return value


def positive_seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return value


def a2max_wire_level(text: str) -> int:
    try:
        return validate_a2max_level(int(text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "A2 Max normalized wire level must be an integer from 1 through 100"
        ) from exc


def ramp_value(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer from 0 through 255") from exc
    if not 0 <= value <= 255:
        raise argparse.ArgumentTypeError("must be an integer from 0 through 255")
    return value


def local_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO local timestamp") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def add_execution_gate(parser: argparse.ArgumentParser) -> None:
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print exact packets without BLE activity")
    mode.add_argument("--live", action="store_true", help="explicitly request the high-level live operation")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fully local RGB Vivid II controller; no account, pairing/reset, firmware, raw-send, or DFU."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="local device JSON")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="local session log directory")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR, help="append-only schedule backups")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="scan for source-known Chihiros advertisements")
    scan.add_argument("--seconds", type=positive_seconds, default=10.0)
    scan.add_argument("--vivid2-only", action="store_true", help="show only verified RGB Vivid II prefixes")

    a2max_probe = subparsers.add_parser(
        "a2max-status-probe",
        help="identity-locked one-packet status compatibility probe for the confirmed A2 Max",
    )
    add_execution_gate(a2max_probe)
    a2max_probe.add_argument("--scan-seconds", type=positive_seconds, default=20.0)
    a2max_probe.add_argument("--connect-timeout", type=positive_seconds, default=30.0)
    a2max_probe.add_argument("--notification-seconds", type=positive_seconds, default=2.0)

    a2max_manual = subparsers.add_parser(
        "a2max-manual",
        help="manual brightness / normalized wire level for the exact locked A2 Max",
        allow_abbrev=False,
    )
    add_execution_gate(a2max_manual)
    a2max_manual.add_argument("--level", required=True, type=a2max_wire_level, metavar="1-100")
    a2max_manual.add_argument("--scan-seconds", type=positive_seconds, default=20.0)
    a2max_manual.add_argument("--connect-timeout", type=positive_seconds, default=30.0)

    configure = subparsers.add_parser("configure", help="store an explicitly selected Vivid II locally")
    configure.add_argument("device", help="local alias, for example vivid2")
    configure.add_argument("--name", required=True, help="exact advertised BLE name")
    configure.add_argument("--address", required=True, help="exact BLE address")
    configure.add_argument("--replace", action="store_true", help="replace this alias only")

    status = subparsers.add_parser("status", help="query runtime and the stored schedule snapshot")
    status.add_argument("device")
    status.add_argument("--verbose", action="store_true", help="include raw NUS TX hex")
    status.add_argument("--dry-run", action="store_true", help="print packet without BLE activity")

    manual = subparsers.add_parser("manual", help="enter manual mode and set all three channels")
    manual.add_argument("device")
    manual.add_argument("--red", required=True, type=rgb_value)
    manual.add_argument("--green", required=True, type=rgb_value)
    manual.add_argument("--blue", required=True, type=rgb_value)
    manual.add_argument("--dry-run", action="store_true", help="print packets without BLE activity")

    auto = subparsers.add_parser("auto", help="activate the existing stored auto schedule")
    auto.add_argument("device")
    auto.add_argument("--dry-run", action="store_true", help="print packet without BLE activity")

    off = subparsers.add_parser("off", help="manual mode with R=G=B=0")
    off.add_argument("device")
    off.add_argument("--dry-run", action="store_true", help="print packets without BLE activity")

    schedule = subparsers.add_parser("schedule", help="read, back up, or plan a Vivid II schedule")
    schedule.add_argument("device")
    schedule_actions = schedule.add_subparsers(dest="schedule_action", required=True)
    schedule_show = schedule_actions.add_parser("show", help="read and decode the stored schedule")
    schedule_show.add_argument("--raw", action="store_true", help="include complete response hex")
    schedule_show.add_argument("--dry-run", action="store_true", help="print status query without BLE activity")
    schedule_backup = schedule_actions.add_parser("backup", help="read and append an immutable local backup")
    schedule_backup.add_argument("--dry-run", action="store_true", help="show the read request without BLE activity")
    schedule_set = schedule_actions.add_parser("set", help="build one source-derived A5/19 period")
    schedule_set.add_argument("--start", required=True)
    schedule_set.add_argument("--end", required=True)
    schedule_set.add_argument("--ramp-minutes", required=True, type=ramp_value)
    schedule_set.add_argument("--red", required=True, type=rgb_value)
    schedule_set.add_argument("--green", required=True, type=rgb_value)
    schedule_set.add_argument("--blue", required=True, type=rgb_value)
    schedule_set.add_argument("--days", required=True, help="all or comma-separated weekday names")
    add_execution_gate(schedule_set)
    schedule_restore = schedule_actions.add_parser("restore", help="rebuild a typed schedule backup")
    schedule_restore.add_argument("backup", type=Path)
    add_execution_gate(schedule_restore)

    clock = subparsers.add_parser("clock", help="inspect RTC support or explicitly synchronize it")
    clock.add_argument("device")
    clock_actions = clock.add_subparsers(dest="clock_action", required=True)
    clock_actions.add_parser("show", help="report whether device time can be read reliably")
    clock_sync = clock_actions.add_parser("sync", help="explicitly set RTC from local Windows time")
    clock_sync.add_argument(
        "--at",
        type=local_timestamp,
        help="fixed local timestamp for reproducible dry-run only",
    )
    add_execution_gate(clock_sync)
    return parser


def _configured_controller(args: argparse.Namespace) -> Vivid2Controller:
    devices = load_devices(args.config.resolve())
    if args.device not in devices:
        aliases = ", ".join(sorted(devices))
        raise ConfigurationError(f"Unknown device alias {args.device!r}; configured aliases: {aliases}")
    return Vivid2Controller(devices[args.device], args.log_dir.resolve())


async def _run(args: argparse.Namespace) -> int:
    if args.command == "a2max-manual":
        if args.dry_run:
            print(format_a2max_manual_plan(args.level))
            print("\nDRY RUN: no scan, connection, subscription, read, or characteristic write occurred.")
            return 0
        log_path = await run_a2max_manual(
            args.log_dir.resolve(),
            level=args.level,
            scan_seconds=args.scan_seconds,
            connect_timeout=args.connect_timeout,
        )
        print(f"A2 Max manual brightness writes submitted for normalized wire level {args.level}.")
        print("Write completion is not a device acknowledgement; verify the physical result.")
        print("Official-app UI percentage equivalence is not universally established.")
        print("Stored schedule and RTC data were not written.")
        print(f"Session log: {log_path}")
        return 0

    if args.command == "a2max-status-probe":
        print(format_a2max_status_dry_run())
        if args.dry_run:
            print("\nDRY RUN: no scan, connection, subscription, or characteristic write occurred.")
            return 0
        notifications, log_path = await run_a2max_status_probe(
            args.log_dir.resolve(),
            scan_seconds=args.scan_seconds,
            connect_timeout=args.connect_timeout,
            notification_seconds=args.notification_seconds,
        )
        print(f"Status-only probe completed; received {len(notifications)} NUS TX notification(s).")
        print(f"Session log: {log_path}")
        return 0

    if args.command == "scan":
        results = await scan_known_chihiros(args.seconds)
        if args.vivid2_only:
            results = [item for item in results if item.model == RGB_VIVID_II_MODEL]
        if not results:
            print("No advertisements with source-known Chihiros prefixes were found.")
            return 0
        print("Known Chihiros advertisements:")
        for result in results:
            rssi = f"{result.rssi} dBm" if result.rssi is not None else "unavailable"
            print(f"  {result.name} | {result.address} | {result.model} | RSSI {rssi}")
            if result.model == RGB_VIVID_II_MODEL:
                print(
                    "    configure with: python chihirosctl.py configure <alias> "
                    f"--name {result.name} --address {result.address}"
                )
        return 0

    if args.command == "configure":
        device = configure_vivid2_device(
            args.config.resolve(),
            args.device,
            args.name,
            args.address,
            replace=args.replace,
        )
        print(
            f"Configured {device.alias}: {device.name} | {device.address} | {device.model}\n"
            "No Bluetooth connection was made. Commands remain locked to this explicit identity."
        )
        return 0

    controller = _configured_controller(args)
    if args.command == "status":
        if args.dry_run:
            print(format_plan("Status dry-run", build_status_plan()))
            return 0
        report, log_path = await controller.status(verbose=args.verbose)
        print(report)
        print(f"Session log: {log_path}")
        return 0

    if args.command == "manual":
        plan = build_manual_plan(args.red, args.green, args.blue)
        if args.dry_run:
            print(format_plan(f"Manual RGB dry-run: R={args.red} G={args.green} B={args.blue}", plan))
            return 0
        log_path = await controller.manual(args.red, args.green, args.blue)
        print(f"Manual RGB applied: R={args.red} G={args.green} B={args.blue}")
        print("Stored schedule was not changed.")
        print(f"Session log: {log_path}")
        return 0

    if args.command == "auto":
        if args.dry_run:
            print(format_plan("Auto-mode dry-run", build_auto_plan()))
            return 0
        log_path = await controller.auto()
        print("Existing stored auto schedule activated; RTC and schedule data were not written.")
        print(f"Session log: {log_path}")
        return 0

    if args.command == "off":
        print(f"Warning: {OFF_WARNING}")
        if args.dry_run:
            print(format_plan("Manual-off dry-run", build_off_plan()))
            return 0
        log_path = await controller.off()
        print("Manual RGB 0/0/0 applied; stored schedule was not changed.")
        print(f"Session log: {log_path}")
        return 0
    if args.command == "schedule":
        if args.schedule_action == "show":
            if args.dry_run:
                print(format_plan("Schedule-read dry-run", build_status_plan()))
                return 0
            report, log_path = await controller.status(verbose=args.raw)
            print(report)
            print(f"Session log: {log_path}")
            return 0
        if args.schedule_action == "backup":
            if args.dry_run:
                print(format_plan("Schedule-backup read dry-run", build_status_plan()))
                print(f"Append-only destination: {args.backup_dir.resolve() / args.device}")
                return 0
            backup_path, report, _document, log_path = await controller.backup_schedule(
                args.backup_dir.resolve()
            )
            print(report)
            print(f"Backup created: {backup_path}")
            print(f"Session log: {log_path}")
            return 0
        if args.schedule_action == "set":
            period = SchedulePeriod(
                parse_time(args.start),
                parse_time(args.end),
                args.ramp_minutes,
                args.red,
                args.green,
                args.blue,
                parse_weekdays(args.days),
            )
            plan = build_schedule_period_plan(period)
            print("Proposed schedule definition:")
            print(f"  {period.to_dict()}")
            print(format_plan("Schedule-period packet plan", plan))
            if args.dry_run:
                print(
                    "Live schedule writing remains fail-closed: the current 5B/FE readback does not expose "
                    "enough source-established metadata for a lossless automatic restore."
                )
                return 0
            backup_path, current, document, log_path = await controller.backup_schedule(
                args.backup_dir.resolve()
            )
            print("Current schedule read before any proposed write:")
            print(current)
            print(f"Automatic backup created: {backup_path}")
            print(f"Backup session log: {log_path}")
            rebuild = document.get("rebuild")
            if not isinstance(rebuild, dict) or rebuild.get("supported") is not True:
                print(
                    "SCHEDULE WRITE BLOCKED BEFORE A5/19: the automatic backup cannot yet reconstruct "
                    "weekday and per-channel metadata. No schedule packet was transmitted."
                )
                return 3
            raise TransportSafetyError("Schedule live write is not enabled pending reviewed round-trip evidence")
        if args.schedule_action == "restore":
            document = load_backup_document(args.backup.resolve())
            period = definition_from_backup(document, controller.device)
            plan = build_schedule_period_plan(period)
            print(format_plan("Typed backup restoration plan", plan))
            if args.dry_run:
                return 0
            current_backup, current, _current_document, log_path = await controller.backup_schedule(
                args.backup_dir.resolve()
            )
            print(current)
            print(f"Current schedule safeguarded at: {current_backup}")
            print(f"Backup session log: {log_path}")
            print(
                "RESTORE WRITE BLOCKED: current device metadata is not yet losslessly reconstructable; "
                "no schedule packet was transmitted."
            )
            return 3
    if args.command == "clock":
        if args.clock_action == "show":
            print(
                "Device RTC: Unknown. The pinned protocol provides a write-only 5A/09 setter and no "
                "source-established time-read response. No Bluetooth connection was made."
            )
            return 0
        if args.clock_action == "sync":
            if args.live and args.at is not None:
                raise ValueError("--at is for reproducible dry-runs only; live sync always uses current local time")
            timestamp = args.at or datetime.now().astimezone().replace(tzinfo=None)
            plan = build_clock_sync_plan(timestamp)
            print(format_plan("Explicit RTC synchronization", plan))
            if args.dry_run:
                return 0
            log_path = await controller.sync_clock(timestamp)
            print(f"RTC synchronization command sent for local time {timestamp.isoformat(timespec='seconds')}")
            print(f"Session log: {log_path}")
            return 0
    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("Interrupted; the BLE session cleanup path was requested.", file=sys.stderr)
        return 130
    except (BackupError, ConfigurationError, TransportSafetyError, ValueError, OSError) as exc:
        detail = f"{type(exc).__name__}: {exc!r}" if args.command == "a2max-manual" else str(exc)
        print(f"Error: {detail}", file=sys.stderr)
        return 2
    except ModuleNotFoundError as exc:
        if exc.name == "bleak":
            print("Error: Bleak is not installed; run: python -m pip install -r requirements.txt", file=sys.stderr)
            return 2
        raise
    except Exception as exc:
        detail = repr(exc) if args.command == "a2max-manual" else str(exc)
        print(f"Error: {type(exc).__name__}: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
