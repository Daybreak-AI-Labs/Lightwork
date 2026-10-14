from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("vendor_ocr_build", ROOT / "scripts/build_vendor_ocr.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def test_changed_cached_source_cannot_be_used_or_silently_redownloaded(tmp_path, monkeypatch):
    path = tmp_path / "source.tar.gz"
    path.write_bytes(b"changed source")
    item = {"size": 4, "sha256": hashlib.sha256(b"good").hexdigest()}
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not access network"))
    with pytest.raises(ValueError, match="reviewed source hash"):
        build.fetch(item, path)
    assert path.read_bytes() == b"changed source"


@pytest.mark.parametrize("kind", ["traversal", "symlink", "hardlink", "wrong_root", "duplicate", "oversized"])
def test_archive_refuses_unsafe_entries_before_creating_source_files(tmp_path, kind):
    archive = tmp_path / "archive.tar"
    with tarfile.open(archive, "w") as stream:
        info = tarfile.TarInfo("source/good")
        info.size = 1
        stream.addfile(info, io.BytesIO(b"x"))
        unsafe = tarfile.TarInfo("source/other")
        if kind == "traversal":
            unsafe.name = "source/../outside"
        elif kind == "wrong_root":
            unsafe.name = "unreviewed/other"
        elif kind == "duplicate":
            unsafe.name = "source/good"
        elif kind == "symlink":
            unsafe.type, unsafe.linkname = tarfile.SYMTYPE, "../../outside"
        elif kind == "hardlink":
            unsafe.type, unsafe.linkname = tarfile.LNKTYPE, "../../outside"
        else:
            unsafe.size = 33 * 1024 * 1024
        stream.addfile(unsafe)
    target = tmp_path / "target"
    with pytest.raises((ValueError, tarfile.ReadError)):
        build.unpack(archive, target, "source")
    assert not target.exists()


def test_source_extraction_preserves_exact_license_and_file_bytes(tmp_path):
    archive = tmp_path / "archive.tar"
    with tarfile.open(archive, "w") as stream:
        for name, data in {"source/LICENSE": b"exact notice", "source/sub/code.c": b"exact source"}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            stream.addfile(info, io.BytesIO(data))
    source = build.unpack(archive, tmp_path / "target", "source")
    assert (source / "LICENSE").read_bytes() == b"exact notice"
    assert (source / "sub/code.c").read_bytes() == b"exact source"


def test_cmake_cannot_fall_back_to_unreviewed_host_image_libraries(tmp_path):
    prefix = tmp_path / "prefix"
    (prefix / "lib").mkdir(parents=True)
    with pytest.raises(ValueError, match="Zlib"):
        build.configure("cmake", tmp_path / "src", tmp_path / "build", prefix, "leptonica", "Ninja")
    (prefix / "lib/libz.a").write_bytes(b"synthetic static library")
    with pytest.raises(ValueError, match="PNG"):
        build.configure("cmake", tmp_path / "src", tmp_path / "build", prefix, "leptonica", "Ninja")
    (prefix / "lib/libpng16.a").write_bytes(b"synthetic static library")
    command = build.configure("cmake", tmp_path / "src", tmp_path / "build", prefix, "leptonica", "Ninja")
    assert "-DZLIB_LIBRARY=" + str(prefix / "lib/libz.a") in command
    assert "-DPNG_LIBRARY=" + str(prefix / "lib/libpng16.a") in command
    assert "-DENABLE_TIFF=OFF" in command and "-DENABLE_JPEG=OFF" in command
    assert "-DBUILD_SHARED_LIBS=OFF" in command
