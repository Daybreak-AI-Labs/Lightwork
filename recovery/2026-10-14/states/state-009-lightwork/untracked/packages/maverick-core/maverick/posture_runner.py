"""Run the secure-posture suite through a fully observed pytest lifecycle.

Pytest's console entry point can turn an exception raised during late plugin
cleanup into process status 1, which is also the expected status for ordinary
test failures. The posture gate deliberately permits status 1 while reviewed
failure debt exists, so that ambiguity must be removed outside pytest.

This launcher runs pytest in an isolated worker, maps any exception escaping
``pytest.main`` to reserved infrastructure status 3, and transports ordinary
pytest results as statuses 10/11 so a late interpreter failure cannot alias
pytest status 1. The parent similarly transports decoded results to its caller
as statuses 20/21. Worker- and parent-owned unraisable/thread exception hooks
turn late ignored failures into infrastructure status 3. The parent waits for
the worker to exit completely and only then writes a hash-bound completion
record. The gate requires that record in every authority mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    # Declare the launcher-owned dynamic pytest-plugin edge for reachability.
    from . import posture_reporter  # noqa: F401

COMPLETION_SCHEMA = "lightwork.posture-run-completion.v1"
MAX_REPORT_BYTES = 4 * 1024 * 1024
INFRASTRUCTURE_EXIT_STATUS = 3
WORKER_SUCCESS_STATUS = 10
WORKER_TEST_FAILURE_STATUS = 11
PARENT_SUCCESS_STATUS = 20
PARENT_TEST_FAILURE_STATUS = 21


def _fatal_unraisable(unraisable: Any) -> None:
    sys.__unraisablehook__(unraisable)
    os._exit(INFRASTRUCTURE_EXIT_STATUS)


def _fatal_thread_exception(args: threading.ExceptHookArgs) -> None:
    threading.__excepthook__(args)
    os._exit(INFRASTRUCTURE_EXIT_STATUS)


def _install_fatal_process_hooks() -> None:
    sys.unraisablehook = _fatal_unraisable
    threading.excepthook = _fatal_thread_exception


def _atomic_json(path: Path, document: dict[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
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


def _write_completion(
    report: Path, completion: Path, *, pytest_exit_status: int
) -> None:
    try:
        report_bytes = report.read_bytes()
    except OSError as exc:
        raise ValueError("the posture reporter did not publish its JSON artifact") from exc
    if not report_bytes or len(report_bytes) > MAX_REPORT_BYTES:
        raise ValueError("the posture reporter artifact has an invalid bounded size")
    _atomic_json(
        completion,
        {
            "schema": COMPLETION_SCHEMA,
            "pytest_exit_status": pytest_exit_status,
            "posture_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        },
    )


def _pytest_worker(pytest_args: list[str]) -> int:
    try:
        pytest_status = int(pytest.main(pytest_args))
    except BaseException:  # pytest teardown can raise any plugin exception.
        print(
            "posture-runner: pytest infrastructure failed after or outside the "
            "normal test lifecycle",
            file=sys.stderr,
        )
        traceback.print_exc()
        return INFRASTRUCTURE_EXIT_STATUS
    if pytest_status == 0:
        return WORKER_SUCCESS_STATUS
    if pytest_status == 1:
        return WORKER_TEST_FAILURE_STATUS
    return pytest_status if 1 < pytest_status <= 255 else INFRASTRUCTURE_EXIT_STATUS


def _contains_reserved_reporter_option(args: list[str]) -> bool:
    for index, value in enumerate(args):
        if value == "--posture-node-report" or value.startswith(
            "--posture-node-report="
        ):
            return True
        if "maverick.posture_reporter" in value:
            return True
        if value == "-p" and index + 1 < len(args):
            if "maverick.posture_reporter" in args[index + 1]:
                return True
    return False


def _parent(report: Path, completion: Path, pytest_args: list[str]) -> int:
    report = report.resolve()
    completion = completion.resolve()
    if report == completion:
        print("posture-runner: report and completion paths must differ", file=sys.stderr)
        return INFRASTRUCTURE_EXIT_STATUS
    if not pytest_args:
        print("posture-runner: no pytest collection scope was supplied", file=sys.stderr)
        return INFRASTRUCTURE_EXIT_STATUS
    if _contains_reserved_reporter_option(pytest_args):
        print(
            "posture-runner: reporter options are launcher-owned",
            file=sys.stderr,
        )
        return INFRASTRUCTURE_EXIT_STATUS

    try:
        report.unlink(missing_ok=True)
        completion.unlink(missing_ok=True)
    except OSError as exc:
        print(f"posture-runner: cannot clear stale evidence: {exc}", file=sys.stderr)
        return INFRASTRUCTURE_EXIT_STATUS

    command = [
        sys.executable,
        "-m",
        "maverick.posture_runner",
        "--worker",
        "--",
        *pytest_args,
        "-p",
        "maverick.posture_reporter",
        "--posture-node-report",
        str(report),
    ]
    try:
        result = subprocess.run(command, check=False)
    except OSError as exc:
        print(f"posture-runner: cannot start pytest worker: {exc}", file=sys.stderr)
        return INFRASTRUCTURE_EXIT_STATUS

    transport_status = result.returncode
    decoded_status = {
        WORKER_SUCCESS_STATUS: 0,
        WORKER_TEST_FAILURE_STATUS: 1,
    }.get(transport_status)
    if decoded_status is None:
        return (
            transport_status
            if 1 < transport_status <= 255
            else INFRASTRUCTURE_EXIT_STATUS
        )
    try:
        _write_completion(report, completion, pytest_exit_status=decoded_status)
    except (OSError, ValueError) as exc:
        print(f"posture-runner: cannot publish completion evidence: {exc}", file=sys.stderr)
        return INFRASTRUCTURE_EXIT_STATUS
    return (
        PARENT_SUCCESS_STATUS
        if decoded_status == 0
        else PARENT_TEST_FAILURE_STATUS
    )


def main(argv: list[str] | None = None) -> int:
    # The parent is also authority-bearing: a failure after completion is
    # written but before its process exits must not alias ordinary pytest 1.
    _install_fatal_process_hooks()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--completion-report", type=Path)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args.pop(0)

    if args.worker:
        if args.report is not None or args.completion_report is not None:
            parser.error("the internal worker does not accept output paths")
        return _pytest_worker(pytest_args)
    if args.report is None or args.completion_report is None:
        parser.error("--report and --completion-report are required")
    return _parent(args.report, args.completion_report, pytest_args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
