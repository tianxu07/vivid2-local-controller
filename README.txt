Chihiros Local Controller 1.2.0
==============================

Unofficial community tool. Not affiliated with Chihiros Aquatic Studio.

Created by Tianxu Yang
Instagram: @tianxu_07

SUPPORTED MODELS

- Chihiros RGB Vivid II: manual Red / Green / Blue, each 0-100.
- Chihiros A2 Max: manual Brightness, 1-100.
- Chihiros Magnetic Light II: Red / Green / Blue / White, each 0-100, Apply WRGB.
  DYMNC classification and manual WRGB control are physically validated on
  three Magnetic Light II units. Isolated testing confirmed R=0/G=1/B=2/W=3
  and zero turning a channel off. Official-app and local R20/G40/B60/W30 settings
  matched visible color and apparent brightness in practical physical testing.
  This supports direct 0-100 control, not photometric linearity or calibrated
  equivalence. Other revisions are not guaranteed; older Magnetic Light is
  not included.

A2 Max support has been physically validated on one DYNCMC A2 Max unit.
Compatibility with every hardware/firmware revision is not guaranteed.
Brightness is a normalized wire level, not a guarantee of exact equivalence
to official-app percentages. Unrelated Nordic UART devices are not supported.

START

Windows 10/11 x64 with Bluetooth LE is required; Python is not required.
Extract the entire ChihirosLocalController-1.2.0-windows-x64.zip package.
Open the ChihirosLocalController folder
and run ChihirosLocalController.exe. Keep the _internal directory beside it.
The optional ChihirosLocalController-1.2.0-windows-x64-onefile.exe extracts its
runtime and may start slower.

WORKFLOW

1. Close My Chihiros on nearby phones/tablets.
2. Enable Windows Bluetooth and power the intended lights nearby.
3. Launch Chihiros Local Controller.
4. Click Scan for Lights.
5. Select the desired supported light.
6. Adjust its RGB, Brightness or WRGB controls.
7. Click Apply RGB, Apply Brightness or Apply WRGB.
8. Select another supported light, adjust its controls, and Apply as needed.

No Chihiros account, login, internet or Chihiros cloud service is required.
Control is direct over Bluetooth LE. Do not pair manually in Windows Settings.
Use one application window at a time.

Multiple lights of the same model remain distinct by full BLE address.
The dropdown shows model plus a short unique address suffix. Names/labels
are never used as device identity keys. Selection and moving sliders send
nothing. Values are requested inputs, not current brightness readings.
Only the last selected device is remembered locally.
Magnetic Light II WRGB inputs are kept per full address during the session,
separately from Vivid II and A2 Max inputs. Three advertising Magnetic II units
appear as three choices. Apply WRGB connects only to the selected full address,
sends manual mode then R/G/B/W in channel order 0/1/2/3 with about 30 ms between
writes, and disconnects. It sends no clearing prelude, status query or subscription.

Physical GUI validation passed on all three Magnetic Light II units, including
independent selection, selected-device-only control and per-address slider state.
A2 Max and RGB Vivid II regression checks and model-control switching passed.

IMPORTANT

Manual settings may persist on the lamp, including through power loss.
Manual operation overrides automatic operation. To resume your normal
schedule-driven configuration, use My Chihiros after the local connection ends.
Use a physical switch or smart plug for normal on/off timing.

The GUI has no Off button, clear/reset control, schedule editor, RTC,
auto-mode control, firmware/DFU, pairing reset, rename or raw-packet interface.
FE59/DFU is permanently blacklisted; writes use only validated service-scoped NUS.
There is no automatic write retry. A failed write may still have reached the lamp.

PRIVACY AND TROUBLESHOOTING

The existing per-user data directory is retained for v1.0.0 preference compatibility.
Selection and diagnostic files stay local and are not uploaded. Diagnostics may
contain device identifiers; review/redact them before sharing.

If a device is unavailable, scan again and select the intended light.
Keep lights nearby and close any other app holding the Bluetooth connection.
Check the physical result and local diagnostics after any failure.

This application is unsigned. Windows may show a SmartScreen warning.
Verify the download against SHA256SUMS.txt and only run a copy you trust.

LICENSES

LICENSE contains our MIT license and copyright notice.
THIRD_PARTY_LICENSES.txt preserves upstream chihiros-led-control MIT attribution
and required third-party notices. Additional runtime license files are included.
No Chihiros logo or proprietary artwork is used.

Provided as is, without warranty. Use at your own discretion.
