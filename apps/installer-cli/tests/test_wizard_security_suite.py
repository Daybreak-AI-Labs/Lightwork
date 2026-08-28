"""Installer coverage for the Security/GRC and defensive-hunter controls."""
from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


def test_pick_security_suite_safe_defaults(monkeypatch):
    from maverick_installer import wizard

    answers = iter([True] + [False] * 60)
    monkeypatch.setattr(
        wizard, "_q_confirm", lambda *args, **kwargs: next(answers)
    )

    assert wizard.pick_security_suite() == {
        "security_ops": True,
        "evidence_graph": False,
        "model_risk_assurance": False,
        "evidence_gateway": False,
        "model_improvement": False,
        "allow_hosted_training": False,
        "ai_inventory": False,
        "inventory_connectors": [],
        "ai_vendor_risk": False,
        "portfolio_risk": False,
        "policy_packs": False,
        "ai_regulatory_feed": False,
        "red_team_assurance": False,
        "eu_ai_incidents": False,
        "grc_outbound_sync": False,
        "ai_literacy": False,
        "ai_business_intake": False,
        "ai_cost_roi": False,
        "ai_dynamic_risk": False,
        "ai_policy_management": False,
        "ai_runtime_guardrails": False,
        "ai_explainability": False,
        "ai_conformity_workflow": False,
        "ai_insurability": False,
        "ai_regulated_verticals": False,
        "ai_bank_mrm": False,
        "ai_benchmark_exchange": False,
        "ai_discovery_sensors": False,
        "ai_media_provenance": False,
        "ai_identity_governance_connectors": False,
        "threat_hunt": False,
        "env_hunt": False,
        "response_execution": False,
    }


def test_pick_security_suite_response_is_separate_opt_in(monkeypatch):
    from maverick_installer import wizard

    answers = iter([True] * 60)
    monkeypatch.setattr(
        wizard, "_q_confirm", lambda *args, **kwargs: next(answers)
    )
    checkbox_answers = iter([
        ["AWS Bedrock", "Google Vertex AI"],
        ["AWS CloudTrail", "Microsoft Entra ID"],
        ["AWS CloudTrail"],
        ["Microsoft Entra ID"],
    ])
    monkeypatch.setattr(wizard, "_q_checkbox", lambda *args, **kwargs: next(checkbox_answers))
    text_answers = iter(["virustotal, abuseipdb", "120"])
    monkeypatch.setattr(wizard, "_q_text", lambda *args, **kwargs: next(text_answers))

    picked = wizard.pick_security_suite()
    assert picked["evidence_gateway"] is True
    assert picked["model_improvement"] is True
    assert picked["allow_hosted_training"] is True
    assert picked["inventory_connectors"] == ["bedrock", "vertex_ai"]
    assert picked["ai_vendor_risk"] is True
    assert picked["portfolio_risk"] is True
    assert picked["policy_packs"] is True
    assert picked["ai_regulatory_feed"] is True
    assert picked["red_team_assurance"] is True
    assert picked["eu_ai_incidents"] is True
    assert picked["grc_outbound_sync"] is True
    assert picked["ai_literacy"] is True
    assert picked["ai_business_intake"] is True
    assert picked["ai_cost_roi"] is True
    assert picked["ai_dynamic_risk"] is True
    assert picked["ai_policy_management"] is True
    assert picked["ai_runtime_guardrails"] is True
    assert picked["ai_explainability"] is True
    assert picked["ai_conformity_workflow"] is True
    assert picked["ai_insurability"] is True
    assert picked["ai_regulated_verticals"] is True
    assert picked["ai_bank_mrm"] is True
    assert picked["ai_benchmark_exchange"] is True
    assert picked["ai_discovery_sensors"] is True
    assert picked["ai_media_provenance"] is True
    assert picked["ai_identity_governance_connectors"] is True
    assert picked["env_hunt"] is True
    assert picked["response_execution"] is True
    assert picked["connectors"]["cloudtrail"] == {
        "enable": True, "push_enable": True, "pivot_enable": False,
    }
    assert picked["connectors"]["entra"] == {
        "enable": True, "push_enable": False, "pivot_enable": True,
    }
    assert picked["connectors"]["edr"] == {
        "enable": False, "push_enable": False, "pivot_enable": False,
    }
    assert picked["enrichment_sources"] == ["virustotal", "abuseipdb"]
    assert picked["poll_seconds"] == 120


