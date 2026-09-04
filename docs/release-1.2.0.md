# Chihiros Local Controller 1.2.0

This version adds Magnetic Light II manual WRGB control to the existing unified
Windows controller. RGB Vivid II and A2 Max retain their verified behavior.

## Magnetic Light II

- DYMNC discovery and manual WRGB control are physically validated on three
  Magnetic Light II units. Compatibility with every hardware or firmware
  revision is not guaranteed. The older Magnetic Light is not included.
- Red, Green, Blue and White sliders each accept 0..100. Apply WRGB enters manual
  mode, then sends the complete requested state on channels 0, 1, 2 and 3, with
  approximately 30 ms between writes, and disconnects.
- Isolated testing confirmed the channel mapping and zero/off behavior. A
  comparison at R20/G40/B60/W30 with another unit controlled by My Chihiros
  matched visible color and apparent brightness. This supports the practical
  direct 0..100 convention; no photometric calibration or linearity is claimed.

## Multiple lights and validation

Physical GUI testing confirmed all three Magnetic Light II units were discovered
separately and independently selectable. Apply reached only the selected light;
no cross-device writes or routing mix-ups were observed. WRGB inputs were retained
per address during the session. A2 Max and RGB Vivid II regression checks and
switching among all three model-specific control panels passed.

The full normalized BLE address/identifier remains the canonical identity.
Dropdown labels are presentation only and never route a connection. Selection
and slider movement do not control a light.

## Safety and packages

Only validated service-scoped Nordic UART endpoints are used. The entire FE59/DFU
subtree remains permanently blacklisted. There is no pairing, firmware/DFU,
schedule, RTC, auto-mode, reset, rename or raw-command UI. Failed transactions
are not automatically retried. Manual mode may persist; use My Chihiros to
resume normal scheduled operation after the local connection ends.

The recommended Windows x64 package is
`ChihirosLocalController-1.2.0-windows-x64.zip`; extract the complete folder and
run `ChihirosLocalController.exe` with `_internal` beside it. The optional
`ChihirosLocalController-1.2.0-windows-x64-onefile.exe` includes the runtime.
`SHA256SUMS.txt` covers both artifacts. Builds are unsigned.

Local release preparation runs the hardware-free suite and audits source and
packaged contents, including decompressed executable modules. Device identities,
preferences, logs, captures, research CLIs, APK/decompiled artifacts and secrets
are excluded from packages. No commit, tag, push or publication is performed
by the build process. Final physical testing of the packaged EXE remains a
separate user action.
