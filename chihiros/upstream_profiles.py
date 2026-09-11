"""Explicit LED evidence from TheMicDiet revision 05ae7654b3d4.

Local model detection is authoritative and is never extended here. Family
assignments plus const.py authorize transport; naming conventions alone do not.
See docs/upstream-support.md for the evidence and deliberately withheld models.
"""

from dataclasses import dataclass
from enum import Enum

from .models import supported_model as local_model


class Transport(str, Enum):
    NUS = "NUS"
    HM10 = "HM10"


@dataclass(frozen=True)
class Profile:
    name: str
    prefixes: tuple[str, ...]
    controls: tuple[str, ...]
    transport: Transport
    evidence: str
    vivid3: bool = False
    minimum: int = 0
    maximum: int = 100


W = ("White",)
RGB = ("Red", "Green", "Blue")
WRGB = RGB + ("White",)
REGISTRY = "Pinned explicit device family + const.py transport-family declaration"
NUS, HM10 = Transport.NUS, Transport.HM10
PROFILES = (
    Profile("A II", ("DYNA2", "DYNA2N"), W, NUS, REGISTRY),
    Profile("New C (new)", ("DYNC2",), W, NUS, REGISTRY),
    Profile("RGB+APLUS (new)", ("DYNARGB",), RGB, NUS, REGISTRY),
    Profile("SEA_LED", ("DYSEA",), WRGB, NUS, REGISTRY),
    Profile("WRGB II (new)", ("DYNT90", "DYNW30", "DYNW45", "DYNW60", "DYNW90", "DYNW12P", "DYNWRGB"), RGB, NUS, REGISTRY),
    Profile("C II", ("DYNC2N",), W, NUS, REGISTRY),
    Profile("Commander 4 (new)", ("DYNLED",), WRGB, NUS, REGISTRY),
    Profile("A Series", ("DYA",), W, HM10, REGISTRY),
    Profile("New C (legacy)", ("DYC",), W, HM10, REGISTRY),
    Profile("RGB+APLUS (legacy)", ("DYARGB", "DYRGBA+", "DYRGBA"), RGB, HM10, REGISTRY),
    Profile("RGB VIVID", ("DYREE",), RGB, HM10, REGISTRY),
    Profile("Commander X", ("DYONE",), W, HM10, REGISTRY),
    Profile("X300", ("DYTWO",), ("White", "Warm White"), HM10, REGISTRY),
    Profile("WRGB II (legacy)", ("DYWRGB",), RGB, HM10, REGISTRY),
    Profile("Commander 4 (legacy)", ("DYLED",), WRGB, HM10, REGISTRY),
    Profile("WRGB VIVID III", ("DYVVD3",), WRGB, NUS,
            "const.py explicitly names VIVID III; protocol.md captures corroborate", True),
)


@dataclass(frozen=True)
class Unavailable:
    name: str
    prefixes: tuple[str, ...]
    reason: str


UNAVAILABLE = (
    Unavailable("Z Light TINY", ("DYZSD",), "Same product as DYSSD does not establish transport"),
    Unavailable("Tiny Terrarium Egg", ("DYDD",), "Channel metadata only; transport unresolved"),
    Unavailable("Commander 1", ("DYCOM",), "Requires explicit White/RGB/WRGB layout; transport unresolved"),
    Unavailable("WRGB II Pro", ("DYWPRO30", "DYWPRO45", "DYWPRO60", "DYWPRO80", "DYWPRO90", "DYWPR120"), "SeaLed assignment is inferred from naming conventions"),
    Unavailable("WRGB II Slim", ("DYSILN", "DYSL30", "DYSL45", "DYSL60", "DYSL90", "DYSL120", "DYSL12"), "SeaLed assignment is inferred from naming conventions"),
    Unavailable("Universal WRGB", ("DYU550", "DYU600", "DYU700", "DYU800", "DYU920", "DYU1000", "DYU1200", "DYU1500"), "SeaLed assignment is inferred from naming conventions"),
    Unavailable("C II RGB", ("DYNCRGP", "DYNCRGB"), "Transport-family evidence relies on new-generation inference"),
)
ACCESSORIES = ("DYDOSE", "DYTDOS", "DYNDOS", "DYAPRCO2", "DYCO2", "DYNSCO2",
               "DYCHIL", "DYFAN", "DYECO", "DYGATE", "DYNGATE", "DYHET",
               "DYMIXR", "DYPWR", "DYPWSK", "DYNDOC")
_PREFIXES = tuple(sorted(((prefix, profile) for profile in PROFILES for prefix in profile.prefixes),
                         key=lambda pair: len(pair[0]), reverse=True))


def profile_for(name: str | None) -> Profile | None:
    if not isinstance(name, str) or local_model(name) is not None:
        return None
    normalized = name.strip().upper()
    blocked = ACCESSORIES + tuple(p for item in UNAVAILABLE for p in item.prefixes)
    if normalized.startswith(blocked):
        return None
    return next((profile for prefix, profile in _PREFIXES if normalized.startswith(prefix)), None)


def supported_model(name: str | None):
    """GUI-only union; existing controllers continue using the local registry."""
    return local_model(name) or profile_for(name)


def detect_supported_model(name: str | None) -> str | None:
    model = supported_model(name)
    return model.name if model is not None else None
