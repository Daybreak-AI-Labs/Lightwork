"""Build the pinned PNG-only OCR runtime; never install tools or change host trust.

Run with the same Python environment used for the source app or frozen build.
Compilers/CMake must already be provisioned by the build host. Source archives
are hashed before unpacking; no package manager or runtime download is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

PINS = Path(__file__).with_name("vendor_ocr_sources.json")
MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
OPTIONS = {
    "zlib": ["ZLIB_BUILD_SHARED=OFF", "ZLIB_BUILD_STATIC=ON", "ZLIB_BUILD_TESTING=OFF"],
    "libpng": ["PNG_SHARED=OFF", "PNG_STATIC=ON", "PNG_TESTS=OFF", "PNG_TOOLS=OFF"],
    "leptonica": ["SW_BUILD=OFF", "BUILD_PROG=OFF", "ENABLE_ZLIB=ON", "ENABLE_PNG=ON",
                  "ENABLE_GIF=OFF", "ENABLE_JPEG=OFF", "ENABLE_TIFF=OFF", "ENABLE_WEBP=OFF", "ENABLE_OPENJPEG=OFF"],
    "tesseract": ["SW_BUILD=OFF", "BUILD_TRAINING_TOOLS=OFF", "BUILD_TESTS=OFF",
                  "GRAPHICS_DISABLED=ON", "DISABLE_TIFF=ON", "DISABLE_ARCHIVE=ON", "DISABLE_CURL=ON",
                  "OPENMP_BUILD=OFF", "ENABLE_NATIVE=OFF", "ENABLE_LTO=OFF", "INSTALL_CONFIGS=ON"],
}


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def fetch(item: dict, target: Path) -> None:
    if not 0 < item["size"] <= MAX_ARCHIVE_BYTES:
        raise ValueError("Pinned source size is outside the supported bound")
    if target.exists():
        if target.is_symlink() or target.stat().st_size != item["size"] or digest(target) != item["sha256"]:
            raise ValueError("Cached dependency differs from its reviewed source hash")
        return
    temporary = target.with_suffix(target.suffix + ".partial")
    request = urllib.request.Request(item["url"], headers={"User-Agent": "Lightwork-pinned-document-runtime"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("xb") as output:
            if not response.url.startswith("https://"):
                raise ValueError("Dependency download left HTTPS")
            remaining = item["size"]
            while block := response.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(block)
                if remaining < 0:
                    raise ValueError("Dependency download exceeded its pinned size")
                output.write(block)
        if remaining or digest(temporary) != item["sha256"]:
            raise ValueError("Dependency download does not match its reviewed hash")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def unpack(archive: Path, destination: Path, expected_root: str) -> Path:
    """Extract only bounded ordinary files, never archive links or traversal."""
    with tarfile.open(archive) as source:
        members = source.getmembers()
        if len(members) > 20000 or sum(item.size for item in members) > 256 * 1024 * 1024:
            raise ValueError("Source archive exceeds extraction bounds")
        names = set()
        for item in members:
            relative = PurePosixPath(item.name)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in item.name or ":" in item.name
                    or relative.parts[0] != expected_root or item.name in names
                    or not (item.isdir() or item.isfile()) or item.size > 32 * 1024 * 1024):
                raise ValueError("Source archive contains an unsupported path or entry")
            names.add(item.name)
        for item in members:
            target = destination.joinpath(*PurePosixPath(item.name).parts)
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(item) as stream, target.open("xb") as output:
                    shutil.copyfileobj(stream, output)
    return destination / expected_root


def run(command: list[str], log: Path, *, timeout: int = 900) -> None:
    print("Document runtime: " + " ".join(command[:4]), flush=True)
    with log.open("ab") as output:
        output.write(("\n" + " ".join(command) + "\n").encode("utf-8"))
        subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=timeout, check=True)


def configure(cmake: str, source: Path, build: Path, prefix: Path, name: str, generator: str) -> list[str]:
    options = ["BUILD_SHARED_LIBS=OFF", "CMAKE_BUILD_TYPE=Release", "CMAKE_INSTALL_LIBDIR=lib",
               "CMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded", "CMAKE_POSITION_INDEPENDENT_CODE=ON",
               "CMAKE_FIND_USE_PACKAGE_REGISTRY=OFF", "CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF",
               "CMAKE_POLICY_DEFAULT_CMP0091=NEW", *OPTIONS[name]]
    command = [cmake, "-S", str(source), "-B", str(build), "-G", generator]
    if generator.startswith("Visual Studio"):
        command += ["-A", "x64"]
    command += ["-DCMAKE_INSTALL_PREFIX=" + str(prefix), "-DCMAKE_PREFIX_PATH=" + str(prefix)]
    if name != "zlib":
        # Exact staged Zlib, not an unrelated library preinstalled on the host.
        libraries = [path for path in (prefix / "lib").glob("*") if path.name in {"z.lib", "zlibstatic.lib", "zlib.lib", "libz.a", "zs.lib"}]
        if len(libraries) != 1:
            raise ValueError("The pinned static Zlib installation is incomplete or ambiguous")
        command += ["-DZLIB_LIBRARY=" + str(libraries[0]), "-DZLIB_INCLUDE_DIR=" + str(prefix / "include")]
    if name in {"leptonica", "tesseract"}:
        png = [path for path in (prefix / "lib").glob("*") if path.name in {"libpng16_static.lib", "libpng16.a"}]
        if len(png) != 1:
            raise ValueError("The pinned static PNG installation is incomplete or ambiguous")
        command += ["-DPNG_LIBRARY=" + str(png[0]), "-DPNG_PNG_INCLUDE_DIR=" + str(prefix / "include")]
    return command + ["-D" + option for option in options]


def stage(prefix: Path, roots: dict[str, Path], pins: dict, model: Path, target: Path, cache_key: str) -> None:
    executable = "tesseract.exe" if os.name == "nt" else "tesseract"
    (target / "bin").mkdir(parents=True)
    (target / "tessdata" / "configs").mkdir(parents=True)
    (target / "licenses").mkdir()
    shutil.copy2(prefix / "bin" / executable, target / "bin" / executable)
    shutil.copy2(model, target / "tessdata" / "eng.traineddata")
    # Use an app-owned exact output config; no configurable executable or script.
    (target / "tessdata" / "configs" / "tsv").write_text("tessedit_create_tsv 1\n", encoding="ascii")
    notices = ["Lightwork offline document recognition dependencies", "",
               "Tesseract is built with PNG/Zlib support only. No Tesserocr, cysignals,",
               "JPEG/TIFF/GIF/WebP/OpenJPEG, CURL, LibArchive or training tools are included.", ""]
    for item in pins["sources"]:
        name = item["name"]
        shutil.copyfile(roots[name] / item["license_file"], target / "licenses" / (name + ".txt"))
        notices += [name + " " + item["version"] + " — " + item["license"], item["url"],
                    "Source SHA-256: " + item["sha256"], "See " + name + ".txt", ""]
    # The official traineddata repository uses the same Apache-2.0 text as Tesseract.
    shutil.copyfile(roots["tesseract"] / "LICENSE", target / "licenses" / "tessdata-fast.txt")
    notices += ["English tessdata_fast — Apache-2.0", pins["model"]["url"],
                "Model SHA-256: " + pins["model"]["sha256"], "See tessdata-fast.txt", ""]
    (target / "licenses" / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(notices), encoding="utf-8")
    files = [{"path": path.relative_to(target).as_posix(), "sha256": digest(path), "size": path.stat().st_size}
             for path in sorted(target.rglob("*")) if path.is_file()]
    manifest = {"schema": "lightwork.vendor-ocr.v1", "version": 1, "tesseract_version": "5.5.2",
                "language": "eng", "model_sha256": pins["model"]["sha256"],
                "executable": "bin/" + executable, "tessdata": "tessdata", "files": files,
                "sources": pins["sources"], "model": pins["model"], "build_key": cache_key,
                "platform": platform.system(), "architecture": platform.machine(), "image_formats": ["png"]}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=Path(sys.prefix) / "share/lightwork/vendor-pdf-runtime")
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--generator", default="Visual Studio 17 2022" if os.name == "nt" else "Unix Makefiles")
    parser.add_argument("--jobs", type=int, default=2, choices=range(1, 9))
    args = parser.parse_args()
    from maverick.vendor_pdf_runtime import runtime
    pins = json.loads(PINS.read_text(encoding="utf-8"))
    cmake = shutil.which(args.cmake)
    if not cmake:
        raise RuntimeError("Provision CMake on the build host before building document tools.")
    tool_version = subprocess.run([cmake, "--version"], capture_output=True, text=True, timeout=15, check=True).stdout
    identity = PINS.read_bytes() + Path(__file__).read_bytes() + (platform.platform() + args.generator + tool_version).encode()
    cache_key = hashlib.sha256(identity).hexdigest()
    cache = args.cache.resolve() / cache_key[:20]
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / "runtime"
    if not target.exists():
        downloads = cache / "downloads"
        downloads.mkdir(exist_ok=True)
        roots = {}
        for item in pins["sources"]:
            archive = downloads / (item["name"] + ".tar.gz")
            fetch(item, archive)
            root = cache / item["root"]
            if not root.exists():
                unpack(archive, cache, item["root"])
            roots[item["name"]] = root
        model = downloads / "eng.traineddata"
        fetch(pins["model"], model)
        prefix = cache / "prefix"
        for item in pins["sources"]:
            name = item["name"]
            build = cache / (name + "-build")
            log = cache / (name + "-build.log")
            run(configure(cmake, roots[name], build, prefix, name, args.generator), log)
            run([cmake, "--build", str(build), "--config", "Release", "--parallel", str(args.jobs)], log)
            run([cmake, "--install", str(build), "--config", "Release"], log)
        stage(prefix, roots, pins, model, target, cache_key)
    verified = runtime(target)
    version = subprocess.run([str(verified["executable"]), "--version"], capture_output=True, text=True, timeout=15, check=True)
    if not (version.stdout + version.stderr).startswith("tesseract 5.5.2"):
        raise RuntimeError("Built OCR version does not match the source pin")
    destination = args.destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        previous = runtime(destination)
        if previous["manifest"] != verified["manifest"]:
            raise RuntimeError("An older document runtime is present; use a fresh isolated build environment.")
    else:
        with tempfile.TemporaryDirectory(prefix="vendor-ocr-stage-", dir=destination.parent) as temporary:
            staged = Path(temporary) / "runtime"
            shutil.copytree(target, staged)
            runtime(staged)
            staged.replace(destination)
    print("Verified offline document runtime: " + str(destination), flush=True)


if __name__ == "__main__":
    main()
