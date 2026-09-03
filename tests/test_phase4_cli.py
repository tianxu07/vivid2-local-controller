from __future__ import annotations

import contextlib
import io
import sys
import unittest

import chihirosctl


class Phase4CliTests(unittest.TestCase):
    def test_manual_dry_run_uses_no_bleak_import(self) -> None:
        before = "bleak" in sys.modules
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = chihirosctl.main(
                ["manual", "vivid2", "--red", "10", "--green", "20", "--blue", "30", "--dry-run"]
            )
        self.assertEqual(result, 0)
        self.assertEqual("bleak" in sys.modules, before)
        self.assertIn("5A 01 08 00 02 05 0B FF FF 05", output.getvalue())
        self.assertIn("Excluded: RTC, schedule writes", output.getvalue())

    def test_off_dry_run_prints_required_warning(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = chihirosctl.main(["off", "vivid2", "--dry-run"])
        self.assertEqual(result, 0)
        self.assertIn(chihirosctl.OFF_WARNING, output.getvalue())

    def test_no_generic_on_command_exists(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                chihirosctl.build_parser().parse_args(["on", "vivid2"])

    def test_cli_rejects_invalid_rgb_before_ble(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                chihirosctl.build_parser().parse_args(
                    ["manual", "vivid2", "--red", "101", "--green", "20", "--blue", "30"]
                )


if __name__ == "__main__":
    unittest.main()
