"""Locate and verify the application-shipped, offline vendor OCR runtime."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

SCHEMA = "lightwork.vendor-ocr.v1"
TESSERACT_VERSION = "5.5.2"
MODEL_SHA256 = "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"


def directory() -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    return (Path(frozen) / "vendor-pdf-runtime" if frozen else
            Path(sys.prefix) / "share" / "lightwork" / "vendor-pdf-runtime")


def _file(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or ":" in relative or PurePosixPath(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise ValueError("The local document runtime contains an invalid resource path.")
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
        raise ValueError("The local document runtime is incomplete or contains an unsupported link.")
    return path


def runtime(root: Path | None = None) -> dict:
    """No PATH lookup, customer executable configuration or runtime download.

    ``root`` is a build/test integration seam. The PDF worker uses the default
    installation location. The signed application payload protects the manifest;
    file digests detect missing or changed shipped resources before execution.
    """
    root = directory() if root is None else Path(root)
    try:
        manifest_path = _file(root, "manifest.json")
        if manifest_path.stat().st_size > 65536:
            raise ValueError("The local document runtime manifest exceeds its limit.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        executable_name = "bin/tesseract.exe" if os.name == "nt" else "bin/tesseract"
        if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA
                or manifest.get("tesseract_version") != TESSERACT_VERSION
                or manifest.get("language") != "eng" or manifest.get("model_sha256") != MODEL_SHA256
                or manifest.get("executable") != executable_name or manifest.get("tessdata") != "tessdata"
                or not isinstance(manifest.get("files"), list) or not 3 <= len(manifest["files"]) <= 32):
            raise ValueError("The local document runtime manifest is incompatible.")
        names = set()
        for entry in manifest["files"]:
            if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                    or entry["path"] in names or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))
                    or type(entry.get("size")) is not int or not 0 < entry["size"] <= 128 * 1024 * 1024):
                raise ValueError("The local document runtime file manifest is invalid.")
            names.add(entry["path"])
            path = _file(root, entry["path"])
            if path.stat().st_size != entry["size"]:
                raise ValueError("A local document runtime resource changed.")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != entry["sha256"]:
                raise ValueError("A local document runtime resource changed.")
        if not {executable_name, "tessdata/eng.traineddata", "licenses/THIRD_PARTY_NOTICES.txt"} <= names:
            raise ValueError("The local document runtime lacks a required resource.")
        model = next(item for item in manifest["files"] if item["path"] == "tessdata/eng.traineddata")
        if model["sha256"] != MODEL_SHA256:
            raise ValueError("The local document language data changed.")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Local document recognition is unavailable. Repair or update Lightwork to restore its bundled document tools.") from exc
    return {"executable": root / executable_name, "tessdata": root / "tessdata", "manifest": manifest}
