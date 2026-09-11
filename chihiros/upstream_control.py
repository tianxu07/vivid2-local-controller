"""One-shot upstream operations with explicit transport and no write replay."""

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .constants import BATCH_WRITE_DELAY_SECONDS, UPSTREAM_COMMIT
from .transport import (DeviceConfig, LocalSessionLog, TransportSafetyError,
                        characteristic_properties, normalize_address, normalize_uuid,
                        service_characteristics, strict_resolve_nus, validate_address)
from .upstream_profiles import Transport, profile_for
from .upstream_protocol import Action, Request, Telemetry, build_plan, parse_telemetry


FFE0 = "0000ffe0-0000-1000-8000-00805f9b34fb"
FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"


def resolve_endpoint(services, transport):
    """Return service, write, optional notify; never search alternate transports."""
    services = tuple(services)
    if transport is Transport.NUS:
        nus = strict_resolve_nus(tuple(services))
        return nus.service, nus.rx, nus.tx
    if transport is not Transport.HM10:
        raise TransportSafetyError("No authorized transport")
    matches = [s for s in services if normalize_uuid(s.uuid) == FFE0]
    if len(matches) != 1:
        raise TransportSafetyError("Exactly one HM-10 FFE0 service is required")
    service = matches[0]
    children = [c for c in service_characteristics(service) if normalize_uuid(c.uuid) == FFE1]
    if len(children) != 1:
        raise TransportSafetyError("Exactly one FFE1 direct child is required")
    write = children[0]
    parent = getattr(write, "service_uuid", None)
    if parent is not None and normalize_uuid(parent) != FFE0:
        raise TransportSafetyError("FFE1 reports a different parent service")
    if "write-without-response" not in characteristic_properties(write):
        raise TransportSafetyError("FFE1 requires write-without-response")
    for other in services:
        if other is not service and any(normalize_uuid(c.uuid) == FFE1
                                        for c in service_characteristics(other)):
            raise TransportSafetyError("Ambiguous FFE1 outside the intended service")
    return service, write, None


class OperationFailed(TransportSafetyError):
    def __init__(self, attempted: int, path: Path):
        self.attempted = attempted
        self.path = path
        super().__init__("Device outcome uncertain; do not automatically retry."
                         if attempted else "Operation failed before any command was sent.")


@dataclass(frozen=True)
class Receipt:
    address: str
    request: Request
    telemetry: Telemetry | None
    log_path: Path


def validate_device(device):
    profile = profile_for(device.name) if isinstance(device, DeviceConfig) else None
    if (profile is None or device.model != profile.name or not isinstance(device.address, str)
            or device.address != validate_address(device.address)):
        raise TransportSafetyError("An explicit upstream model and canonical address are required")
    return profile


