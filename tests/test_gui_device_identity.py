from __future__ import annotations

import asyncio
import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from chihiros.constants import RGB_VIVID_II_MODEL
from chihiros.models import A2_MAX_MODEL
from gui.app import ChihirosApplication
from gui.controller import (
    ApplicationController, CompatibleDevice, DevicePreferences, DiscoverySafetyError,
    build_device_choices, preferred_device,
)


def vivid(address: str, name: str = "DYNVSAME") -> CompatibleDevice:
    return CompatibleDevice(name, address, RGB_VIVID_II_MODEL)


def a2max(address: str, name: str = "DYNCMCSAME") -> CompatibleDevice:
    return CompatibleDevice(name, address, A2_MAX_MODEL)


class DeviceChoicesTests(unittest.TestCase):
    def test_two_vivid_devices_with_different_names(self) -> None:
        devices = (vivid("AA:BB:CC:DD:C7:4D", "DYNVFIRST"),
                   vivid("AA:BB:CC:DD:91:A2", "DYNVSECOND"))
        choices = build_device_choices(devices)
        self.assertEqual(choices.labels, ("RGB Vivid II — …C74D", "RGB Vivid II — …91A2"))
        self.assertEqual(choices.addresses, tuple(d.identity for d in devices))
        self.assertEqual(len(choices.devices_by_address), 2)

    def test_duplicate_advertised_names_do_not_merge_either_model(self) -> None:
        for factory in (vivid, a2max):
            with self.subTest(model=factory.__name__):
                devices = (factory("AA:BB:CC:DD:C7:4D"), factory("AA:BB:CC:DD:91:A2"))
                choices = build_device_choices(devices)
                self.assertEqual(len(choices.labels), 2)
                self.assertEqual(len(set(choices.labels)), 2)
                self.assertEqual(set(choices.devices_by_address), {d.identity for d in devices})
                self.assertIs(choices.devices_by_address[devices[0].identity], devices[0])
                self.assertIs(choices.devices_by_address[devices[1].identity], devices[1])

    def test_only_colliding_suffixes_extend_until_unique(self) -> None:
        devices = (vivid("AA:BB:CC:1A:C7:4D"), vivid("AA:BB:CC:2A:C7:4D"),
                   a2max("AA:BB:CC:DD:34:59"))
        choices = build_device_choices(devices)
        self.assertEqual(choices.labels, (
            "RGB Vivid II — …1AC74D", "RGB Vivid II — …2AC74D", "A2 Max — …3459",
        ))
        self.assertEqual(choices.addresses, tuple(d.identity for d in devices))
        # Scan ordering does not affect the suffix selected for each identity.
        reversed_choices = build_device_choices(reversed(devices))
        self.assertEqual(dict(zip(choices.addresses, choices.labels)),
                         dict(zip(reversed_choices.addresses, reversed_choices.labels)))

    def test_suffix_collisions_extend_even_between_models(self) -> None:
        choices = build_device_choices((vivid("AA:BB:CC:01:34:59"), a2max("AA:BB:CC:02:34:59")))
        self.assertEqual(choices.labels, ("RGB Vivid II — …13459", "A2 Max — …23459"))

    def test_collision_can_extend_to_all_identifier_digits(self) -> None:
        choices = build_device_choices((a2max("0A:BB:CC:DD:34:59"), a2max("1A:BB:CC:DD:34:59")))
        self.assertEqual(choices.labels, ("A2 Max — …0ABBCCDD3459", "A2 Max — …1ABBCCDD3459"))

    def test_normalized_address_is_the_only_dictionary_key(self) -> None:
        device = vivid("aa-bb-cc-dd-c7-4d")
        duplicate = vivid("AA:BB:CC:DD:C7:4D")
        choices = build_device_choices((device, duplicate))
        self.assertEqual(choices.addresses, ("AA:BB:CC:DD:C7:4D",))
        self.assertEqual(set(choices.devices_by_address), {"AA:BB:CC:DD:C7:4D"})
        self.assertEqual(choices.devices_by_address[device.identity].address, device.identity)
        self.assertEqual(device.as_core_config().address, device.identity)
        self.assertIs(preferred_device((duplicate,), device), duplicate)

    def test_conflicting_identity_cannot_overwrite_existing_address(self) -> None:
        first = vivid("AA:BB:CC:DD:C7:4D")
        for second in (vivid(first.address, "DYNVOTHER"), a2max(first.address)):
            with self.subTest(second=second), self.assertRaises(DiscoverySafetyError):
                build_device_choices((first, second))

    def test_empty_choices_and_invalid_device(self) -> None:
        choices = build_device_choices(())
        self.assertEqual(choices.addresses, ())
        self.assertEqual(choices.labels, ())
        self.assertEqual(choices.devices_by_address, {})
        for device in (vivid("not-an-address"), vivid("AA:BB:CC:DD:C7:4D", "NORDIC_UART")):
            with self.subTest(device=device), self.assertRaises(DiscoverySafetyError):
                build_device_choices((device,))

    def test_preferences_store_only_last_selection_with_full_normalized_address(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            preferences = DevicePreferences(Path(temp) / "selected.json")
            for device in (vivid("aa-bb-cc-dd-c7-4d"), vivid("aa-bb-cc-dd-91-a2")):
                preferences.save(device)
                saved = json.loads(preferences.path.read_text(encoding="utf-8"))
                self.assertEqual(saved, {"name": device.name, "model": device.model, "address": device.identity})
                self.assertEqual(preferences.load().identity, device.identity)


class DropdownIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.attributes("-alpha", 0.0)
        self.temp = tempfile.TemporaryDirectory()
        self.controller = ApplicationController(Path(self.temp.name))
        self.application = ChihirosApplication(self.root, controller=self.controller)

    def tearDown(self) -> None:
        if hasattr(self, "application"):
            self.application.close()
            self.temp.cleanup()

    def select_row(self, index: int) -> CompatibleDevice:
        self.application.device_combo.current(index)
        self.application._device_selected()
        return self.application._selected_device()

    def test_every_same_model_dropdown_row_resolves_to_full_identity_without_ble(self) -> None:
        for devices in (
            (vivid("AA:BB:CC:DD:C7:4D", "DYNVFIRST"), vivid("AA:BB:CC:DD:91:A2", "DYNVSECOND")),
            (vivid("AA:BB:CC:DD:C7:4D"), vivid("AA:BB:CC:DD:91:A2")),
            (a2max("AA:BB:CC:DD:C7:4D"), a2max("AA:BB:CC:DD:91:A2")),
            (vivid("AA:BB:CC:1A:C7:4D"), vivid("AA:BB:CC:2A:C7:4D")),
        ):
            with self.subTest(devices=devices), mock.patch.object(self.application, "_submit") as submit:
                self.application._scan_completed(devices)
                self.assertEqual(set(self.application.devices_by_address), {d.identity for d in devices})
                self.assertEqual(len(self.application.device_combo.cget("values")), 2)
                self.assertFalse(hasattr(self.application, "devices_by_label"))
                for index, device in enumerate(devices):
                    selected = self.select_row(index)
                    self.assertEqual(selected.identity, device.identity)
                    self.assertEqual(self.application._selected_address, device.identity)
                    self.assertEqual(self.controller.preferences.load().identity, device.identity)
                submit.assert_not_called()

    def test_display_text_changes_cannot_change_selected_identity(self) -> None:
        devices = (vivid("AA:BB:CC:DD:C7:4D"), vivid("AA:BB:CC:DD:91:A2"))
        self.application._scan_completed(devices)
        self.select_row(1)
        self.application.device_var.set("arbitrary presentation text")
        self.assertIs(self.application._selected_device(), devices[1])
        self.application.device_combo.configure(values=("first display", "second display"))
        for index, device in enumerate(devices):
            self.assertIs(self.select_row(index), device)
        # Apply-time lookup must not consult either displayed text or widget index.
        with mock.patch.object(self.application.device_var, "get", side_effect=AssertionError("label lookup")), mock.patch.object(
            self.application.device_combo, "current", side_effect=AssertionError("widget lookup")
        ):
            self.assertIs(self.application._selected_device(), devices[1])

    def test_apply_routes_exact_device_after_index_selection(self) -> None:
        devices = (vivid("AA:BB:CC:1A:C7:4D"), vivid("AA:BB:CC:2A:C7:4D"),
                   a2max("AA:BB:CC:1A:34:59"), a2max("AA:BB:CC:2A:34:59"))
        self.application._scan_completed(devices)
        def run_mocked_operation(_operation, coroutine):
            asyncio.run(coroutine)
        with mock.patch.object(self.application, "_submit", side_effect=run_mocked_operation), mock.patch.object(
            self.controller, "apply_rgb", new_callable=mock.AsyncMock
        ) as rgb, mock.patch.object(self.controller, "apply_brightness", new_callable=mock.AsyncMock) as brightness:
            for index, device in enumerate(devices):
                self.select_row(index)
                if device.model == RGB_VIVID_II_MODEL:
                    self.application.apply_button.invoke()
                    self.assertIs(rgb.await_args.args[0], device)
                else:
                    self.application.apply_brightness_button.invoke()
                    self.assertIs(brightness.await_args.args[0], device)
            self.assertEqual([c.args[0].identity for c in rgb.await_args_list], [d.identity for d in devices[:2]])
            self.assertEqual([c.args[0].identity for c in brightness.await_args_list], [d.identity for d in devices[2:]])

    def test_rescan_reorder_and_suffix_changes_preserve_saved_full_identity(self) -> None:
        first, second = vivid("AA:BB:CC:1A:C7:4D"), vivid("AA:BB:CC:2A:C7:4D")
        self.application._scan_completed((first,))
        self.assertEqual(self.application.device_combo.get(), "RGB Vivid II — …C74D")
        self.application._scan_completed((second, first))
        self.assertIs(self.application._selected_device(), first)
        self.assertEqual(self.application.device_combo.current(), 1)
        self.assertEqual(self.application.device_combo.get(), "RGB Vivid II — …1AC74D")
        self.application._scan_completed((first,))
        self.assertIs(self.application._selected_device(), first)
        self.assertEqual(self.application.device_combo.get(), "RGB Vivid II — …C74D")

    def test_restart_loads_last_saved_address_not_label(self) -> None:
        device = a2max("AA:BB:CC:DD:34:59")
        self.controller.preferences.save(device)
        self.application._load_saved_device()
        self.assertEqual(self.application._selected_address, device.identity)
        self.assertEqual(set(self.application.devices_by_address), {device.identity})
        self.assertEqual(self.application.device_combo.get(), "A2 Max — …3459")

    def test_gui_has_no_clear_control_and_keeps_last_selection_without_ble(self) -> None:
        devices = (a2max("AA:BB:CC:DD:C7:4D"), a2max("AA:BB:CC:DD:91:A2"))

        def button_texts(widget):
            texts = {str(widget.cget("text"))} if widget.winfo_class() in {"Button", "TButton"} else set()
            for child in widget.winfo_children():
                texts.update(button_texts(child))
            return texts

        expected_buttons = {
            "Scan for Devices", "Apply RGB", "Apply Brightness", "Apply RG", "Apply WRGB",
            "Apply White", "Refresh Status", "Apply Manual", "Apply Automatic",
        }
        self.assertEqual(button_texts(self.root), expected_buttons)
        self.assertFalse(hasattr(self.application, "forget_button"))
        self.assertFalse(hasattr(self.application, "forget_device"))
        with mock.patch.object(self.application, "_submit") as submit:
            self.application._scan_completed(devices)
            for index, device in enumerate(devices):
                self.assertIs(self.select_row(index), device)
                self.application._set_busy(True)
                self.application._set_busy(False)
                self.assertEqual(self.controller.preferences.load().identity, device.identity)
                self.assertEqual(button_texts(self.root), expected_buttons)
            submit.assert_not_called()
        self.assertIs(self.application._selected_device(), devices[-1])
        self.assertEqual(set(self.application.devices_by_address), {device.identity for device in devices})

    def test_invalid_widget_selection_clears_identity(self) -> None:
        self.application._scan_completed((vivid("AA:BB:CC:DD:C7:4D"),))
        self.application.device_var.set("")
        self.application._device_selected()
        self.assertIsNone(self.application._selected_device())


if __name__ == "__main__":
    unittest.main()
