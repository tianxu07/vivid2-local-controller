from __future__ import annotations

import ast
import importlib.util
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

import chihiros_local_controller
import vivid2_gui
from chihiros import a2max
from chihiros.constants import WINDOWS_APP_VERSION, DEVELOPMENT_APP_VERSION
from gui import app
from gui.controller import DISPLAY_NAME

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_audit", ROOT / "packaging" / "release_audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_and_title_are_public_v110(self) -> None:
        self.assertEqual(WINDOWS_APP_VERSION, "1.1.0")
        self.assertEqual(DEVELOPMENT_APP_VERSION, WINDOWS_APP_VERSION)
        self.assertEqual(DISPLAY_NAME, "Chihiros Local Controller")
        text = (ROOT / "gui" / "app.py").read_text(encoding="utf-8")
        self.assertIn("Local manual control for RGB Vivid II and A2 Max", text)
        self.assertNotIn("development support", text)
        self.assertIn("Unofficial community tool. Not affiliated with Chihiros Aquatic Studio.", text)
        self.assertIn("Created by Tianxu Yang", text)
        self.assertIn("@tianxu_07", text)

    def test_launchers_import_the_same_main_without_startup_side_effects(self) -> None:
        self.assertIs(chihiros_local_controller.main, app.main)
        self.assertIs(vivid2_gui.main, app.main)
        with mock.patch.object(app, "main", return_value=0) as main:
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(str(ROOT / "chihiros_local_controller.py"), run_name="__main__")
            self.assertEqual(result.exception.code, 0)
            main.assert_called_once_with()
        with mock.patch.object(chihiros_local_controller, "main", return_value=0) as main:
            with self.assertRaises(SystemExit):
                runpy.run_path(str(ROOT / "vivid2_gui.py"), run_name="__main__")
            main.assert_called_once_with()

    def test_both_specs_use_public_entry_point_and_preserve_notices(self) -> None:
        for filename in ("ChihirosLocalController.spec", "ChihirosLocalController-onefile.spec"):
            text = (ROOT / filename).read_text(encoding="utf-8")
            ast.parse(text)
            with self.subTest(spec=filename):
                self.assertIn('project_root / "chihiros_local_controller.py"', text)
                self.assertIn('name="ChihirosLocalController"', text)
                for notice in ("README.txt", "LICENSE", "THIRD_PARTY_LICENSES.txt"):
                    self.assertIn(f'project_root / "{notice}"', text)
                self.assertIn('"chihiros.a2max"', text)  # Explicit private CLI exclusion.
                self.assertIn('"chihirosctl"', text)
                self.assertNotIn('project_root / "vivid2_gui.py"', text)

    def test_windows_version_resource_is_consistent(self) -> None:
        text = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn("filevers=(1, 1, 0, 0)", text)
        self.assertIn("prodvers=(1, 1, 0, 0)", text)
        self.assertIn("u'ChihirosLocalController.exe'", text)
        self.assertIn("u'Chihiros Local Controller'", text)
        self.assertNotIn("1.0.0", text)

    def test_public_readmes_document_both_models_and_limits(self) -> None:
        for filename in ("README.md", "README.txt"):
            text = (ROOT / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                for required in ("1.1.0", "RGB Vivid II", "A2 Max", "DYNCMC", "ChihirosLocalController.exe",
                                 "Scan for Lights", "Apply RGB", "Apply Brightness", "smart plug",
                                 "THIRD_PARTY_LICENSES.txt", "Tianxu Yang", "@tianxu_07"):
                    self.assertIn(required, text)
                self.assertIn("not guaranteed", text.replace("**", ""))
                self.assertNotIn("1.1.0.dev1", text)

    def test_builder_requires_fresh_versioned_paths_and_has_no_publication_step(self) -> None:
        text = (ROOT / "packaging" / "build_release.ps1").read_text(encoding="utf-8")
        self.assertIn('"release\\v$Version-$RunName"', text)
        self.assertIn("Fresh output directories are required", text)
        self.assertNotIn("Remove-Item", text)
        for forbidden in ("git push", "git merge", "git tag", "gh release", "Start-Process"):
            self.assertNotIn(forbidden, text)
        self.assertIn("SHA256SUMS.txt", text)
        self.assertIn("--private-patterns", text)


class PrivateIdentityConfigTests(unittest.TestCase):
    def test_no_private_config_fails_closed_without_bluetooth(self) -> None:
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            a2max, "PRIVATE_PROBE_CONFIG", Path(temp) / "missing.json"
        ), mock.patch.dict(sys.modules, {"bleak": None}):
            with self.assertRaisesRegex(ValueError, "requires valid local config"):
                a2max._private_probe_identity.__wrapped__()

    def test_private_identity_is_validated_and_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "unit.json"
            path.write_text(json.dumps({"name": "DYNCMC001122334455", "address": "00-11-22-33-44-55"}), encoding="utf-8")
            with mock.patch.object(a2max, "PRIVATE_PROBE_CONFIG", path):
                self.assertEqual(a2max._private_probe_identity.__wrapped__(),
                                 ("DYNCMC001122334455", "00:11:22:33:44:55"))
                path.write_text(json.dumps({"name": "NORDIC_UART", "address": "00:11:22:33:44:55"}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    a2max._private_probe_identity.__wrapped__()


class ReleaseAuditTests(unittest.TestCase):
    def make_folder(self, root: Path) -> Path:
        folder = root / audit.PRODUCT
        folder.mkdir()
        for filename in audit.REQUIRED_FILES:
            (folder / filename).write_bytes(b"synthetic required file")
        (folder / "_internal").mkdir()
        (folder / "_internal" / "runtime.dll").write_bytes(b"synthetic runtime")
        return folder

    def test_complete_folder_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = self.make_folder(Path(temp))
            result = audit.audit_folder(folder, (), inspect_executable=False)
            self.assertEqual(result["payload_files"], 7)

    def test_every_required_file_is_enforced(self) -> None:
        for filename in audit.REQUIRED_FILES:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp:
                folder = self.make_folder(Path(temp))
                (folder / filename).unlink()
                with self.assertRaisesRegex(audit.AuditError, "Missing required"):
                    audit.audit_folder(folder, (), inspect_executable=False)

    def test_private_development_paths_and_secret_files_are_rejected(self) -> None:
        for path in ("logs/session.json", "captures/raw.bin", ".venv/runtime.dll", "config/unit.json",
                     "research.apk", "app.xapk", "classes.dex", "jadx-output/example.java",
                     "decompiled/code.txt", ".env", "credentials.json", "secrets.json", "tests/test.py",
                     "../outside", "chihirosctl.py"):
            with self.subTest(path=path), self.assertRaises(audit.AuditError):
                audit.check_name(path)

    def test_private_strings_detected_in_text_and_utf16(self) -> None:
        for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
            with self.subTest(encoding=encoding), self.assertRaises(audit.AuditError):
                audit.check_bytes("fixture.bin", "secret-unit-identifier".encode(encoding), ("secret-unit-identifier",))

    def test_zip_entries_are_decompressed_for_privacy_checks(self) -> None:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("module.pyc", b"synthetic-private-marker")
        with self.assertRaises(audit.AuditError):
            audit.audit_zip_bytes("runtime.zip", output.getvalue(), ("synthetic-private-marker",))

    def test_frozen_modules_are_decompressed_and_private_cli_is_rejected(self) -> None:
        archive = types.SimpleNamespace(toc={"PYZ.pyz": (0, 1, 1, 0, "z")}, extract=lambda _: b"compressed",
                                        open_embedded_archive=lambda _: types.SimpleNamespace(
                                            toc={"gui.app": ()}, extract=lambda *args, **kwargs: b"private-marker"))
        with mock.patch("PyInstaller.archive.readers.CArchiveReader", return_value=archive):
            with self.assertRaises(audit.AuditError):
                audit.audit_executable(Path("test.exe"), ("private-marker",))
        archive.open_embedded_archive = lambda _: types.SimpleNamespace(toc={"chihiros.a2max": ()})
        with mock.patch("PyInstaller.archive.readers.CArchiveReader", return_value=archive):
            with self.assertRaisesRegex(audit.AuditError, "Private/developer Python module"):
                audit.audit_executable(Path("test.exe"), ())

    def test_zip_must_match_audited_folder_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = self.make_folder(root)
            path = root / "release.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for item in folder.rglob("*"):
                    if item.is_file():
                        archive.write(item, f"{folder.name}/{item.relative_to(folder).as_posix()}")
            self.assertEqual(audit.audit_release_zip(path, folder)["zip_files_verified"], 7)
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("extra-private-file", "not allowed")
            with self.assertRaises(audit.AuditError):
                audit.audit_release_zip(path, folder)

    def test_notice_contents_must_match_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = self.make_folder(Path(temp))
            with self.assertRaisesRegex(audit.AuditError, "notice differs"):
                audit.verify_notices(folder, ROOT)


if __name__ == "__main__":
    unittest.main()
