# A2 Max manual CLI — developer-only compatibility tooling

The public GUI is the supported user entry point. The separate exact-unit CLI
is retained for controlled developer testing and is excluded from the Windows
package. It shares the GUI's A2 Max packet builders.

The private unit identity is read from the ignored local
`config/a2max-private-unit.json` file. The file contains string `name` and
`address` fields for one explicitly confirmed DYNCMC unit. Missing or invalid
configuration fails closed. Identity is loaded once per process, and both exact
name and normalized full address must match the advertisement before connection.
No private configuration or example containing a real device identity is shipped.

The CLI accepts only integer normalized wire levels 1–100 and an explicit
`--dry-run` or `--live` gate. Public tests use synthetic identities and mocked BLE.
No raw-packet, channel, rename, firmware, pairing, RTC or schedule interface is added.

For a developer with the private configuration already in place:

```powershell
python .\chihirosctl.py a2max-manual --level 60 --dry-run
```

Dry-run prints the exact packets and checksum equations without Bluetooth.
The existing live gate remains available only as an explicit developer action;
packaging/testing must never invoke it.

At wire level 60, the manual-mode and white-channel packets remain:

```text
5A 01 08 00 02 05 0B FF FF 05
5A 01 07 00 03 07 00 3C 3E
```

Checksums XOR every byte after the family byte through the payload:
`01 XOR 08 XOR 00 XOR 02 XOR 05 XOR 0B XOR FF XOR FF = 05`;
`01 XOR 07 XOR 00 XOR 03 XOR 07 XOR 00 XOR 3C = 3E`.
The inter-packet wait remains 30 ms, with one attempt per packet.
Literal levels are preserved; reserved message-ID/checksum avoidance is unchanged.

Live CLI diagnostics retain execution phases, exception type/repr/str, attempted
and completed write counts, and best-effort session-log paths on failure.
A failed write may still have reached the lamp; no retry or guessed rollback occurs.
The complete FE59 subtree stays blacklisted and NUS endpoints are service-scoped.
Manual settings may persist; disconnecting or power cycling is not a rollback.
