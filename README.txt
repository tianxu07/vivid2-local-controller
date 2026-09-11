Chihiros Local Controller 1.4.0
Version 1.4.0 adds isolated upstream-derived
light support and VIVID III fan/telemetry controls. See docs/upstream-support.md
for exact coverage, withheld devices, evidence and verification. The six local
implementations remain unchanged; new devices still require hardware validation.
==============================

Unofficial community tool. Not affiliated with Chihiros Aquatic Studio.

Created by Tianxu Yang
Instagram: @tianxu_07

SUPPORTED MODELS

This 1.3.0 physical-validation build includes all six supported models below.
Validation of the complete packaged build is pending. The current public release
remains v1.2.0; these local artifacts have not been published.

- Chihiros RGB Vivid II: manual Red / Green / Blue, each 0-100.
- Chihiros A2 Max: manual Brightness, 1-100.
- Chihiros Magnetic Light (original / first generation): Red / Green, each
  0-100, Apply RG. DYCX classification, R=0/G=1 and direct normal-range values
  were physically validated on a real device. Local R20/G40 visibly matched the
  official app at R20/G40. Values above 100 (overclock) are intentionally not
  supported. Compatibility with every hardware/firmware revision is not guaranteed.
- Chihiros Magnetic Light II: Red / Green / Blue / White, each 0-100, Apply WRGB.
  DYMNC classification and manual WRGB control are physically validated on
  three Magnetic Light II units. Isolated testing confirmed R=0/G=1/B=2/W=3
  and zero turning a channel off. Official-app and local R20/G40/B60/W30 settings
  matched visible color and apparent brightness in practical physical testing.
  This supports direct 0-100 control, not photometric linearity or calibrated
  equivalence. Other revisions are not guaranteed. Magnetic Light II remains
  a separate four-channel model.
- Chihiros Cooling Fan: DYNFAN, manual device speed 0-20, device-side automatic
  thermostat Start Temperature / Max-Speed Temperature configuration, and
  explicit Refresh Status for room temperature, water temperature and humidity.
  No current fan-speed reading is exposed because no such field is independently
  verified. Max must be greater than Start. The fan regulates
  itself after configuration and disconnect, so the PC does not need to remain
  connected or powered on. Threshold read-back is not supported; displayed
  Start/Max values are locally remembered session values. Silent Mode is not
  supported. DYNFAN Cooling Fan support has been physically validated on a real
  device, without claiming compatibility with every hardware/firmware revision.
- Chihiros Z Light: DYSSD, Cool White and Warm White, each 0-100, Apply White.
  Control is direct over local Bluetooth LE with no Chihiros account or cloud.
  Prefix classification and isolated channel control were physically validated
  on a real device. Compatibility with every hardware/firmware revision is not
  claimed.

A2 Max support has been physically validated on one DYNCMC A2 Max unit.
Compatibility with every hardware/firmware revision is not guaranteed.
Brightness is a normalized wire level, not a guarantee of exact equivalence
to official-app percentages. Unrelated Nordic UART devices are not supported.

START

Windows 10/11 x64 with Bluetooth LE is required; Python is not required.
Extract the entire ChihirosLocalController-1.3.0-windows-x64.zip package.
Open the ChihirosLocalController folder
and run ChihirosLocalController.exe. Keep the _internal directory beside it.
The optional ChihirosLocalController-1.3.0-windows-x64-onefile.exe extracts its
runtime and may start slower.
The previous public v1.2.0 package predates original Magnetic Light, Cooling Fan
and Z Light support. This 1.3.0 package is for physical validation.

WORKFLOW

1. Close My Chihiros on nearby phones/tablets.
2. Enable Windows Bluetooth and power the intended devices nearby.
3. Launch Chihiros Local Controller.
4. Click Scan for Devices.
5. Select the desired supported device.
6. Adjust its RGB, Brightness, RG, WRGB or Cool/Warm White controls.
7. Click Apply RGB, Apply Brightness, Apply RG, Apply WRGB or Apply White.
8. For a Cooling Fan, use Refresh Status, Apply Manual or Apply Automatic.
9. Select another supported device as needed.

