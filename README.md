# Chihiros Local Controller 1.4.0

Version 1.4.0 adds separately isolated
upstream-derived manual light support and VIVID III fan/telemetry controls. See
[coverage, evidence, safety decisions and testing](docs/upstream-support.md).
The six physically validated local implementations remain unchanged. New upstream-derived profiles have not been physically validated on every hardware variant.

A local Windows application for control of supported Chihiros aquarium lights
and Cooling Fans directly over Bluetooth Low Energy. No Chihiros account, login, internet
connection or Chihiros cloud service is required.

**Unofficial community tool. Not affiliated with Chihiros Aquatic Studio.**

Created by **Tianxu Yang** · Instagram: **[@tianxu_07](https://www.instagram.com/tianxu_07/)**

This **1.3.0 physical-validation build** includes all six supported models below.
Validation of the complete packaged build is pending. The current public release
remains **v1.2.0**; these local artifacts have not been published.

## Supported devices

- **Chihiros RGB Vivid II:** manual Red / Green / Blue sliders, 0–100.
  Existing verified advertisement prefixes: `DYNV`, `DYNVVD`, `DYRGBV`.
- **Chihiros A2 Max:** one manual Brightness slider, 1–100.
  The `DYNCMC` implementation has been physically validated on one A2 Max unit.
  Compatibility with every hardware/firmware revision, or all A2 Max prefixes,
  is **not guaranteed**. Brightness is a normalized wire level internally, not
  a universal claim of exact official-app percentage equivalence.
- **Chihiros Magnetic Light (original / first generation):** Red / Green sliders,
  0–100, and **Apply RG**. The `DYCX` classification, channel order R=0/G=1,
  and direct normal-range values were physically validated on a real device.
  Local R20/G40 visibly matched the official app's R20/G40 setting. Overclock
  values above 100 are intentionally unsupported. Compatibility with every
  hardware or firmware revision is not guaranteed.
- **Chihiros Magnetic Light II:** Red / Green / Blue / White sliders, 0–100,
  and **Apply WRGB**. The observed `DYMNC` prefix and manual WRGB control are
  physically validated on three Magnetic Light II units. Channel order
  R=0/G=1/B=2/W=3 and zero/off were confirmed in isolated testing. An official-app
  R20/G40/B60/W30 comparison matched visible color and apparent brightness.
  This validates the practical direct 0–100 convention, not photometric
  linearity or laboratory-calibrated equivalence. Other revisions are not
  guaranteed. Magnetic Light II remains a separate four-channel model.
- **Chihiros Cooling Fan:** `DYNFAN` devices expose manual device speed 0–20,
  device-side automatic thermostat configuration, and explicit **Refresh
  Status** for room temperature, water temperature and humidity. Automatic
  configuration accepts integer Celsius Start Temperature
  and Max-Speed Temperature values, with Max strictly greater than Start. The
  fan regulates itself after the one-time configuration and BLE disconnect, so
  the PC does not need to remain connected or powered on. Threshold read-back
  is not currently supported: Start/Max values displayed in the GUI are locally
  remembered session values, not values read from the fan. Silent Mode is not
  currently supported. DYNFAN Cooling Fan support has been physically validated
  on a real device; compatibility with every hardware or firmware revision is
  not claimed.
- **Chihiros Z Light:** `DYSSD` devices expose **Cool White** and **Warm White**
  sliders, each using the normal 0–100 range, and **Apply White**. Control is
  direct over local Bluetooth LE; no Chihiros account or cloud is required.
  The prefix classification and channel-isolated white control were physically
  validated on a real Z Light. Compatibility with every Z Light hardware or
  firmware revision is not claimed.

An unrelated Nordic UART device is never accepted just because it exposes NUS.
Multiple devices, including multiple units of the same model or identical
advertised names, remain distinct by their full BLE addresses. Dropdown labels
show the model and a short address suffix, extended when needed for uniqueness.
Labels are presentation only, never device identity keys.

## Windows package

Windows 10/11 x64 with Bluetooth LE is required. Python is not required for the
packaged application. The recommended package is:

```text
ChihirosLocalController-1.3.0-windows-x64.zip
```

Extract the **entire ZIP**, open the `ChihirosLocalController` folder, and run
`ChihirosLocalController.exe`. Keep its `_internal` directory alongside it.

The previous public v1.2.0 package predates original Magnetic Light, Cooling Fan
and Z Light support. This 1.3.0 package is for physical validation. The optional
`ChihirosLocalController-1.3.0-windows-x64-onefile.exe` includes the
runtime in one executable; extraction can make startup slower. Verify the
artifact against `SHA256SUMS.txt`. The application is unsigned; Windows may
show a SmartScreen warning. Only run a copy from a source you trust.

## Normal workflow

1. Close My Chihiros on nearby phones/tablets.
2. Enable Windows Bluetooth and power the intended devices nearby.
3. Launch Chihiros Local Controller.
4. Click **Scan for Devices**.
5. Select the desired supported device.
6. Adjust RGB for Vivid II, Brightness for A2 Max, RG for Magnetic Light, WRGB
   for Magnetic Light II, or Cool/Warm White for Z Light.
7. Click **Apply RGB**, **Apply Brightness**, **Apply RG**, **Apply WRGB**, or
   **Apply White**, as appropriate.
8. For a Cooling Fan, use **Refresh Status**, **Apply Manual**, or **Apply
   Automatic**. Refresh connects only long enough to receive one telemetry
   response. Apply Automatic writes the thermostat configuration once and
   disconnects.
9. Select another supported device if needed.

Scanning, selection and slider movement never send control commands. One
operation runs at a time. Slider values are requested inputs, not readings of
the currently selected lamp. Magnetic Light RG, Magnetic Light II WRGB
and Z Light white requests are kept per full device address during the session
and use separate variables from each other and from Vivid II/A2 Max. Existing
device input behavior is preserved. Each Apply revalidates the
exact selected address, advertised name and model before control.

Cooling Fan manual speed, automatic Start/Max values and telemetry are also
kept separately per full canonical address for the current session. **Apply
Manual** sends one explicit fan-speed command and switches the physical fan
away from autonomous thermostat behavior. **Apply Automatic** sends the full
thermostat setup sequence with approximately 200 ms between writes, sends no
fan-speed command, disconnects, and leaves temperature regulation entirely in
the fan firmware. There is no background polling or continuous PC-side fan
control. **Refresh Status** is informational only; it subscribes only to NUS TX,
sends the safe query once, ignores notifications whose subtype is not `0x25`,
retains the first valid telemetry frame, unsubscribes, and disconnects. No
current fan-speed reading is exposed because no such telemetry field has been
independently verified.

Apply RG targets only the selected full Magnetic Light address. It sends manual
mode followed by Red channel 0 and Green channel 1, with approximately 30 ms
between writes, then disconnects. It never probes or writes channels 2 or 3 and
sends no status query or notification subscription during Apply.

Apply White targets only the selected full Z Light address. It sends manual
mode, Cool White channel 0, and Warm White channel 1, with approximately 30 ms
between writes, then disconnects. It never probes or writes channels 2 or 3 and
sends no status query or notification subscription during Apply.

Three advertising Magnetic Light II units appear as three separate dropdown
rows, even with identical advertised names or colliding short address suffixes.
Apply WRGB targets only the selected full address. It sends manual mode followed
by all requested R/G/B/W values in channel order 0/1/2/3, with approximately
30 ms between writes, then disconnects. No clearing prelude, status query or
notification subscription is part of this transaction.

Physical GUI validation confirmed all three Magnetic Light II units were
independently selectable, only the selected unit changed, and per-address WRGB
inputs survived selection changes during the session. Vivid II and A2 Max
regressions and switching among all three model-specific controls also passed.

Manual light settings may persist on the physical device, including across
power loss. Manual light operation overrides automatic operation; the GUI does
not edit stored light schedules. To resume a normal schedule-driven light
configuration, use My Chihiros after the local connection has ended. Recovery
through the official app was verified on the tested units; this is not a
guarantee for every revision. For Cooling Fans, Apply Automatic is the explicit
route back to the configured device-side thermostat.

Use a physical switch or smart plug for normal light on/off timing. The GUI has
no light Off button, manual clear/reset control, schedule editor, exposed RTC,
firmware/DFU, pairing reset, rename or raw-packet entry. Cooling Fan Silent Mode
is not exposed.

## Safety and privacy

RX/TX endpoints must be direct children of the unique validated NUS service.
Endpoint topology is checked before each write. The complete FE59/DFU subtree
is permanently blacklisted. Handles are diagnostics only, not endpoint selectors.
There is no pairing, firmware access, guessed rollback or automatic write retry.

The last selected device is remembered locally. For compatibility with v1.0.0,
the existing per-user `%LOCALAPPDATA%\Vivid2Controller` data directory is retained.
Local diagnostics include device identity and transaction details; they are
never uploaded. Review/redact diagnostics before sharing them. Saved preferences,
logs, captures and research files are not included in the Windows package.

## Troubleshooting

- Keep the device nearby and close other apps holding its BLE connection.
- Do not pair the device manually in Windows Settings.
- If a saved device is unavailable, Scan and select the intended device again.
- A failed write may still have reached the device. Check the local diagnostics
  and physical result before deciding whether to try again.
- Use one application window at a time.

## From source

From the repository root in a Windows Python environment:

```powershell
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests
python .\chihiros_local_controller.py
```

The old `vivid2_gui.py` launcher is only a compatibility shim for the same GUI.

[Local packaging](docs/releasing.md) builds audited 1.3.0 Windows artifacts
without publishing them. [1.3.0 validation notes](docs/release-1.3.0.md) describe
the supported scope and packaged validation checklist.
[Previous release notes](docs/release-1.2.0.md) retain the 1.2.0 history.
[Magnetic Light II integration](docs/magnetic2.md) documents packets, validation,
address routing and physical test instructions.
[Magnetic Light integration](docs/magnetic1.md) documents the first-generation
device's physically validated RG scope and exact Apply transaction.
[Cooling Fan integration](docs/fan.md) documents DYNFAN telemetry, manual speed,
the autonomous thermostat sequence and BLE safety boundaries.
[GUI implementation](docs/windows-app.md) and
[validation notes](docs/gui-development.md) describe the tested scope.

## Credits and license

This project uses and adapts work from
[TheMicDiet/chihiros-led-control](https://github.com/TheMicDiet/chihiros-led-control).
Cooling Fan protocol behavior was independently implemented in Python using
the public btsnoop-derived research in
[BartdeJonge/chihiros-esphome](https://github.com/BartdeJonge/chihiros-esphome)
as a reference; no C++ source is vendored.
The pinned upstream MIT copyright and license, and notices for Bleak, PyWinRT,
CPython, Tcl/Tk and PyInstaller, are preserved in
[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt) and the packaged notices.

Original project code is under the [MIT License](LICENSE),
copyright © 2026 Tianxu Yang. Third-party components retain their own terms.
Product names belong to their respective owners; no Chihiros logos or
proprietary artwork are used. This software is provided “as is,” without warranty.
