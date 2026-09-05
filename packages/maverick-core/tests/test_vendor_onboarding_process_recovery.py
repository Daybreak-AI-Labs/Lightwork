"""Process-loss acceptance with a separate durable synthetic ticket provider.

The first interpreter exits inside create(), after the external fixture commits
but before it returns a receipt. Recovery runs in a fresh interpreter and can
only search/read that provider; a third interpreter records human acceptance.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from maverick.governed_operations import ServiceContext
from maverick.vendor_onboarding import VendorOnboardingService
from maverick.vendor_onboarding_adapters import (
    EvidenceDocument,
    FakeGoogleDriveEvidence,
    FakeJiraTickets,
)

_CRASH_EXIT = 73
_NOW = 1_800_000_000.0
_DOCUMENT = b"We do not train models on customer data. Data is encrypted at rest and in transit."


def _write_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class _DurableTickets(FakeJiraTickets):
    """Test-only provider process state, deliberately outside Lightwork's store."""

    def __init__(self, path: Path, *, crash_after_create=False):
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        state = {(row["tenant"], row["key"]): row["value"] for row in saved.get("tickets", [])}
        super().__init__(state)
        self.path = path
        self.creates = saved.get("creates", 0)
        self.reads = saved.get("reads", 0)
        self.crash_after_create = crash_after_create

    def _persist(self):
        _write_json(self.path, {
            "creates": self.creates, "reads": self.reads,
            "tickets": [{"tenant": tenant, "key": key, "value": value}
                        for (tenant, key), value in self.state.items()],
        })

    def create(self, ctx, payload, operation_id, **kwargs):
        if not self.crash_after_create:
            # Persist the attempted retry too: even if the application catches
            # this assertion, the parent must observe the forbidden dispatch.
            self.creates += 1
            self._persist()
            raise AssertionError("recovery is read-only; create must never be retried")
        super().create(ctx, payload, operation_id, **kwargs)
        self._persist()
        # Deliberately bypass every finally block/exception handler. The OS
        # releases the service lock when this interpreter terminates.
        os._exit(_CRASH_EXIT)

    def find(self, ctx, payload, **kwargs):
        self.reads += 1
        result = super().find(ctx, payload, **kwargs)
        self._persist()
        return result

    def verify(self, ctx, key, payload, **kwargs):
        self.reads += 1
        result = super().verify(ctx, key, payload, **kwargs)
        self._persist()
        return result


def _worker(mode: str, root: Path) -> dict:
    from maverick import audit, config

    # Test-only audit sink, matching the existing offline acceptance fixtures.
    audit.record = lambda *args, **kwargs: True
    audit.record_global = lambda *args, **kwargs: True
    config.reset_config_cache()
    ctx = ServiceContext("customer-process", "reviewer@example.test", "procurement",
                         "reviewer@example.test", "jira", human=True)
    documents = FakeGoogleDriveEvidence({(ctx.tenant, "security-paper"): EvidenceDocument(
        "security-paper", "1", _NOW - 86400, _DOCUMENT,
    )})
    tickets = _DurableTickets(root / "external-provider.json", crash_after_create=mode == "crash")
    service = VendorOnboardingService(
        documents, tickets,
        lambda context, capability, resource: context.tenant == ctx.tenant and context.actor == ctx.actor,
        clock=lambda: _NOW,
    )
    manifest_path = root / "case-reference.json"
    if mode == "crash":
        row = service.intake(ctx, "Synthetic Process Vendor", "security-paper", "VEND", "Offline recovery")
        row = service.collect_evidence(ctx, row["id"], row["revision"])
        row = service.assess(ctx, row["id"], row["revision"])
        row = service.review(ctx, row["id"], row["revision"], "approved", "Restricted synthetic pilot")
        row = service.prepare_action(ctx, row["id"], row["revision"])
        row = service.approve_action(ctx, row["id"], row["revision"])
        _write_json(manifest_path, {"case_id": row["id"], "operation_id": row["operation"]["id"]})
        service.execute(ctx, row["id"])
        raise AssertionError("fixture must terminate after provider success")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    row = service.get(ctx, manifest["case_id"])
    before = {"state": row["operation"]["state"], "receipt": row["operation"]["receipt"],
              "confirmation": row["operation"]["external_confirmation"], "outcome": row["outcome"]}
    if mode == "reconcile":
        row = service.execute(ctx, row["id"])
        assert row["operation"]["state"] == "reconciliation_required"
        row = service.reconcile(ctx, row["id"])
        assert row["operation"]["state"] == "verified"
        with pytest.raises(PermissionError, match="human"):
            service.accept(replace(ctx, human=False), row["id"], row["revision"], True, "Model approval")
        assert service.get(ctx, row["id"])["outcome"] is None
    elif mode == "accept":
        row = service.accept(ctx, row["id"], row["revision"], True, "Procurement accepted verified ticket")
    else:
        raise ValueError(mode)
    return {"pid": os.getpid(), "before": before, "case": row,
            "creates": tickets.creates, "reads": tickets.reads}


def _child(mode, root, env):
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--worker", mode, str(root)],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        check=False,
    )


def test_process_loss_after_provider_success_recovers_without_repeating_create(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        "[vendor_onboarding]\nenable=true\n[vendor_management]\nenable=true\n"
        "[ai_vendor_risk]\nenable=true\n[model_risk_assurance]\nenable=true\n",
        encoding="utf-8",
    )
    env = dict(os.environ, MAVERICK_HOME=str(tmp_path / "lightwork-state"),
               MAVERICK_CONFIG=str(config), MAVERICK_ENTERPRISE="0",
               MAVERICK_SECURE_DEFAULT="0", MAVERICK_ENCRYPT_AT_REST="0",
               PYTHONIOENCODING="utf-8")
    env.pop("MAVERICK_TENANT", None)
    env.pop("MAVERICK_CLIENT_ID", None)
    crashed = _child("crash", tmp_path, env)
    assert crashed.returncode == _CRASH_EXIT, crashed.stderr
    external = json.loads((tmp_path / "external-provider.json").read_text(encoding="utf-8"))
    assert external["creates"] == 1
    assert len(external["tickets"]) == 1
    assert external["reads"] == 0  # died before readback or local receipt

    recovered = _child("reconcile", tmp_path, env)
    assert recovered.returncode == 0, recovered.stderr
    proof = json.loads(recovered.stdout)
    assert proof["before"] == {"state": "executing", "receipt": None,
                               "confirmation": None, "outcome": None}
    assert proof["creates"] == 1
    assert proof["reads"] == 2  # search + exact payload readback
    assert proof["case"]["operation"]["attempts"] == 1
    assert proof["case"]["operation"]["external_confirmation"]["reconciled"] is True
    assert proof["case"]["stage"] == "execution_completed"
    assert proof["case"]["outcome"] is None

    accepted = _child("accept", tmp_path, env)
    assert accepted.returncode == 0, accepted.stderr
    final = json.loads(accepted.stdout)
    assert final["pid"] != proof["pid"]
    assert final["before"]["outcome"] is None
    assert final["case"]["outcome"]["accepted"] is True
    assert final["case"]["outcome"]["released"] is False
    assert final["case"]["operation"]["id"] == proof["case"]["operation"]["id"]
    assert final["creates"] == 1
    assert final["reads"] == 3  # acceptance rechecks the external state


if __name__ == "__main__":
    assert sys.argv[1] == "--worker"
    print(json.dumps(_worker(sys.argv[2], Path(sys.argv[3])), sort_keys=True))