class UpstreamOperation:
    """Consume one typed request once, including after failure or cancellation.

All packet creation happens here before BLE access. No send(bytes) interface is
exposed. Each write rechecks identity and the original resolved endpoint objects.
    """

    def __init__(self, device: DeviceConfig, request: Request, log_dir: Path, *,
                 now=datetime.now, telemetry_seconds=4.0):
        self.profile = validate_device(device)
        self.device, self.request, self.log_dir = device, request, log_dir
        self.timestamp = now()
        self.plan = build_plan(self.profile, request, self.timestamp)
        if (isinstance(telemetry_seconds, bool) or not isinstance(telemetry_seconds, (int, float))
                or not math.isfinite(telemetry_seconds) or not 0 < telemetry_seconds <= 15):
            raise ValueError("Telemetry interval must be finite and within 0–15 seconds")
        self.telemetry_seconds = telemetry_seconds
        self._used = False

    async def run(self) -> Receipt:
        if self._used:
            raise TransportSafetyError("An upstream operation cannot be replayed")
        self._used = True
        profile = validate_device(self.device)
        device, request = self.device, self.request
        plan = build_plan(profile, request, self.timestamp)
        if profile is not self.profile or plan != self.plan:
            raise TransportSafetyError("Operation changed after validation")
        from bleak import BleakClient, BleakScanner

        log = LocalSessionLog(self.log_dir, device, "upstream_" + request.action.value)
        client = None
        endpoints = None
        subscribed = False
        attempted = 0
        telemetry = None
        notification_error = None
        event = asyncio.Event()

        def verify():
            if (client is None or not client.is_connected
                    or normalize_address(str(client.address)) != device.address):
                raise TransportSafetyError("Connection identity changed")
            repeated = resolve_endpoint(tuple(client.services), profile.transport)
            if endpoints is None or any(a is not b for a, b in zip(endpoints, repeated)):
                raise TransportSafetyError("GATT endpoint objects changed")

        def on_notification(sender, data):
            nonlocal telemetry, notification_error
            try:
                verify()
                if sender is not endpoints[2]:
                    raise TransportSafetyError("Notification did not originate from resolved TX")
                parsed = parse_telemetry(bytes(data))
                if parsed is not None:
                    telemetry = parsed
                    event.set()
            except Exception as exc:
                notification_error = exc
                event.set()

        try:
            log.record("plan", upstream_commit=UPSTREAM_COMMIT, transport=profile.transport.value,
                       requested=request.values, packets=[c.packet_hex for c in plan], retries=0)
            found = await asyncio.wait_for(BleakScanner.discover(timeout=8, return_adv=True), 12)
            matches = [(d, a) for d, a in found.values()
                       if normalize_address(str(d.address)) == device.address]
            if len(matches) != 1:
                raise TransportSafetyError("Exactly one selected-address advertisement is required")
            target, advertisement = matches[0]
            name = getattr(advertisement, "local_name", None) or getattr(target, "name", None)
            if name != device.name or profile_for(name) is not profile:
                raise TransportSafetyError("Selected address advertised a different identity")
            client = BleakClient(target, pair=False)
            await asyncio.wait_for(client.connect(timeout=20), 25)
            endpoints = resolve_endpoint(tuple(client.services), profile.transport)
            verify()
            if request.action is Action.TELEMETRY:
                # Set before awaiting: subscription failure may leave a partial CCCD setup.
                subscribed = True
                await asyncio.wait_for(client.start_notify(endpoints[2], on_notification), 10)
                await asyncio.wait_for(event.wait(), self.telemetry_seconds)
                if notification_error is not None:
                    raise notification_error
                verify()
            else:
                for index, command in enumerate(plan):
                    if index:
                        await asyncio.sleep(BATCH_WRITE_DELAY_SECONDS)
                    verify()
                    log.record("write_started", packet=command.packet_hex)
                    attempted += 1  # An exception after this point has an uncertain outcome.
                    await asyncio.wait_for(client.write_gatt_char(
                        endpoints[1], command.packet, response=False), 10)
                    verify()
                    log.record("write_completed", packet=command.packet_hex)
            return Receipt(device.address, request, telemetry, log.path)
        except asyncio.CancelledError:
            log.record("cancelled", attempted=attempted, outcome_uncertain=bool(attempted))
            raise
        except Exception as exc:
            log.record("failed", error_type=type(exc).__name__, detail=str(exc), attempted=attempted)
            raise OperationFailed(attempted, log.path) from exc
        finally:
            # Cleanup must run even if unsubscribe fails. Neither cleanup path writes commands.
            if client is not None:
                try:
                    if subscribed and client.is_connected:
                        await asyncio.wait_for(client.stop_notify(endpoints[2]), 5)
                except Exception as exc:
                    log.record("unsubscribe_failed", detail=str(exc))
                finally:
                    try:
                        if client.is_connected:
                            await asyncio.wait_for(client.disconnect(), 5)
                    except Exception as exc:
                        log.record("disconnect_failed", detail=str(exc))
            log.finish()
