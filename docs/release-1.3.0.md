# Chihiros Local Controller 1.3.0 — physical-validation build

This local build awaits physical release validation. It has not been published;
v1.2.0 remains the previous public release. The six supported device families
use direct local BLE without a Chihiros account or cloud service.

| Model | Generic prefix | Controls and range |
| --- | --- | --- |
| RGB Vivid II | DYNV (also existing DYNVVD / DYRGBV) | Red / Green / Blue, 0–100 |
| A2 Max | DYNCMC | Brightness, existing 1–100 range |
| Magnetic Light II | DYMNC | Red / Green / Blue / White, 0–100 |
| Magnetic Light | DYCX | Red channel 0 / Green channel 1, 0–100 |
| Cooling Fan | DYNFAN | Manual Speed Level 0–20; automatic thermostat; telemetry |
| Z Light | DYSSD | Cool White channel 0 / Warm White channel 1, 0–100 |

Magnetic Light II support was physically validated on multiple real devices.
Original Magnetic Light, Cooling Fan and Z Light support were each physically
validated on a real device. Existing Vivid II and A2 Max behavior is preserved.
This evidence does not guarantee compatibility with every hardware or firmware
revision. There is no overclock or Unknown / Experimental Channel Mode.

Cooling Fan Apply Automatic writes the complete validated ten-command sequence,
with approximately 200 ms between commands, and disconnects. It sends no
FAN_SPEED command. Regulation then runs in the fan using its own sensor; the PC
does not remain connected or poll temperatures. Start Temperature and Max-Speed
Temperature are locally remembered inputs, with Max strictly greater than Start.

Refresh Status displays Water Temperature, Room Temperature and Humidity. It
accepts the expected 13-byte frame with `x[5] == 0x25`, ignores `0x0A`, and uses:

- humidity: `((x[6] << 8) | x[7]) / 100.0` percent;
- room temperature: `((x[8] << 8) | x[9]) / 100.0` °C;
- water temperature: `((x[10] << 8) | x[11]) / 10.0` °C.

Byte 12 is unverified and ignored. Current Fan Speed, Silent Mode and automatic
threshold read-back are not supported. Manual Speed Level is a device scale,
not a percentage.

Magnetic Light and Z Light Apply each send manual mode followed by their two
requested channels, with approximately 30 ms between writes, then disconnect.
Neither transaction writes channels 2/3, queries status or subscribes.

All routing uses full normalized BLE addresses and service-scoped NUS endpoints.
FE59 and Buttonless DFU remain permanently blacklisted. No pairing, reset,
rename, firmware, raw-command UI or hard-coded handle routing is exposed.

## Physical validation checklist

Run the recommended executable from the complete extracted
`ChihirosLocalController-1.3.0-windows-x64.zip` payload. Verify its title shows
1.3.0. Close other BLE clients before explicitly scanning or applying controls.

1. Discover and select each of the six supported models; verify the appropriate
   controls and ranges appear when switching between devices.
2. Where multiple same-model units are available, verify independent selection,
   selected-device-only control and retained per-address inputs.
3. Verify Red/Green on Magnetic Light and Cool/Warm White on Z Light separately,
   using only channels 0/1 and normal levels.
4. Verify Cooling Fan manual levels 0–20, all three telemetry readings, and
   autonomous behavior after Apply Automatic and PC disconnection. The existing
   approximately 24 °C observations were high speed at Start 20 / Max 23, lower
   speed at 23 / 26, and stopped at 26 / 27.
5. Verify the established Vivid II, A2 Max and Magnetic Light II behavior, and
   restore the desired normal device operation through My Chihiros when needed.

Keep physical identifiers and results in private local notes. Do not commit
preferences, logs or captures. Artifact creation and launch checks alone do not
complete this physical checklist. See [packaging instructions](releasing.md).
