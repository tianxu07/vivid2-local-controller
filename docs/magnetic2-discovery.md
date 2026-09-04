# Magnetic Light II: research and advertisement identification

Historical discovery record. Current supported GUI behavior is documented in
[Magnetic Light II integration](magnetic2.md) for 1.2.0.

Research date: 2026-09-03 (America/Los_Angeles).
Branch: `feature/magnetic2`, based on public v1.1.0 (`0a093b0`).
Initial stage: prepare physical identification; no Magnetic Light II control
support has been registered or enabled. This is the advertisement-research
record. The subsequent user-confirmed identification and live GATT/status
experiment are documented in [magnetic2-status-probe.md](magnetic2-status-probe.md).

## Evidence and unresolved questions

`DYMNC...` is the leading local candidate, not a confirmed model prefix.
Two existing local advertisement captures contain three distinct identifiers
with that prefix. A count of three does not establish that these are the three
Magnetic Light II units: the older Magnetic Light and nearby devices have not
been excluded by a controlled power cycle.

| Item | Evidence | What remains unknown |
| --- | --- | --- |
| Magnetic Light II / Magnetic Light 2 | Chihiros describes four adjustable white/red/green/blue channels. Its launch announcement specifies 8 RGB packages, 16 white LEDs, a fan, temperature probe, and 5 V / 3 A supply. | Advertisement prefix, app protocol class, wire channel order, brightness scaling, command payloads, response format, and GATT layout of the owned unit. |
| Older Magnetic Light | The official Terrarium Set page describes white plus RGB LEDs and two adjustable colors, naming red and green. This is product wording, not a demonstrated protocol mapping. | Actual wire controls, channel order, prefix, and relationship to the second generation. This phase will not test the older unit. |
| `DYMNC...` | Observed in local scans; absent from the inspected open-source registries and protocol notes. | Whether it identifies generation I, generation II, multiple generations, or another family. |
| `DYMIX...` / `DYMIXR...` | Open-source projects associate these with magnetic stirrers. | These supply no evidence for a magnetic-light model mapping. |
| Other advertisements | Retain every device, including unnamed entries and unknown `DY...` names. NUS alone is not a Chihiros model identifier. | A differently named unit may be the one that disappears and returns. |

Manufacturer sources:

