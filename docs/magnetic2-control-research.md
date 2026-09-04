# Magnetic Light II control research and combined WRGB experiment

Historical research phases below precede the source GUI integration in
[Magnetic Light II integration](magnetic2.md), version 1.2.0. Earlier
approval gates and registry restrictions describe their phase at that time.

Date: 2026-09-03 America/Los_Angeles. Branch: `feature/magnetic2`.
At this historical research phase, the public application version was v1.1.0.

The initial evidence review stopped without a control plan because the sources
do not establish Magnetic Light II channel IDs or input-to-wire scaling.
The user subsequently performed the prepared channel-0 experiment and reported
that both writes succeeded and the lamp visibly turned red. On that verified
unit, channel 0 = Red and the legacy manual-mode + `5A/07` channel path works.
After ambiguous non-isolated observations, the user completed isolated physical
tests and confirmed `0=Red, 1=Green, 2=Blue, 3=White`, with wire zero turning an
isolated channel off. A second Magnetic Light II at 20% through normal Chihiros
control had the same apparent output as the wire-level-20 isolated tests. The
controller convention is direct 0..100 levels; this practical comparison does
not establish laboratory-verified linearity or exact photometric equivalence.

The next prepared experiment is `wrgb-combined-test`: manual mode, then
R20/G40/B60/W30. It has not been executed. The user skipped the isolated-red60
scaling test; no such test was added or run in this phase. No BLE scan,
connection, subscription or write was performed in this preparation phase.
APK/public-source investigation remains stopped at the user's request.

The preceding [GATT/status experiment](magnetic2-status-probe.md) confirms NUS
and legacy status-query response behavior on one user-identified unit. Its
private address/name remain runtime-only. `DYMNC` is physically observed on
that unit, not registered as universally identifying Magnetic Light II.

## Sources inspected

Re-read the local controller's research, protocol, A2 Max adapter, diagnostic,
and prior observations. Inspected the sole registered Git worktree, including
ignored/hidden research paths. No APK, DEX, decompiled Dart/Smali/assembly,
vendor library, or standalone app model registry was found in the worktree.
The local notes describe upstream app analysis; they are not the underlying
decompiled application. No new APK was downloaded and no vendor app was run.

Re-inspected source/documentation archives at the existing pins, without
executing their code or changing dependencies:

