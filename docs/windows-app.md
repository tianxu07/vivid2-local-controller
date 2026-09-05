# Chihiros Local Controller 1.3.0 — Windows application

The primary source launcher is `chihiros_local_controller.py`.
Both PyInstaller variants produce `ChihirosLocalController.exe`.
The historical launcher is a small compatibility shim, not a second application.
This 1.3.0 build is prepared for physical validation of all six models. The
current public release remains v1.2.0 until a separately authorized publication.

The shared Tkinter shell shows RGB controls for RGB Vivid II, one Brightness
control for A2 Max, Red/Green controls with Apply RG for original Magnetic
Light, and Red/Green/Blue/White controls with Apply WRGB for Magnetic Light II.
DYSSD Z Light selection shows only Cool White and Warm White controls, 0–100,
with Apply White. This direct local BLE support was physically validated on a
real device, without claiming every hardware or firmware revision.
Selecting a DYNFAN Cooling Fan instead shows current status, manual device speed 0–20,
and automatic Start Temperature / Max-Speed Temperature controls. No light
brightness controls remain visible in the fan view.
Scanning is explicit, selection never
controls a light, and Apply submits one operation at a time.

Device choices use normalized full BLE addresses as keys. A dropdown row maps
to a stored address; the shortened visible label is never an identity key.
The last selection is saved locally; there is no GUI clear/reset control.
The historical per-user data directory is retained for preference compatibility.
Magnetic Light requested RG and Magnetic Light II requested WRGB inputs are
retained separately per full address during the session. All three tested
Magnetic II units remain independently
selectable; Apply connects only to the selected canonical address.
Z Light Cool/Warm inputs are likewise retained separately per canonical full
address, so multiple DYSSD devices remain independently routable.
Cooling Fan manual speed, locally remembered Start/Max values and telemetry are
also isolated per canonical full address. Start/Max values are not read back
from the device.

Vivid II uses its established controller and packet builders. A2 Max uses its
manual-only adapter and two-packet allowlist. Magnetic Light uses an isolated
three-packet manual/R/G allowlist that can write only channels 0 and 1. Magnetic
Light II uses its
manual-only adapter and ordered five-packet allowlist: manual mode, then
Red/Green/Blue/White on channels 0/1/2/3 at direct 0..100 levels, with about
30 ms between writes and immediate disconnection afterward. All use strict NUS
transport. The unique service's direct RX/TX children must have the required
properties. FE59 and its entire subtree remain blacklisted. Endpoints are
rechecked before every write, handles never select endpoints, and pairing is off.

Z Light uses its own ordered three-packet allowlist: manual mode, Cool White on
channel 0, and Warm White on channel 1, with about 30 ms between writes and then
disconnection. Apply White never writes channels 2/3, subscribes, or sends a
status query. No Chihiros account or cloud service is involved.

Refresh Status opens a short-lived connection, subscribes only to service-scoped
NUS TX, writes the safe status query once, and waits past non-telemetry/status
notifications until a structurally valid DYNFAN frame with subtype `0x25`
arrives. It retains the first valid telemetry frame, safely ignores any duplicate
frames, then stops notifications and disconnects. It decodes 16-bit big-endian
humidity, room temperature and water temperature fields. Byte 12 remains
unknown. No current fan-speed value is displayed or inferred. Refresh does not
poll continuously or take part in thermostat control.

Apply Manual writes only BASE FAN_SPEED data `[0xFF, device_speed]`, where the
device speed is the direct integer 0–20 scale, then disconnects.
Any explicit speed changes the physical fan from autonomous thermostat behavior.
Apply Automatic writes BASE AUTH, RTC twice, DEVICE AUTH_EXT1/AUTH_EXT2,
TEMP_THRESH, MODE 0x23, MODE 0x22 and final AUTH_EXT1/AUTH_EXT2, with 200 ms
between commands. It sends no FAN_SPEED command, disconnects after the final
write, and leaves regulation entirely device-side; the PC may be closed or
powered off. There is no background polling loop. Silent Mode is not supported.

No GUI route exposes light Off, schedules, a standalone RTC action, firmware,
pairing reset, rename or raw packets. The only automatic control is the
Cooling Fan's one-shot device-side thermostat configuration.
See [user instructions](../README.md), [validation](gui-development.md), and
[local packaging](releasing.md).