def test_write_config_emits_independent_security_knobs(tmp_path, monkeypatch):
    from maverick_installer import wizard

    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wizard, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(wizard, "ENV_FILE", tmp_path / ".env")
    wizard.write_config(
        providers=["anthropic"],
        role_models={},
        channels={},
        safety={"profile": "balanced"},
        budget={"max_dollars": 5.0, "max_wall_seconds": 600,
                "max_tool_calls": 30},
        sandbox={"backend": "local", "workdir": "~/ws"},
        keys={},
        capabilities={},
        security_suite={
            "security_ops": True,
            "evidence_graph": True,
            "model_risk_assurance": True,
            "evidence_gateway": True,
            "model_improvement": True,
            "allow_hosted_training": False,
            "ai_inventory": True,
            "inventory_connectors": [
                "bedrock",
                "vertex_ai",
                "mlflow",
                "aws_sagemaker",
            ],
            "ai_vendor_risk": True,
            "portfolio_risk": True,
            "policy_packs": True,
            "ai_regulatory_feed": True,
            "red_team_assurance": True,
            "eu_ai_incidents": True,
            "grc_outbound_sync": True,
            "ai_literacy": True,
            "ai_business_intake": True,
            "ai_cost_roi": True,
            "ai_dynamic_risk": True,
            "ai_policy_management": True,
            "ai_runtime_guardrails": True,
            "ai_explainability": True,
            "ai_conformity_workflow": True,
            "ai_insurability": True,
            "ai_regulated_verticals": True,
            "ai_bank_mrm": True,
            "ai_benchmark_exchange": True,
            "ai_discovery_sensors": True,
            "ai_media_provenance": True,
            "ai_identity_governance_connectors": True,
            "threat_hunt": True,
            "env_hunt": True,
            "response_execution": False,
        },
    )

    parsed = tomllib.loads((tmp_path / "config.toml").read_text())
    assert parsed["security_ops"]["enable"] is True
    assert parsed["governed_records"] == {"backend": "auto"}
    assert parsed["evidence_graph"]["enable"] is True
    assert parsed["model_risk_assurance"] == {
        "enable": True,
        "gate_promotions": True,
    }
    assert parsed["evidence_gateway"] == {"enable": True}
    assert parsed["ai_inventory"] == {
        "enable": True,
        "connectors": ["bedrock", "vertex_ai", "mlflow", "aws_sagemaker"],
        "page_cap": 100,
        "record_cap": 5000,
        "retry_attempts": 3,
        "timeout_seconds": 30,
        "missing_grace_runs": 2,
        "poll_seconds": 900,
    }
    assert parsed["ai_vendor_risk"] == {"enable": True}
    assert parsed["portfolio_risk"] == {"enable": True}
    assert parsed["policy_packs"] == {"enable": True}
    assert parsed["ai_regulatory_feed"] == {"enable": True}
    assert parsed["red_team_assurance"] == {
        "enable": True,
        "commitment_secret_ref": "MAVERICK_RED_TEAM_COMMITMENT_KEY",
        "max_cases": 12,
        "max_response_bytes": 65536,
        "campaign_ttl_seconds": 604800,
        "evidence_validity_seconds": 7776000,
        "scheduler_poll_seconds": 30,
    }
    assert parsed["eu_ai_incidents"] == {"enable": True, "authorities": []}
    assert parsed["grc_outbound_sync"] == {
        "enable": True,
        "targets": ["servicenow_irm", "archer"],
        "max_attempts_per_revision": 8,
        "servicenow_irm": {
            "credential_secret_ref": "SERVICENOW_TOKEN",
            "timeout_seconds": 15,
            "max_response_bytes": 262144,
        },
        "archer": {
            "credential_secret_ref": "ARCHER_GRC_TOKEN",
            "timeout_seconds": 15,
            "max_response_bytes": 262144,
        },
    }
    assert parsed["ai_literacy"] == {"enable": True}
    assert parsed["ai_business_intake"] == {"enable": True}
    assert parsed["ai_cost_roi"] == {
        "enable": True,
        "collector_poll_seconds": 86400,
        "collector_lookback_days": 1,
        "collector_limits": {
            "timeout_seconds": 20,
            "page_cap": 20,
            "record_cap": 1000,
            "max_period_days": 93,
            "page_size": 200,
            "max_bytes_billed": 1000000000,
        },
        "aws": {"enable": False},
        "azure": {"enable": False},
        "gcp": {"enable": False},
        "openai": {"enable": False},
    }
    assert parsed["ai_dynamic_risk"] == {"enable": True}
    assert parsed["ai_policy_management"] == {"enable": True}
    assert parsed["ai_runtime_guardrails"] == {"enable": True}
    assert parsed["ai_explainability"] == {"enable": True}
    assert parsed["ai_conformity_workflow"] == {"enable": True}
    assert parsed["ai_insurability"] == {"enable": True}
    assert parsed["ai_regulated_verticals"] == {"enable": True}
    assert parsed["ai_bank_mrm"] == {"enable": True}
    assert parsed["ai_benchmark_exchange"] == {"enable": True}
    assert parsed["ai_discovery_sensors"] == {"enable": True}
    assert parsed["ai_media_provenance"] == {"enable": True}
    assert parsed["ai_identity_governance_connectors"] == {
        "enable": True,
        "entra": {
            "enable": False,
            "tenant_id": "",
            "base_url": "https://graph.microsoft.com",
            "credential_secret_ref": "ENTRA_IDENTITY_GOVERNANCE_TOKEN",
        },
        "okta": {
            "enable": False,
            "tenant_id": "",
            "base_url": "https://replace-me.okta.com",
            "credential_secret_ref": "OKTA_IDENTITY_GOVERNANCE_TOKEN",
        },
        "purview": {
            "enable": False,
            "tenant_id": "",
            "base_url": "https://replace-me.purview.azure.com",
            "credential_secret_ref": "PURVIEW_IDENTITY_GOVERNANCE_TOKEN",
        },
    }
    environment = (tmp_path / ".env").read_text(encoding="utf-8")
    commitment_line = next(
        line
        for line in environment.splitlines()
        if line.startswith("MAVERICK_RED_TEAM_COMMITMENT_KEY=")
    )
    assert len(commitment_line.partition("=")[2]) >= 32
    assert parsed["external_agents"]["enable"] is True
    assert parsed["model_improvement"] == {
        "enable": True,
        "allow_hosted": False,
        "allow_cross_tenant": False,
        "require_signed_receipt": True,
        "minimum_train_families": 20,
        "minimum_holdout_families": 20,
    }
    assert parsed["threat_hunt"]["enable"] is True
    assert parsed["env_hunt"] == {
        "enable": True,
        "response_execution": False,
        "poll_seconds": 300,
        "connectors": {
            "cloudtrail": {"enable": False, "push_enable": False, "pivot_enable": False},
            "guardduty": {"enable": False, "push_enable": False, "pivot_enable": False},
            "syslog": {"enable": False, "push_enable": False, "pivot_enable": False},
            "edr": {"enable": False, "push_enable": False, "pivot_enable": False},
            "splunk": {"enable": False, "push_enable": False, "pivot_enable": False},
            "elastic": {"enable": False, "push_enable": False, "pivot_enable": False},
            "sentinel": {"enable": False, "push_enable": False, "pivot_enable": False},
            "kubernetes_audit": {"enable": False, "push_enable": False, "pivot_enable": False},
            "okta": {"enable": False, "push_enable": False, "pivot_enable": False},
            "entra": {"enable": False, "push_enable": False, "pivot_enable": False},
        },
    }


