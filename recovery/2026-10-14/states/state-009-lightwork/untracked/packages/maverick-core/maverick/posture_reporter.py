"""Pytest plugin that emits exact posture-failure identities as strict JSON.

Pytest's human short-summary format is intentionally readable, not an
authority format: parameter ids and failure reasons can both contain the same
``" - "`` delimiter. This plugin records ``TestReport.nodeid`` directly so
the production-posture ratchet never has to reconstruct authority from prose.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import tempfile
from pathlib import Path
from typing import Any

import pytest

SCHEMA = "lightwork.posture-failures.v2"
_FAILURES: list[tuple[str, str, bool]] = []
_COLLECTED_NODEIDS: list[str] = []
_FINISHED_NODEIDS: list[str] = []
_DESELECTED_COUNT = 0
_SESSION_SCOPE: dict[str, object] | None = None


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "<missing>"


def _constraints_digest() -> str:
    constraints = Path(__file__).resolve().parents[3] / "requirements" / "ci.txt"
    try:
        contents = constraints.read_bytes()
    except OSError:
        return "<missing>"
    # Git may materialize this text file with CRLF on Windows. Bind the report
    # to the repository content, not to checkout-specific line endings.
    canonical = contents.replace(b"\r\n", b"\n")
    return hashlib.sha256(canonical).hexdigest()


_RUNTIME_PROVENANCE = {
    "platform_system": platform.system(),
    "python_implementation": platform.python_implementation(),
    "python_version": platform.python_version(),
    "pytest_version": _distribution_version("pytest"),
    "pytest_asyncio_version": _distribution_version("pytest-asyncio"),
    "pytest_timeout_version": _distribution_version("pytest-timeout"),
    "pyyaml_version": _distribution_version("PyYAML"),
    "fastapi_version": _distribution_version("fastapi"),
    "starlette_version": _distribution_version("starlette"),
    "constraints_sha256": _constraints_digest(),
}


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("maverick-posture")
    group.addoption(
        "--posture-node-report",
        action="store",
        default=None,
        dest="posture_node_report",
        help="write exact failed/error node ids to a strict JSON report",
    )


def pytest_sessionstart(session: Any) -> None:
    global _DESELECTED_COUNT, _SESSION_SCOPE
    _FAILURES.clear()
    _COLLECTED_NODEIDS.clear()
    _FINISHED_NODEIDS.clear()
    _DESELECTED_COUNT = 0
    _SESSION_SCOPE = {
        "collection_args": [str(value) for value in session.config.args],
        "secure_default": (
            os.environ.get("MAVERICK_TEST_SECURE_DEFAULT", "").strip().lower()
            in {"1", "true", "yes", "on"}
        ),
        "import_mode": str(session.config.getoption("importmode")),
        **_RUNTIME_PROVENANCE,
    }


def pytest_deselected(items: list[Any]) -> None:
    global _DESELECTED_COUNT
    _DESELECTED_COUNT += len(items)


def pytest_collection_finish(session: Any) -> None:
    nodeids = [getattr(item, "nodeid", None) for item in session.items]
    if any(not isinstance(nodeid, str) or not nodeid for nodeid in nodeids):
        raise RuntimeError("collected pytest item omitted its exact nodeid")
    if len(nodeids) != len(set(nodeids)):
        raise RuntimeError("pytest collection contains duplicate nodeids")
    _COLLECTED_NODEIDS.extend(nodeids)


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    del location
    if not isinstance(nodeid, str) or not nodeid:
        raise RuntimeError("finished pytest item omitted its exact nodeid")
    _FINISHED_NODEIDS.append(nodeid)


def _record(report: Any, phase: str) -> None:
    if not bool(getattr(report, "failed", False)):
        return
    nodeid = getattr(report, "nodeid", None)
    if not isinstance(nodeid, str) or not nodeid:
        raise RuntimeError("failed pytest report omitted its exact nodeid")
    timed_out = "from pytest-timeout." in str(
        getattr(report, "longreprtext", "")
    )
    _FAILURES.append((nodeid, phase, timed_out))


def pytest_runtest_logreport(report: Any) -> None:
    phase = getattr(report, "when", None)
    if phase not in {"setup", "call", "teardown"}:
        raise RuntimeError("pytest runtime report has an unsupported phase")
    _record(report, phase)


def pytest_collectreport(report: Any) -> None:
    _record(report, "collect")


def _document(
    *,
    exit_status: int,
    collection_args: list[str],
    secure_default: bool,
    import_mode: str,
    platform_system: str,
    python_implementation: str,
    python_version: str,
    pytest_version: str,
    pytest_asyncio_version: str,
    pytest_timeout_version: str,
    pyyaml_version: str,
    fastapi_version: str,
    starlette_version: str,
    constraints_sha256: str,
    collected_nodeids: list[str],
    finished_nodeids: list[str],
    deselected_count: int,
    rows: list[tuple[str, str, bool]],
) -> dict[str, object]:
    ordered = sorted(rows)
    identities = {nodeid for nodeid, _phase, _timed_out in ordered}
    ordered_collection = sorted(collected_nodeids)
    ordered_finished = sorted(finished_nodeids)
    collection_bytes = json.dumps(
        ordered_collection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    finished_bytes = json.dumps(
        ordered_finished,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return {
        "schema": SCHEMA,
        "pytest_exit_status": exit_status,
        "scope": {
            "collection_args": collection_args,
            "secure_default": secure_default,
            "import_mode": import_mode,
            "platform_system": platform_system,
            "python_implementation": python_implementation,
            "python_version": python_version,
            "pytest_version": pytest_version,
            "pytest_asyncio_version": pytest_asyncio_version,
            "pytest_timeout_version": pytest_timeout_version,
            "pyyaml_version": pyyaml_version,
            "fastapi_version": fastapi_version,
            "starlette_version": starlette_version,
            "constraints_sha256": constraints_sha256,
        },
        "collection": {
            "item_count": len(ordered_collection),
            "deselected_count": deselected_count,
            "nodeids_sha256": hashlib.sha256(collection_bytes).hexdigest(),
            "finished_item_count": len(ordered_finished),
            "finished_nodeids_sha256": hashlib.sha256(finished_bytes).hexdigest(),
        },
        "failure_occurrence_count": len(ordered),
        "failure_identity_count": len(identities),
        "failures": [
            {"nodeid": nodeid, "phase": phase, "timed_out": timed_out}
            for nodeid, phase, timed_out in ordered
        ],
    }


def _write_report(
    path: Path,
    *,
    exit_status: int,
    collection_args: list[str],
    secure_default: bool,
    import_mode: str,
    platform_system: str,
    python_implementation: str,
    python_version: str,
    pytest_version: str,
    pytest_asyncio_version: str,
    pytest_timeout_version: str,
    pyyaml_version: str,
    fastapi_version: str,
    starlette_version: str,
    constraints_sha256: str,
    collected_nodeids: list[str],
    finished_nodeids: list[str],
    deselected_count: int,
    rows: list[tuple[str, str, bool]],
) -> None:
    document = _document(
        exit_status=exit_status,
        collection_args=collection_args,
        secure_default=secure_default,
        import_mode=import_mode,
        platform_system=platform_system,
        python_implementation=python_implementation,
        python_version=python_version,
        pytest_version=pytest_version,
        pytest_asyncio_version=pytest_asyncio_version,
        pytest_timeout_version=pytest_timeout_version,
        pyyaml_version=pyyaml_version,
        fastapi_version=fastapi_version,
        starlette_version=starlette_version,
        constraints_sha256=constraints_sha256,
        collected_nodeids=collected_nodeids,
        finished_nodeids=finished_nodeids,
        deselected_count=deselected_count,
        rows=rows,
    )
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                document,
                stream,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_sessionfinish(session: Any, exitstatus: Any):
    """Publish only after every inner session-finish hook returned cleanly."""
    yield
    if int(exitstatus) != int(session.exitstatus):
        raise RuntimeError("a pytest session-finish hook changed the process status")
    destination = session.config.getoption("posture_node_report", default=None)
    if destination is None:
        return
    if not isinstance(destination, str) or not destination.strip():
        raise RuntimeError("posture node report path is empty")
    if session.testscollected != len(_COLLECTED_NODEIDS):
        raise RuntimeError(
            "pytest's collected-item count differs from the exact nodeid inventory"
        )
    if _SESSION_SCOPE is None:
        raise RuntimeError("posture reporter did not capture session provenance")
    _write_report(
        Path(destination),
        exit_status=int(session.exitstatus),
        collection_args=_SESSION_SCOPE["collection_args"],
        secure_default=_SESSION_SCOPE["secure_default"],
        import_mode=_SESSION_SCOPE["import_mode"],
        platform_system=_SESSION_SCOPE["platform_system"],
        python_implementation=_SESSION_SCOPE["python_implementation"],
        python_version=_SESSION_SCOPE["python_version"],
        pytest_version=_SESSION_SCOPE["pytest_version"],
        pytest_asyncio_version=_SESSION_SCOPE["pytest_asyncio_version"],
        pytest_timeout_version=_SESSION_SCOPE["pytest_timeout_version"],
        pyyaml_version=_SESSION_SCOPE["pyyaml_version"],
        fastapi_version=_SESSION_SCOPE["fastapi_version"],
        starlette_version=_SESSION_SCOPE["starlette_version"],
        constraints_sha256=_SESSION_SCOPE["constraints_sha256"],
        collected_nodeids=list(_COLLECTED_NODEIDS),
        finished_nodeids=list(_FINISHED_NODEIDS),
        deselected_count=_DESELECTED_COUNT,
        rows=list(_FAILURES),
    )
