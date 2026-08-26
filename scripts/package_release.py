from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parent / "ParcelPilot_Customer_Support_Trust_Reliability.zip"
EXCLUDED_DIRS = {".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__", "model_cache", "chroma"}
EXCLUDED_FILES = {".env", ".coverage", OUTPUT.name, "parcelpilot.db", "parcelpilot.db-shm", "parcelpilot.db-wal"}


def included(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        return False
    if path.name in EXCLUDED_FILES or path.suffix in {".pyc", ".log", ".ses"}:
        return False
    return path.is_file()


def main() -> None:
    if OUTPUT.exists():
        OUTPUT.unlink()
    files = sorted(path for path in ROOT.rglob("*") if included(path))
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            arcname = Path(ROOT.name) / path.relative_to(ROOT)
            info = zipfile.ZipInfo.from_file(path, arcname=str(arcname))
            info.date_time = (2026, 8, 26, 0, 0, 0)
            info.external_attr = (0o755 if os.access(path, os.X_OK) else 0o644) << 16
            with path.open("rb") as source:
                archive.writestr(info, source.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"Created: {OUTPUT}")
    print(f"Files: {len(files)}")
    print(f"Bytes: {OUTPUT.stat().st_size}")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
