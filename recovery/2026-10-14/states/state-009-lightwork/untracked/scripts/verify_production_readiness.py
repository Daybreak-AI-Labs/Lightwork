#!/usr/bin/env python3
"""Validate the signed production-readiness evidence contract.

The production-surface manifest deliberately defaults a release to ineligible.
This verifier prevents that Boolean from becoming a self-attestation: a ready
release must enumerate every P0/P1 gate, provide repository evidence for each
closed or excluded gate, bind the canonical source content assessed, and have
no unresolved blocker. P2 gates may remain pending only when the supported
profile makes no broader claim.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "lightwork.production-readiness-evidence.v3"
APPROVAL_SCHEMA = "lightwork.production-readiness-approval.v1"
TRUST_SCHEMA = "lightwork.production-readiness-trust.v1"
TRUST_PATH = "release/production-readiness-trust.json"
PRODUCTION_SCHEMA = "lightwork.production-surface.v1"
PRODUCTION_SURFACE_DIGEST_MODE = "utf8-lf-sha256"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_KEY_ID_RE = re.compile(r"[0-9a-f]{64}")
_PUBLIC_KEY_RE = re.compile(r"[0-9a-f]{64}")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")
_DISPOSITIONS = frozenset({"satisfied", "excluded", "blocked", "pending"})
_READY_DISPOSITIONS = frozenset({"satisfied", "excluded"})
MAX_READINESS_JSON_BYTES = 4 * 1024 * 1024
MAX_READINESS_JSON_DEPTH = 32

# A readiness packet must survive being committed and tagged.  These are the
# only paths omitted from its stable source-content identity:
#
# * the readiness packet itself;
# * proof outputs and the inventory/ledger/release-state views generated after
#   source readiness is assessed.
#
# The canonical source-content algorithm includes this exact tuple in its
# digest, so widening the set changes the identity and invalidates a packet.
READINESS_SOURCE_EXCLUSIONS = (
    "proof/RESULTS.md",
    "proof/results",
    "release/claim-evidence-ledger.json",
    "release/production-code-inventory.json",
    "release/production-readiness-evidence.json",
    "release/release-state.json",
)

# A structurally valid readiness packet is not, by itself, reviewed evidence.
# These digests bind every semantic field in the packet, including the exact
# assessed source content, assessed revision, and production surface.  Only the
# signature envelope is the only field outside that digest.  A ready envelope
# must sign the digest with an independently held
# Ed25519 key trusted by the assessed source; this avoids an in-source digest
# fixed point without weakening the exact source/surface binding.
_SEMANTICALLY_VARIABLE_FIELDS = frozenset({"approval"})
REVIEWED_SEMANTIC_SHA256_BY_STATUS: Mapping[str, str] = {
    "draft": "8f96843f1694cf12bd9d4d96b2337038cae8c280651817672956066f2b9e7d62",
}

_GATE_RANGES = {
    "SCOPE": 8,
    "SEC": 20,
    "SBOX": 15,
    "DATA": 22,
    "RUN": 29,
    "DGM": 17,
    "DEP": 18,
    "OPS": 11,
    "API": 14,
    "INT": 18,
    "REL": 27,
    "QA": 20,
}
P0_GATES = frozenset(
    """SCOPE-01 SCOPE-02 SCOPE-03 SEC-01 SEC-02 SEC-03 SEC-04 SEC-05
    SEC-06 SEC-07 SEC-18 SEC-19 SBOX-01 SBOX-02 SBOX-11 DATA-01 DATA-02
    DATA-03 RUN-01 RUN-02 RUN-03 RUN-19 DGM-01 DGM-02 DGM-03 DGM-04
    DEP-01 OPS-01 API-01 API-13 INT-01 INT-13 INT-14 REL-17 REL-18 REL-19
    QA-01""".split()
)
P2_GATES = frozenset(
    """SBOX-15 DATA-15 DATA-16 DATA-22 RUN-18 RUN-25 RUN-26 RUN-27 RUN-28
    RUN-29 DGM-13 DGM-17 DEP-15 DEP-16 DEP-18 OPS-10 API-12 INT-12 INT-18
    REL-16 REL-26 REL-27 QA-14 QA-15 QA-20""".split()
)
CONDITIONAL_GATES = frozenset(
    """SCOPE-03 SBOX-09 DATA-01 DATA-02 DATA-03 RUN-09 DGM-01 DGM-02 DGM-03
    DGM-04 DGM-05 DGM-06 DGM-07 DGM-08 DGM-09 DGM-10 DGM-13 DGM-14 DGM-15
    DGM-16 DGM-17 DEP-16 DEP-18 API-07 API-08 API-09 API-13 INT-09 INT-10
    INT-13 INT-14 INT-16 INT-17 REL-11 REL-12 REL-13 REL-20 REL-21""".split()
)


class ProductionReadinessError(ValueError):
    """The evidence contract is incomplete, malformed, or contradictory."""


def _metadata_is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(marker and attributes & marker)


def expected_gates() -> frozenset[str]:
    return frozenset(
        f"{prefix}-{number:02d}"
        for prefix, maximum in _GATE_RANGES.items()
        for number in range(1, maximum + 1)
    )


ALL_GATES = expected_gates()
P1_GATES = ALL_GATES - P0_GATES - P2_GATES


def gate_priority(gate_id: str) -> str:
    if gate_id in P0_GATES:
        return "P0"
    if gate_id in P2_GATES:
        return "P2"
    if gate_id in P1_GATES:
        return "P1"
    raise ProductionReadinessError(f"unknown production gate: {gate_id}")


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionReadinessError(f"{label} must be an object")
    return value


def _exact(value: object, keys: set[str], *, label: str) -> Mapping[str, Any]:
    item = _mapping(value, label=label)
    if set(item) != keys:
        raise ProductionReadinessError(f"{label} fields are not exact")
    return item


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProductionReadinessError(f"{label} must be non-empty")
    return value.strip()


def _repo_file(root: Path, value: object, *, label: str) -> str:
    relative = Path(_text(value, label=label))
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ProductionReadinessError(f"{label} must stay inside the repository")
    root = root.resolve()
    candidate = Path(os.path.abspath(root / relative))
    try:
        normalized = candidate.relative_to(root)
    except ValueError as exc:
        raise ProductionReadinessError(f"{label} must stay inside the repository") from exc
    cursor = root
    for index, part in enumerate(normalized.parts):
        cursor /= part
        try:
            metadata = cursor.lstat()
        except FileNotFoundError as exc:
            raise ProductionReadinessError(
                f"{label} does not exist: {normalized.as_posix()}"
            ) from exc
        if cursor.is_symlink() or _metadata_is_reparse_point(metadata):
            raise ProductionReadinessError(
                f"{label} must not contain a symlink or reparse point"
            )
        is_leaf = index == len(normalized.parts) - 1
        if not is_leaf and not stat.S_ISDIR(metadata.st_mode):
            raise ProductionReadinessError(f"{label} parent is not a directory")
        if is_leaf and (
            not stat.S_ISREG(metadata.st_mode) or getattr(metadata, "st_nlink", 1) != 1
        ):
            raise ProductionReadinessError(
                f"{label} must be a single-link regular repository file"
            )
    if not normalized.parts:
        raise ProductionReadinessError(f"{label} does not exist: {relative.as_posix()}")
    return normalized.as_posix()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _check_json_depth(value: object, *, depth: int = 0) -> None:
    if depth > MAX_READINESS_JSON_DEPTH:
        raise ProductionReadinessError("readiness JSON is too deeply nested")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProductionReadinessError("readiness JSON keys must be strings")
            _check_json_depth(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_json_depth(item, depth=depth + 1)


def _read_json(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProductionReadinessError(f"{label} cannot be read") from exc
    if len(raw) > MAX_READINESS_JSON_BYTES:
        raise ProductionReadinessError(f"{label} exceeds the bounded size")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProductionReadinessError(f"{label} is not valid UTF-8 JSON") from exc
    _check_json_depth(value)
    return _mapping(value, label=label)


def production_surface_sha256(path: Path) -> str:
    """Hash UTF-8 production-surface text after canonical LF normalization."""
    try:
        raw = path.read_bytes()
    except (OSError, UnicodeDecodeError) as exc:
        raise ProductionReadinessError(
            "production surface manifest is not valid UTF-8"
        ) from exc
    if len(raw) > MAX_READINESS_JSON_BYTES:
        raise ProductionReadinessError("production surface manifest exceeds the bounded size")
    try:
        source = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProductionReadinessError(
            "production surface manifest is not valid UTF-8"
        ) from exc
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def readiness_semantic_sha256(value: object) -> str:
    """Return the canonical digest requiring readiness-policy review.

    The helper is public so an editor can calculate the candidate digest after
    reviewing a readiness diff.  Calculation is not approval: normal
    verification still requires the draft digest to match the reviewed source
    constant or a ready digest to carry a valid external approval signature.
    """
    evidence = _mapping(value, label="production readiness evidence")
    semantic = {
        key: field
        for key, field in evidence.items()
        if key not in _SEMANTICALLY_VARIABLE_FIELDS
    }
    try:
        canonical = json.dumps(
            semantic,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProductionReadinessError(
            "production readiness semantics are not canonical JSON"
        ) from exc
    return hashlib.sha256(canonical).hexdigest()


def readiness_approval_message(semantic_sha256: str) -> bytes:
    """Return the domain-separated canonical bytes an external approver signs."""
    if _SHA256_RE.fullmatch(semantic_sha256) is None:
        raise ProductionReadinessError("readiness approval semantic digest is invalid")
    return APPROVAL_SCHEMA.encode("ascii") + b"\0" + bytes.fromhex(semantic_sha256)


def _trusted_readiness_keys(root: Path) -> dict[str, str]:
    relative = _repo_file(root, TRUST_PATH, label="production readiness trust policy")
    trust = _exact(
        _read_json(root / relative, label="production readiness trust policy"),
        {"schema", "status", "trusted_ed25519_public_keys"},
        label="production readiness trust policy",
    )
    if trust["schema"] != TRUST_SCHEMA:
        raise ProductionReadinessError("production readiness trust schema is unsupported")
    status = trust["status"]
    if status not in {"active", "unconfigured"}:
        raise ProductionReadinessError("production readiness trust status is invalid")
    raw_keys = _mapping(
        trust["trusted_ed25519_public_keys"],
        label="production readiness trusted keys",
    )
    keys: dict[str, str] = {}
    for key_id, public_key in raw_keys.items():
        if (
            not isinstance(key_id, str)
            or _KEY_ID_RE.fullmatch(key_id) is None
            or not isinstance(public_key, str)
            or _PUBLIC_KEY_RE.fullmatch(public_key) is None
            or public_key == "0" * 64
            or hashlib.sha256(bytes.fromhex(public_key)).hexdigest() != key_id
        ):
            raise ProductionReadinessError("production readiness trusted key is malformed")
        keys[key_id] = public_key
    if (status == "active") != bool(keys):
        raise ProductionReadinessError(
            "production readiness trust status and key inventory disagree"
        )
    return keys


def _validate_signed_approval(
    root: Path,
    approval: object,
    *,
    semantic_digest: str,
) -> None:
    envelope = _exact(
        approval,
        {"schema", "algorithm", "key_id", "semantic_sha256", "signature"},
        label="production readiness approval",
    )
    if envelope["schema"] != APPROVAL_SCHEMA or envelope["algorithm"] != "ed25519":
        raise ProductionReadinessError("production readiness approval scheme is unsupported")
    key_id = envelope["key_id"]
    signature = envelope["signature"]
    if (
        not isinstance(key_id, str)
        or _KEY_ID_RE.fullmatch(key_id) is None
        or envelope["semantic_sha256"] != semantic_digest
        or not isinstance(signature, str)
        or _SIGNATURE_RE.fullmatch(signature) is None
        or signature == "0" * 128
    ):
        raise ProductionReadinessError("production readiness approval is malformed")
    trusted_keys = _trusted_readiness_keys(root)
    public_key = trusted_keys.get(key_id)
    if public_key is None:
        raise ProductionReadinessError(
            "production readiness approval signer is not independently trusted"
        )
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
            bytes.fromhex(signature),
            readiness_approval_message(semantic_digest),
        )
    except ImportError as exc:
        raise ProductionReadinessError(
            "Ed25519 support is required to verify production readiness approval"
        ) from exc
    except (InvalidSignature, ValueError) as exc:
        raise ProductionReadinessError(
            "production readiness approval signature is invalid"
        ) from exc


def _validate_reviewed_semantics(
    root: Path,
    evidence: Mapping[str, Any],
    *,
    status: str,
) -> str:
    semantic_digest = readiness_semantic_sha256(evidence)
    if status == "ready":
        _validate_signed_approval(
            root,
            evidence.get("approval"),
            semantic_digest=semantic_digest,
        )
        return semantic_digest
    if evidence.get("approval") is not None:
        raise ProductionReadinessError("draft readiness evidence cannot carry an approval")
    reviewed_digest = REVIEWED_SEMANTIC_SHA256_BY_STATUS["draft"]
    if semantic_digest != reviewed_digest:
        raise ProductionReadinessError(
            "production readiness semantics differ from the reviewed contract; "
            f"status={status}, calculated_sha256={semantic_digest}; review the "
            "complete semantic diff before updating "
            "REVIEWED_SEMANTIC_SHA256_BY_STATUS"
        )
    return semantic_digest


def _load_proof_provenance(root: Path):
    path = root / "packages" / "maverick-core" / "maverick" / "proof_provenance.py"
    module_name = "readiness_proof_provenance"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ProductionReadinessError("cannot load source-content identity verifier")
    module = importlib.util.module_from_spec(spec)
    sentinel = object()
    previous_module = sys.modules.get(module_name, sentinel)
    previous_dont_write_bytecode = sys.dont_write_bytecode
    try:
        # Loading the verifier must not create a nonignored ``__pycache__``
        # entry and thereby mutate the very source inventory being measured.
        # Register the temporary module because dataclasses and similar
        # import-time helpers resolve their defining namespace through
        # ``sys.modules``.
        sys.modules[module_name] = module
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ProductionReadinessError(
            "cannot load source-content identity verifier"
        ) from exc
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
        if previous_module is sentinel:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
    return module


def _readiness_source_binding(root: Path) -> dict[str, Any]:
    """Return canonical source content plus the current diagnostic revision."""
    root = root.resolve()
    provenance = _load_proof_provenance(root)
    try:
        recorded = provenance.repository_provenance(
            root,
            exclude_paths=READINESS_SOURCE_EXCLUSIONS,
        )
        if recorded.get("available") is not True:
            raise ValueError("readiness source is not a Git worktree")
        revision = recorded.get("revision")
        if not isinstance(revision, str) or _COMMIT_RE.fullmatch(revision) is None:
            raise ValueError("readiness source revision is malformed")
        dirty = recorded.get("dirty")
        if not isinstance(dirty, bool):
            raise ValueError("readiness source worktree state is malformed")
        identity = provenance.source_content_identity(recorded)
        return {"revision": revision, "dirty": dirty, "source_content": identity}
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProductionReadinessError(
            "cannot calculate canonical readiness source binding"
        ) from exc


def readiness_source_content_identity(root: Path) -> dict[str, Any]:
    """Return the canonical source identity bound by a ready packet."""
    return _readiness_source_binding(root)["source_content"]


def _validated_recorded_source_content(
    root: Path,
    value: object,
) -> dict[str, Any] | None:
    if value is None:
        return None
    provenance = _load_proof_provenance(root)
    try:
        return provenance.source_content_identity({"source_content": value})
    except ValueError as exc:
        raise ProductionReadinessError(
            "assessed source-content identity is malformed"
        ) from exc


def _readiness_source_excludes(relative: str) -> bool:
    return any(
        relative == excluded or relative.startswith(f"{excluded}/")
        for excluded in READINESS_SOURCE_EXCLUSIONS
    )


def _normalized_bounded_utf8(path: Path, *, label: str) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProductionReadinessError(f"{label} cannot be read") from exc
    if len(raw) > MAX_READINESS_JSON_BYTES:
        raise ProductionReadinessError(f"{label} exceeds the bounded size")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProductionReadinessError(f"{label} is not valid UTF-8") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _require_canonical_ready_path(
    root: Path,
    supplied: Path,
    relative: str,
    *,
    label: str,
) -> str:
    canonical_relative = _repo_file(root, relative, label=label)
    canonical = Path(os.path.abspath(root / canonical_relative))
    observed = Path(os.path.abspath(supplied))
    if observed != canonical:
        raise ProductionReadinessError(f"ready evidence must use the canonical {label}")
    return canonical_relative


def _run_trusted_git(
    root: Path,
    args: list[str],
    *,
    label: str,
    accepted_returncodes: frozenset[int] = frozenset({0}),
) -> subprocess.CompletedProcess[bytes]:
    provenance = _load_proof_provenance(root)
    try:
        return provenance.trusted_git_completed(
            root,
            args,
            accepted_returncodes=tuple(sorted(accepted_returncodes)),
            max_stdout_bytes=MAX_READINESS_JSON_BYTES,
            max_stderr_bytes=MAX_READINESS_JSON_BYTES,
            timeout_seconds=30.0,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProductionReadinessError(f"cannot {label}") from exc


def _validate_assessed_revision(
    root: Path,
    assessed: str,
    *,
    current_revision: str,
    current_dirty: bool,
) -> None:
    if current_dirty:
        raise ProductionReadinessError(
            "ready source content must be committed outside generated readiness exclusions"
        )
    _run_trusted_git(
        root,
        ["cat-file", "-e", f"{assessed}^{{commit}}"],
        label="resolve assessed source revision",
    )
    ancestry = _run_trusted_git(
        root,
        ["merge-base", "--is-ancestor", assessed, current_revision],
        label="verify assessed source ancestry",
        accepted_returncodes=frozenset({0, 1}),
    )
    if ancestry.returncode != 0:
        raise ProductionReadinessError(
            "assessed source revision is not an ancestor of the current revision"
        )
    exclusions = [
        f":(top,exclude){relative}/**" if relative == "proof/results" else f":(top,exclude){relative}"
        for relative in READINESS_SOURCE_EXCLUSIONS
    ]
    for left, right, label in (
        (
            assessed,
            current_revision,
            "assessed revision differs from the current revision outside readiness exclusions",
        ),
        (
            current_revision,
            None,
            "current worktree differs from its revision outside readiness exclusions",
        ),
    ):
        revision_range = [left] if right is None else [left, right]
        difference = _run_trusted_git(
            root,
            [
                "diff",
                "--quiet",
                "--no-ext-diff",
                *revision_range,
                "--",
                ":(top)**",
                *exclusions,
            ],
            label="verify assessed revision source content",
            accepted_returncodes=frozenset({0, 1}),
        )
        if difference.returncode != 0:
            raise ProductionReadinessError(label)


def _require_head_committed_path(root: Path, relative: str, *, label: str) -> None:
    for cached in (True, False):
        arguments = [
            "--literal-pathspecs",
            "diff",
            "--quiet",
            "--no-ext-diff",
        ]
        if cached:
            arguments.append("--cached")
        arguments.extend(["HEAD", "--", relative])
        difference = _run_trusted_git(
            root,
            arguments,
            label=f"verify committed {label}",
            accepted_returncodes=frozenset({0, 1}),
        )
        if difference.returncode != 0:
            raise ProductionReadinessError(
                f"ready {label} must exactly match its committed HEAD bytes"
            )


def _require_tracked_regular_evidence(root: Path, relatives: set[str]) -> None:
    if not relatives:
        return
    completed = _run_trusted_git(
        root,
        [
            "--literal-pathspecs",
            "ls-files",
            "--stage",
            "-z",
            "--",
            *sorted(relatives),
        ],
        label="verify tracked ready evidence paths",
    )
    observed: set[str] = set()
    try:
        records = completed.stdout.rstrip(b"\0").split(b"\0") if completed.stdout else []
        for raw in records:
            header, encoded_path = raw.split(b"\t", 1)
            mode, _oid, stage = header.split(b" ")
            relative = encoded_path.decode("utf-8", errors="strict")
            if mode not in {b"100644", b"100755"} or stage != b"0" or relative in observed:
                raise ValueError("unsupported evidence index entry")
            observed.add(relative)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProductionReadinessError(
            "tracked ready evidence inventory is malformed"
        ) from exc
    if observed != relatives:
        missing = sorted(relatives - observed)
        raise ProductionReadinessError(
            f"ready evidence paths are not all tracked regular files: {missing}"
        )


def _validate_evidence_sets(
    root: Path,
    value: object,
) -> dict[str, Mapping[str, Any]]:
    sets = _mapping(value, label="readiness evidence_sets")
    validated: dict[str, Mapping[str, Any]] = {}
    for name, raw in sets.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", name):
            raise ProductionReadinessError("readiness evidence-set name is invalid")
        item = _exact(
            raw,
            {"implementation", "regressions", "adverse_result", "runbook"},
            label=f"readiness evidence set {name}",
        )
        normalized_item = dict(item)
        for field in ("implementation", "regressions"):
            paths = item[field]
            if not isinstance(paths, list) or not paths:
                raise ProductionReadinessError(
                    f"readiness evidence set {name} {field} must be non-empty"
                )
            normalized = [
                _repo_file(root, path, label=f"readiness evidence set {name} {field}")
                for path in paths
            ]
            if len(normalized) != len(set(normalized)):
                raise ProductionReadinessError(
                    f"readiness evidence set {name} {field} contains duplicates"
                )
            normalized_item[field] = normalized
        _text(item["adverse_result"], label=f"readiness evidence set {name} result")
        runbook = _repo_file(
            root,
            item["runbook"],
            label=f"readiness evidence set {name} runbook",
        )
        normalized_item["runbook"] = runbook
        validated[name] = normalized_item
    return validated


def _reject_source_excluded_evidence_refs(
    evidence_sets: Mapping[str, Mapping[str, Any]],
) -> None:
    for name, item in evidence_sets.items():
        for field in ("implementation", "regressions"):
            if any(_readiness_source_excludes(path) for path in item[field]):
                raise ProductionReadinessError(
                    f"ready evidence set {name} {field} cites a source-excluded artifact"
                )
        if _readiness_source_excludes(item["runbook"]):
            raise ProductionReadinessError(
                f"ready evidence set {name} runbook cites a source-excluded artifact"
            )


def _ready_evidence_paths(
    evidence_sets: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    paths = {
        "release/production-readiness-evidence.json",
        TRUST_PATH,
        "release/production-surface-manifest.json",
    }
    for item in evidence_sets.values():
        paths.update(item["implementation"])
        paths.update(item["regressions"])
        paths.add(item["runbook"])
    return paths


def validate_production_readiness(  # noqa: C901 - exact schema/cross-field gate
    root: Path,
    *,
    evidence_path: Path,
    production_surface_path: Path,
    require_ready: bool = False,
) -> dict[str, Any]:
    """Validate one evidence contract and return a bounded summary."""
    root = root.resolve()
    evidence = _exact(
        _read_json(evidence_path, label="production readiness evidence"),
        {
            "schema",
            "profile_id",
            "source_version",
            "production_surface_sha256",
            "production_surface_digest_mode",
            "assessed_source_content",
            "assessed_source_revision",
            "status",
            "updated_at",
            "approval",
            "evidence_sets",
            "gates",
        },
        label="production readiness evidence",
    )
    if evidence["schema"] != SCHEMA:
        raise ProductionReadinessError("production readiness schema is unsupported")
    profile_id = _text(evidence["profile_id"], label="readiness profile_id")
    source_version = _text(evidence["source_version"], label="readiness source_version")
    status = evidence["status"]
    if status not in {"draft", "ready"}:
        raise ProductionReadinessError("production readiness status is invalid")
    if not isinstance(evidence["updated_at"], str) or _UTC_RE.fullmatch(evidence["updated_at"]) is None:
        raise ProductionReadinessError("production readiness updated_at is invalid")

    canonical_surface_relative = _repo_file(
        root,
        "release/production-surface-manifest.json",
        label="canonical production surface manifest",
    )
    canonical_surface_path = root / canonical_surface_relative

    if evidence["production_surface_digest_mode"] != PRODUCTION_SURFACE_DIGEST_MODE:
        raise ProductionReadinessError(
            "production surface evidence digest mode is unsupported"
        )
    surface_digest = production_surface_sha256(production_surface_path)
    if not _SHA256_RE.fullmatch(str(evidence["production_surface_sha256"])):
        raise ProductionReadinessError("production surface evidence digest is invalid")
    if evidence["production_surface_sha256"] != surface_digest:
        raise ProductionReadinessError(
            "production readiness evidence targets different normalized surface content"
        )
    surface = _read_json(production_surface_path, label="production surface manifest")
    if surface.get("schema") != PRODUCTION_SCHEMA:
        raise ProductionReadinessError("production surface schema is unsupported")
    if surface.get("profile_id") != profile_id or surface.get("source_version") != source_version:
        raise ProductionReadinessError("readiness identity differs from production surface")
    release_state = _mapping(surface.get("release_state"), label="production release state")
    release_eligible = release_state.get("eligible")
    if not isinstance(release_eligible, bool):
        raise ProductionReadinessError("production release eligibility is malformed")
    if (status == "ready") != release_eligible:
        raise ProductionReadinessError(
            "readiness status and production release eligibility disagree"
        )
    if require_ready and (status != "ready" or release_eligible is not True):
        raise ProductionReadinessError(
            "--require-ready requires ready status and production release eligibility"
        )

    assessed = evidence["assessed_source_revision"]
    if assessed is not None and (
        not isinstance(assessed, str) or _COMMIT_RE.fullmatch(assessed) is None
    ):
        raise ProductionReadinessError("assessed source revision is invalid")
    assessed_content = _validated_recorded_source_content(
        root,
        evidence["assessed_source_content"],
    )

    evidence_sets = _validate_evidence_sets(root, evidence["evidence_sets"])
    raw_gates = evidence["gates"]
    if not isinstance(raw_gates, list):
        raise ProductionReadinessError("production readiness gates must be an array")
    dispositions: dict[str, str] = {}
    for index, raw in enumerate(raw_gates):
        gate = _exact(
            raw,
            {"id", "priority", "disposition", "owner", "rationale", "evidence_refs"},
            label=f"production readiness gate {index}",
        )
        gate_id = _text(gate["id"], label=f"production readiness gate {index} id")
        if gate_id not in ALL_GATES or gate_id in dispositions:
            raise ProductionReadinessError(f"unknown or duplicate production gate: {gate_id}")
        if gate["priority"] != gate_priority(gate_id):
            raise ProductionReadinessError(f"production gate priority drifted: {gate_id}")
        disposition = gate["disposition"]
        if disposition not in _DISPOSITIONS:
            raise ProductionReadinessError(f"production gate disposition is invalid: {gate_id}")
        if disposition == "excluded" and gate_id not in CONDITIONAL_GATES:
            raise ProductionReadinessError(
                f"non-conditional production gate cannot be excluded: {gate_id}"
            )
        if gate_id == "REL-17" and disposition == "satisfied" and (
            status != "ready" or assessed_content is None or not release_eligible
        ):
            raise ProductionReadinessError(
                "REL-17 requires a source-content-bound, release-eligible evidence packet"
            )
        _text(gate["owner"], label=f"production gate {gate_id} owner")
        _text(gate["rationale"], label=f"production gate {gate_id} rationale")
        refs = gate["evidence_refs"]
        if (
            not isinstance(refs, list)
            or not refs
            or any(
                not isinstance(ref, str) or ref not in evidence_sets
                for ref in refs
            )
        ):
            raise ProductionReadinessError(f"production gate evidence is invalid: {gate_id}")
        if len(refs) != len(set(refs)):
            raise ProductionReadinessError(f"production gate evidence is duplicated: {gate_id}")
        dispositions[gate_id] = disposition

    required = P0_GATES | P1_GATES
    missing = sorted(required - set(dispositions))
    if missing:
        raise ProductionReadinessError(
            f"production readiness evidence omits required gates: {missing}"
    )
    if status == "ready":
        _reject_source_excluded_evidence_refs(evidence_sets)
        _require_canonical_ready_path(
            root,
            evidence_path,
            "release/production-readiness-evidence.json",
            label="production readiness evidence packet",
        )
        _require_canonical_ready_path(
            root,
            production_surface_path,
            "release/production-surface-manifest.json",
            label="production surface manifest",
        )
        if _normalized_bounded_utf8(
            production_surface_path,
            label="supplied production surface manifest",
        ) != _normalized_bounded_utf8(
            canonical_surface_path,
            label="canonical production surface manifest",
        ):
            raise ProductionReadinessError(
                "ready evidence must target the canonical repository production surface"
            )
        _require_tracked_regular_evidence(root, _ready_evidence_paths(evidence_sets))
        _require_head_committed_path(
            root,
            "release/production-readiness-evidence.json",
            label="production readiness evidence packet",
        )
    unresolved = sorted(
        gate_id
        for gate_id in required
        if dispositions[gate_id] not in _READY_DISPOSITIONS
    )
    if require_ready or status == "ready":
        if unresolved:
            raise ProductionReadinessError(
                f"production readiness evidence has unresolved gates: {unresolved}"
            )
        if assessed_content is None:
            raise ProductionReadinessError(
                "ready production evidence must bind canonical source content"
            )
        if assessed is None:
            raise ProductionReadinessError(
                "ready production evidence must record the assessed source revision"
            )
        current_binding = _readiness_source_binding(root)
        current_content = current_binding["source_content"]
        if assessed_content != current_content:
            raise ProductionReadinessError(
                "production evidence does not assess the current canonical source content"
            )
        _validate_assessed_revision(
            root,
            assessed,
            current_revision=current_binding["revision"],
            current_dirty=current_binding["dirty"],
        )
    semantic_digest = _validate_reviewed_semantics(root, evidence, status=status)
    return {
        "schema": SCHEMA,
        "profile_id": profile_id,
        "source_version": source_version,
        "status": status,
        "assessed_source_content_sha256": (
            assessed_content["sha256"] if assessed_content is not None else None
        ),
        "assessed_source_revision": assessed,
        "recorded_gates": len(dispositions),
        "required_gates": len(required),
        "unresolved_required_gates": len(unresolved),
        "release_eligible": release_eligible,
        "reviewed_semantic_sha256": semantic_digest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path("release/production-readiness-evidence.json"),
    )
    parser.add_argument(
        "--production-surface",
        type=Path,
        default=Path("release/production-surface-manifest.json"),
    )
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument(
        "--print-semantic-digest",
        action="store_true",
        help=(
            "print the candidate semantic digest without treating calculation "
            "as review or approval"
        ),
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    evidence = args.evidence if args.evidence.is_absolute() else root / args.evidence
    surface = (
        args.production_surface
        if args.production_surface.is_absolute()
        else root / args.production_surface
    )
    try:
        if args.print_semantic_digest:
            candidate = _read_json(evidence, label="production readiness evidence")
            status = candidate.get("status")
            if status not in {"draft", "ready"}:
                raise ProductionReadinessError(
                    "production readiness status is invalid"
                )
            calculated = readiness_semantic_sha256(candidate)
            reviewed = REVIEWED_SEMANTIC_SHA256_BY_STATUS.get(str(status))
            print(
                json.dumps(
                    {
                        "calculated_sha256": calculated,
                        "matches_reviewed": reviewed is not None
                        and calculated == reviewed,
                        "reviewed_sha256": reviewed,
                        "signed_approval_required": status == "ready",
                        "status": status,
                    },
                    sort_keys=True,
                )
            )
            return 0
        summary = validate_production_readiness(
            root,
            evidence_path=evidence,
            production_surface_path=surface,
            require_ready=args.require_ready,
        )
    except (OSError, ProductionReadinessError) as exc:
        print(f"production readiness verification failed: {exc}", file=__import__("sys").stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
