"""Constants and read-only model metadata for the local Chihiros controller."""

from __future__ import annotations


UPSTREAM_COMMIT = "05ae7654b3d4de1df887be3827cd85dd33fe0af6"
UPSTREAM_ROOT = f"https://github.com/TheMicDiet/chihiros-led-control/blob/{UPSTREAM_COMMIT}"
CONTROLLER_VERSION = "5.0.0"
WINDOWS_APP_VERSION = "1.1.0"
DEVELOPMENT_APP_VERSION = WINDOWS_APP_VERSION  # Compatibility for older source integrations.

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"

DFU_SERVICE_UUID = "0000fe59-0000-1000-8000-00805f9b34fb"
DFU_BUTTONLESS_UUID = "8ec90003-f315-4f60-9fb8-838830daea50"
BLACKLISTED_SERVICE_UUIDS = frozenset({DFU_SERVICE_UUID})
BLACKLISTED_CHARACTERISTIC_UUIDS = frozenset({DFU_BUTTONLESS_UUID})

# Observed on the user's RGB Vivid II. UUID plus parent-service membership is
# the identity mechanism; these handles are diagnostics only.
ORIGINAL_DEVICE_NUS_SERVICE_HANDLE = 14
ORIGINAL_DEVICE_NUS_RX_HANDLE = 15
ORIGINAL_DEVICE_NUS_TX_HANDLE = 17

RGB_CHANNELS = {"red": 0, "green": 1, "blue": 2}
RGB_VIVID_II_MODEL = "RGB Vivid II"
RGB_VIVID_II_NEW_BLE_LED_PREFIXES = ("DYRGBV",)
RGB_VIVID_II_SEA_LED_PREFIXES = ("DYNVVD", "DYNV")
RGB_VIVID_II_PREFIXES = RGB_VIVID_II_NEW_BLE_LED_PREFIXES + RGB_VIVID_II_SEA_LED_PREFIXES

# One physically identified A2 Max advertised with this prefix. This is
# deliberately not a KNOWN_PREFIX_MODELS entry. The development GUI explicitly
# opts into this candidate via models.py; it is not universal A2 Max coverage.
A2_MAX_CANDIDATE_PREFIX = "DYNCMC"
A2_MAX_CANDIDATE_SAMPLE_COUNT = 1

STATUS_RESPONSE_WAIT_SECONDS = 1.0
BATCH_WRITE_DELAY_SECONDS = 0.03

# Upstream model registry at UPSTREAM_COMMIT. Longest prefix wins.
KNOWN_PREFIX_MODELS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("DYRGBV", "RGB Vivid II"),
            ("DYNVVD", "RGB Vivid II"),
            ("DYNV", "RGB Vivid II"),
            ("DYVVD3", "WRGB Vivid III"),
            ("DYWPRO30", "WRGB II Pro"),
            ("DYWPRO45", "WRGB II Pro"),
            ("DYWPRO60", "WRGB II Pro"),
            ("DYWPRO80", "WRGB II Pro"),
            ("DYWPRO90", "WRGB II Pro"),
            ("DYWPR120", "WRGB II Pro"),
            ("DYSL120", "WRGB II Slim"),
            ("DYSL30", "WRGB II Slim"),
            ("DYSL45", "WRGB II Slim"),
            ("DYSL60", "WRGB II Slim"),
            ("DYSL90", "WRGB II Slim"),
            ("DYSL12", "WRGB II Slim"),
            ("DYSILN", "WRGB II Slim"),
            ("DYU1500", "Universal WRGB"),
            ("DYU1200", "Universal WRGB"),
            ("DYU1000", "Universal WRGB"),
            ("DYU920", "Universal WRGB"),
            ("DYU800", "Universal WRGB"),
            ("DYU700", "Universal WRGB"),
            ("DYU600", "Universal WRGB"),
            ("DYU550", "Universal WRGB"),
            ("DYNW12P", "WRGB II"),
            ("DYNWRGB", "WRGB II"),
            ("DYNW30", "WRGB II"),
            ("DYNW45", "WRGB II"),
            ("DYNW60", "WRGB II"),
            ("DYNW90", "WRGB II"),
            ("DYWRGB", "WRGB II"),
            ("DYNT90", "WRGB II"),
            ("DYNCRGP", "C II RGB"),
            ("DYNCRGB", "C II RGB"),
            ("DYNC2N", "C II"),
            ("DYNC2", "New C"),
            ("DYNARGB", "RGB+APLUS"),
            ("DYARGB", "RGB+APLUS"),
            ("DYRGBA+", "RGB+APLUS"),
            ("DYRGBA", "RGB+APLUS"),
            ("DYDOSED", "Dosing pump"),
            ("DYDOSE", "Dosing pump"),
            ("DYTDOS", "Dosing pump"),
            ("DYNDOS", "Dosing pump"),
            ("DYNA2N", "A II"),
            ("DYNA2", "A II"),
            ("DYNLED", "Commander 4"),
            ("DYLED", "Commander 4"),
            ("DYSSD", "Z Light TINY"),
            ("DYZSD", "Z Light TINY"),
            ("DYDD", "Tiny Terrarium Egg"),
            ("DYREE", "RGB Vivid"),
            ("DYSEA", "SEA_LED"),
            ("DYONE", "Commander X"),
            ("DYTWO", "X300"),
            ("DYCOM", "Commander 1"),
            ("DYA", "A Series"),
            ("DYC", "New C"),
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)


def detect_model(advertised_name: str | None) -> str | None:
    """Return a source-derived model name for a known advertisement prefix."""
    normalized = (advertised_name or "").strip().upper()
    for prefix, model in KNOWN_PREFIX_MODELS:
        if normalized.startswith(prefix):
            return model
    return None


def vivid2_curve_family(advertised_name: str) -> str:
    """Return the upstream model family's 0x5A/0x06 encoding for a Vivid II."""
    normalized = advertised_name.strip().upper()
    if any(normalized.startswith(prefix) for prefix in RGB_VIVID_II_NEW_BLE_LED_PREFIXES):
        return "NewBleLed"
    if any(normalized.startswith(prefix) for prefix in RGB_VIVID_II_SEA_LED_PREFIXES):
        return "SeaLed"
    raise ValueError(f"Advertised name {advertised_name!r} is not a source-verified RGB Vivid II")
