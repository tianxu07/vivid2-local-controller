# Independent upstream support implementation

This development branch starts at released tag `v1.3.0`, commit
`0ab7955329dd8a79b82a1c51235e033b0fbe7b60`. Application release version is 1.4.0;
this work does not describe a new packaged release. New devices are supported
from source through the existing Windows GUI. They have not been physically
validated by this project.

## Authority and transport evidence

The sole upstream implementation authority is TheMicDiet/chihiros-led-control,
revision `05ae7654b3d4de1df887be3827cd85dd33fe0af6`:

- [models.py](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/src/chihiros_led_control/models.py): exact prefixes and channel maps.
- [const.py](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/src/chihiros_led_control/const.py): NUS for SeaLed/NewBleLed/VIVID III and HM-10 for BleLed.
- [client.py](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/src/chihiros_led_control/client.py): transaction prelude, pacing, and fan clamp.
- [commands.py](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/src/chihiros_led_control/commands.py) and [protocol.py](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/src/chihiros_led_control/protocol.py): frame builders and telemetry fields.
- [protocol.md](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/docs/protocol.md): offline device registry, captures, family inference limitations and VIVID III ambiguity.
- [number.py](https://github.com/TheMicDiet/chihiros-led-control/blob/05ae7654b3d4de1df887be3827cd85dd33fe0af6/custom_components/chihiros/number.py): safe editable fan threshold bounds.

An explicit model-family assignment together with const.py's family transport
statement is accepted as upstream-derived transport evidence. This is not a
claim that every individual prefix/hardware revision has a physical capture.
Where even the family assignment relies only on new-generation naming
conventions, this implementation withholds writes. `sea_led_family=False` by
default does not prove BleLed/HM-10.

The pinned protocol table contains stale statements: its introductory assertion
that every light is BleLed conflicts with the same table's SeaLed rows, and its
combined RGB+APLUS row conflicts with models.py's explicit DYNARGB split. The
specific per-generation model definitions and corroborating family lists are
used instead of those blanket statements.

## New writable profiles

All levels are whole numbers 0–100. W means White=0; RGB means Red=0, Green=1,
Blue=2; WRGB adds White=3. No overclock or generic unknown-channel controls exist.

| Profile | Advertisement prefixes | Channels | Fixed transport |
| --- | --- | --- | --- |
| A II | DYNA2, DYNA2N | W | NUS |
| New C, newer | DYNC2 | W | NUS |
| RGB+APLUS, newer | DYNARGB | RGB | NUS |
| SEA_LED | DYSEA | WRGB | NUS |
| WRGB II, newer | DYNT90, DYNW30, DYNW45, DYNW60, DYNW90, DYNW12P, DYNWRGB | RGB | NUS |
| C II | DYNC2N | W | NUS |
| Commander 4, newer | DYNLED | WRGB | NUS |
| A Series | DYA | W | HM-10 |
| New C, legacy | DYC | W | HM-10 |
| RGB+APLUS, legacy | DYARGB, DYRGBA+, DYRGBA | RGB | HM-10 |
| RGB VIVID | DYREE | RGB | HM-10 |
| Commander X | DYONE | W | HM-10 |
| X300 | DYTWO | White=0, Warm White=1 | HM-10 |
| WRGB II, legacy | DYWRGB | RGB | HM-10 |
| Commander 4, legacy | DYLED | WRGB | HM-10 |
| WRGB VIVID III | DYVVD3 | WRGB | NUS |

VIVID III has an explicit model-specific NUS statement in const.py and captured
traffic in protocol.md. Other authorized profiles use the family evidence
described above. The registry is intentionally not organized by development phase.

## Withheld support

| Product | Prefixes | Reason |
| --- | --- | --- |
| Z Light TINY additional prefix | DYZSD | Same upstream product as DYSSD does not transfer local transport validation |
| Tiny Terrarium Egg | DYDD | Red=0/Green=1 is known; physical transport is unresolved |
| Commander 1 | DYCOM | White/RGB/WRGB selection is required; physical transport remains unresolved |
| WRGB II Pro | DYWPRO30/45/60/80/90, DYWPR120 | Family classification relies on naming conventions |
| WRGB II Slim | DYSILN, DYSL30/45/60/90/120/12 | Family classification relies on naming conventions |
| Universal WRGB | DYU550/600/700/800/920/1000/1200/1500 | Family classification relies on naming conventions |
| C II RGB | DYNCRGP, DYNCRGB | Source classification relies on new-generation inference |

These entries remain research metadata. They are not selectable writable GUI
devices, even when NUS, HM-10, or both services exist. Commander configuration is
deferred until it can enable a justified operation; no misleading layout-only
configuration workflow is provided. Unknown devices, dosing pumps, CO2 devices,
heaters, and other accessories are excluded.

## Locked local behavior

The original model registry, constants, shared NUS transport, framing, and six
device-specific implementations are unchanged. The GUI alone uses a union
detector that asks the local registry first. Existing device controllers still
import only the original local detector.

- RGB Vivid II: DYRGBV, DYNVVD, DYNV; original controller and state.
- A2 Max: DYNCMC; original 1–100 range and encoding.
- Magnetic Light: DYCX; original Red/Green implementation.
- Magnetic Light II: DYMNC; original WRGB implementation.
- Z Light: DYSSD; original Cool White/Warm White; 90 remains literal wire 90.
- Cooling Fan: DYNFAN; original standalone 0–20 manual scale, thermostat and telemetry.

Longest-prefix matching applies only after local-device precedence and accessory
exclusions. In particular DYC cannot absorb DYCX, DYCOM, DYCO2 or DYCHIL, and
DYA cannot absorb DYAPRCO2. DYNC2N wins over DYNC2; DYARGB wins over DYA.

## Transactions and uncertainty

`upstream_profiles.py` holds authorized profiles and separate unavailable
metadata. `upstream_protocol.py` accepts immutable typed requests, creates complete
plans and parses only established telemetry fields. `upstream_control.py` executes
one request once. `gui/upstream_panel.py` renders new-device inputs and reports.
Only two existing GUI modules need small integration changes.

Each operation validates model and canonical full address before importing BLE,
rescans that exact address, requires the same name/profile, and connects without
pairing. Transport is selected solely by the profile. NUS reuses the existing
strict service-scoped resolver. HM-10 requires exactly one FFE0 service and one
direct FFE1 write-without-response child; ambiguous endpoints are rejected.
Numeric handles never select endpoints. No fallback or global characteristic
lookup occurs. Both transport services may coexist; only the declared one is used.

Each write verifies connection identity and endpoint object identity. Operation
objects are consumed before connection and cannot be replayed after success,
failure, or cancellation. Scan, connect, writes, subscription and cleanup have
bounded waits. Writes are paced at the existing 30 ms interval. Uncertain writes
are never retried. Diagnostic logs stay in the existing local per-user log folder.

Manual light transactions mirror pinned upstream: auth/status, time twice,
manual mode, then one brightness packet per semantic channel. Action message IDs
are allocated before prelude IDs, matching pinned upstream. Device time uses ISO
weekday, not day-of-month. Clock updates are disclosed in the new UI.

Requested 90 is encoded as payload 89 (0x59) for upstream compatibility.
This describes the encoded byte, not measured hardware brightness or fan speed.
This compatibility behavior is retained and displayed for new upstream controls.
Vendor-protocol prose describes literal manual levels, so hardware necessity of
the substitution is not claimed. It is never applied to locked local devices.
Auto-curve payload preservation is not generalized to manual commands.

## VIVID III integrated fan

VIVID III light and fan use separate UI tabs. Manual fan accepts 0–100: zero
remains zero and 1–24 clamps to 25. Requested 90 is encoded as payload 89 (0x59)
for upstream compatibility; actual speed/mode is not confirmed. Fan Auto sends
5A/05 [11 FF FF]. Thresholds send A5/2D [start stop], constrained to 15–60 °C,
step 1 °C, start at least 2 °C above stop. Initial inputs 38/33 are never sent
automatically and are not represented as device readback.

Fan state-changing operations use pinned upstream's connection prelude (auth and
time twice), followed by the single selected feature command. They never enter
manual light mode or write brightness. This avoids the earlier feature branch's
unverified omission of the fresh-connection prelude. Physical acceptance of this
sequence still requires real-device testing.