No Chihiros account, login, internet or Chihiros cloud service is required.
Control is direct over Bluetooth LE. Do not pair manually in Windows Settings.
Use one application window at a time.

Multiple lights of the same model remain distinct by full BLE address.
The dropdown shows model plus a short unique address suffix. Names/labels
are never used as device identity keys. Selection and moving sliders send
nothing. Values are requested inputs, not current brightness readings.
Only the last selected device is remembered locally.
Magnetic Light RG, Magnetic Light II WRGB and Z Light white inputs are kept per
full address during the session, separately from each other and other models.
Apply RG connects only to the selected full Magnetic Light address, sends manual
mode then Red channel 0 and Green channel 1 with about 30 ms between writes, and
disconnects. It never probes or writes channels 2 or 3 and sends no status query
or notification subscription during Apply.
Apply White connects only to the selected full Z Light address, sends manual
mode then Cool White channel 0 and Warm White channel 1 with about 30 ms between
writes, and disconnects. It never probes or writes channels 2 or 3 and sends no
status query or notification subscription during Apply.
Three advertising Magnetic II units
appear as three choices. Apply WRGB connects only to the selected full address,
sends manual mode then R/G/B/W in channel order 0/1/2/3 with about 30 ms between
writes, and disconnects. It sends no clearing prelude, status query or subscription.

Cooling Fan manual speed, locally remembered automatic Start/Max values and
telemetry are isolated per full BLE address during the session. Refresh Status
subscribes only to NUS TX, sends one safe query, ignores notifications whose
subtype is not 0x25, retains valid humidity/room/water telemetry, unsubscribes
and disconnects; it does not poll continuously. Apply Manual sends only the
requested FAN_SPEED command and switches the fan away from autonomous thermostat
behavior. Apply Automatic sends the full ten-command configuration with about
200 ms between writes, sends no FAN_SPEED command, disconnects, and leaves all
thermostat regulation in the fan. There is no background PC-side fan control.

Physical GUI validation passed on all three Magnetic Light II units, including
independent selection, selected-device-only control and per-address slider state.
A2 Max and RGB Vivid II regression checks and model-control switching passed.

IMPORTANT

Manual light settings may persist on the lamp, including through power loss.
Manual light operation overrides automatic operation. To resume your normal
schedule-driven light configuration, use My Chihiros after the local connection
ends. For a Cooling Fan, Apply Automatic explicitly configures and resumes its
device-side thermostat.
Use a physical switch or smart plug for normal on/off timing.

The GUI has no light Off button, clear/reset control, schedule editor,
standalone RTC action, firmware/DFU, pairing reset, rename or raw-packet
interface. Cooling Fan Silent Mode is not exposed.
FE59/DFU is permanently blacklisted; writes use only validated service-scoped NUS.
There is no automatic write retry. A failed write may still have reached the lamp.

PRIVACY AND TROUBLESHOOTING

The existing per-user data directory is retained for v1.0.0 preference compatibility.
Selection and diagnostic files stay local and are not uploaded. Diagnostics may
contain device identifiers; review/redact them before sharing.

If a device is unavailable, scan again and select the intended device.
Keep devices nearby and close any other app holding the Bluetooth connection.
Check the physical result and local diagnostics after any failure.

This application is unsigned. Windows may show a SmartScreen warning.
Verify the download against SHA256SUMS.txt and only run a copy you trust.

LICENSES

LICENSE contains our MIT license and copyright notice.
THIRD_PARTY_LICENSES.txt preserves upstream chihiros-led-control MIT attribution
and required third-party notices. Additional runtime license files are included.
No Chihiros logo or proprietary artwork is used.

Provided as is, without warranty. Use at your own discretion.
