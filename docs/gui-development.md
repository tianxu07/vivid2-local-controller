# v1.1.0 validation and architecture

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
- `chihiros/models.py`: explicit verified Vivid II prefixes and the DYNCMC candidate.
- `chihiros/vivid2.py`: existing Vivid II behavior, unchanged.
- `chihiros/a2max_controller.py`: manual mode then channel-0 brightness, no retries.
- `chihiros/a2max_protocol.py`: shared A2 Max packet generation.
- `chihiros/transport.py`: shared scanner, service-scoped NUS and FE59 exclusion.

The full normalized BLE address is the internal identity. Dropdown labels start
with four address digits, extending colliding suffixes until unique. Row ordering
and label changes do not change saved identity. Duplicate advertised names and
same-model devices remain distinct. Sliders are shared requested inputs, not
per-device state readback. There is no multi-device settings database.

The GUI import graph excludes the private exact-unit probe CLI. Its optional
local configuration, diagnostics and research material are never packaged.
The public launcher is `chihiros_local_controller.py`; startup performs no BLE work.

Hardware-free tests cover model routing, real Tk visibility, address identity,
duplicate names, suffix collisions, exact packets, brightness boundaries,
failed-write cleanup, no retries and DFU rejection.
See [local packaging checks](releasing.md) for release-content tests and audits.
