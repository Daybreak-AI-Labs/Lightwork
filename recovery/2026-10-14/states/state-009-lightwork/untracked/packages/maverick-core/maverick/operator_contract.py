"""Machine-discoverable contract for the first production operator CLI.

Production CLI admission and automation compatibility are deliberately
different decisions.  The manifest admits a broader surface for trained human
operators; this module promises a stable machine interface only for the small,
explicit registry below.  Every other command remains default-deny for
automation even when it is visible to a human operator.
"""
from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import click

SCHEMA = "lightwork.production-operator-cli.v1"
STATE_CLASSES = frozenset({"read-only", "mutating", "destructive"})
CONTROL_REQUIREMENTS = frozenset(
    {"not-applicable", "optional", "required", "unsupported"}
)


class OperatorContractError(RuntimeError):
    """The reviewed operator contract drifted from the registered CLI."""


@dataclass(frozen=True)
class ControlContract:
    requirement: str
    option: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {"requirement": self.requirement, "option": self.option}


@dataclass(frozen=True)
class OutputContract:
    format: str
    schema: str
    top_level: str
    required_fields: tuple[str, ...] = ()
    item_required_fields: tuple[str, ...] = ()
    canonical_serialization: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_serialization": self.canonical_serialization,
            "format": self.format,
            "item_required_fields": list(self.item_required_fields),
            "required_fields": list(self.required_fields),
            "schema": self.schema,
            "top_level": self.top_level,
        }


@dataclass(frozen=True)
class OperatorCommand:
    path: str
    template: str
    state_class: str
    output: OutputContract
    declared_options: tuple[str, ...]
    required_options: tuple[str, ...]
    fixed_options: tuple[tuple[str, str | bool], ...]
    arguments: tuple[str, ...]
    dry_run: ControlContract
    confirmation: ControlContract
    operation_id: ControlContract
    exit_one: str = "execution or production precondition failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "arguments": list(self.arguments),
            "controls": {
                "confirmation": self.confirmation.to_dict(),
                "dry_run": self.dry_run.to_dict(),
                "operation_id": self.operation_id.to_dict(),
            },
            "exit_codes": {
                "0": "completed and emitted the declared output schema",
                "1": self.exit_one,
                "2": "CLI usage error or command visibility refusal",
            },
            "options": {
                "declared": list(self.declared_options),
                "fixed": dict(self.fixed_options),
                "required": list(self.required_options),
            },
            "output": self.output.to_dict(),
            "path": self.path,
            "state_class": self.state_class,
            "template": self.template,
        }


_NOT_APPLICABLE = ControlContract("not-applicable")

# Keep this tuple sorted by path.  Exact Click option/argument inventories are
# intentional: adding an option does not silently expand the automation API.
AUTOMATION_SAFE_COMMANDS: tuple[OperatorCommand, ...] = (
    OperatorCommand(
        path="capability revocations",
        template="maverick capability revocations --json",
        state_class="read-only",
        output=OutputContract(
            format="application/json",
            schema="lightwork.cli.capability-revocations.v1",
            top_level="object",
        ),
        declared_options=("--json",),
        required_options=("--json",),
        fixed_options=(("--json", True),),
        arguments=(),
        dry_run=_NOT_APPLICABLE,
        confirmation=_NOT_APPLICABLE,
        operation_id=_NOT_APPLICABLE,
    ),
    OperatorCommand(
        path="operator-contract",
        template="maverick operator-contract --json",
        state_class="read-only",
        output=OutputContract(
            format="application/json",
            schema=SCHEMA,
            top_level="object",
            required_fields=(
                "automation_safe_commands",
                "compatibility_scope",
                "exit_codes",
                "production_manifest",
                "schema",
            ),
            canonical_serialization=True,
        ),
        declared_options=("--json",),
        required_options=("--json",),
        fixed_options=(("--json", True),),
        arguments=(),
        dry_run=_NOT_APPLICABLE,
        confirmation=_NOT_APPLICABLE,
        operation_id=_NOT_APPLICABLE,
    ),
    OperatorCommand(
        path="preflight",
        template="maverick preflight --profile run --json",
        state_class="read-only",
        output=OutputContract(
            format="application/json",
            schema="lightwork.operator-preflight.v1",
            top_level="object",
            required_fields=(
                "attention_count",
                "blocker_count",
                "checks",
                "next_action",
                "profile",
                "ready",
                "schema",
            ),
        ),
        declared_options=("--json", "--profile"),
        required_options=("--json", "--profile"),
        fixed_options=(("--json", True), ("--profile", "run")),
        arguments=(),
        dry_run=_NOT_APPLICABLE,
        confirmation=_NOT_APPLICABLE,
        operation_id=_NOT_APPLICABLE,
        exit_one="readiness blockers were reported or execution failed",
    ),
    OperatorCommand(
        path="runs",
        template="maverick runs --limit LIMIT --json",
        state_class="read-only",
        output=OutputContract(
            format="application/json",
            schema="lightwork.cli.runs.v1",
            top_level="array",
            item_required_fields=(
                "cost_dollars",
                "duration_s",
                "ended_at",
                "episode_id",
                "goal_id",
                "goal_status",
                "goal_title",
                "input_tokens",
                "outcome",
                "output_tokens",
                "running",
                "started_at",
                "tool_calls",
            ),
        ),
        declared_options=("--goal", "--json", "--limit"),
        required_options=("--json", "--limit"),
        fixed_options=(("--json", True),),
        arguments=(),
        dry_run=_NOT_APPLICABLE,
        confirmation=_NOT_APPLICABLE,
        operation_id=_NOT_APPLICABLE,
    ),
    OperatorCommand(
        path="spend",
        template=(
            "maverick spend --limit LIMIT --tag-field FIELD --top TOP --json"
        ),
        state_class="read-only",
        output=OutputContract(
            format="application/json",
            schema="lightwork.cli.spend.v1",
            top_level="object",
            required_fields=("by_goal", "by_tag", "tag_field", "total"),
        ),
        declared_options=("--json", "--limit", "--tag-field", "--top"),
        required_options=("--json", "--limit", "--tag-field", "--top"),
        fixed_options=(("--json", True),),
        arguments=(),
        dry_run=_NOT_APPLICABLE,
        confirmation=_NOT_APPLICABLE,
        operation_id=_NOT_APPLICABLE,
    ),
    OperatorCommand(
        path="whoami",
        template="maverick whoami --principal PRINCIPAL --json",
        state_class="read-only",
        output=OutputContract(
            format="application/json",
            schema="lightwork.cli.whoami.v1",
            top_level="object",
            required_fields=(
                "allow_hosts",
                "allow_paths",
                "allow_tools",
                "deny_tools",
                "enforcement",
                "expires_at",
                "max_risk",
                "principal",
                "role",
            ),
        ),
        declared_options=("--channel", "--json", "--principal", "--user"),
        required_options=("--json", "--principal"),
        fixed_options=(("--json", True),),
        arguments=(),
        dry_run=_NOT_APPLICABLE,
        confirmation=_NOT_APPLICABLE,
        operation_id=_NOT_APPLICABLE,
    ),
)


