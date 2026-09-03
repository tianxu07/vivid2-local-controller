"""Fully local, account-free Chihiros RGB Vivid II controller."""

from .constants import RGB_VIVID_II_MODEL
from .vivid2 import DeviceConfig, Vivid2Controller

__all__ = ["DeviceConfig", "RGB_VIVID_II_MODEL", "Vivid2Controller"]

