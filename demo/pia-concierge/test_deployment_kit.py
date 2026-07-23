"""The partner deployment kit stays truthful: the standalone requirements
cover the real third-party imports, the container copies every module the app
needs, and the launcher honors the deployment bind variables."""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The modules that make up the standalone SKU (mirrors Dockerfile COPY).
_STANDALONE_MODULES = ("app.py", "backend.py", "capabilities.py",
                       "pia_engine.py", "store.py", "mailsink.py",
                       "serve_standalone.py")


def _top_level_imports(path: Path) -> set[str]:
    roots: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            roots.add(m.group(1))
    return roots


def test_requirements_cover_every_third_party_import():
    import sys
    stdlib = set(sys.stdlib_module_names)
    local = {p.removesuffix(".py") for p in _STANDALONE_MODULES}
    reqs = {re.match(r"[A-Za-z0-9_.-]+", ln).group(0).lower()
            for ln in (HERE / "requirements-standalone.txt").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}
    # Import-name -> distribution-name mapping where they differ.
    dist_of = {"jinja2": "jinja2", "multipart": "python-multipart",
               "fastapi": "fastapi", "uvicorn": "uvicorn", "httpx": "httpx",
               "cryptography": "cryptography"}
    third_party: set[str] = set()
    for mod in _STANDALONE_MODULES:
        for root in _top_level_imports(HERE / mod):
            if root in stdlib or root in local or root == "maverick":
                continue
            third_party.add(root)
    # jinja2 is pulled in via fastapi.templating at runtime; require it anyway.
    third_party.add("jinja2")
    missing = {m for m in third_party if dist_of.get(m, m).lower() not in reqs}
    assert not missing, f"requirements-standalone.txt is missing {missing}"


def test_dockerfile_copies_every_standalone_module():
    dockerfile = (HERE / "Dockerfile.standalone").read_text(encoding="utf-8")
    for mod in _STANDALONE_MODULES:
        assert mod in dockerfile, f"Dockerfile.standalone does not COPY {mod}"
    assert "templates/" in dockerfile
    assert "PIA_STANDALONE=1" in dockerfile
    assert "USER pia" in dockerfile          # never root
    assert "HEALTHCHECK" in dockerfile
    assert "-p 127.0.0.1:8890:8890" in dockerfile
    assert "-p 8890:8890" not in dockerfile
    # The standalone Dockerfile must never pull the platform in.
    assert "maverick" not in dockerfile.lower().replace(
        "no maverick", "")


def test_launcher_bind_honors_deployment_env(monkeypatch):
    import importlib.util
    import sys
    sys.path.insert(0, str(HERE))
    try:
        spec = importlib.util.spec_from_file_location(
            "pia_serve_standalone_test", HERE / "serve_standalone.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(HERE))
    monkeypatch.delenv("PIA_HOST", raising=False)
    monkeypatch.setenv("WORLD_PORT", "9001")
    assert mod.bind() == ("127.0.0.1", 9001)   # loopback by default
    monkeypatch.setenv("PIA_HOST", "0.0.0.0")
    assert mod.bind() == ("0.0.0.0", 9001)     # container/proxy deployments


def test_deployment_guide_covers_every_target():
    guide = (HERE / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "-p 127.0.0.1:8890:8890" in guide
    assert "-p 8890:8890" not in guide
    for probe in ("Docker", "App Runner", "ECS", "Cloud Run", "Compute Engine",
                  "Container Apps", "systemd", "Windows", "macOS", "PIA_HOST",
                  "ONETRUST_TOKEN", "/health", "Backup", "TLS"):
        assert probe in guide, f"DEPLOYMENT.md is missing coverage of {probe}"
