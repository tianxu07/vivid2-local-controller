"""Append-only local schedule backups."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import CONTROLLER_VERSION, RGB_VIVID_II_MODEL, UPSTREAM_COMMIT
from .schedule import SchedulePeriod
from .status import (
    ParsedNotification,
    RuntimeNotification,
    ScheduleSnapshotNotification,
    decoded_to_dict,
    schedule_notification,
)
from .transport import DeviceConfig


BACKUP_SCHEMA = "chihiros-local-schedule-backup-v1"


class BackupError(ValueError):
    pass


def create_backup_document(
    device: DeviceConfig,
    notifications: list[ParsedNotification],
    *,
    created_at: datetime | None = None,
    known_definition: SchedulePeriod | None = None,
) -> dict[str, Any]:
    snapshot = schedule_notification(notifications)
    if snapshot is None:
        raise BackupError("No schedule snapshot was received; backup was not created")
    timestamp = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    runtime_frames = [
        item.raw.hex(" ").upper()
        for item in notifications
        if isinstance(item, RuntimeNotification)
    ]
    schedule_frames = [
        item.raw.hex(" ").upper()
        for item in notifications
        if isinstance(item, ScheduleSnapshotNotification)
    ]
    definition = known_definition.to_dict() if known_definition is not None else None
    return {
        "schema": BACKUP_SCHEMA,
        "timestamp_utc": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "controller_version": CONTROLLER_VERSION,
        "protocol_source_commit": UPSTREAM_COMMIT,
        "device": {
            "alias": device.alias,
            "advertised_name": device.name,
            "address": device.address,
            "detected_model": device.model,
        },
        "raw": {
            "runtime_status_responses": runtime_frames,
            "schedule_snapshot_responses": schedule_frames,
            "all_notifications": [item.raw.hex(" ").upper() for item in notifications],
        },
        "decoded": {
            "notifications": [decoded_to_dict(item) for item in notifications],
            "schedule": decoded_to_dict(snapshot),
        },
        "rebuild": {
            "supported": definition is not None,
            "schedule_definition": definition,
            "reason": (
                None
                if definition is not None
                else "The 5B/FE snapshot exposes common curve points but not source-established "
                "weekday, per-channel RGB, and complete A5/19 metadata. Raw packets are never replayed."
            ),
        },
    }


def save_backup_document(root: Path, device: DeviceConfig, document: dict[str, Any]) -> Path:
    directory = root / device.alias
    directory.mkdir(parents=True, exist_ok=True)
    stamp = str(document["timestamp_utc"]).replace(":", "-")
    candidate = directory / f"{stamp}.json"
    suffix = 1
    while True:
        try:
            with candidate.open("x", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            return candidate
        except FileExistsError:
            candidate = directory / f"{stamp}_{suffix}.json"
            suffix += 1


def load_backup_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackupError(f"Backup not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BackupError(f"Invalid backup JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema") != BACKUP_SCHEMA:
        raise BackupError("Unsupported schedule backup schema")
    return document


def definition_from_backup(document: dict[str, Any], device: DeviceConfig) -> SchedulePeriod:
    backup_device = document.get("device")
    if not isinstance(backup_device, dict):
        raise BackupError("Backup has no device identity")
    if backup_device.get("advertised_name") != device.name or backup_device.get("address") != device.address:
        raise BackupError("Backup device identity does not match the configured target")
    if (
        backup_device.get("detected_model") != RGB_VIVID_II_MODEL
        or device.model != RGB_VIVID_II_MODEL
    ):
        raise BackupError("Backup and target must both identify as RGB Vivid II")
    if document.get("protocol_source_commit") != UPSTREAM_COMMIT:
        raise BackupError("Backup protocol source commit does not match this controller")
    rebuild = document.get("rebuild")
    if not isinstance(rebuild, dict) or rebuild.get("supported") is not True:
        reason = rebuild.get("reason") if isinstance(rebuild, dict) else None
        raise BackupError(f"Backup is not safely rebuildable: {reason or 'complete decoded metadata is absent'}")
    definition = rebuild.get("schedule_definition")
    if not isinstance(definition, dict):
        raise BackupError("Backup rebuild definition is missing")
    try:
        return SchedulePeriod.from_dict(definition)
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError(f"Invalid backup rebuild definition: {exc}") from exc


__all__ = [
    "BACKUP_SCHEMA",
    "BackupError",
    "create_backup_document",
    "definition_from_backup",
    "load_backup_document",
    "save_backup_document",
]
