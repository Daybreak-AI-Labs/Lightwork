"""Measured synthetic local restore; never opens an existing workspace."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lw-drill-") as temporary:
        # Standalone acceptance process: remove inherited deployment overrides
        # before importing modules that cache paths or credentials.
        for name in list(os.environ):
            if name.startswith("MAVERICK_") or name in {"QDRANT_URL", "REDIS_URL"}:
                os.environ.pop(name)
        os.environ["MAVERICK_HOME"] = temporary
        os.environ["MAVERICK_CLIENT_ID"] = "recovery-fixture"
        Path(temporary, "config.toml").write_text("[features]\nworkspace_recovery = true\n", encoding="utf-8")
        from maverick import crypto_at_rest
        from maverick import workspace_recovery as recovery
        from maverick.paths import data_dir

        root = data_dir()
        root.mkdir(parents=True, exist_ok=True)
        crypto_at_rest._KEY_PATH = Path(temporary, "shared-keys", "at_rest.key")
        crypto_at_rest._KEY_PATH.parent.mkdir()
        crypto_at_rest._KEY_PATH.write_text(os.urandom(32).hex(), encoding="ascii")
        draft = root / "draft-recovery" / ("a" * 64) / ("b" * 64 + ".draft")
        draft.parent.mkdir(parents=True)
        draft.write_bytes(crypto_at_rest.seal(b'{"title":"Synthetic unfinished work"}'))
        database = sqlite3.connect(root / "fixture.db")
        try:
            database.execute("CREATE TABLE evidence(value TEXT)")
            database.execute("INSERT INTO evidence VALUES('before')")
            database.commit()
        finally:
            database.close()
        recovery.configure_key()
        snapshot = recovery.create()
        drill = recovery.drill(snapshot["id"])
        draft.write_bytes(crypto_at_rest.seal(b'{"title":"Later synthetic work"}'))
        # Core fixture authority only. Actual startup/lease admission is covered
        # by test_workspace_recovery_api and the browser acceptance journey.
        review = recovery.prepare_restore(snapshot["id"], isolated=True, actor="synthetic-drill")
        restored = recovery.apply_restore(review["review"], review["target"], isolated=True, actor="synthetic-drill")
        assert json.loads(crypto_at_rest.unseal(draft.read_bytes()))["title"] == "Synthetic unfinished work"
        print(json.dumps({"fixture": "synthetic-local-only", "files": snapshot["file_count"],
                          "archive_bytes": snapshot["bytes"], "expanded_bytes": snapshot["expanded_bytes"],
                          "application_version": recovery.version(), "application_identity": recovery.application_identity(),
                          "temporary_restore_seconds": drill["elapsed_seconds"], "controlled_restore_seconds": restored["elapsed_seconds"],
                          "post_restore_verified": restored["post_restore_verified"], "fixture_draft_decrypted": True,
                          "real_workspace_changed": False, "native_runtime_verified": False,
                          "external_stores_tested": False}, indent=2))


if __name__ == "__main__":
    main()
