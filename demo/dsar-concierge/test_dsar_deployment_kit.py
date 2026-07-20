"""The DSAR deployment kit stays truthful: requirements cover the real
third-party imports, the container copies every module, and the launcher
honors the bind env."""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

_STANDALONE_MODULES = ("app.py", "backend.py", "capabilities.py",
                       "dsar_engine.py", "mailsink.py", "value_ledger.py",
                       "license_kit.py", "serve_standalone.py")


def test_requirements_cover_every_third_party_import():
    import sys
    stdlib = set(sys.stdlib_module_names)
    local = {p.removesuffix(".py") for p in _STANDALONE_MODULES}
    reqs = {re.match(r"[A-Za-z0-9_.-]+", ln).group(0).lower()
            for ln in (HERE / "requirements-standalone.txt")
            .read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}
    dist_of = {"multipart": "python-multipart"}
    third_party: set[str] = set()
    for mod in _STANDALONE_MODULES:
        for line in (HERE / mod).read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)",
                         line)
            if not m:
                continue
            root = m.group(1)
            if root in stdlib or root in local or root == "maverick":
                continue
            third_party.add(root)
    third_party.add("jinja2")     # pulled via fastapi.templating at runtime
    missing = {m for m in third_party if dist_of.get(m, m).lower()
               not in reqs}
    assert not missing, f"requirements-standalone.txt is missing {missing}"


def test_dockerfile_copies_every_standalone_module():
    dockerfile = (HERE / "Dockerfile.standalone").read_text(encoding="utf-8")
    for mod in _STANDALONE_MODULES:
        assert mod in dockerfile, f"Dockerfile does not COPY {mod}"
    assert "templates/" in dockerfile
    assert "DSAR_STANDALONE=1" in dockerfile
    assert "USER dsar" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "maverick" not in dockerfile.lower().replace("no maverick", "")


def test_launcher_bind_honors_deployment_env(monkeypatch):
    import importlib.util
    import sys
    sys.path.insert(0, str(HERE))
    try:
        spec = importlib.util.spec_from_file_location(
            "dsar_serve_standalone_test", HERE / "serve_standalone.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(HERE))
    monkeypatch.delenv("DSAR_HOST", raising=False)
    monkeypatch.setenv("DSAR_PORT", "9101")
    assert mod.bind() == ("127.0.0.1", 9101)
    monkeypatch.setenv("DSAR_HOST", "0.0.0.0")
    assert mod.bind() == ("0.0.0.0", 9101)
