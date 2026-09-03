# RGB Vivid II Windows application

`Vivid2Controller.exe` is a native Tkinter application for manual RGB control of
source-verified RGB Vivid II variants. It is an unofficial community tool and
does not use a Chihiros account or cloud service.

Created by Tianxu Yang · Instagram: `@tianxu_07`

## Public Version 1 scope

The GUI exposes only:

- scanning for names beginning with the verified `DYNV`, `DYNVVD`, or `DYRGBV`
  model prefixes;
- choosing one discovered light;
- applying integer red, green, and blue levels from 0 through 100;
- forgetting the locally remembered name/address/model selection.

It has no UI or command-line route for raw packets, Auto mode, schedules, RTC,
DFU, firmware, reset, pairing reset, rename, or bootloader operations.

## BLE safety

The GUI delegates to the same `chihiros.vivid2.Vivid2Controller` used by the
tested CLI. That controller uses `NusSession`, which requires exactly one Nordic
UART service and selects RX/TX only from that service's direct children. RX must
declare `write-without-response`, TX must declare `notify`, and the topology is
resolved again immediately before every write. A duplicate endpoint elsewhere
or any other ambiguity aborts the operation.

The complete FE59 service subtree and Buttonless DFU characteristic remain
blacklisted. The only write call targets the resolved NUS RX object with
`response=False`; the only notification subscription targets resolved NUS TX.
GATT handles are logged as diagnostics and are never identity requirements.

## Local files

No manual configuration file is required. The application stores only the
selected advertised name, Bluetooth address, and model under:

```text
%LOCALAPPDATA%\Vivid2Controller\selected_device.json
```

The **Forget Device** button removes that file. Text application diagnostics and
per-operation JSON logs are under `%LOCALAPPDATA%\Vivid2Controller\logs`. JSON
operation logs include the application/controller version, Windows version,
timestamp, device identity, actual GATT handles, requested RGB values, exact
packet bytes, and any error. No account or credential fields are used.

## Build

Use a clean Windows Python environment; the output does not depend on that
environment after it is built:

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean Vivid2Controller.spec
```

The preferred output is:

```text
dist\Vivid2Controller\Vivid2Controller.exe
```

The optional single-file build is:

```powershell
python -m PyInstaller --noconfirm --clean --distpath dist-single Vivid2Controller-onefile.spec
```

Bleak's WinRT backend, the installed WinRT namespace modules and their native
libraries are collected explicitly in both specifications. The one-folder build
is preferred because extraction is simpler and startup is more predictable. It
also includes the project and third-party license notices at the distribution
root.

## First real-device check

1. Turn on Windows Bluetooth and power the RGB Vivid II near the PC.
2. Fully close My Chihiros on phones/tablets so it does not hold the BLE link.
3. Double-click `Vivid2Controller.exe`; no scan or control starts automatically.
4. Click **Scan for Vivid II**. Confirm only the expected lamp appears and select
   it if more than one supported light is present.
5. Choose deliberately low values such as R=10, G=20, B=30 and click **Apply RGB**.
6. Confirm the success status and visible change. Use a physical switch or smart
   plug for actual power control.
7. If anything fails, keep the files under the local `logs` directory for diagnosis.

No automated build or test in this repository sends a live BLE command.