Refresh is a passive, write-free NUS TX subscription for up to four seconds after
connection/subscription. It accepts only 5B/0B and B6/16, reading RPM from bytes
6–7 big-endian and temperature from byte 8. It requires the exact resolved TX
object and current connection/topology. Unknown 5B/36 is ignored. Trailer/XOR
semantics are not assumed. Silent devices time out without an active query or
fallback; some firmware may require a separately researched status query.

No mode or threshold readback exists here. The UI reports unknown state initially,
last submitted command after success, and uncertainty after failure. Telemetry
is explicitly a last observation; it does not confirm mode, thresholds or switches.
Input/tab/report memory is isolated by full address and never automatically replayed.

The pinned switch encodings conflict:

| Claimed action | 5A/05 payload |
| --- | --- |
| Protection OFF | 30 FF FF |
| Protection ON | 31 FF FF |
| Indicator OFF | 31 FF FF |
| Indicator ON | 32 FF FF |

No discriminator, coupling rule or reliable readback is proven. All these switch
commands remain outside the typed action set. No builder, switch widget, optimistic
switch state, or raw packet route exists. The shared standalone DYNFAN implementation
is not reused for any integrated-fan behavior.

## Verification and hardware follow-up

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
git diff --check
```

The original 284 tests are retained without changes. Added behavioral tests cover
every new profile, fixed external packet vectors, the full fan percentage range,
threshold pairs, transport ambiguity/mutation, each packet failure position,
cancellation, zero-write refresh, notification origin, no fallback/replay, real Tk
views, full-address routing, input isolation, uncertainty and footer visibility.
A Git-object regression test compares locked source modules with v1.3.0.

Implementation checkpoint verification: 284 baseline tests passed before the
extension; the final combined suite passed all 324 tests (40 new tests), with no
skips. An additional offline differential check matched 1,818 complete light/fan
plans against the pinned upstream protocol encoder, including every light level
0–100 for all 16 profiles. Git whitespace checks passed. No real BLE operation
was performed during implementation. These results do not establish hardware
acceptance of the new profiles.

At this checkpoint the extension adds four production modules (three core and
one GUI panel), approximately 558 net production lines including the two small
GUI integration edits, versus 2,951 on the preserved feature branch. New tests
occupy 611 lines versus 4,201 phase-test lines on that branch. These physical LOC
counts include comments and blank lines. Coverage differs deliberately: 16 new
writable profiles versus 20, and no non-runnable Commander configuration UI.

Before claiming physical validation, test a representative real device for each
transport/family, confirming advertisements, service topology, channel order,
90 behavior, fresh-connection prelude acceptance and disconnect behavior. Test
VIVID III fan zero/minimum/maximum, Auto, threshold pair behavior and both telemetry
firmware forms. Do not enable withheld profiles through trial writes. Resolve
their transport evidence first.

The existing feature/upstream-led-support branch is preserved as a comparison
reference. This implementation has fewer layers and less coverage by choice:
it withholds convention-derived profiles and omits non-runnable Commander UI.
It also strengthens explicit uncertainty reporting and uses the pinned fan
connection sequence. Reduced LOC is not a substitute for hardware validation.
