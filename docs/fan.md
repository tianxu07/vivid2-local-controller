# DYNFAN Cooling Fan support

Chihiros Local Controller 1.3.0 recognizes the generic BLE advertised-name prefix
`DYNFAN` as **Cooling Fan**. It has been physically validated on a real device;
compatibility with every hardware or firmware revision is not claimed.

Every fan is routed by its canonical full BLE address. Display labels may show
only a short suffix, but labels and advertised names are never routing keys.
Manual speed, locally remembered automatic temperatures and telemetry are kept
separately for each address during the application session.

## Refresh Status

Refresh Status connects only on explicit user action, resolves RX and TX as
children of the unique Nordic UART Service, subscribes only to NUS TX, writes
the known safe query `5A 01 06 00 02 04 01 00` exactly once, waits for the
expected telemetry, stops notifications and disconnects. It does not poll in
the background and does not participate in fan control.

The confirmed telemetry subtype is byte 5 equal to `0x25`. A preceding frame
with byte 5 equal to `0x0A` has the same outer structure but is not telemetry and
is ignored. Refresh Status requires the expected 13-byte frame structure and
the `0x25` subtype, rather than relying only on byte 4 equal to `0x01`.

The physically confirmed sensor fields are:

- humidity: big-endian bytes 6–7 divided by 100;
- room temperature: big-endian bytes 8–9 divided by 100 °C;
- water temperature: big-endian bytes 10–11 divided by 10 °C.

Byte 12 currently has no verified public meaning and is ignored. Byte 5 is a
telemetry sub-command/type, not current fan speed. No current fan-speed value is
exposed or inferred from the manual setting. Malformed, implausible and unrelated
notifications do not complete Refresh Status; it continues waiting for the next
valid telemetry frame. If duplicate valid frames arrive, the first is retained
safely for that refresh.

## Manual

Manual speed accepts direct integer device levels from 0 through 20. Apply
Manual sends one BASE FAN_SPEED command (`0x07`) with data
`[0xFF, device_speed]` and disconnects. It does not send any thermostat
configuration commands. Any explicit speed command switches the physical fan
away from autonomous thermostat behavior.

The scale is confirmed independently of the telemetry sensor layout. The public
reference's
[`aquarium-ble-bridge-ventilator.yaml`](https://github.com/BartdeJonge/chihiros-esphome/blob/main/aquarium-ble-bridge-ventilator.yaml)
defines the user-facing manual-speed number with minimum 0, maximum 20 and step
1, then casts that entity value directly to `uint8_t`. Its
[`chihiros_devices.h`](https://github.com/BartdeJonge/chihiros-esphome/blob/main/components/chihiros_ble/chihiros_devices.h)
passes the same `speed` value to `fan_speed()`, while
[`chihiros_ble.h`](https://github.com/BartdeJonge/chihiros-esphome/blob/main/components/chihiros_ble/chihiros_ble.h)
encodes it unchanged as `{SKIP, speed}`. There is no percentage conversion.
Physical testing also confirmed clearly increasing speeds at levels 5, 10 and
20, with 20 approximately maximum. Manual speed level is not presented as a
percentage.

## Automatic thermostat

Start Temperature and Max-Speed Temperature are integer Celsius protocol bytes;
Max-Speed Temperature must be strictly greater than Start Temperature. Apply
Automatic uses the current local system date/time and sends:

1. BASE AUTH
2. RTC
3. RTC a second time
4. DEVICE AUTH_EXT1
5. DEVICE AUTH_EXT2
6. DEVICE TEMP_THRESH `0x21` with `[start, max, 0xFF]`
7. BASE MODE `[0x23, 0xFF, 0xFF]`
8. BASE MODE `[0x22, 0xFF, 0xFF]`
9. DEVICE AUTH_EXT1
10. DEVICE AUTH_EXT2

Writes are separated by approximately 200 ms. The operation disconnects after
the final command and sends no FAN_SPEED command—not zero, not a remembered
manual speed and not a cleanup value. The fan's own sensor and firmware then
regulate speed autonomously. The Windows application has no polling loop, does
not remain connected and is not required to stay powered on.

Physical testing at approximately 24 °C confirmed high speed with Start 20 /
Max 23, lower speed with Start 23 / Max 26, and a stopped fan with Start 26 /
Max 27. These are observations on the tested unit, not a calibration guarantee.

Automatic threshold read-back is not supported. Start/Max values shown by the
GUI are locally remembered session values unless the user has just entered
them; they are not presented as device readings. Silent Mode is not supported
or exposed.

## BLE safety

The FE59 service and buttonless DFU characteristic remain permanently
blacklisted. NUS endpoints are selected by UUID and parent-service membership,
not handles. The fan UI exposes no pairing, firmware, reset, rename, schedule,
raw-command or Silent Mode operation.

Protocol behavior was independently implemented in Python using the public
btsnoop-derived Cooling Fan research in
[BartdeJonge/chihiros-esphome](https://github.com/BartdeJonge/chihiros-esphome)
as a reference. No C++ source is copied or vendored.
