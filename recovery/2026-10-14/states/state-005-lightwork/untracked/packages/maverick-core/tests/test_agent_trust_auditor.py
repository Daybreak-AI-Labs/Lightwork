"""Core Agent Trust Auditor: governed estate, safe evidence, and findings."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from maverick import agent_trust_auditor as auditor
from maverick.agent_trust import TrustedAgent
from maverick.mcp_client import MCPServerSpec
from maverick.privacy_ops import RecordConflict


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_AGENT_TRUST", raising=False)
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")

    from maverick import audit, config

    config.reset_config_cache()
    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    yield tmp_path
    config.reset_config_cache()


def _enable(tmp_path, *, stale_after_seconds: int = 60, providers: str = "") -> None:
    from maverick import config

    (tmp_path / "config.toml").write_text(
        "[agent_trust_auditor]\n"
        "enable = true\n"
        f"stale_after_seconds = {stale_after_seconds}\n"
        f"{providers}",
        encoding="utf-8",
    )
    config.reset_config_cache()


def _otel_span(
    *,
    trace: str = "a" * 32,
    span: str = "b" * 16,
    agent_id: str = "runtime-agent",
    tool: str = "shell",
    timestamp: float = 1_000,
    extra: dict | None = None,
) -> dict:
    attributes = {
        "gen_ai.agent.id": agent_id,
        "gen_ai.agent.name": "Runtime Agent",
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": tool,
        "gen_ai.request.model": "model-a",
    }
    attributes.update(extra or {})
    return {
        "trace_id": trace,
        "span_id": span,
        "timestamp": timestamp,
        "attributes": attributes,
    }


def test_feature_gate_is_fail_closed(tmp_path):
    assert auditor.enabled() is False
    with pytest.raises(auditor.AgentTrustAuditorDisabled):
        auditor.ingest_otel_spans([], source="collector", actor="operator")

    (tmp_path / "config.toml").write_text(
        "[agent_trust_auditor]\n"
        "enable = true\n"
        'stale_after_seconds = "forever"\n',
        encoding="utf-8",
    )
    from maverick import config

    config.reset_config_cache()
    assert auditor.enabled() is False
    with pytest.raises(auditor.AgentTrustAuditorConfigError):
        auditor.refresh_local_projection(actor="operator")


def test_local_projection_uses_configured_specs_and_never_mutates_authority(
    tmp_path,
    monkeypatch,
):
    _enable(
        tmp_path,
        providers=(
            "\n[providers.openai]\n"
            'api_key = "provider-secret-must-not-project"\n'  # pragma: allowlist secret
        ),
    )
    from maverick import agent_trust, mcp_client, mcp_registry

    peer = TrustedAgent(id="peer", allow_tools=frozenset(), max_risk=None)
    monkeypatch.setattr(agent_trust, "load_trust_state", lambda: (True, {"peer": peer}))
    spec = MCPServerSpec(
        name="configured-server",
        command="npx",
        args=["@example/mcp"],
        env={"MCP_TOKEN": "mcp-secret-must-not-project"},
    )
    monkeypatch.setattr(mcp_client, "load_mcp_specs_from_config", lambda: [spec])

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("discovery must not mutate authority or read the catalog")

    monkeypatch.setattr(mcp_registry, "load_mcp_registry", _forbidden)
    monkeypatch.setattr(agent_trust, "put_agent", _forbidden)
    monkeypatch.setattr(agent_trust, "remove_agent", _forbidden)
    monkeypatch.setattr(agent_trust, "set_revoked", _forbidden)

    projection = auditor.refresh_local_projection(
        actor="estate-scanner",
        observed_at=1_000,
    )
    records = projection["records"]
    by_source_id = {
        row["identity"]["source_id"]: row
        for row in records
    }
    assert by_source_id["peer"]["identity"]["source"] == "agent_trust"
    assert by_source_id["configured-server"]["identity"]["source"] == "mcp_config"
    assert by_source_id["configured-server"]["posture"]["pinned"] is False
    assert by_source_id["openai"]["identity"]["source"] == "provider_config"
    assert projection["counts"]["tool"] > 10
    projected_tools = [row for row in records if row["entity_type"] == "tool"]
    assert {row["status"] for row in projected_tools} == {"catalogued"}
    assert not any(row["metadata"]["available"] for row in projected_tools)
    assert not any(row["metadata"]["configured"] for row in projected_tools)

    projected_json = json.dumps(records, sort_keys=True)
    assert "provider-secret-must-not-project" not in projected_json
    assert "mcp-secret-must-not-project" not in projected_json
    graph_json = json.dumps(__import__("maverick.evidence_graph", fromlist=["list_nodes"]).list_nodes())
    assert "provider-secret-must-not-project" not in graph_json
    assert "mcp-secret-must-not-project" not in graph_json

    peer_findings = {
        row["finding_type"]
        for row in auditor.findings(now=2_000)
        if row["entity_id"] == by_source_id["peer"]["entity_id"]
    }
    assert {"unowned", "unpinned", "overprivileged", "stale"} <= peer_findings


def test_otel_ingestion_drops_content_and_deduplicates_trace_span(tmp_path):
    _enable(tmp_path)
    span = _otel_span(extra={
        "gen_ai.prompt": "prompt-secret-must-not-persist",
        "gen_ai.tool.arguments": "argument-secret-must-not-persist",
        "gen_ai.tool.result": "result-secret-must-not-persist",
        "http.request.body": "body-secret-must-not-persist",
    })

    first = auditor.ingest_otel_spans(
        [span],
        source="primary_collector",
        actor="otel-ingester",
        received_at=1_001,
    )
    second = auditor.ingest_otel_spans(
        [span],
        source="primary_collector",
        actor="otel-ingester",
        received_at=1_002,
    )
    assert first["accepted"] == 1
    assert first["dropped_attributes"] == 4
    assert second["accepted"] == 0
    assert second["deduplicated"] == 1
    assert first["observation_ids"] == second["observation_ids"]

    record = first["records"][0]
    assert record["observed_tools"] == ["shell"]
    assert record["observation_ids"][0].startswith("EGN-")
    persisted = json.dumps({
        "record": auditor.get_observed(record["entity_id"]),
        "nodes": __import__("maverick.evidence_graph", fromlist=["list_nodes"]).list_nodes(),
    })
    for secret in (
        "prompt-secret-must-not-persist",
        "argument-secret-must-not-persist",
        "result-secret-must-not-persist",
        "body-secret-must-not-persist",
    ):
        assert secret not in persisted

    undeclared = [
        row
        for row in auditor.findings(now=1_010)
        if row["finding_type"] == "undeclared-tool"
    ]
    assert undeclared[0]["details"]["undeclared_tools"] == ["shell"]
    assert undeclared[0]["observation_ids"] == record["observation_ids"]


def test_projection_uses_runtime_registry_as_availability_evidence(tmp_path):
    _enable(tmp_path)

    class _Registry:
        def all(self):
            return [SimpleNamespace(name="read_file"), SimpleNamespace(name="write_file")]

        def exposed(self):
            return [SimpleNamespace(name="read_file")]

    projection = auditor.refresh_local_projection(
        actor="estate-scanner",
        observed_at=1_000,
        tool_registry=_Registry(),
    )
    tools = {
        row["identity"]["source_id"]: row
        for row in projection["records"]
        if row["entity_type"] == "tool"
    }
    assert tools["read_file"]["status"] == "available"
    assert tools["read_file"]["metadata"]["available"] is True
    assert tools["read_file"]["metadata"]["configured"] is True
    assert tools["write_file"]["status"] == "configured"
    assert tools["write_file"]["metadata"]["available"] is False


def test_future_observation_timestamps_are_rejected(tmp_path):
    _enable(tmp_path)
    now = time.time()
    with pytest.raises(ValueError, match="clock-skew"):
        auditor.refresh_local_projection(
            actor="estate-scanner",
            observed_at=now + 301,
        )
    with pytest.raises(ValueError, match="received_at"):
        auditor.ingest_otel_spans(
            [_otel_span(timestamp=now)],
            source="collector",
            actor="ingester",
            received_at=now + 301,
        )
    with pytest.raises(ValueError, match="OTel timestamp"):
        auditor.ingest_otel_spans(
            [_otel_span(timestamp=now + 301)],
            source="collector",
            actor="ingester",
            received_at=now,
        )


def test_older_observation_cannot_roll_current_snapshot_back(tmp_path):
    _enable(tmp_path)
    newer = auditor.ingest_otel_spans(
        [_otel_span(
            trace="c" * 32,
            span="d" * 16,
            tool="write_file",
            timestamp=2_000,
            extra={"gen_ai.agent.name": "Current Agent"},
        )],
        source="collector",
        actor="ingester",
        received_at=2_001,
    )["records"][0]
    replayed = auditor.ingest_otel_spans(
        [_otel_span(
            trace="e" * 32,
            span="f" * 16,
            tool="shell",
            timestamp=1_000,
            extra={"gen_ai.agent.name": "Stale Agent"},
        )],
        source="collector",
        actor="ingester",
        received_at=2_001,
    )["records"][0]

    assert replayed["display_name"] == "Current Agent"
    assert replayed["observed_tools"] == ["write_file"]
    assert replayed["snapshot_sha256"] == newer["snapshot_sha256"]
    assert replayed["first_seen"] == 1_000
    assert replayed["last_seen"] == 2_000
    assert len(replayed["observation_ids"]) == 2


def test_otel_allowed_string_attributes_are_secret_scanned(tmp_path):
    _enable(tmp_path)
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij"  # pragma: allowlist secret
    result = auditor.ingest_otel_spans(
        [_otel_span(extra={"service.namespace": jwt})],
        source="collector",
        actor="ingester",
        received_at=1_001,
    )
    assert result["accepted"] == 1
    assert result["dropped_attributes"] == 1
    persisted = json.dumps({
        "records": result["records"],
        "nodes": __import__(
            "maverick.evidence_graph",
            fromlist=["list_nodes"],
        ).list_nodes(),
    })
    assert jwt not in persisted


def test_source_namespaced_identity_refuses_hash_collision(tmp_path, monkeypatch):
    _enable(tmp_path)
    assert auditor.stable_entity_id("source-a", "same") != auditor.stable_entity_id(
        "source-b",
        "same",
    )
    monkeypatch.setattr(auditor, "_identity_digest", lambda *_args: "a" * 64)
    first = auditor.ingest_otel_spans(
        [_otel_span(agent_id="agent-a")],
        source="collector",
        actor="ingester",
        received_at=1_001,
    )
    assert first["accepted"] == 1
    with pytest.raises(auditor.IdentityCollisionError):
        auditor.ingest_otel_spans(
            [_otel_span(
                trace="c" * 32,
                span="d" * 16,
                agent_id="agent-b",
            )],
            source="collector",
            actor="ingester",
            received_at=1_001,
        )


def test_declaration_metadata_and_lifecycle_updates_require_cas(tmp_path):
    _enable(tmp_path)
    observed = auditor.ingest_otel_spans(
        [_otel_span()],
        source="collector",
        actor="ingester",
        received_at=1_001,
    )["records"][0]
    declaration = auditor.declare_entity(
        observed["entity_id"],
        owner="security@example.com",
        purpose="Controlled runtime automation",
        risk_tier="high",
        lifecycle="proposed",
        allowed_tools=["read_file"],
        actor="security-reviewer",
    )
    updated = auditor.update_declaration(
        observed["entity_id"],
        expected_revision=declaration["revision"],
        owner="platform@example.com",
        actor="security-reviewer",
    )
    assert updated is not None
    assert updated["owner"] == "platform@example.com"

    with pytest.raises(RecordConflict):
        auditor.update_declaration(
            observed["entity_id"],
            expected_revision=declaration["revision"],
            purpose="stale write",
            actor="security-reviewer",
        )
    with pytest.raises(ValueError, match="transition"):
        auditor.update_declaration(
            observed["entity_id"],
            expected_revision=updated["revision"],
            lifecycle="production",
            actor="security-reviewer",
        )
    approved = auditor.update_declaration(
        observed["entity_id"],
        expected_revision=updated["revision"],
        lifecycle="approved",
        allowed_tools=["shell"],
        actor="security-reviewer",
    )
    assert approved is not None
    assert approved["lifecycle"] == "approved"
    assert auditor.get_observed(observed["entity_id"])["revision"] == observed["revision"]
    assert not any(
        row["finding_type"] == "undeclared-tool"
        for row in auditor.findings(now=1_010)
    )


@pytest.mark.parametrize(
    ("lifecycle", "policy_active"),
    [
        ("proposed", False),
        ("rejected", False),
        ("suspended", False),
        ("retired", False),
        ("approved", True),
        ("production", True),
    ],
)
def test_only_active_lifecycle_declarations_confer_policy(
    tmp_path,
    lifecycle,
    policy_active,
):
    _enable(tmp_path)
    observed = auditor.ingest_otel_spans(
        [_otel_span()],
        source="collector",
        actor="ingester",
        received_at=1_001,
    )["records"][0]
    auditor.declare_entity(
        observed["entity_id"],
        owner="security@example.com",
        purpose="Lifecycle policy regression",
        risk_tier="high",
        lifecycle=lifecycle,
        allowed_tools=["shell"],
        actor="security-reviewer",
    )

    entity_findings = {
        row["finding_type"]: row
        for row in auditor.findings(now=1_010)
        if row["entity_id"] == observed["entity_id"]
    }
    if policy_active:
        assert "unowned" not in entity_findings
        assert "undeclared-tool" not in entity_findings
    else:
        assert entity_findings["unowned"]["details"]["policy_active"] is False
        assert (
            entity_findings["undeclared-tool"]["details"]["undeclared_tools"]
            == ["shell"]
        )


def _forge_pack_with_new_key(pack):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    key_id = hashlib.sha256(bytes.fromhex(public_key)).hexdigest()[:16]
    receipt = {
        "v": 1,
        "event": "agent_trust_evidence_pack",
        "attestation_id": "attacker-controlled",
        "actor": "attacker",
        "generated_at": pack["generated_at"],
        "pack_sha256": pack["pack_sha256"],
        "prev_hash": "",
        "key_id": key_id,
    }
    row_hash = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    signature = private_key.sign(bytes.fromhex(row_hash)).hex()
    receipt.update({"hash": row_hash, "sig": signature})
    forged = copy.deepcopy(pack)
    forged["attestation"] = {
        "algorithm": "Ed25519",
        "key_id": key_id,
        "public_key": public_key,
        "signature": signature,
        "event_hash": row_hash,
        "signed_message": "hex-decoded event_hash",
        "receipt": receipt,
    }
    return forged


def test_evidence_pack_requires_external_anchor_and_rejects_forged_key(tmp_path):
    _enable(tmp_path)
    auditor.ingest_otel_spans(
        [_otel_span()],
        source="collector",
        actor="ingester",
        received_at=1_001,
    )
    pack = auditor.render_evidence_pack(
        actor="evidence-exporter",
    )
    assert pack["schema"] == "lightwork.agent-trust-evidence-pack.v1"
    assert pack["pack_sha256"]
    assert pack["attestation"]["algorithm"] == "Ed25519"
    assert pack["evidence_graph"]["nodes"]
    trusted = {
        pack["attestation"]["key_id"]: pack["attestation"]["public_key"],
    }
    assert auditor.verify_evidence_pack(pack) is False
    assert auditor.verify_evidence_pack(pack, trusted_pubkeys={}) is False
    assert auditor.verify_evidence_pack(pack, trusted_pubkeys=trusted) is True

    forged = _forge_pack_with_new_key(pack)
    forged_trust = {
        forged["attestation"]["key_id"]: forged["attestation"]["public_key"],
    }
    assert auditor.verify_evidence_pack(forged, trusted_pubkeys=forged_trust) is True
    assert auditor.verify_evidence_pack(forged, trusted_pubkeys=trusted) is False

    tampered = copy.deepcopy(pack)
    tampered["observed_estate"][0]["display_name"] = "tampered"
    assert auditor.verify_evidence_pack(tampered, trusted_pubkeys=trusted) is False
    tampered_attestation = copy.deepcopy(pack)
    tampered_attestation["attestation"]["algorithm"] = "none"
    assert (
        auditor.verify_evidence_pack(tampered_attestation, trusted_pubkeys=trusted)
        is False
    )

    with pytest.raises(ValueError, match="generated_at"):
        auditor.render_evidence_pack(
            actor="evidence-exporter",
            generated_at=1_010,
        )
