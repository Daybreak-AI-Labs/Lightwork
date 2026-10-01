"""Generate and build the checked-in Apple targets; never sign or publish."""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["watch", "vision"])
    parser.add_argument("--test-destination", help="Run authored tests on an installed simulator destination")
    parser.add_argument("--check", action="store_true", help="Report prerequisites without building")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    prerequisites = {name: shutil.which(name) for name in ("xcodegen", "xcodebuild")}
    ready = platform.system() == "Darwin" and all(prerequisites.values())
    if args.check or not ready:
        print(json.dumps({"ready": ready, "host": platform.system(), "tools": prerequisites,
                          "qualification": "not run", "requires": "macOS, Xcode 16 with watchOS/visionOS SDK; XcodeGen 2.42.0"}, indent=2))
        return 0 if ready else 2
    version = subprocess.run([prerequisites["xcodegen"], "--version"], check=True, capture_output=True, text=True).stdout
    if version.strip() != "Version: 2.42.0":
        raise SystemExit("Use the pinned XcodeGen 2.42.0 from Mintfile.")
    subprocess.run([prerequisites["xcodegen"], "generate", "--spec", str(root / "project.yml")], cwd=root, check=True)
    scheme, destination = {"watch": ("MaverickGlance", "generic/platform=watchOS Simulator"),
                           "vision": ("MaverickPlanTree", "generic/platform=visionOS Simulator")}[args.target]
    build = [prerequisites["xcodebuild"], "-project", str(root / "LightworkCompanions.xcodeproj"),
             "-scheme", scheme, "-configuration", "Debug", "-derivedDataPath", str(root / "build" / scheme),
             "-destination", args.test_destination or destination, "CODE_SIGNING_ALLOWED=NO",
             "test" if args.test_destination else "build"]
    subprocess.run(build, cwd=root, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
