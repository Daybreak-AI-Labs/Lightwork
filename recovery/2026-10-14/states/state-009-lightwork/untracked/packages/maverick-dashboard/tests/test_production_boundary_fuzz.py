"""Deterministic property tests for first-profile production parsers.

These boundaries consume reconnect headers, signed release configuration, or
durable replay records.  The corpus is fixed so failures are reproducible, and
the contract is stricter than merely "does not crash": ambiguous coercions,
duplicate authority keys, non-finite JSON, and oversized records must fail with
the boundary's documented typed error.
"""

from __future__ import annotations

import json
import random
from typing import Any

import pytest
from maverick import production_scope
from maverick.api_idempotency import (
    MAX_IDEMPOTENT_RESULT_BYTES,
    ApiIdempotencyError,
    ApiIdempotencyIndeterminate,
    decode_goal_result,
    encode_goal_result,
    request_sha256,
    validate_request_sha256,
)
from maverick_dashboard.stream_cursor import (
    MAX_EVENT_ID,
    numeric_cursor,
    snapshot_cursor,
    snapshot_id,
)


def _json_value(rng: random.Random, depth: int = 0) -> Any:
    if depth >= 4 or rng.random() < 0.52:
        return rng.choice(
            [
                None,
                True,
                False,
                -1,
                0,
                1,
                1 << 53,
                -19.25,
                0.0,
                "",
                "plain",
                " edge text ",
                "\u2603\U0001f680",
                "../authority",
                "x" * 257,
            ]
        )
    if rng.random() < 0.5:
        return [_json_value(rng, depth + 1) for _ in range(rng.randrange(5))]
    return {
        f"k{depth}-{index}-{rng.randrange(1000)}": _json_value(rng, depth + 1)
        for index in range(rng.randrange(5))
    }


def test_numeric_cursor_has_one_exact_monotonic_decimal_grammar() -> None:
    rng = random.Random(0xC0FFEE)
    for _ in range(500):
        query = rng.randrange(MAX_EVENT_ID + 1)
        header = rng.randrange(MAX_EVENT_ID + 1)
        assert numeric_cursor(str(query), f" {header} ") == max(query, header)

    ambiguous: tuple[object, ...] = (
        True,
        False,
        1.0,
        1.9,
        b"1",
        "+1",
        "-0",
        "1_0",
        "1.0",
        "\uff11",
        "0x10",
        "0" * 20,
        str(MAX_EVENT_ID + 1),
    )
    for value in ambiguous:
        with pytest.raises(ValueError):
            numeric_cursor(value)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            numeric_cursor(0, value)  # type: ignore[arg-type]


def test_snapshot_cursor_is_canonical_and_content_addressed() -> None:
    payload_a = {"z": [1, {"two": True}], "a": "value"}
    payload_b = {"a": "value", "z": [1, {"two": True}]}
    cursor = snapshot_id(payload_a)

    assert snapshot_id(payload_b) == cursor
    assert snapshot_cursor(cursor.upper()) == cursor
    assert snapshot_cursor(f"  {cursor}  ") == cursor
    for value in (True, b"0" * 64, "f" * 63, "f" * 65, "g" * 64, "0x" + "0" * 64):
        with pytest.raises(ValueError):
            snapshot_cursor(value)  # type: ignore[arg-type]


def test_production_manifest_parser_fuzz_fails_only_with_typed_error(tmp_path) -> None:
    rng = random.Random(0x51A7)
    path = tmp_path / "mutated-production-manifest.json"
    for _ in range(300):
        value = _json_value(rng)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        try:
            production_scope.load_manifest(path)
        except production_scope.ProductionScopeError:
            pass

    source = production_scope.load_manifest()
    document = json.loads(source.path.read_text(encoding="utf-8"))
    malformed = []

    duplicate_top_level = '{"schema":"shadow",' + json.dumps(document)[1:]
    malformed.append(duplicate_top_level.encode("utf-8"))

    non_finite = dict(document)
    non_finite["constraints"] = dict(document["constraints"])
    non_finite["constraints"]["control_plane_replicas"] = float("nan")
    malformed.append(json.dumps(non_finite).encode("utf-8"))

    malformed.append(("[" * 2000 + "0" + "]" * 2000).encode("utf-8"))
    malformed.append(b"{\"schema\":\xff}")

    for raw in malformed:
        path.write_bytes(raw)
        with pytest.raises(production_scope.ProductionScopeError):
            production_scope.load_manifest(path)


@pytest.mark.parametrize("extra", ["ga", {"unhashable": True}, ["nested"]])
def test_production_status_vocabulary_rejects_duplicates_and_non_text(
    tmp_path,
    extra: object,
) -> None:
    source = production_scope.load_manifest()
    document = json.loads(source.path.read_text(encoding="utf-8"))
    document["allowed_statuses"].append(extra)
    path = tmp_path / "status-vocabulary.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(production_scope.ProductionScopeError, match="vocabulary"):
        production_scope.load_manifest(path)


def test_idempotency_json_round_trip_property_is_canonical() -> None:
    rng = random.Random(0x1D3A)
    for iteration in range(300):
        response = {"iteration": iteration, "payload": _json_value(rng)}
        dispatch = (
            None
            if iteration % 3 == 0
            else {"attempt": iteration % 7, "payload": _json_value(rng)}
        )
        encoded = encode_goal_result(response, dispatch)
        decoded_response, decoded_dispatch = decode_goal_result(encoded)

        assert decoded_response == response
        assert decoded_dispatch == dispatch
        assert encode_goal_result(decoded_response, decoded_dispatch) == encoded
        assert request_sha256(response) == request_sha256(dict(reversed(response.items())))


def test_idempotency_decoder_rejects_ambiguous_or_unbounded_records() -> None:
    duplicate = (
        '{"dispatch":null,"response":{},"response":{"shadow":true},'
        '"schema":"lightwork.api-idempotent-goal-result.v1"}'
    )
    malformed: tuple[object, ...] = (
        duplicate,
        '{"value":NaN}',
        '{"value":Infinity}',
        "[" * 2000 + "0" + "]" * 2000,
        json.dumps({"value": "x" * MAX_IDEMPOTENT_RESULT_BYTES}),
        b"{}",
        7,
    )
    for value in malformed:
        with pytest.raises(ApiIdempotencyIndeterminate):
            decode_goal_result(value)  # type: ignore[arg-type]


def test_request_digest_never_coerces_an_object_identity() -> None:
    class DigestLookalike:
        def __str__(self) -> str:
            return "a" * 64

    assert validate_request_sha256("a" * 64) == "a" * 64
    for value in (DigestLookalike(), b"a" * 64, None, True):
        with pytest.raises(ApiIdempotencyError, match="digest is invalid"):
            validate_request_sha256(value)  # type: ignore[arg-type]
