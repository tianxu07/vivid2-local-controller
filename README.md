# Chihiros Local Controller 1.1.0

A local Windows application for manual control of Chihiros aquarium lights
directly over Bluetooth Low Energy. No Chihiros account, login, internet
connection or Chihiros cloud service is required.

**Unofficial community tool. Not affiliated with Chihiros Aquatic Studio.**

Created by **Tianxu Yang** · Instagram: **[@tianxu_07](https://www.instagram.com/tianxu_07/)**

## Supported lights

- **Chihiros RGB Vivid II:** manual Red / Green / Blue sliders, 0–100.
  Existing verified advertisement prefixes: `DYNV`, `DYNVVD`, `DYRGBV`.
- **Chihiros A2 Max:** one manual Brightness slider, 1–100.
  The `DYNCMC` implementation has been physically validated on one A2 Max unit.
  Compatibility with every hardware/firmware revision, or all A2 Max prefixes,
  is **not guaranteed**. Brightness is a normalized wire level internally, not
  a universal claim of exact official-app percentage equivalence.

An unrelated Nordic UART device is never accepted just because it exposes NUS.
Multiple lights, including multiple devices of the same model or identical
advertised names, remain distinct by their full BLE addresses. Dropdown labels
show the model and a short address suffix, extended when needed for uniqueness.
Labels are presentation only, never device identity keys.

## Download and run

Windows 10/11 x64 with Bluetooth LE is required. Python is not required for the
packaged application. The recommended package is:

```text
ChihirosLocalController-1.1.0-windows-x64.zip
```

Extract the **entire ZIP**, open the `ChihirosLocalController` folder, and run
`ChihirosLocalController.exe`. Keep its `_internal` directory alongside it.

The optional `ChihirosLocalController-1.1.0-windows-x64-onefile.exe` includes the
runtime in one executable; extraction can make startup slower. Verify the
artifact against `SHA256SUMS.txt`. The application is unsigned; Windows may
show a SmartScreen warning. Only run a copy from a source you trust.

## Normal workflow

1. Close My Chihiros on nearby phones/tablets.
2. Enable Windows Bluetooth and power the intended lights nearby.
3. Launch Chihiros Local Controller.
4. Click **Scan for Lights**.
5. Select the desired supported light.
6. Adjust Red/Green/Blue for Vivid II, or Brightness for A2 Max.
7. Click **Apply RGB** or **Apply Brightness**.
8. Select another supported light if needed, adjust its controls, and Apply.

Scanning, selection and slider movement never send control commands. One
operation runs at a time. Slider values are requested inputs shared between
devices, not readings of the currently selected lamp. Each Apply revalidates
the exact selected address, advertised name and model before control.

Manual settings may persist on the physical device, including across power
loss. Manual operation overrides automatic operation; the GUI does not edit
stored schedules. To resume a normal schedule-driven configuration, use My
Chihiros after the local connection has ended. Recovery through the official
app was verified on the tested units; this is not a guarantee for every revision.

Use a physical switch or smart plug for normal on/off timing. The GUI has no
Off button, manual clear/reset control, schedule editor, RTC, auto-mode control,
firmware/DFU, pairing reset, rename or raw-packet entry.

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

- Keep the lamp nearby and close other apps holding its BLE connection.
- Do not pair the lamp manually in Windows Settings.
- If a saved device is unavailable, Scan and select the intended light again.
- A failed write may still have reached the lamp. Check the local diagnostics
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

For local builds, see [packaging preparation](docs/releasing.md).
[GUI implementation](docs/windows-app.md) and
[validation notes](docs/gui-development.md) describe the tested scope.

## Credits and license

This project uses and adapts work from
[TheMicDiet/chihiros-led-control](https://github.com/TheMicDiet/chihiros-led-control).
The pinned upstream MIT copyright and license, and notices for Bleak, PyWinRT,
CPython, Tcl/Tk and PyInstaller, are preserved in
[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt) and the packaged notices.

Original project code is under the [MIT License](LICENSE),
copyright © 2026 Tianxu Yang. Third-party components retain their own terms.
Product names belong to their respective owners; no Chihiros logos or
proprietary artwork are used. This software is provided “as is,” without warranty.