| Source | Revision | Relevant result |
| --- | --- | --- |
| [TheMicDiet/chihiros-led-control](https://github.com/TheMicDiet/chihiros-led-control/tree/05ae7654b3d4de1df887be3827cd85dd33fe0af6) | `05ae7654b3d4de1df887be3827cd85dd33fe0af6` | 82 text source/configuration/documentation files searched. No Magnetic Light or `DYMNC` model mapping; the magnetic match is a `DYMIXR` stirrer test entry. |
| [BartdeJonge/chihiros-esphome](https://github.com/BartdeJonge/chihiros-esphome/tree/74201fc99051e934f75b5bd9aad6c2020329548b) | `74201fc99051e934f75b5bd9aad6c2020329548b` | 12 files searched. Magnetic references concern the stirrer. |
| [d1rty-pixel/chihiros-ble-proxy](https://github.com/d1rty-pixel/chihiros-ble-proxy/tree/b1be4076423aca3acad01fd13b1a5468adc0cca5) | `b1be4076423aca3acad01fd13b1a5468adc0cca5` | 6 files searched. `DYMIX` is a stirrer; no magnetic-light mapping. |
| [caleb-venner/AquaBle](https://github.com/caleb-venner/AquaBle/tree/43a31d61e18dbba64e968cc0f3f9d69e60a23f41) | `43a31d61e18dbba64e968cc0f3f9d69e60a23f41` | 42 files searched. No magnetic-light or `DYMNC` match. |

Search terms included `DYMNC`, other `DYMN` names, magnetic/magnet, and the
Chinese character for magnetism. Public issue/PR and web searches supplied
two additional primary reports:

- [Issue 9](https://github.com/TheMicDiet/chihiros-led-control/issues/9), dated
  March 11, 2024, reports Magnetic Light compatibility only in its title. It
  has no body or comments with a model prefix, channel map, commands, or levels.
  It predates the manufacturer's November 2024
  [second-generation announcement](https://bbs.chihirosaquaticstudio.com/threads/chihiros-magnetic-light-2.346/).
  It cannot establish Light II behavior.
- [Issue 52](https://github.com/TheMicDiet/chihiros-led-control/issues/52)
  concerns a flashing inline Bluetooth indicator. It supplies no WRGB mapping,
  scaling, or model-specific control path. The generation is not established.

This is a bounded evidence review, not a claim that no other research exists.

## Confirmed code evidence and its applicability

The following source facts refer to the pinned TheMicDiet implementation and
record the limits of the offline review. Physical confirmation is separate and
described below.

| Question | Source evidence | Offline research limit before physical control testing |
| --- | --- | --- |
| Manual mode | `create_switch_to_manual_mode_command` encodes `5A / 05 / [0B, FF, FF]`. | General LED command established; model-specific applicability and necessity unknown. |
| Channel write | `create_set_brightness_command` encodes `5A / 07 / [channel, level]`. | General LED command established; no `DYMNC` dispatch or override found. |
| Independent channels | Chihiros support describes four adjustable WRGB channels for Light II. | Four user-facing controls are expected; the wire mapping is not established by that description. |
| Channel order | `WRGB_CHANNELS` maps white to 3, red to 0, green to 1, blue to 2. Other registered models have different maps. | No model entry assigns this table to `DYMNC`. None of the four IDs is confirmed for this unit. |
| Input scaling | The upstream client accepts 0..100 and passes each value to the brightness builder without a `max_level` conversion. | This is that client's policy for its supported models, not evidence that Light II uses it. |
| Family and reserved bytes | The generic builder uses family `5A`, avoids message-ID/checksum byte `5A`, and by default substitutes payload byte `5A` with `59`. | Status framing is physically confirmed. The control-path family and payload-byte policy remain unverified. |
| App sequence | `set_brightness` queues manual mode followed by the requested channel writes. Its comment attributes that order to the vendor app. | No inspected Light II-specific widget/call site connects its controls to that sequence. |

Exact source locations:

- [Manual-mode builder](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/src/chihiros_led_control/commands.py#L181-L183)
- [Brightness builder](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/src/chihiros_led_control/commands.py#L56-L58)
- [Channel tables and registry](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/src/chihiros_led_control/models.py#L29-L43)
- [Input validation and manual transaction](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/src/chihiros_led_control/client.py#L197-L259)
- [Reserved-byte implementation](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/src/chihiros_led_control/protocol.py#L127-L163)
- [Manufacturer's four-channel description](https://bbs.chihirosaquaticstudio.com/threads/chihiroa-magnetic-light-2-%E2%80%93-settings-for-dry-start-uns-5u-dhg-carpet.10485/)

## App-analysis evidence is indirect here

The pinned [app-analysis notes](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/docs/protocol.md#max-brightness-per-channel)
report that BleLed/NewBleLed/SeaLed use a base manual setter that forwards the
level, whereas NewA2Led uses `max(1, floor(level * 100 / max_level[channel]))`.
Neither the Light II class nor its `max_level` metadata is identified.
Consequently, direct scaling, minimum nonzero clamps, and any other transforms
remain unknown for this model. The source also distinguishes app payload-byte
handling from the older reserved-byte convention; that distinction cannot be
resolved for Light II without its actual control path.

The local Vivid II brightness builder applies legacy reserved-byte handling
and supports channels 0..2. The A2 Max builder writes a literal normalized
level on its physically tested single channel, with separate message-ID and
checksum handling. Neither establishes Light II semantics. They remain
unchanged. The fixed experiment uses the shared legacy manual-mode builder and
generic frame encoder directly, with an independent exact-packet allowlist.

The upstream client also has a connection prelude containing a status request
and two time writes, plus retry logic. Its complete live client must not be
reused for this experiment. Any later implementation must continue using the
local service-scoped resolver and a narrowly allowed packet plan.
See [upstream connection prelude](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/src/chihiros_led_control/client.py#L707-L720).

## Inferences and remaining unknowns

The physical tests confirm transport, the manual/channel-write sequence and
all four channel IDs on the verified unit. The level-20 practical comparison
supports the direct 0..100 convention; exact physical scaling is not measured.
The unknown `0B`/`36` notifications do not identify
control channels or scaling and were not used to derive either.

The official-app chain remains unresolved; this investigation is paused:

```text
Light II / physically observed prefix
    -> app model record and device_type
    -> actual instantiated class / overrides
    -> named control-to-channel-ID mapping
    -> level conversion and packet builder
    -> manual-mode / slider call sequence
```

`R=0, G=1, B=2, W=3` is now physically confirmed on the verified unit. Wire level
20 is decimal 20 (`14` hex), sent unchanged. No app-specific max_level conversion
was inferred or added. The combined test passes 20/40/60/30 directly to the
existing generic builder; none of those values or packets needs reserved-byte
substitution. No universal prefix registration or older Magnetic Light support
is implied by these results.

## Channel-0 physical result

The user reported both application writes completed and the lamp visibly turned
red after `channel0-test`: manual `5A 01 08 00 02 05 0B FF FF 05`, then channel 0
`5A 01 07 00 03 07 00 14 16`. This establishes channel 0 = Red on this verified
unit and successful operation of that two-command sequence. It does not show
whether manual mode is strictly necessary, establish other channels or scaling,
or identify every nearby advertisement sharing the prefix.

## Previous non-isolated experiments

These original two-write operations retain their behavior. The user reported
ambiguous channel-2/3 observations; they do not establish a color mapping. The
isolated experiments below are the current proposed identification workflow.

The source-only `channel1-test`, `channel2-test`, and `channel3-test` operations
extend `chihiros.magnetic2_diagnostic`. The original `channel0-test` remains
available with identical wire packets. These are not public GUI features or
model entries. No channel, level, raw-packet, retry, delay or restoration options
are accepted. Each invocation accepts exactly one named operation.

Every run starts with the same legacy manual-mode packet, message ID `00 02`:
`5A 01 08 00 02 05 0B FF FF 05`. After approximately 30 ms, it sends only the
selected row below, message ID `00 03`, then disconnects. IDs restart for each
separate connection.

| Operation | Single channel write at wire level 20 | Original color hypothesis |
| --- | --- | --- |
| `channel1-test` | `5A 01 07 00 03 07 01 14 17` | Green |
| `channel2-test` | `5A 01 07 00 03 07 02 14 14` | Blue |
| `channel3-test` | `5A 01 07 00 03 07 03 14 15` | White |

The checksum excludes the family byte and XORs all remaining bytes before the
checksum:

```text
Manual:    01 XOR 08 XOR 00 XOR 02 XOR 05 XOR 0B XOR FF XOR FF = 05
Channel 1: 01 XOR 07 XOR 00 XOR 03 XOR 07 XOR 01 XOR 14       = 17
Channel 2: 01 XOR 07 XOR 00 XOR 03 XOR 07 XOR 02 XOR 14       = 14
Channel 3: 01 XOR 07 XOR 00 XOR 03 XOR 07 XOR 03 XOR 14       = 15
```

The length fields are `08 = 10 - 2` and `07 = 9 - 2`. All packets are checked
against the existing protocol implementation and operation-specific fixed
expected bytes. A valid packet for a different channel is rejected too.

Before any BLE access, the command validates runtime address/name and packet
allowlist. It scans for that exact address and requires the exact advertised
name before connecting without pairing. It enumerates metadata and resolves
only RX/TX objects belonging directly to the unique NUS service. The existing
resolver permanently excludes the FE59/DFU subtree. Handles are diagnostics,
never endpoint routing inputs. No arbitrary characteristic or descriptor read,
notification subscription, or status query occurs in this operation.

Both packets are printed and recorded in the ignored local log before the
first write. Identity, connection, topology and packet allowlist are rechecked
immediately before each write. Manual mode is followed by a 30 ms async pause,
then exactly one channel write, using NUS RX write-without-response. The
connection is then closed with no deliberate post-write wait. Local logging
and OS scheduling can add overhead to the 30 ms interval.

A failure or cancellation aborts the sequence, without retries or compensating
commands. No schedule, RTC, auto mode, power, rename, reset, firmware, DFU or
other channel command is sent. Manual mode is a device-wide mode change; it
could expose other stored manual channel values. Only one explicit channel
level is written, but this does not guarantee that only one visible component
changes. The experiment leaves manual mode selected. It does not automatically
restore the normal schedule.

Earlier channel levels are not cleared. For example, Red from the completed
channel-0 test may remain visible during the next test. Observe the incremental
change; do not assume each test produces a pure isolated color. No extra
channel-clearing command is included.

## Isolated experiments and physical result

The following records the prepared isolated sequences. The user subsequently
reported that these physical tests established the complete channel mapping
and that zero turns an isolated channel off. No exact private identity is
included here, and this report does not claim independent photometric testing.

Each of `isolated-channel1-test`, `isolated-channel2-test`, and
`isolated-channel3-test` allows exactly six ordered application writes. All
three start with these five packets:

```text
Manual  5A 01 08 00 02 05 0B FF FF 05
ch0=0   5A 01 07 00 03 07 00 00 02
ch1=0   5A 01 07 00 04 07 01 00 04
ch2=0   5A 01 07 00 05 07 02 00 06
ch3=0   5A 01 07 00 06 07 03 00 04
```

Only the sixth packet differs:

| Operation | Sixth and final packet |
| --- | --- |
| `isolated-channel1-test` | `5A 01 07 00 07 07 01 14 13` |
| `isolated-channel2-test` | `5A 01 07 00 07 07 02 14 10` |
| `isolated-channel3-test` | `5A 01 07 00 07 07 03 14 11` |

There is a 30 ms async pause between every pair of writes, including after the
last clear and before the target receives 20. Local logging and OS scheduling
can add overhead. The command disconnects after the sixth write with no
deliberate post-write wait. The complete six-packet plan is printed and saved
to the ignored local log before the first write. Identity, NUS endpoints and
the exact operation-specific plan are revalidated before every write.

The code reuses `create_manual_mode` and the same
`create_command_encoding(0x5A, 0x07, message_id, [channel, level])` path as the
previous channel experiments. Source inspection and offline execution confirm
that zero stays `00` at payload level offset 7 for all four channels. The
generic encoder only substitutes payload `5A` with `59`; it neither clamps
zero to one nor applies max_level scaling. This path does not call the A2 Max
adapter or any app-derived minimum-level conversion. The exact packet
allowlist rejects any builder that changes a clear value, including a
correctly checksummed packet with level 1 instead of zero, before BLE access.

Zero/off was the intended wire request at preparation time. The user has now
confirmed that wire zero turns an isolated channel off on the verified unit.

Checksums exclude the leading family byte and XOR through the last payload
byte. These are all the distinct packets used by the three isolated plans:

```text
Manual: 01 XOR 08 XOR 00 XOR 02 XOR 05 XOR 0B XOR FF XOR FF = 05
ch0=0:  01 XOR 07 XOR 00 XOR 03 XOR 07 XOR 00 XOR 00       = 02
ch1=0:  01 XOR 07 XOR 00 XOR 04 XOR 07 XOR 01 XOR 00       = 04
ch2=0:  01 XOR 07 XOR 00 XOR 05 XOR 07 XOR 02 XOR 00       = 06
ch3=0:  01 XOR 07 XOR 00 XOR 06 XOR 07 XOR 03 XOR 00       = 04
ch1=20: 01 XOR 07 XOR 00 XOR 07 XOR 07 XOR 01 XOR 14       = 13
ch2=20: 01 XOR 07 XOR 00 XOR 07 XOR 07 XOR 02 XOR 14       = 10
ch3=20: 01 XOR 07 XOR 00 XOR 07 XOR 07 XOR 03 XOR 14       = 11
```

All length fields validate (`08 = 10 - 2` for manual mode; `07 = 9 - 2` for
channel commands). IDs are `00 02` through `00 07` per isolated run. No packet
contains a payload requiring reserved-byte substitution.

Each isolated test uses the same exact runtime identity checks, service-scoped
NUS routing and permanent FE59/DFU exclusion as the previous experiments. No
pairing, reads, subscriptions, status query, retries, schedule, RTC, auto mode,
power command, rename, reset, firmware, DFU, arbitrary raw command or other
application write is allowed. A failure or cancellation disconnects without
completing later steps or sending restoration commands. Partial clearing may
remain if a run fails. Successful runs leave manual mode selected with the
requested target level; there is no automatic restoration.

Use runtime prompts to keep identifiers out of source and command examples:

```powershell
# Run from the repository root.
$magneticAddress = Read-Host 'Private BLE address of the identified Magnetic Light II'
$magneticName = Read-Host 'Private exact advertised name'
& .\.venv\Scripts\python.exe -u -m chihiros.magnetic2_diagnostic isolated-channel1-test --address $magneticAddress --name $magneticName --dry-run
& .\.venv\Scripts\python.exe -u -m chihiros.magnetic2_diagnostic isolated-channel2-test --address $magneticAddress --name $magneticName --dry-run
& .\.venv\Scripts\python.exe -u -m chihiros.magnetic2_diagnostic isolated-channel3-test --address $magneticAddress --name $magneticName --dry-run
```

After explicit approval, use the appropriate live command as a separate
experiment, with physical observation between runs. Do not batch the live tests.

```powershell
& .\.venv\Scripts\python.exe -u -m chihiros.magnetic2_diagnostic isolated-channel1-test --address $magneticAddress --name $magneticName --live
```

```powershell
& .\.venv\Scripts\python.exe -u -m chihiros.magnetic2_diagnostic isolated-channel2-test --address $magneticAddress --name $magneticName --live
```

```powershell
& .\.venv\Scripts\python.exe -u -m chihiros.magnetic2_diagnostic isolated-channel3-test --address $magneticAddress --name $magneticName --live
```

Dry-run has no Bluetooth access and creates no session log. Live logs contain
private identity and stay under ignored `logs/magnetic2/`; do not commit them.
After any approved experiment, record the visible change, including any other
colors affected by manual mode. A single observation cannot establish a full
four-channel map, scaling curve, or universal prefix compatibility.

## Combined WRGB test, pending approval

The new source-only operation `wrgb-combined-test` fixes the following five
packets and accepts no level, channel or raw-packet overrides:

```text
Manual       5A 01 08 00 02 05 0B FF FF 05
Red   ch0=20 5A 01 07 00 03 07 00 14 16
Green ch1=40 5A 01 07 00 04 07 01 28 2C
Blue  ch2=60 5A 01 07 00 05 07 02 3C 3A
White ch3=30 5A 01 07 00 06 07 03 1E 1A
```

There is a 30 ms async pause between each pair of writes, then immediate
disconnect after White. IDs are `00 02` through `00 06`. No isolation-clearing
prelude, status query, notification subscription or restoration is added.

The existing `create_manual_mode` and `create_command_encoding(0x5A, 0x07, ...,
[channel, level])` builders produce the plan. The exact packet allowlist is
independent of the builder and rejects missing, extra, reordered or altered
packets. Every planned level is a direct decimal value with no max_level
conversion or clamp. Checksums, excluding the family byte, are:

```text
Manual: 01 XOR 08 XOR 00 XOR 02 XOR 05 XOR 0B XOR FF XOR FF = 05
Red:    01 XOR 07 XOR 00 XOR 03 XOR 07 XOR 00 XOR 14       = 16
Green:  01 XOR 07 XOR 00 XOR 04 XOR 07 XOR 01 XOR 28       = 2C
Blue:   01 XOR 07 XOR 00 XOR 05 XOR 07 XOR 02 XOR 3C       = 3A
White:  01 XOR 07 XOR 00 XOR 06 XOR 07 XOR 03 XOR 1E       = 1A
```

Length fields are `08 = 10 - 2` and `07 = 9 - 2`. All five packets are printed
and logged before the first application write. Runtime address/name, connection,
service-scoped NUS endpoints and the selected allowlist are checked before each
write. FE59/DFU remains permanently excluded; there is no pairing, arbitrary
read, global characteristic lookup, handle-based endpoint selection or retry.
There are no schedule, RTC, auto-mode, power, rename, reset, firmware, DFU or
arbitrary raw writes. Any failure/cancellation disconnects without further
writes or restoration. A partially applied target state can remain on failure.

The complete dry-run command, with private identity supplied only at runtime:

```powershell
# Run from the repository root.
$magneticAddress = Read-Host 'Private BLE address of the verified Magnetic Light II'
$magneticName = Read-Host 'Private exact advertised name'
& .\.venv\Scripts\python.exe -u -m chihiros.magnetic2_diagnostic wrgb-combined-test --address $magneticAddress --name $magneticName --dry-run
```

Prepared live command, only after explicit approval:

```powershell
& .\.venv\Scripts\python.exe -u -m chihiros.magnetic2_diagnostic wrgb-combined-test --address $magneticAddress --name $magneticName --live
```

Dry-run uses no Bluetooth and creates no session log. Live logs remain private
under ignored `logs/magnetic2/`. No public GUI/model registration or version
change accompanies this experiment, and no combined live result is claimed.

## Validation and scope

The existing channel-0 hardware-free tests cover exact packet generation/checksums,
rejection of other channels/levels/commands, the two-write order and 30 ms pause,
immediate disconnect, failures/cancellation without retries, identity/topology
checks before both writes, offline dry-run, and the explicit CLI live gate.
Six additional tests from the previous phase, parameterized across its operations, cover each exact
packet/checksum, cross-channel plan rejection, the complete two-write sequence,
failures without retries, target/topology guards, and dry-run/live CLI dispatch.
Nine isolated-plan tests cover six-packet goldens, literal zero and a
simulated clamp, missing/reordered/extra writes, pacing, failures at all six
writes, identity/DFU changes and cancellation at every gap, initial safety
guards, and the offline/explicit-live CLI boundary.
Eight new combined-plan tests cover exact packets/direct levels/checksums,
rejection of other plans and level transformations, the five-write/four-gap
sequence, failures at every write, identity/DFU guards at every gap, unsafe
initial target/topology, cancellation, and the offline/explicit-live CLI gate.
Prior preview assertions now reflect the user's confirmed mapping and clearly
limit the scaling claim to the practical comparison.
Existing GATT/status diagnostic tests remain in place. The shared protocol,
transport, Vivid II/A2 Max behavior, model registry, GUI and public version are
unchanged. No build, release or commit was made. Live execution of the combined
WRGB operation remains pending the user's review and approval.

Validation completed: `python -m unittest discover -s tests` passed all 220
hardware-free tests (212 existing plus eight new). The combined standalone
dry-run passed with a synthetic identity and no Bluetooth access. A privacy
audit of 53 tracked/non-ignored files found no exact private test-device
identifiers. The shared protocol, transport, GUI and version files have no
tracked changes; all preparation remains uncommitted.
