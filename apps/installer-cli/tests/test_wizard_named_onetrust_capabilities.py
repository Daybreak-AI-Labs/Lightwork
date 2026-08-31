"""Installer coverage for named OneTrust capability increments."""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


def test_named_capability_prompts_enable_safe_dependencies(monkeypatch) -> None:
    from maverick.config_lint import lint_config
    from maverick_installer import wizard

    selected_prefixes = (
        "Enable bounded multimodal AI Evidence Analysis",
        "Enable the packaged Breach Response",
        "Enable named AWS Bedrock",
        "Enable the content-free, versioned Databricks",
    )

    def answer(prompt, *args, **kwargs):
        del args, kwargs
        return prompt.startswith(selected_prefixes)

    monkeypatch.setattr(wizard, "_q_confirm", answer)
    picked = wizard.pick_security_suite()

    assert picked["governance_agents"] is True
    assert picked["ai_evidence_analysis"] is True
    assert picked["ai_cloud_agent_governance"] is True
    assert picked["databricks_ai_security_framework"] is True
    assert picked["evidence_gateway"] is True
    assert picked["ai_inventory"] is True
    assert picked["ai_policy_management"] is True
    assert picked["ai_runtime_guardrails"] is True
    assert picked["policy_packs"] is True

    rendered = tomllib.loads("\n".join(wizard._cfg_security_suite(picked)))
    assert rendered["governance_agents"]["enabled_agents"] == [
        "breach_response",
        "privacy_risk_assessment",
        "program_copilot",
        "knowledgebase_governance",
        "third_party_risk",
    ]
    assert rendered["ai_evidence_analysis"]["enabled_media_types"] == [
        "pdf",
        "jpeg",
        "png",
        "xlsx",
        "text",
    ]
    assert rendered["ai_cloud_agent_governance"] == {
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
    }
    assert rendered["ai_inventory"]["connectors"] == [
        "bedrock",
        "azure_ai_foundry",
        "vertex_ai",
    ]
    assert lint_config(rendered) == []


def test_direct_writer_keeps_named_capabilities_default_off() -> None:
    from maverick.config_lint import lint_config
    from maverick_installer import wizard

    rendered = tomllib.loads("\n".join(wizard._cfg_security_suite({})))
    assert rendered["governance_agents"]["enable"] is False
    assert rendered["ai_evidence_analysis"]["enable"] is False
    assert rendered["ai_cloud_agent_governance"]["enable"] is False
    assert rendered["ai_cloud_agent_governance"]["databricks_framework_enable"] is False
    assert lint_config(rendered) == []


def test_direct_writer_derives_fail_closed_dependencies() -> None:
    from maverick.config_lint import lint_config
    from maverick_installer import wizard

    rendered = tomllib.loads(
        "\n".join(
            wizard._cfg_security_suite(
                {
                    "governance_agents": True,
                    "ai_evidence_analysis": True,
                    "ai_cloud_agent_governance": True,
                    "databricks_ai_security_framework": True,
                }
            )
        )
    )
    assert rendered["evidence_gateway"]["enable"] is True
    assert rendered["ai_inventory"]["enable"] is True
    assert rendered["ai_policy_management"]["enable"] is True
    assert rendered["ai_runtime_guardrails"]["enable"] is True
    assert rendered["policy_packs"]["enable"] is True
    assert lint_config(rendered) == []
