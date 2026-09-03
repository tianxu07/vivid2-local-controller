# Vivid2Controller

A small, fully local Windows application for setting manual red, green, and
blue brightness on supported Chihiros RGB Vivid II aquarium lights.

**Unofficial community tool. Not affiliated with Chihiros Aquatic Studio.**
“Chihiros” and related product names are trademarks of their respective owners.
This project does not use the Chihiros logo or official artwork.

Created by **Tianxu Yang** · Instagram: **[@tianxu_07](https://www.instagram.com/tianxu_07/)**

## What it does

The Windows GUI provides one intentionally narrow workflow:

1. Click **Scan for Vivid II**.
2. Select the intended light if more than one is found.
3. Set **Red**, **Green**, and **Blue** from 0 to 100.
4. Click **Apply RGB**.

The application connects locally over Bluetooth Low Energy, enters manual mode,
writes the three RGB values, and disconnects. No Chihiros account, vendor app,
internet connection, or cloud service is required.

Use a physical switch or smart plug for actual power control.

## Supported scope

Version 1 supports only source-verified **RGB Vivid II** advertisement families:

- `DYNV…`
- `DYNVVD…`
- `DYRGBV…`

An unrelated device is not accepted merely because it exposes Nordic UART.
Device addresses and GATT handles are discovered at runtime and are not
hardcoded.

The public GUI provides **manual RGB only**. It does not provide:

- schedule or Auto-mode editing;
- clock/RTC changes;
- firmware updates, DFU, bootloader, reset, pairing reset, or rename functions;
- arbitrary packet or command entry.

The FE59 DFU service and Buttonless DFU characteristic are permanently
blacklisted. BLE writes are restricted to the RX characteristic found as a
direct child of the one validated Nordic UART service.

## Download and run

Windows 10 or Windows 11 with a Bluetooth Low Energy adapter is required.
Python is not required for a packaged release.

From the GitHub **Releases** page, download the recommended file:

```text
Vivid2Controller-1.0.0-windows-x64.zip
```

Extract the complete ZIP, open the `Vivid2Controller` folder, and double-click
`Vivid2Controller.exe`. Do not move the EXE away from its `_internal` folder.

An optional single-file EXE may also be attached to a release. It is convenient
but can start more slowly because it extracts its runtime on each launch.

This project is not currently code-signed, so Windows SmartScreen may show an
unknown-publisher warning. Verify the release checksum before running it.

## Privacy and local data

The application has no account or cloud integration. If a device is selected,
it stores only the advertised name, Bluetooth address, and detected model in:

```text
%LOCALAPPDATA%\Vivid2Controller\selected_device.json
```

The **Forget Device** button removes that selection. Diagnostic logs are stored
locally under `%LOCALAPPDATA%\Vivid2Controller\logs` and are never uploaded by
the application.

## Troubleshooting

- Make sure Windows Bluetooth is on and the light is powered and nearby.
- Fully close My Chihiros on phones or tablets; many BLE devices allow only one
  active client.
- Do not pair the light manually in Windows Settings. The application connects
  without pairing.
- If no supported device appears, move closer and scan again.
- If the app reports an unexpected BLE service layout, it deliberately sent no
  command. Keep the local diagnostic log when reporting the problem.
- If a saved light is no longer available, click **Forget Device**, then scan
  again.

## Build from source

For development, use Python 3.10 or newer on Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean Vivid2Controller.spec
```

The preferred build is written to
`dist\Vivid2Controller\Vivid2Controller.exe`. The optional single-file build is:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean `
  --distpath dist-single --workpath build-single Vivid2Controller-onefile.spec
```

Generated binaries and build directories are intentionally excluded from source
control. See [docs/releasing.md](docs/releasing.md) for the release-asset layout
and [docs/windows-app.md](docs/windows-app.md) for implementation details.

## Credits and acknowledgements

Vivid2Controller uses and adapts implementation details and code from
[TheMicDiet/chihiros-led-control](https://github.com/TheMicDiet/chihiros-led-control),
which is distributed under the MIT License. That upstream project and its author
have not endorsed, sponsored, or affiliated themselves with this application.
Its original copyright and complete MIT notice are preserved in
[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt).

The packaged Windows application also uses
[Bleak](https://github.com/hbldh/bleak),
[PyWinRT](https://github.com/pywinrt/pywinrt), CPython, Tcl/Tk, and the
PyInstaller bootloader. Applicable notices are shipped with the recommended
one-folder distribution and summarized in `THIRD_PARTY_LICENSES.txt`.

## License

Original Vivid2Controller code is released under the
[MIT License](LICENSE), copyright © 2026 Tianxu Yang. Third-party components
remain subject to their respective licenses.

This software is provided “as is,” without warranty. Use it at your own risk,
especially around aquarium equipment and livestock.
