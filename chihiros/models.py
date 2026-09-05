"""Development GUI model capabilities, separate from the upstream registry."""

from dataclasses import dataclass

from .constants import (
    A2_MAX_CANDIDATE_PREFIX, A2_MAX_CANDIDATE_SAMPLE_COUNT,
    COOLING_FAN_MODEL, COOLING_FAN_PREFIXES,
    FAN_MANUAL_SPEED_MAX, FAN_MANUAL_SPEED_MIN,
    MAGNETIC_LIGHT_MODEL, MAGNETIC_LIGHT_PREFIXES,
    MAGNETIC_II_MODEL, MAGNETIC_II_PREFIXES,
    RGB_VIVID_II_MODEL, RGB_VIVID_II_PREFIXES, detect_model,
    Z_LIGHT_MODEL, Z_LIGHT_PREFIXES,
)

A2_MAX_MODEL = "A2 Max"


@dataclass(frozen=True)
class ModelMetadata:
    name: str
    prefixes: tuple[str, ...]
    controls: tuple[str, ...]
    minimum: int
    maximum: int
    candidate: bool = False
    physical_samples: int | None = None


SUPPORTED_MODELS = (
    ModelMetadata(RGB_VIVID_II_MODEL, RGB_VIVID_II_PREFIXES, ("Red", "Green", "Blue"), 0, 100),
    # Extend this tuple only when another advertisement prefix has evidence.
    # DYNCMC is a candidate from one physically confirmed unit, not universal coverage.
    ModelMetadata(A2_MAX_MODEL, (A2_MAX_CANDIDATE_PREFIX,), ("Brightness",), 1, 100,
                  candidate=True, physical_samples=A2_MAX_CANDIDATE_SAMPLE_COUNT),
    ModelMetadata(MAGNETIC_LIGHT_MODEL, MAGNETIC_LIGHT_PREFIXES, ("Red", "Green"), 0, 100),
    ModelMetadata(MAGNETIC_II_MODEL, MAGNETIC_II_PREFIXES, ("Red", "Green", "Blue", "White"), 0, 100),
    ModelMetadata(
        COOLING_FAN_MODEL,
        COOLING_FAN_PREFIXES,
        ("Fan",),
        FAN_MANUAL_SPEED_MIN,
        FAN_MANUAL_SPEED_MAX,
    ),
    ModelMetadata(Z_LIGHT_MODEL, Z_LIGHT_PREFIXES, ("Cool White", "Warm White"), 0, 100),
)


def supported_model(advertised_name: str | None) -> ModelMetadata | None:
    if not isinstance(advertised_name, str):
        return None
    # Preserve the existing upstream Vivid II detection exactly.
    if detect_model(advertised_name) == RGB_VIVID_II_MODEL:
        return SUPPORTED_MODELS[0]
    normalized = advertised_name.strip().upper()
    for model in SUPPORTED_MODELS[1:]:
        if any(normalized.startswith(prefix) for prefix in model.prefixes):
            return model
    return None


def detect_supported_model(advertised_name: str | None) -> str | None:
    metadata = supported_model(advertised_name)
    return metadata.name if metadata is not None else None
