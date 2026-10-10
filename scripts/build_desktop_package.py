"""Build a Windows offline package from explicit program/runtime inputs only."""

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_package(output: Path, python_root: Path, dependencies: Path, model: Path | None = None):
    version = re.search(r'__version__ = "([0-9.]+)"',
                        (ROOT / "src/support_agent/__init__.py").read_text("utf-8")).group(1)
    manifest = {"format": 1, "version": version, "schema": 1, "files": {}}
    output.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for folder in ("src", "static", "scripts"):
        entries.extend((p, p.relative_to(ROOT).as_posix()) for p in (ROOT / folder).rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts and not p.is_symlink())
    for filename in ("LICENSE", ".env.example", "README.md"):
        entries.append((ROOT / filename, filename))
    entries.append((ROOT / "tests/fixtures/answer_acceptance.json",
                    "scripts/fixtures/answer_acceptance.json"))
    entries.append((ROOT / "docs/local_operations.md", "scripts/INSTALLATION.md"))
    for p in python_root.rglob("*"):
        rel = p.relative_to(python_root)
        if p.is_file() and not p.is_symlink() and not any(
            part in {"site-packages", "__pycache__", "Scripts", "test", "tests",
                     "include", "libs", "tcl"} for part in rel.parts
        ):
            entries.append((p, ".runtime/" + rel.as_posix()))
    for p in dependencies.rglob("*"):
        rel = p.relative_to(dependencies)
        if p.is_file() and not p.is_symlink() and not any(
            part in {"__pycache__", "tests", "test", "bin"} for part in rel.parts
        ) and p.name != ".env" and not (
            rel.parts[0] == "sqlalchemy" and p.suffix == ".pyd"
        ):
            # SQLAlchemy ships equivalent Python implementations. Avoid optional
            # native extensions in relocated Windows bundles.
            entries.append((p, ".localdeps/" + rel.as_posix()))
    if model:
        for p in model.rglob("*"):
            if p.is_file() and not p.is_symlink() and (
                p.suffix in {".onnx", ".json"} or p.name.startswith(("LICENSE", "NOTICE"))
            ):
                entries.append((p, "models/embedding/" + p.relative_to(model).as_posix()))
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path, entry in sorted(entries, key=lambda x: x[1]):
            digest = hashlib.sha256()
            with path.open("rb") as src, archive.open(entry, "w", force_zip64=True) as dst:
                while block := src.read(1024 * 1024):
                    digest.update(block)
                    dst.write(block)
            manifest["files"][entry] = digest.hexdigest()
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("Install.cmd", '@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\n'
                         'set "PYTHONPATH=%~dp0src;%~dp0.localdeps"\r\n'
                         'set "PYTHONNOUSERSITE=1"\r\nset "PYTHONHOME="\r\n'
                         '"%~dp0.runtime\\python.exe" "%~dp0scripts\\manage_install.py" install '
                         '--package "%~dp0." --target "%LOCALAPPDATA%\\SmartPVSupport"\r\n'
                         'pause\r\n')
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(checksum + "  " + output.name, encoding="ascii")
    return {"version": version, "files": len(manifest["files"]),
            "bytes": output.stat().st_size, "path": str(output.resolve())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python-root", type=Path, default=Path(sys.base_prefix))
    parser.add_argument("--dependencies", type=Path, default=ROOT / ".localdeps")
    parser.add_argument("--embedding-model", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_package(args.output, args.python_root, args.dependencies,
                                   args.embedding_model), ensure_ascii=False))
