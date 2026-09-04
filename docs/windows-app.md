# Chihiros Local Controller 1.1.0 — Windows application

The primary source launcher is `chihiros_local_controller.py`.
Both PyInstaller variants produce `ChihirosLocalController.exe`.
The historical launcher is a small compatibility shim, not a second application.

The shared Tkinter shell shows RGB controls only for RGB Vivid II and one
Brightness control only for A2 Max. Scanning is explicit, selection never
controls a light, and Apply submits one operation at a time.

Device choices use normalized full BLE addresses as keys. A dropdown row maps
to a stored address; the shortened visible label is never an identity key.
The last selection is saved locally; there is no GUI clear/reset control.
The historical per-user data directory is retained for preference compatibility.

Vivid II uses its established controller and packet builders. A2 Max uses its
manual-only adapter and two-packet allowlist. Both use the shared strict NUS
transport. The unique service's direct RX/TX children must have the required
properties. FE59 and its entire subtree remain blacklisted. Endpoints are
rechecked before every write, handles never select endpoints, and pairing is off.

No GUI route exposes Off, auto mode, schedules, RTC, firmware, pairing reset,
rename or raw packets. Only manual controls are shipped.
See [user instructions](../README.md), [validation](gui-development.md), and
[local packaging](releasing.md).
