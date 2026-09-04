"""Offline public-source and Windows payload audit; never runs the application."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

PRODUCT = "ChihirosLocalController"
REQUIRED_FILES = frozenset({f"{PRODUCT}.exe", "README.txt", "LICENSE", "THIRD_PARTY_LICENSES.txt",
                            "PYTHON_LICENSE.txt", "PYINSTALLER_COPYING.txt"})
BLOCKED_PARTS = frozenset({"logs", "captures", ".venv", "venv", "config", ".git", "tests",
                           "__pycache__", "jadx", "jadx-output", "decompiled", "app-analysis",
                           "proprietary-research", "apktool-output"})
BLOCKED_SUFFIXES = frozenset({".apk", ".xapk", ".apks", ".aab", ".dex", ".pem", ".pfx", ".key"})
BLOCKED_MODULES = frozenset({"chihiros.a2max", "chihirosctl", "vivid2_gui",
                            "vivid2_live_probe", "vivid2_rgb_test", "scan_chihiros", "vivid2_control"})
SECRET_PATTERNS = (
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class AuditError(ValueError):
    pass


def load_private_patterns(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return ()
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
        raise AuditError("Private audit patterns must be a non-empty JSON string array")
    return tuple(values)


def check_name(name: str, *, payload: bool = True) -> None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = {part.lower() for part in path.parts}
    blocked = BLOCKED_PARTS if payload else BLOCKED_PARTS - {"tests"}
    if path.is_absolute() or ".." in parts or re.match(r"^[A-Za-z]:", normalized):
        raise AuditError(f"Absolute/traversing artifact path: {name}")
    if parts & blocked or path.suffix.lower() in BLOCKED_SUFFIXES:
        raise AuditError(f"Private/developer artifact path: {name}")
    if path.name.lower().startswith((".env", "credentials", "secrets")):
        raise AuditError(f"Secret/environment artifact path: {name}")
    if payload and (path.name in {"chihirosctl.py", "vivid2_gui.py", "a2max-private-unit.json"}
                    or path.suffix.lower() in {".spec", ".ps1"}):
        raise AuditError(f"Developer-only artifact path: {name}")


def check_bytes(name: str, data: bytes, patterns: tuple[str, ...]) -> None:
    # Cover plain text, JSON-escaped Windows paths, case variations and UTF-16 PE strings.
    lowered = data.lower()
    for pattern in patterns:
        variants = {pattern, pattern.replace("\\", "/"), pattern.replace("\\", "\\\\")}
        for variant in variants:
            for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
                if variant.lower().encode(encoding) in lowered:
                    raise AuditError(f"Private identity/path found in {name}")
    if any(pattern.search(data) for pattern in SECRET_PATTERNS):
        raise AuditError(f"Credential/private-key signature found in {name}")


def audit_zip_bytes(name: str, data: bytes, patterns: tuple[str, ...]) -> int:
    count = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            check_name(entry.filename)
            if not entry.is_dir():
                check_bytes(f"{name}!{entry.filename}", archive.read(entry), patterns)
                count += 1
    return count


def audit_executable(path: Path, patterns: tuple[str, ...]) -> dict[str, int]:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(path))
    entries = modules = 0
    for name, entry in archive.toc.items():
        check_name(name)
        data = archive.extract(name)
        check_bytes(f"{path.name}!{name}", data, patterns)
        entries += 1
        if entry[-1] == "z":
            pyz = archive.open_embedded_archive(name)
            for module_name in pyz.toc:
                if module_name in BLOCKED_MODULES or module_name.startswith("tests."):
                    raise AuditError(f"Private/developer Python module bundled: {module_name}")
                module_data = pyz.extract(module_name, raw=True)
                if module_data is not None:  # Namespace packages have no code blob.
                    check_bytes(module_name, module_data, patterns)
                modules += 1
        elif name.endswith(".zip"):
            audit_zip_bytes(name, data, patterns)
    return {"executable_entries": entries, "python_modules": modules}


def public_source_files(root: Path) -> tuple[Path, ...]:
    result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=root, capture_output=True, check=True)
    return tuple(root / name for name in sorted(set(result.stdout.decode("utf-8").split("\0")))
                 if name and (root / name).is_file())


def audit_source(root: Path, patterns: tuple[str, ...]) -> dict[str, int]:
    files = public_source_files(root)
    for path in files:
        relative = path.relative_to(root).as_posix()
        check_name(relative, payload=False)
        check_bytes(relative, path.read_bytes(), patterns)
    return {"public_source_files": len(files)}


def audit_folder(folder: Path, patterns: tuple[str, ...], *, inspect_executable: bool = True) -> dict[str, int]:
    for required in REQUIRED_FILES:
        if not (folder / required).is_file():
            raise AuditError(f"Missing required release file: {required}")
    if not (folder / "_internal").is_dir():
        raise AuditError("Missing required one-folder runtime directory")
    files = [path for path in folder.rglob("*") if path.is_file()]
    for path in folder.rglob("*"):
        if path.is_symlink():
            raise AuditError("Release payload must not contain symbolic links")
        check_name(path.relative_to(folder).as_posix())
    for path in files:
        data = path.read_bytes()
        check_bytes(path.relative_to(folder).as_posix(), data, patterns)
        if path.suffix.lower() == ".zip":
            audit_zip_bytes(path.name, data, patterns)
    result = {"payload_files": len(files)}
    if inspect_executable:
        result.update(audit_executable(folder / f"{PRODUCT}.exe", patterns))
    return result


def audit_release_zip(path: Path, folder: Path) -> dict[str, int]:
    expected = {f"{folder.name}/{item.relative_to(folder).as_posix()}": item
                for item in folder.rglob("*") if item.is_file()}
    with zipfile.ZipFile(path) as archive:
        names = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise AuditError("Release ZIP must contain exactly the audited one-folder payload")
        for name in names:
            check_name(name)
            if hashlib.sha256(archive.read(name)).digest() != hashlib.sha256(expected[name].read_bytes()).digest():
                raise AuditError(f"ZIP differs from audited payload: {name}")
    return {"zip_files_verified": len(expected)}


def verify_notices(folder: Path, root: Path) -> None:
    for name in ("README.txt", "LICENSE", "THIRD_PARTY_LICENSES.txt"):
        if (folder / name).read_bytes() != (root / name).read_bytes():
            raise AuditError(f"Packaged notice differs from source: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--private-patterns", type=Path)
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--onefile", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    patterns = load_private_patterns(args.private_patterns)
    result = {"result": "PASS", **audit_source(args.source_root, patterns)}
    if args.folder:
        result.update(audit_folder(args.folder, patterns))
        verify_notices(args.folder, args.source_root)
    if args.zip:
        if not args.folder:
            parser.error("--zip requires --folder")
        result.update(audit_release_zip(args.zip, args.folder))
    if args.onefile:
        check_bytes(args.onefile.name, args.onefile.read_bytes(), patterns)
        result["onefile"] = audit_executable(args.onefile, patterns)
    print(json.dumps(result, indent=2))
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
