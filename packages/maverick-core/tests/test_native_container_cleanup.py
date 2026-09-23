"""Opt-in real engine ownership; requires an explicitly selected local image."""
from __future__ import annotations

import os
import subprocess
import time

import pytest
from maverick.sandbox.container_cleanup import CLEANUP_SECONDS, cleanup_container
from maverick.sandbox.docker import DockerBackend

_IMAGE = os.environ.get("MAVERICK_TEST_CLEANUP_IMAGE") or os.environ.get("MAVERICK_TEST_DOCUMENT_IMAGE")
pytestmark = pytest.mark.skipif(not _IMAGE, reason="requires an explicitly selected local container image")


@pytest.mark.parametrize("reuse", [False, True], ids=["fresh-container", "reused-container"])
def test_real_timed_out_container_is_removed_and_not_reused(tmp_path, reuse, monkeypatch):
    # Resolve an already present image before any run. This test does not build
    # an image, start an engine, or intentionally pull a network dependency.
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", _IMAGE],
        capture_output=True, text=True, timeout=10, check=True,
    )
    image = inspected.stdout.strip()
    assert image.startswith("sha256:") and len(image) == 71
    backend = DockerBackend(workdir=tmp_path, image=image, reuse_container=reuse,
                            memory="128m", cpus="1", pids_limit=32)
    names = []
    original = backend._cleanup_owned

    def observed_cleanup(name, timeout=CLEANUP_SECONDS):
        names.append(name)
        return original(name, timeout)

    monkeypatch.setattr(backend, "_cleanup_owned", observed_cleanup)
    try:
        assert backend.exec("printf ready", timeout=20).stdout == "ready"
        started = time.monotonic()
        result = backend.exec("printf started > /workspace/started; sleep 30", timeout=3)
        assert time.monotonic() - started < 3 + CLEANUP_SECONDS + 3
        assert result.exit_code == 124
        assert (tmp_path / "started").read_text() == "started"
        assert names and not backend.pending_cleanup
        assert backend._warm_name is None
        for name in set(names):
            listed = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            assert name not in listed.stdout.splitlines()
        assert backend.exec("printf recovered", timeout=20).stdout == "recovered"
    finally:
        backend.close()
        for name in set(names):
            assert cleanup_container(name).removed, f"owned test container cleanup unresolved: {name}"
