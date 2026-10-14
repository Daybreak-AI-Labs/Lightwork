"""Default-deny production operator automation contract."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_MANIFEST = REPO_ROOT / "release" / "production-surface-manifest.json"


def _production_cli(monkeypatch, tmp_path) -> None:
    from maverick import config

    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.setenv("MAVERICK_PRODUCTION_MANIFEST", str(PRODUCTION_MANIFEST))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    # This test exercises CLI visibility. Configuration exclusion behavior has
    # its own exhaustive suite and must not depend on the caller's environment.
    monkeypatch.setattr(
        "maverick.production_exclusions.validate_production_configuration",
        lambda: (),
    )
    config.reset_config_cache()


def test_contract_is_deterministic_canonical_and_manifest_bound(
    monkeypatch, tmp_path
) -> None:
    from maverick.cli import main
    from maverick.operator_contract import (
        SCHEMA,
        build_operator_contract,
        canonical_json,
    )
    from maverick.production_scope import load_manifest

    _production_cli(monkeypatch, tmp_path)
    first = build_operator_contract(main)
    second = build_operator_contract(main)
    assert first == second

    result = CliRunner().invoke(main, ["operator-contract", "--json"])
    assert result.exit_code == 0, result.output
    assert result.output == canonical_json(first) + "\n"
    document = json.loads(result.output)
    manifest = load_manifest()
    assert document["schema"] == SCHEMA
    assert document["production_manifest"] == {
        "production_surface_required": True,
        "profile_id": manifest.profile_id,
        "schema": "lightwork.production-surface.v1",
        "sha256": manifest.sha256,
        "source_version": manifest.source_version,
    }


def test_contract_lists_only_exact_production_core_leaf_commands() -> None:
    from maverick.cli import main
    from maverick.operator_contract import AUTOMATION_SAFE_COMMANDS
    from maverick.production_exclusions import cli_command_classification

    paths = {command.path for command in AUTOMATION_SAFE_COMMANDS}
    assert paths == {
        "capability revocations",
        "operator-contract",
        "preflight",
        "runs",
        "spend",
        "whoami",
    }
    for path in paths:
        assert cli_command_classification(path) == "core"
    assert "start" not in paths
    assert "tenant postgres-erase" not in paths
    assert "plugin" not in paths
    assert main.commands["operator-contract"].params


def test_contract_distinguishes_human_admission_from_machine_compatibility() -> None:
    from maverick.cli import main
    from maverick.operator_contract import build_operator_contract

    scope = build_operator_contract(main)["compatibility_scope"]
    assert "Unlisted commands have no automation compatibility promise" in scope[
        "automation"
    ]
    assert "broader human-operator CLI" in scope["human_interactive"]
    assert "outside" in scope["root_options"]


def test_validator_rejects_duplicate_and_malformed_entries() -> None:
    from maverick.cli import main
    from maverick.operator_contract import (
        AUTOMATION_SAFE_COMMANDS,
        OperatorContractError,
        validate_operator_commands,
    )

    first = AUTOMATION_SAFE_COMMANDS[0]
    with pytest.raises(OperatorContractError, match="duplicate paths"):
        validate_operator_commands(main, (first, first))
    malformed = replace(first, state_class="observational")
    with pytest.raises(OperatorContractError, match="state class"):
        validate_operator_commands(main, (malformed,))


def test_validator_rejects_removed_click_option(monkeypatch) -> None:
    from maverick.cli import main
    from maverick.operator_contract import (
        AUTOMATION_SAFE_COMMANDS,
        OperatorContractError,
        validate_operator_commands,
    )

    command = click.Group.get_command(
        main, click.Context(main, info_name="maverick"), "whoami"
    )
    assert command is not None
    monkeypatch.setattr(
        command,
        "params",
        [
            parameter
            for parameter in command.params
            if not (
                isinstance(parameter, click.Option)
                and "--principal" in parameter.opts
            )
        ],
    )
    with pytest.raises(OperatorContractError, match="Click options drifted for whoami"):
        validate_operator_commands(main, AUTOMATION_SAFE_COMMANDS)


def test_validator_rejects_unclassified_or_unsupported_command() -> None:
    from maverick.cli import main
    from maverick.operator_contract import (
        AUTOMATION_SAFE_COMMANDS,
        OperatorContractError,
        validate_operator_commands,
    )

    unsupported = replace(
        AUTOMATION_SAFE_COMMANDS[0],
        path="cache stats",
        template="maverick cache stats --json",
        declared_options=(),
        required_options=(),
        fixed_options=(),
    )
    with pytest.raises(OperatorContractError, match="not production core"):
        validate_operator_commands(main, (unsupported,))

    missing = replace(
        AUTOMATION_SAFE_COMMANDS[0],
        path="future-unreviewed-command",
        template="maverick future-unreviewed-command --json",
    )
    with pytest.raises(OperatorContractError, match="not production core"):
        validate_operator_commands(main, (missing,))


def test_production_help_exposes_contract_and_refuses_unadmitted_command(
    monkeypatch, tmp_path
) -> None:
    from maverick.cli import main

    _production_cli(monkeypatch, tmp_path)
    runner = CliRunner()
    help_result = runner.invoke(main, ["--help"])
    assert help_result.exit_code == 0, help_result.output
    assert "operator-contract" in help_result.output
    assert "cache" not in set(main.list_commands(click.Context(main)))

    refused = runner.invoke(main, ["cache", "stats"])
    assert refused.exit_code == 2
    assert "No such command" in refused.output


def test_contract_requires_every_declared_option_and_control_to_exist() -> None:
    from maverick.cli import main
    from maverick.operator_contract import (
        AUTOMATION_SAFE_COMMANDS,
        validate_operator_commands,
    )

    validate_operator_commands(main)
    for command in AUTOMATION_SAFE_COMMANDS:
        assert set(command.required_options) <= set(command.declared_options)
        assert command.state_class == "read-only"
        assert command.dry_run.requirement == "not-applicable"
        assert command.confirmation.requirement == "not-applicable"
        assert command.operation_id.requirement == "not-applicable"


def test_declared_output_shapes_match_live_commands(monkeypatch, tmp_path) -> None:
    from maverick import config
    from maverick.cli import main
    from maverick.operator_contract import AUTOMATION_SAFE_COMMANDS

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_PRODUCTION_MANIFEST", str(PRODUCTION_MANIFEST))
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    config.reset_config_cache()
    invocations = {
        "capability revocations": ["capability", "revocations", "--json"],
        "operator-contract": ["operator-contract", "--json"],
        "preflight": ["preflight", "--profile", "run", "--json"],
        "runs": [
            "--db",
            str(tmp_path / "world.db"),
            "runs",
            "--limit",
            "10",
            "--json",
        ],
        "spend": [
            "spend",
            "--limit",
            "100",
            "--tag-field",
            "tag",
            "--top",
            "10",
            "--json",
        ],
        "whoami": ["whoami", "--principal", "user:operator", "--json"],
    }
    specs = {command.path: command for command in AUTOMATION_SAFE_COMMANDS}

    for path, invocation in invocations.items():
        result = CliRunner().invoke(main, invocation)
        if path == "preflight":
            assert result.exit_code in {0, 1}, result.output
        else:
            assert result.exit_code == 0, result.output
        value = json.loads(result.output)
        output = specs[path].output
        assert isinstance(value, list if output.top_level == "array" else dict)
        if isinstance(value, dict):
            assert set(output.required_fields) <= set(value)
        else:
            for item in value:
                assert set(output.item_required_fields) <= set(item)