- [Magnetic Light 2 launch announcement](https://bbs.chihirosaquaticstudio.com/threads/chihiros-magnetic-light-2.346/)
- [Chihiros support confirms four WRGB channels](https://bbs.chihirosaquaticstudio.com/threads/chihiroa-magnetic-light-2-%E2%80%93-settings-for-dry-start-uns-5u-dhg-carpet.10485/)
- [Older Magnetic Light Terrarium Set description](https://www.chihirosaquaticstudio.com/products/chihiros-magnetic-light-terrarium-set)

These establish product capabilities, not protocol compatibility. No universal
Magnetic Light II prefix, device class, or command allowlist is justified yet.

## Open-source inspection

Inspected the existing local research, scanner, model constants, protocol and
transport code, and the text source/documentation in these pinned repositories:

| Repository | Inspected revision | Magnetic-light evidence |
| --- | --- | --- |
| [TheMicDiet/chihiros-led-control](https://github.com/TheMicDiet/chihiros-led-control/tree/05ae7654b3d4de1df887be3827cd85dd33fe0af6) | `05ae7654b3d4de1df887be3827cd85dd33fe0af6` | Neither magnetic-light generation nor `DYMNC` is registered. The upstream `main` API resolved to this same commit during research. A test mentions `DYMIXR` as a stirrer. |
| [BartdeJonge/chihiros-esphome](https://github.com/BartdeJonge/chihiros-esphome/tree/74201fc99051e934f75b5bd9aad6c2020329548b) | `74201fc99051e934f75b5bd9aad6c2020329548b` | Magnetic references are to the stirrer, including `DYMIX`; no `DYMNC` mapping. |
| [d1rty-pixel/chihiros-ble-proxy](https://github.com/d1rty-pixel/chihiros-ble-proxy/tree/b1be4076423aca3acad01fd13b1a5468adc0cca5) | `b1be4076423aca3acad01fd13b1a5468adc0cca5` | `DYMIX` is a stirrer; no magnetic-light or `DYMNC` mapping. |
| [caleb-venner/AquaBle](https://github.com/caleb-venner/AquaBle/tree/43a31d61e18dbba64e968cc0f3f9d69e60a23f41) | `43a31d61e18dbba64e968cc0f3f9d69e60a23f41` | No magnetic-light or `DYMNC` mapping found. |

Searches for `DYMNC` and magnetic-light BLE implementations did not yield a
primary source establishing the prefix. This is a bounded negative finding,
not proof that no implementation exists elsewhere.

## Related protocol, not yet validated for Magnetic Light II

The pinned [protocol notes](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/docs/protocol.md)
describe frames as family, `01`, length, two-byte message ID, mode, payload,
and XOR checksum. Length is payload size plus five; XOR excludes the family
and checksum bytes. Common families include `5A`, `A5`, and `5F`.
Other supported LEDs use `5A/07` for channel/level commands. Their channel
numbering and level scale must not be transferred to Magnetic Light II.

The related status request is `5A/04` with parameter `01`, sent by writing
the command characteristic. It is not a GATT read and is excluded here.
Other LEDs can return `5B/0A` runtime data or `5B/FE` schedule data; the latter's
trailer is not fully established. No Magnetic Light II query, response layout,
or unsolicited-notification behavior has been confirmed.

The upstream registry distinguishes BleLed, NewBleLed, and SeaLed variants.
Neither WRGB marketing terminology nor the letters in `DYMNC` identify which
class this light uses. No executable packets or control examples are supplied.

## Existing architecture and safety boundary

Use the existing `scan_chihiros.py` in this development workspace. Its
`--scan-only` branch saves all advertisements and returns before selection or
connection. The GUI/shared known-model scanner filters unregistered prefixes,
so it is unsuitable for this initial identification.

Specify both `--no-notify` and `--notify-seconds 0` as well. This also makes the
saved safety metadata explicitly disallow notification CCCD changes. There
will be no connection, pairing, GATT reads/writes, subscriptions, endpoint
selection, status query, or control operation. BLE discovery may use ordinary
advertisement scan requests; it does not send Chihiros application commands.

For any later, separately scoped transport work, reuse
`chihiros.transport.strict_resolve_nus`: require one NUS service
`6e400001-b5a3-f393-e0a9-e50e24dcca9e`, with RX `6e400002...` and TX
`6e400003...` as direct children, valid properties, and no ambiguous duplicate
endpoints. FE59 and its entire DFU subtree remain permanently blacklisted.
Handles must never select endpoints. No pairing or legacy transport fallback
is introduced. The discovery below does not exercise any GATT service.

## One physical unit: ON, OFF, ON

Choose one lamp that its physical label/packaging identifies as Magnetic Light
II. Call it unit A for the notes. Keep the other lamps, including the older
Magnetic Light, in the same power state throughout. Keep the computer in the
same location, enable Windows Bluetooth, and close My Chihiros and the local
controller. Do not open the vendor app during the sequence.

OFF means disconnect unit A's individual power lead so its BLE electronics
lose power. Making its LEDs dark through an app is not the identification
procedure. Wait about ten seconds after each power change before scanning.

Run this setup once in PowerShell; it creates a fresh local capture directory:

```powershell
# Run from the repository root.
$magneticCaptureDir = Join-Path (Get-Location).Path ('captures\magnetic2_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $magneticCaptureDir | Out-Null
```

With unit A powered ON and settled, run:

```powershell
& .\.venv\Scripts\python.exe .\scan_chihiros.py --scan-only --no-notify --notify-seconds 0 --scan-seconds 20 --output (Join-Path $magneticCaptureDir '01_on.json')
```

Unplug only unit A, wait ten seconds, then run:

```powershell
& .\.venv\Scripts\python.exe .\scan_chihiros.py --scan-only --no-notify --notify-seconds 0 --scan-seconds 20 --output (Join-Path $magneticCaptureDir '02_off.json')
```

Reconnect unit A's power, wait ten seconds, then run:

```powershell
& .\.venv\Scripts\python.exe .\scan_chihiros.py --scan-only --no-notify --notify-seconds 0 --scan-seconds 20 --output (Join-Path $magneticCaptureDir '03_on_again.json')
```

Each invocation starts a fresh discovery. The scanner should print
`Scan-only mode: skipping connection and GATT inspection.` It can legitimately
label `DYMNC` as `not identified` or `possible transport match only`.
Do not remove the scan-only option to get a model label.

## Return and interpretation

Send the three JSON files, and identify which physical Magnetic Light II was
cycled. Include a label photo or the exact printed model designation when
available, plus any scan errors or unusual power/connection behavior.
The JSON files contain nearby BLE identifiers; keep the raw captures local
to this investigation, in the already ignored `captures/` directory.

For comparison, retain the full advertised name, address/identifier, RSSI,
service UUIDs, manufacturer data, service data, and timestamps in each phase.
If attachments are unavailable, send the complete device lists for all three
phases and the corresponding candidate advertisement records, not just the
strongest-RSSI entry or a screenshot of one name.

Check that each report has `connection.attempted = false`, no selected device,
empty GATT services/subscription attempts/events, disabled notification CCCD
changes, and no scan errors. Scan-only cannot reveal firmware, GATT topology,
channel order, or status responses.

A candidate must be present in both ON captures and absent from OFF, with a
consistent identifier/name and supporting advertisement data. RSSI and list
position are not identity. An empty OFF scan, multiple disappearing devices,
address changes, missing ON advertisements, or a persistent connection from a
phone/hub make the result inconclusive; repeat advertisement-only scans after
reviewing the cause. Do not resolve ambiguity by trying a command.

A clean transition confirms only unit A's advertisement identity. Record that
exact unit as physically identified, with generation established from the
physical product. It does not prove an exclusive or universal `DYMNC` mapping,
nor validate its control protocol. Stop here for capture review.

## Local validation

The existing 172 hardware-free tests passed with
`.\.venv\Scripts\python.exe -m unittest discover -s tests`.
The scanner's `--help` was checked without starting BLE discovery.
No runtime, GUI, model registry, release version, or existing test was changed.
No live scan, connection, or control command was run during this research.
