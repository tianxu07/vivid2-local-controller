# v1.3.0 validation and architecture

The 1.3.0 package combines RGB Vivid II, A2 Max, Magnetic Light II, Magnetic
Light, Cooling Fan and Z Light. Model support has physical validation evidence;
the complete 1.3.0 packaged build still requires physical release validation.
See [the validation checklist](release-1.3.0.md).

## Earlier v1.2.0 physical validation

The user completed physical GUI validation of the 1.2.0 development build with
three Magnetic Light II units. All three were discovered separately and correctly
classified from DYMNC advertisements. Each Apply WRGB reached only the selected
canonical address, with no observed cross-device writes or routing mix-ups.
Per-address WRGB inputs were retained during the session. A2 Max and RGB Vivid II
physical regression tests and switching between all three control panels passed.
This validates the tested hardware; it does not establish universal revision
compatibility, photometric calibration or linearity.

## Earlier v1.1.0 physical validation

The user reported successful physical regression testing through the unified GUI:
A2 Max discovery, selection and Apply Brightness; switching to RGB Vivid II;
correct RGB controls and Apply RGB; and control reaching the intended lights.
The user subsequently restored normal schedule-driven operation through My
Chihiros, with no schedule corruption observed.

A2 Max evidence comes from one DYNCMC unit. Wire levels 20 and 60 were visibly
different; manual level 20 persisted through power loss. This does not establish
universal hardware/firmware support or exact official-app percentage equivalence.

## Implementation

- `gui/app.py`: one GUI shell; model-specific controls and address-based selection.
- `gui/controller.py`: discovery filtering, local last-selection persistence,
  canonical identity, collision-safe display suffixes and adapter routing.
- `chihiros/models.py`: verified Vivid II prefixes, the DYNCMC A2 Max candidate,
  DYCX Magnetic Light, DYMNC Magnetic Light II observed on three physical units,
  DYNFAN Cooling Fan and DYSSD Z Light.
- `chihiros/vivid2.py`: existing Vivid II behavior, unchanged.
- `chihiros/a2max_controller.py`: manual mode then channel-0 brightness, no retries.
- `chihiros/a2max_protocol.py`: shared A2 Max packet generation.
- `chihiros/magnetic2_controller.py`: manual mode then complete R/G/B/W state,
  an ordered five-packet allowlist, exact-address checks and no retries.
- `chihiros/magnetic2_protocol.py`: validated frame builder with direct 0..100 levels.
- `chihiros/magnetic1_controller.py`: ordered manual/Red/Green transaction.
- `chihiros/zlight_controller.py`: ordered manual/Cool White/Warm White transaction.
- `chihiros/fan_controller.py`: one-shot telemetry, manual 0..20 device levels,
  and autonomous device-side thermostat configuration followed by disconnect.
- `chihiros/transport.py`: shared scanner, service-scoped NUS and FE59 exclusion.

The full normalized BLE address is the internal identity. Dropdown labels start
with four address digits, extending colliding suffixes until unique. Row ordering
and label changes do not change saved identity. Duplicate advertised names and
same-model devices remain distinct. Sliders are requested inputs, not device
state readback. Magnetic Light, Magnetic II, Z Light and Cooling Fan inputs are
kept separately per address for the current session;
existing Vivid II and A2 Max input behavior is preserved. Only the selected
address is persisted; there is no persistent multi-device settings database.

The GUI import graph excludes the private exact-unit probe CLI. Its optional
local configuration, diagnostics and research material are never packaged.
The public launcher is `chihiros_local_controller.py`; startup performs no BLE work.

Hardware-free tests cover model routing, real Tk visibility, address identity,
duplicate names, suffix collisions, exact packets, brightness boundaries,
failed-write cleanup, no retries and DFU rejection.
See [local packaging checks](releasing.md) for release-content tests and audits.
