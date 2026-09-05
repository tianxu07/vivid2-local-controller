# Magnetic Light integration (original / first generation)

Chihiros Local Controller supports manual Red/Green control for original
Chihiros Magnetic Light advertisements beginning with the generic public prefix
`DYCX`. This classification and control path were physically validated on a
real device. Compatibility with every hardware or firmware revision is not
claimed.

The tested device exposed the standard Nordic UART Service (NUS):

- service `6e400001-b5a3-f393-e0a9-e50e24dcca9e`;
- RX/write `6e400002-b5a3-f393-e0a9-e50e24dcca9e`;
- TX/notify `6e400003-b5a3-f393-e0a9-e50e24dcca9e`.

The FE59 Nordic Buttonless DFU service and its DFU characteristic remain
permanently blacklisted. UUID plus direct parent-service membership selects NUS
endpoints; GATT handles are never selectors.

## Physically validated scope

The official app exposes exactly Red and Green for this device. Physical testing
confirmed Red is wire channel 0, Green is wire channel 1, and direct local values
R20/G40 visibly match the official app at R20/G40. The GUI accepts only integers
from 0 through 100. It does not expose overclock values above 100.

A safe status query was observed as `5A 01 06 00 02 04 01 00`, with the tested
device returning a notification beginning `5B 16`. Apply RG does not issue that
query or interpret undocumented response fields.

## Apply RG transaction

Apply RG validates and connects to the selected canonical full BLE address,
resolves RX only beneath the unique NUS service, and emits exactly three writes:

1. manual mode;
2. Red on channel 0;
3. Green on channel 1.

For the physically compared R20/G40 values, the exact packets are:

```text
5A 01 08 00 02 05 0B FF FF 05
5A 01 07 00 03 07 00 14 16
5A 01 07 00 04 07 01 28 2C
```

There is approximately 30 ms between writes. The session then disconnects
cleanly. No clearing prelude, status query, notification subscription, retry,
restoration command, or channel 2/3 write is part of this operation.

Multiple devices, including devices with the same advertised name, remain
independently routable by canonical full BLE address. Requested Red/Green values
are retained per address only for the current GUI session.
