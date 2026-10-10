"""Read-only-root manifest and real temporary-file/publication compatibility.

The transport is synthetic: these tests do not emulate Linux read-only mounts.
Actual EROFS enforcement still needs qualification on the target cluster.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from .test_kubernetes_workspace_transfer import _backend


def test_managed_temporary_files_do_not_pollute_published_workspace(monkeypatch, tmp_path):
    real_run = subprocess.run
    children = []

    def operation(remote, local):
        launch = next(call for call in calls if "run" in call)
        pod = json.loads(launch[launch.index("--overrides") + 1])["spec"]
        container = pod["containers"][0]
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert container["env"] == [{"name": "TMPDIR", "value": "/workspace"}]
        assert container["volumeMounts"] == [{"name": "workspace", "mountPath": "/workspace"}]
        assert len(pod["volumes"]) == 1
        # Map only the Pod's /workspace to the synthetic transport directory.
        # Exercise real Python temp/SQLite/TAR behavior, not mocked file writes.
        child = real_run([sys.executable, "-c", """
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tarfile
import tempfile

workspace = Path.cwd()
assert Path(tempfile.gettempdir()) == workspace
with tempfile.TemporaryDirectory() as temporary:
    scratch = Path(temporary)
    assert scratch.parent == workspace
    with closing(sqlite3.connect(scratch / 'working.sqlite')) as database:
        database.execute('CREATE TABLE result (value TEXT)')
        database.execute('INSERT INTO result VALUES (?)', ('finished',))
        value = database.execute('SELECT value FROM result').fetchone()[0]
    (scratch / 'report.txt').write_text(value)
    with tarfile.open('deliverable.tar', 'w') as archive:
        archive.add(scratch / 'report.txt', arcname='report.txt')
    os.replace(scratch / 'report.txt', workspace / 'result.txt')
assert not scratch.exists()
"""], env={**os.environ, "TMPDIR": str(remote)}, cwd=remote,
            capture_output=True, text=True, timeout=15)
        children.append(child)

    backend, local, calls = _backend(monkeypatch, tmp_path, operation)
    # No new reserved temp/home names may collide with customer workspace data.
    for name in ("tmp", ".tmp", ".cache", ".maverick-tmp"):
        (local / name).write_text("customer data")
    result = backend.exec("fixture")
    assert result.exit_code == 0, result.stderr
    assert len(children) == 1
    assert children[0].returncode == 0, children[0].stderr
    assert {path.name for path in local.iterdir()} == {
        "tmp", ".tmp", ".cache", ".maverick-tmp", "result.txt", "deliverable.tar",
    }
    assert (local / "result.txt").read_text() == "finished"
    for name in ("tmp", ".tmp", ".cache", ".maverick-tmp"):
        assert (local / name).read_text() == "customer data"


def test_unmanaged_scratch_is_not_silently_deleted_or_excluded(monkeypatch, tmp_path):
    def operation(remote, local):
        (remote / "tmp-report.txt").write_text("recover this interrupted work")

    backend, local, _ = _backend(monkeypatch, tmp_path, operation)
    result = backend.exec("fixture")
    assert result.exit_code == 0, result.stderr
    assert (local / "tmp-report.txt").read_text() == "recover this interrupted work"
