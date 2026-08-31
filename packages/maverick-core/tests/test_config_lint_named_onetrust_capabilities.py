"""Closed configuration contracts for the named OneTrust parity increments."""

from __future__ import annotations

from maverick.config_lint import lint_config


def _valid_config() -> dict:
    return {
        "evidence_gateway": {"enable": True},
        "ai_inventory": {"enable": True},
        "ai_policy_management": {"enable": True},
        "ai_runtime_guardrails": {"enable": True},
        "policy_packs": {"enable": True},
        "governance_agents": {
            "enable": True,
            "enabled_agents": [
                "breach_response",
                "privacy_risk_assessment",
                "program_copilot",
                "knowledgebase_governance",
                "third_party_risk",
            ],
            "max_context_items": 100,
            "max_context_bytes": 1_048_576,
            "max_output_bytes": 262_144,
            "retention_seconds": 31_536_000,
        },
        "ai_evidence_analysis": {
            "enable": True,
            "enabled_media_types": ["pdf", "jpeg", "png", "xlsx", "text"],
            "max_artifact_bytes": 52_428_800,
            "max_artifacts_per_case": 20,
            "max_extracted_text_bytes": 1_048_576,
            "retention_seconds": 31_536_000,
        },
        "ai_cloud_agent_governance": {
            "enable": True,
            "enabled_providers": [
                "aws_bedrock",
                "azure_ai_foundry",
                "google_vertex_ai",
            ],
            "databricks_framework_enable": True,
            "max_records_per_sync": 10_000,
            "max_events_per_batch": 5_000,
            "retention_seconds": 31_536_000,
        },
    }


def _messages(config: dict) -> list[str]:
    return [finding.message for finding in lint_config(config)]


def test_named_capability_configuration_is_closed_and_coherent() -> None:
    assert lint_config(_valid_config()) == []


def test_named_capability_allowlists_reject_unknown_duplicate_and_empty_values() -> None:
    config = _valid_config()
    config["governance_agents"]["enabled_agents"] = ["breach_response", "unknown"]
    config["ai_evidence_analysis"]["enabled_media_types"] = []
    config["ai_cloud_agent_governance"]["enabled_providers"] = [
        "aws_bedrock",
        "aws_bedrock",
    ]

    messages = _messages(config)
    assert any("enabled_agents allowlist" in message for message in messages)
    assert any("enabled_media_types allowlist" in message for message in messages)
    assert any("enabled_providers allowlist" in message for message in messages)


def test_named_capabilities_require_their_existing_authorities() -> None:
    config = _valid_config()
    del config["evidence_gateway"]
    del config["ai_inventory"]
    del config["ai_policy_management"]
    del config["ai_runtime_guardrails"]
    del config["policy_packs"]

    messages = _messages(config)
    assert any("packaged governance agents require" in message for message in messages)
    assert any("AI evidence analysis requires" in message for message in messages)
    assert any("cloud-agent governance requires" in message for message in messages)
    assert any("Databricks framework activation requires" in message for message in messages)


def test_named_capability_limits_reject_bool_fractional_and_out_of_range_values() -> None:
    config = _valid_config()
    config["governance_agents"]["max_context_items"] = True
    config["ai_evidence_analysis"]["max_artifacts_per_case"] = 2.5
    config["ai_cloud_agent_governance"]["max_events_per_batch"] = 100_001

    messages = _messages(config)
    assert any("max_context_items must be an integer" in message for message in messages)
    assert any("max_artifacts_per_case must be an integer" in message for message in messages)
    assert any("max_events_per_batch must be an integer" in message for message in messages)


def test_named_capability_boolean_gates_are_strict() -> None:
    config = _valid_config()
    config["governance_agents"]["enable"] = "true"
    config["ai_evidence_analysis"]["enable"] = 1
    config["ai_cloud_agent_governance"]["databricks_framework_enable"] = "yes"

    messages = _messages(config)
    assert any("governance_agents.enable must be true or false" in message for message in messages)
    assert any(
        "ai_evidence_analysis.enable must be true or false" in message for message in messages
    )
    assert any(
        "ai_cloud_agent_governance.databricks_framework_enable must be true or false" in message
        for message in messages
    )
