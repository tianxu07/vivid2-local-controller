# Magnetic Light II integration — 1.2.0

Magnetic Light II is a supported model in Chihiros Local Controller 1.2.0,
in both the source GUI and Windows packages. The Vivid II and A2 Max packet
builders and transport paths remain
unchanged. No exact private device name or address is included in this document.

## Physical basis

The user physically identified and tested Magnetic Light II hardware, confirming
the observed `DYMNC` advertisement prefix, service-scoped Nordic UART transport,
legacy manual mode and `5A/07` channel writes. Isolated tests established
channel 0=Red, 1=Green, 2=Blue, 3=White and wire zero turning a channel off.
A second Magnetic II set in My Chihiros to R20/G40/B60/W30 matched the locally
controlled unit's visible color and apparent brightness at those values.
This is physical validation of the practical direct 0..100 control convention,
not laboratory-verified linearity or exact photometric equivalence.
No older Magnetic Light support or coverage of all hardware/firmware revisions
is claimed.

Physical GUI validation subsequently passed with all three Magnetic Light II
units: each DYMNC advertisement appeared separately and was correctly classified;
Apply WRGB controlled only the selected canonical address; the other units did
not receive observed control changes. Per-address slider state and switching
between all three supported model panels passed. Physical A2 Max and RGB Vivid II
regression checks also passed. DYMNC and manual WRGB support are therefore
physically validated on multiple Magnetic Light II units.

## Discovery and selection

`chihiros.models.SUPPORTED_MODELS` adds `DYMNC` with four 0..100 controls.
The GUI's existing `detect_supported_model` handles the physically validated
registry separately from the immutable pinned upstream registry in
`constants.detect_model`. Vivid II and A2 Max classification stays unchanged.

`CompatibleDevice.identity` normalizes the complete BLE address. Scan results
are deduplicated only by that identity; identical names remain separate at
different addresses. `build_device_choices` produces display labels independently,
extending colliding address suffixes until they are unique. Selection stores a
full address and Apply resolves its `CompatibleDevice` from `devices_by_address`;
neither display text nor a model name routes a connection.

With three Magnetic II units advertising, all three appear as selectable rows.
No automatic Apply occurs after Scan, selection, preference loading or slider
movement. The selected identity and four input values are captured before
dispatching the worker operation; selection and inputs are disabled while busy.
WRGB inputs have separate variables from Vivid II RGB/A2 Max Brightness and are
cached per address for the current session. They are requests, not device readings.

## Apply WRGB

The new `Magnetic2Controller` validates four whole-number values from 0 through
100 and creates one five-command plan. `Magnetic2ManualSession` reuses the
existing `NusSession` discovery, service-scoped resolution and cleanup, with its
own ordered plan guard. This leaves the existing Vivid II command whitelist
unchanged, including its channel range and byte behavior.

Only the selected normalized address is matched during the connection scan.
Its exact advertised name/model must match as a safety check, and the connected
client address is checked before every write. RX/TX must be direct children of
the unique NUS service, with write-without-response and notify properties.
The entire FE59/DFU subtree stays permanently blacklisted. Endpoint resolution
is repeated before every write. No global characteristic lookup or hard-coded
handle routing is used; diagnostic handles never select an endpoint.

For R20/G40/B60/W30, the exact plan is:

```text
Manual       5A 01 08 00 02 05 0B FF FF 05
Red   ch0=20 5A 01 07 00 03 07 00 14 16
Green ch1=40 5A 01 07 00 04 07 01 28 2C
Blue  ch2=60 5A 01 07 00 05 07 02 3C 3A
White ch3=30 5A 01 07 00 06 07 03 1E 1A
```

There is an approximately 30 ms pause between each pair of application writes,
including between manual mode and Red. There is no separate clearing step.
The session disconnects after White. No status query, subscription, schedule,
RTC/time sync, auto mode, power opcode, rename, reset, pairing, firmware, DFU or
arbitrary raw command is available through this adapter or its GUI.
Failures/cancellation stop immediately without retries, continuation or restoration;
a partly applied state may remain on the lamp. Manual mode stays selected.

The protocol adapter uses the existing frame builder and message-ID increment.
It starts at `00 02` and advances from the last emitted ID, avoiding reserved
IDs/checksums. Literal levels are preserved: the builder's payload substitution
is disabled specifically for Magnetic II, so level 90 stays `5A` instead of
becoming 89. Checksum avoidance remains enabled through offline ID selection.
For example, if Red 88 forces its checksum ID to skip `00 03`, Green continues
at `00 05`; IDs are not reused. These boundary rules have offline test coverage;
the stated physical comparison validates the example values, not every level.

## Source physical testing

```powershell
# Run from the repository root.
& .\.venv\Scripts\python.exe .\chihiros_local_controller.py
```

Close other BLE clients, power all three lights, click **Scan for Lights** and
select the intended address suffix. Confirm three distinct Magnetic Light II
rows. Set the four values and click **Apply WRGB**. Observe the selected unit
and verify the other two did not change. The full address is the internal
identity even when the dropdown shows a shortened label.

Local preferences/logs remain under `%LOCALAPPDATA%\Vivid2Controller` for backward
compatibility and can contain private identifiers. Research logs remain ignored
under `logs/magnetic2/`. Neither is copied into source, examples or tests.

## Offline validation

Tests use synthetic addresses, including three equal advertised names with
colliding suffixes. They exercise real GUI Scan/select/Apply routing through the
new adapter and mocked Bleak APIs, proving five writes only to the selected
address. Other coverage includes model/input state isolation, scan reordering,
preferences, all integer levels including 0/90/100, checksums and ID progression,
unsafe topology, address changes, write failures, cancellation and no retries.
These hardware-free checks complement the user-reported physical GUI validation
above. Release builds and automated tests do not perform live BLE operations.

Integration validation passed all 236 hardware-free tests, including the 16
new Magnetic II protocol/adapter/GUI tests. Tk widget tests passed without
skips, including full four-slider layout and footer visibility.
