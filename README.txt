# Vivid2Controller v1.0.0

Unofficial local Windows controller for Chihiros RGB Vivid II.

Created by Tianxu Yang
Instagram: @tianxu_07

## WHAT THIS APP DOES

Vivid2Controller allows you to control the manual RGB brightness of a supported
Chihiros RGB Vivid II directly from a Windows PC using Bluetooth Low Energy.

No My Chihiros account or cloud connection is required.

Current features:

- Scan for nearby supported RGB Vivid II lights
- Select your light
- Set Red / Green / Blue individually from 0 to 100
- Apply the RGB settings directly over Bluetooth

This version is intentionally limited to manual RGB control.

It does NOT provide:

- Schedule editing
- Firmware updates
- DFU
- Factory reset
- Pairing reset

## BEFORE USING

Make sure:

1. Your RGB Vivid II is powered on.
2. Bluetooth is enabled on your Windows PC.
3. Close the My Chihiros app on nearby phones before using this controller.

This is recommended because the phone and Windows PC may otherwise compete for
the same Bluetooth connection.

If the controller has trouble finding or connecting to the light, temporarily
turn off Bluetooth on the phone that normally controls the Vivid II and try
again.

## HOW TO USE

1. Extract the entire ZIP file.

2. Run:

   Vivid2Controller.exe

3. Click:

   Scan for Vivid II

4. Select your RGB Vivid II from the detected device list.

5. Set Red, Green, and Blue to values between 0 and 100.

6. Click:

   Apply RGB

The controller will connect to the light, apply the RGB values, and disconnect
automatically.

Applying non-zero RGB values will also turn on a powered Vivid II in Manual mode.

Use a physical switch or smart plug if you want to control power on/off.

## IMPORTANT

Manual RGB settings may override the light's automatic mode while active.

This application does not modify firmware and does not use the firmware-update
or DFU interface.

Do not run multiple Bluetooth controllers for the same light at the same time.

## WINDOWS SMARTSCREEN

Because this application is currently unsigned, Windows SmartScreen may display
a warning when you run it for the first time.

If you trust the copy you received, you may need to select:

More info -> Run anyway

## PRIVACY

Vivid2Controller works locally over Bluetooth.

It does not require:

- A Chihiros account
- Chihiros cloud services
- Login credentials

## DISCLAIMER

This is an unofficial community tool and is not affiliated with, endorsed by,
or supported by Chihiros Aquatic Studio.

Use at your own discretion.

## OPEN-SOURCE CREDITS

Parts of the Bluetooth protocol implementation are based on open-source work
from the chihiros-led-control project by TheMicDiet.

See THIRD_PARTY_LICENSES.txt for license and attribution information.

## AUTHOR

Tianxu Yang
Instagram: @tianxu_07