def test_evidence_gateway_enables_assurance_dependencies(monkeypatch):
    from maverick_installer import wizard

    answers = iter([True, False, False, True] + [False] * 60)
    monkeypatch.setattr(
        wizard, "_q_confirm", lambda *args, **kwargs: next(answers)
    )

    picked = wizard.pick_security_suite()

    assert picked["evidence_gateway"] is True
    assert picked["evidence_graph"] is True
    assert picked["model_risk_assurance"] is True


def test_security_connector_knobs_are_explicit_and_independent():
    from maverick_installer import wizard

    lines = wizard._cfg_security_suite({
        "security_ops": True,
        "env_hunt": True,
        "connectors": {
            "cloudtrail": {"enable": True, "push_enable": True},
            "entra": {"enable": True, "pivot_enable": True},
        },
    })
    parsed = tomllib.loads("\n".join(lines))
    assert parsed["env_hunt"]["connectors"]["cloudtrail"]["enable"] is True
    assert parsed["env_hunt"]["connectors"]["cloudtrail"]["push_enable"] is True
    assert parsed["env_hunt"]["connectors"]["cloudtrail"]["pivot_enable"] is False
    assert parsed["env_hunt"]["connectors"]["entra"]["enable"] is True
    assert parsed["env_hunt"]["connectors"]["entra"]["pivot_enable"] is True
    assert parsed["env_hunt"]["connectors"]["edr"]["enable"] is False


def test_security_enrichment_choices_render_as_runtime_enable_tables():
    from maverick_installer import wizard

    lines = wizard._cfg_security_suite({
        "security_ops": True,
        "env_hunt": True,
        "enrichment_sources": ["VirusTotal", "abuseipdb", "virustotal"],
    })
    parsed = tomllib.loads("\n".join(lines))

    assert parsed["env_hunt"]["enrichment_sources"] == {
        "virustotal": {"enable": True},
        "abuseipdb": {"enable": True},
    }
