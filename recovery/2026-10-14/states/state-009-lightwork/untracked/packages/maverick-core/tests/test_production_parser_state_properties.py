"""Deterministic properties for every first-profile parser/state gap.

The corpus is fixed and dependency-free so these tests are reproducible in the
ordinary and secure-default suites.  They cover only admitted production
boundaries: governed flow documents/transitions, vLLM's OpenAI-compatible tool
JSON, tenant/workspace path normalization, the automation-safe operator CLI,
and privacy-erasure receipt state.
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import click
import pytest

_SCALARS: tuple[object, ...] = (
    None,
    True,
    False,
    -1,
    0,
    1,
    1 << 70,
    -19.25,
    0.0,
    "",
    "plain",
    " edge text ",
    "../authority",
    "\\\\server\\share",
    "\u2603\U0001f680",
    "x" * 257,
)


def _json_value(rng: random.Random, depth: int = 0) -> Any:
    if depth >= 4 or rng.random() < 0.50:
        return rng.choice(_SCALARS)
    if rng.random() < 0.5:
        return [_json_value(rng, depth + 1) for _ in range(rng.randrange(6))]
    return {
        f"k{depth}-{index}-{rng.randrange(1000)}": _json_value(rng, depth + 1)
        for index in range(rng.randrange(6))
    }


def _deep_value(depth: int) -> dict[str, object]:
    root: dict[str, object] = {}
    cursor = root
    for _ in range(depth):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child
    return root


def test_governed_flow_document_fuzz_fails_only_with_typed_error() -> None:
    from maverick.flow import Flow, FlowDefinitionError

    rng = random.Random(0xF10A)
    for _ in range(500):
        value = _json_value(rng)
        try:
            flow = Flow.from_dict(value)  # type: ignore[arg-type]
        except FlowDefinitionError:
            continue
        assert isinstance(flow.validate(), list)

    hostile: tuple[object, ...] = (
        _deep_value(100),
        {"id": "f", "nodes": "not-an-array"},
        {
            "id": "f",
            "nodes": [
                {"id": "duplicate", "kind": "setvar", "assignments": {"x": 1}},
                {"id": "duplicate", "kind": "setvar", "assignments": {"x": 2}},
            ],
        },
        {"id": "f", "max_seconds": float("nan")},
        {"id": "f", "name": "x" * 1_000_001},
    )
    for value in hostile:
        with pytest.raises(FlowDefinitionError):
            Flow.from_dict(value)  # type: ignore[arg-type]


def test_governed_flow_round_trip_preserves_graph_and_validation() -> None:
    from maverick.flow import Flow

    rng = random.Random(0xDA65)
    for iteration in range(150):
        count = rng.randrange(1, 25)
        nodes = []
        for index in range(count):
            successor = None
            if count > 1 and rng.random() < 0.8:
                # Deliberately include back-edges; root graph cycles are bounded
                # at execution and must still round-trip without parser drift.
                successor = f"n{rng.randrange(count)}"
            node = {
                "id": f"n{index}",
                "kind": "setvar",
                "assignments": {f"v{index}": iteration + index},
            }
            if successor is not None:
                node["next"] = successor
            nodes.append(node)
        document = {
            "id": f"flow-{iteration}",
            "name": f"flow {iteration}",
            "start": "n0",
            "nodes": nodes,
        }
        first = Flow.from_dict(document)
        second = Flow.from_dict(first.to_dict())
        assert second.to_dict() == first.to_dict()
        assert second.validate() == first.validate()


def test_vllm_tool_argument_json_is_bounded_object_only() -> None:
    from maverick.providers.openai_provider import (
        MAX_TOOL_ARGUMENT_BYTES,
        ProviderResponseError,
        parse_tool_arguments,
    )

    rng = random.Random(0x7110)
    for iteration in range(400):
        value = {"iteration": iteration, "value": _json_value(rng)}
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
        assert parse_tool_arguments(raw) == value

    invalid = (
        "null",
        "true",
        "[]",
        "1",
        '"text"',
        '{"a":1,"a":2}',
        '{"value":NaN}',
        "[" * 2_000 + "0" + "]" * 2_000,
        json.dumps({"value": "x" * MAX_TOOL_ARGUMENT_BYTES}),
        b"{}",
        None,
    )
    for raw in invalid:
        with pytest.raises(ProviderResponseError):
            parse_tool_arguments(raw)


def test_vllm_provider_response_rejects_malformed_tool_authority() -> None:
    from maverick.providers.openai_provider import OpenAIClient, ProviderResponseError

    def response(*, call_id: object, name: object, arguments: object):
        call = SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(name=name, arguments=arguments),
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="", reasoning_content=None, tool_calls=[call]
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=None,
        )

    accepted = OpenAIClient._from_response(
        response(call_id="call-1", name="read_file", arguments='{"path":"README.md"}'),
        budget=None,
    )
    assert accepted.tool_calls[0].input == {"path": "README.md"}

    for call_id, name, arguments in (
        ("", "read_file", "{}"),
        ("call-1", "", "{}"),
        ("call-1\nshadow", "read_file", "{}"),
        ("call-1", "read_file", "[]"),
        ("call-1", "read_file", "{not-json"),
        (7, "read_file", "{}"),
    ):
        with pytest.raises(ProviderResponseError):
            OpenAIClient._from_response(
                response(call_id=call_id, name=name, arguments=arguments),
                budget=None,
            )


def test_tenant_canonicalization_is_idempotent_and_path_injective() -> None:
    from maverick.paths import InvalidTenantError, _tenant_segment, canonical_tenant_id

    rng = random.Random(0x7EAA17)
    segment_owner: dict[str, str] = {}
    alphabet = "abcXYZ019._- /\\:%\u00e9\u0301\u2603"
    for _ in range(2_000):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 40)))
        try:
            canonical = canonical_tenant_id(raw)
        except InvalidTenantError:
            continue
        assert canonical_tenant_id(canonical) == canonical
        segment = _tenant_segment(canonical)
        assert segment not in {"", ".", ".."}
        assert "/" not in segment and "\\" not in segment
        previous = segment_owner.setdefault(segment, canonical)
        assert previous == canonical


def test_workspace_path_normalization_confines_every_admitted_spelling(
    tmp_path,
) -> None:
    from maverick.agent import _workspace_relative_path

    sandbox = SimpleNamespace(workdir=tmp_path)
    safe = (
        ".",
        "README.md",
        "nested/../README.md",
        "nested/deeper/file.txt",
        "name with spaces.txt",
    )
    for raw in safe:
        normalized = _workspace_relative_path(sandbox, raw)
        assert normalized == _workspace_relative_path(sandbox, normalized)
        assert not normalized.startswith("/")
        assert ".." not in normalized.split("/")

    outside = tmp_path.parent / "outside.txt"
    hostile = (
        "../outside.txt",
        str(outside),
        "../../../../outside.txt",
    )
    for raw in hostile:
        with pytest.raises(ValueError):
            _workspace_relative_path(sandbox, raw)


def test_operator_cli_parser_fuzz_has_only_click_usage_failures() -> None:
    from maverick.cli import main
    from maverick.operator_contract import AUTOMATION_SAFE_COMMANDS, _resolve_command

    rng = random.Random(0xC11)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789_-"
    for specification in AUTOMATION_SAFE_COMMANDS:
        command = _resolve_command(main, specification.path)
        corpora = [
            [],
            ["--definitely-unknown"],
            ["--" + "".join(rng.choice(alphabet) for _ in range(40))],
            ["--help", "unexpected-positional"],
        ]
        for args in corpora:
            try:
                context = command.make_context(
                    specification.path,
                    args,
                    resilient_parsing=False,
                )
            except (click.ClickException, click.exceptions.Exit):
                continue
            else:
                context.close()


def test_erasure_manifest_fuzz_is_typed_and_retention_is_invariant() -> None:
    from maverick.erasure_receipts import (
        RECEIPT_RETENTION_DAYS,
        ErasureReceiptError,
        build_manifest,
        validate_manifest,
    )

    rng = random.Random(0xE2A5E)
    for _ in range(500):
        value = _json_value(rng)
        try:
            validate_manifest(value)  # type: ignore[arg-type]
        except ErasureReceiptError:
            pass

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for iteration in range(250):
        conversations = {rng.randrange(1, 10_000) for _ in range(rng.randrange(1, 20))}
        goals = {rng.randrange(1, 10_000) for _ in range(rng.randrange(20))}
        episodes = {rng.randrange(1, 10_000) for _ in range(rng.randrange(20))}
        manifest = build_manifest(
            tenant_id=f"tenant-{iteration}",
            conversation_ids=conversations,
            goal_ids=goals,
            episode_ids=episodes,
            now=start + timedelta(seconds=iteration),
            receipt_id=f"{iteration:032x}",
        )
        normalized = validate_manifest(manifest)
        issued = datetime.fromisoformat(normalized["issued_at"].replace("Z", "+00:00"))
        retained = datetime.fromisoformat(
            normalized["retained_until"].replace("Z", "+00:00")
        )
        assert retained - issued == timedelta(days=RECEIPT_RETENTION_DAYS)
        assert normalized["conversation_ids"] == sorted(conversations)


def test_flow_transition_parser_fuzz_fails_only_with_typed_error() -> None:
    from maverick.flow import store

    base: dict[str, object] = {
        "schema": "lightwork.flow-node-transition.v1",
        "run_id": "run",
        "flow_id": "flow",
        "segment_id": "segment",
        "sequence": 1,
        "definition_digest": "1" * 64,
        "definition_revision": "revision",
        "release_id": "2" * 64,
        "node_id": "node",
        "node_kind": "agent",
        "tool_name": "",
        "node_status": "done",
        "input_revision_sha256": "3" * 64,
        "receipt": None,
        "receipt_sha256": "0" * 64,
        "output_sha256": "4" * 64,
        "next_cursor": None,
        "recorded_at": 1.0,
        "previous_transition_sha256": "0" * 64,
        "transition_sha256": "0" * 64,
    }
    base["transition_sha256"] = store._transition_digest(base)
    assert store._validate_run_transition(base) == base

    rng = random.Random(0x72A4517)
    fields = tuple(base)
    values = (*_SCALARS, float("nan"), float("inf"), _deep_value(80))
    for _ in range(1_000):
        mutated = dict(base)
        mutated[rng.choice(fields)] = rng.choice(values)
        try:
            store._validate_run_transition(mutated)
        except store.FlowSnapshotError:
            pass
        except Exception as exc:  # noqa: BLE001 - detects a parser exception leak
            raise AssertionError(
                f"transition parser leaked {type(exc).__name__}"
            ) from exc

    for field, value in (
        ("sequence", float("inf")),
        ("sequence", float("nan")),
        ("recorded_at", 10**10_000),
    ):
        mutated = dict(base)
        mutated[field] = value
        with pytest.raises(store.FlowSnapshotError):
            store._validate_run_transition(mutated)

    for value in (None, [], "record", 7, _deep_value(100)):
        with pytest.raises(store.FlowSnapshotError):
            store._validate_run_transition(value)  # type: ignore[arg-type]


def test_numeric_edge_corpus_is_finite() -> None:
    # Guard the shared deterministic corpus from accidentally introducing a
    # non-finite value into the accepted JSON round-trip tests above.
    assert all(not isinstance(value, float) or math.isfinite(value) for value in _SCALARS)