def _resolve_command(root: click.Group, path: str) -> click.Command:
    command: click.Command = root
    context = click.Context(root, info_name="maverick")
    for index, part in enumerate(path.split()):
        if not isinstance(command, click.Group):
            raise OperatorContractError(f"operator path is not a command: {path}")
        child = click.Group.get_command(command, context, part)
        if child is None:
            raise OperatorContractError(f"operator command is missing: {path}")
        command = child
        if index < len(path.split()) - 1:
            context = click.Context(child, info_name=part, parent=context)
    if isinstance(command, click.Group):
        raise OperatorContractError(f"operator path must resolve to a leaf: {path}")
    return command


def _validate_control(
    command: OperatorCommand,
    name: str,
    control: ControlContract,
) -> None:
    if control.requirement not in CONTROL_REQUIREMENTS:
        raise OperatorContractError(
            f"invalid {name} requirement for {command.path}"
        )
    if control.requirement == "not-applicable":
        if control.option is not None:
            raise OperatorContractError(
                f"not-applicable {name} cannot name an option for {command.path}"
            )
        return
    if not control.option or control.option not in command.declared_options:
        raise OperatorContractError(
            f"{name} option is not declared for {command.path}"
        )


def _validate_command_shape(
    command: OperatorCommand,
    output_schemas: set[str],
) -> None:
    if command.path != " ".join(command.path.split()) or not command.path:
        raise OperatorContractError("operator command path is malformed")
    if command.state_class not in STATE_CLASSES:
        raise OperatorContractError(f"invalid state class for {command.path}")
    if not command.output.schema or command.output.schema in output_schemas:
        raise OperatorContractError(
            f"duplicate or empty output schema for {command.path}"
        )
    output_schemas.add(command.output.schema)
    if command.output.format != "application/json":
        raise OperatorContractError(f"unsupported output format for {command.path}")
    if command.output.top_level not in {"array", "object"}:
        raise OperatorContractError(f"invalid output shape for {command.path}")


def _validate_options_and_controls(command: OperatorCommand) -> None:
    declared = command.declared_options
    required = command.required_options
    fixed = dict(command.fixed_options)
    if (
        declared != tuple(sorted(declared))
        or len(declared) != len(set(declared))
        or any(not option.startswith("--") for option in declared)
    ):
        raise OperatorContractError(
            f"declared options are malformed for {command.path}"
        )
    if len(required) != len(set(required)) or not set(required) <= set(declared):
        raise OperatorContractError(
            f"required options are malformed for {command.path}"
        )
    if len(fixed) != len(command.fixed_options) or not set(fixed) <= set(required):
        raise OperatorContractError(f"fixed options are malformed for {command.path}")
    for name, control in (
        ("dry-run", command.dry_run),
        ("confirmation", command.confirmation),
        ("operation-id", command.operation_id),
    ):
        _validate_control(command, name, control)
    if command.state_class == "read-only" and any(
        control.requirement != "not-applicable"
        for control in (
            command.dry_run,
            command.confirmation,
            command.operation_id,
        )
    ):
        raise OperatorContractError(
            f"read-only command carries mutation controls: {command.path}"
        )
    if (
        command.state_class == "destructive"
        and command.confirmation.requirement != "required"
    ):
        raise OperatorContractError(
            f"destructive command lacks confirmation: {command.path}"
        )


def _validate_template(command: OperatorCommand) -> None:
    tokens = shlex.split(command.template)
    path_tokens = command.path.split()
    if tokens[: 1 + len(path_tokens)] != ["maverick", *path_tokens]:
        raise OperatorContractError(
            f"command template does not match path: {command.path}"
        )
    for option in command.required_options:
        if option not in tokens:
            raise OperatorContractError(
                f"required option missing from template for {command.path}: {option}"
            )
    for option, value in command.fixed_options:
        index = tokens.index(option)
        if value is not True and (
            index + 1 >= len(tokens) or tokens[index + 1] != str(value)
        ):
            raise OperatorContractError(
                f"fixed option value drifted for {command.path}: {option}"
            )


def _validate_policy_and_click(root: click.Group, command: OperatorCommand) -> None:
    from .production_exclusions import (
        cli_command_allowed,
        cli_command_classification,
    )

    if cli_command_classification(command.path) != "core":
        raise OperatorContractError(
            f"operator command is not production core: {command.path}"
        )
    if not cli_command_allowed(command.path):
        raise OperatorContractError(
            f"operator command is not production admitted: {command.path}"
        )

    click_command = _resolve_command(root, command.path)
    actual_options = tuple(
        sorted(
            option
            for parameter in click_command.params
            if isinstance(parameter, click.Option)
            for option in parameter.opts
            if option.startswith("--")
        )
    )
    actual_arguments = tuple(
        parameter.name
        for parameter in click_command.params
        if isinstance(parameter, click.Argument)
    )
    if actual_options != command.declared_options:
        raise OperatorContractError(
            f"Click options drifted for {command.path}: "
            f"expected {command.declared_options}, got {actual_options}"
        )
    if actual_arguments != command.arguments:
        raise OperatorContractError(
            f"Click arguments drifted for {command.path}: "
            f"expected {command.arguments}, got {actual_arguments}"
        )


def validate_operator_commands(
    root: click.Group,
    commands: Sequence[OperatorCommand] = AUTOMATION_SAFE_COMMANDS,
) -> None:
    """Fail closed if the reviewed registry no longer matches Click policy."""
    if not commands:
        raise OperatorContractError("operator contract must not be empty")
    paths = [command.path for command in commands]
    if paths != sorted(paths):
        raise OperatorContractError("operator commands must be sorted by path")
    if len(paths) != len(set(paths)):
        raise OperatorContractError("operator contract contains duplicate paths")

    output_schemas: set[str] = set()
    for command in commands:
        _validate_command_shape(command, output_schemas)
        _validate_options_and_controls(command)
        _validate_template(command)
        _validate_policy_and_click(root, command)


def build_operator_contract(root: click.Group) -> dict[str, Any]:
    """Return the current manifest-bound first-profile automation contract."""
    from .production_scope import SCHEMA as MANIFEST_SCHEMA
    from .production_scope import load_manifest, production_surface_required

    validate_operator_commands(root)
    manifest = load_manifest()
    return {
        "automation_safe_commands": [
            command.to_dict() for command in AUTOMATION_SAFE_COMMANDS
        ],
        "compatibility_scope": {
            "automation": (
                "Only automation_safe_commands are covered by this compatibility "
                "contract. Unlisted commands have no automation compatibility promise."
            ),
            "human_interactive": (
                "The production manifest admits a broader human-operator CLI; "
                "visibility is not an automation compatibility guarantee."
            ),
            "root_options": (
                "Root/global CLI options are outside this automation contract."
            ),
        },
        "exit_codes": {
            "0": "command completed and emitted its declared successful output",
            "1": (
                "command did not establish success: a negative gate result, "
                "execution failure, or production precondition failure"
            ),
            "2": "CLI usage error or command visibility refusal",
            "other": "abnormal process/runtime termination; no compatibility promise",
        },
        "production_manifest": {
            "production_surface_required": production_surface_required(),
            "profile_id": manifest.profile_id,
            "schema": MANIFEST_SCHEMA,
            "sha256": manifest.sha256,
            "source_version": manifest.source_version,
        },
        "schema": SCHEMA,
    }


def canonical_json(document: dict[str, Any]) -> str:
    """Serialize a contract with stable bytes and strict JSON values."""
    return json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "AUTOMATION_SAFE_COMMANDS",
    "CONTROL_REQUIREMENTS",
    "ControlContract",
    "OperatorCommand",
    "OperatorContractError",
    "OutputContract",
    "SCHEMA",
    "STATE_CLASSES",
    "build_operator_contract",
    "canonical_json",
    "validate_operator_commands",
]
