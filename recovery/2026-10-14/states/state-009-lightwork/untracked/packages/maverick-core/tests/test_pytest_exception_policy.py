from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from scripts.verify_pytest_exceptions import (
    ALLOWED_ENVIRONMENT_PREFIXES,
    MAX_TEMPORARY_DAYS,
    PRODUCTION_CRITICAL_GLOBS,
    PytestExceptionPolicyError,
    _is_production_critical,
    scan_repository_occurrences,
    validate_pytest_exception_policy,
)

TODAY = dt.date(2026, 8, 9)


def _write_source(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _entry(root: Path, relative: str, **updates: object) -> dict[str, object]:
    occurrence = scan_repository_occurrences(root, paths=[relative])[0]
    entry: dict[str, object] = {
        "id": "QA03-0001",
        "path": occurrence.path,
        "line": occurrence.line,
        "column": occurrence.column,
        "kind": occurrence.kind,
        "source_sha256": occurrence.source_sha256,
        "reason": occurrence.reason,
        "owner": "@cdayAI",
        "required_environment": "platform:posix",
        "production_critical": _is_production_critical(occurrence.path),
        "expires_on": None,
        "permanent_rationale": (
            "This test exercises operating-system-specific behavior that does not exist "
            "on other supported platform families."
        ),
        "enforced_by": None,
    }
    entry.update(updates)
    return entry


def _write_inventory(root: Path, entry: dict[str, object]) -> PurePosixPath:
    return _write_inventory_entries(root, [entry])


def _write_inventory_entries(
    root: Path,
    entries: list[dict[str, object]],
) -> PurePosixPath:
    relative = PurePosixPath("qa/pytest-exceptions.json")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "lightwork.pytest-exceptions.v1",
                "reviewed_on": TODAY.isoformat(),
                "policy": {
                    "max_temporary_days": MAX_TEMPORARY_DAYS,
                    "production_critical_globs": list(PRODUCTION_CRITICAL_GLOBS),
                    "allowed_environment_prefixes": list(ALLOWED_ENVIRONMENT_PREFIXES),
                },
                "exceptions": entries,
            }
        ),
        encoding="utf-8",
    )
    return relative


def test_scanner_covers_aliases_markers_importorskip_and_ignores_strings(tmp_path: Path):
    relative = "tests/test_sample.py"
    _write_source(
        tmp_path,
        relative,
        """
import pytest as pt
from pytest import mark as pm

TEXT = 'pytest.skip("not executable")'
optional_import = pt.importorskip
optional_import("example_extra")

@pm.skipif(True, reason="platform branch")
def test_platform():
    pass

@pt.mark.xfail(reason="temporary gap", strict=True)
def test_gap():
    pass
""",
    )

    occurrences = scan_repository_occurrences(tmp_path, paths=[relative])

    assert [item.kind for item in occurrences] == [
        "importorskip",
        "mark.skipif",
        "mark.xfail",
    ]
    assert occurrences[-1].strict is True


def test_repository_scan_includes_nonignored_untracked_python(tmp_path: Path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _write_source(tmp_path, "tests/test_tracked.py", "import pytest\npytest.skip('tracked')\n")
    _write_source(tmp_path, "tests/test_new.py", "import pytest\npytest.skip('new')\n")
    _write_source(tmp_path, "scratch/test_ignored.py", "import pytest\npytest.skip('ignored')\n")
    (tmp_path / ".gitignore").write_text("scratch/\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", ".gitignore", "tests/test_tracked.py"],
        check=True,
    )

    occurrences = scan_repository_occurrences(tmp_path)

    assert [(item.path, item.reason) for item in occurrences] == [
        ("tests/test_new.py", "new"),
        ("tests/test_tracked.py", "tracked"),
    ]


def test_valid_inventory_returns_machine_summary(tmp_path: Path):
    relative = "tests/test_sample.py"
    _write_source(
        tmp_path,
        relative,
        "import pytest\npytest.skip('POSIX-only branch')\n",
    )
    inventory = _write_inventory(tmp_path, _entry(tmp_path, relative))

    summary = validate_pytest_exception_policy(
        tmp_path,
        inventory_path=inventory,
        paths=[relative],
        as_of=TODAY,
    )

    assert summary["occurrence_count"] == 1
    assert summary["strict_xfail_count"] == 0


def test_inventory_ids_are_stable_across_source_order(tmp_path: Path):
    first = "tests/test_a.py"
    second = "tests/test_z.py"
    _write_source(tmp_path, first, "import pytest\npytest.skip('first')\n")
    _write_source(tmp_path, second, "import pytest\npytest.skip('second')\n")
    second_entry = _entry(tmp_path, second)
    first_entry = _entry(tmp_path, first)
    second_entry["id"] = "QA03-0001"
    first_entry["id"] = "QA03-0002"
    inventory = _write_inventory_entries(tmp_path, [second_entry, first_entry])

    summary = validate_pytest_exception_policy(
        tmp_path,
        inventory_path=inventory,
        paths=[first, second],
        as_of=TODAY,
    )

    assert summary["occurrence_count"] == 2


def test_duplicate_inventory_location_fails_closed(tmp_path: Path):
    first = "tests/test_a.py"
    second = "tests/test_z.py"
    _write_source(tmp_path, first, "import pytest\npytest.skip('first')\n")
    _write_source(tmp_path, second, "import pytest\npytest.skip('second')\n")
    first_entry = _entry(tmp_path, first)
    duplicate_entry = dict(first_entry)
    duplicate_entry["id"] = "QA03-0002"
    inventory = _write_inventory_entries(tmp_path, [first_entry, duplicate_entry])

    with pytest.raises(PytestExceptionPolicyError, match="locations are duplicated"):
        validate_pytest_exception_policy(
            tmp_path,
            inventory_path=inventory,
            paths=[first, second],
            as_of=TODAY,
        )


def test_inventory_ids_remain_ordered_and_contiguous(tmp_path: Path):
    first = "tests/test_a.py"
    second = "tests/test_z.py"
    _write_source(tmp_path, first, "import pytest\npytest.skip('first')\n")
    _write_source(tmp_path, second, "import pytest\npytest.skip('second')\n")
    first_entry = _entry(tmp_path, first)
    second_entry = _entry(tmp_path, second)
    first_entry["id"] = "QA03-0002"
    second_entry["id"] = "QA03-0001"
    inventory = _write_inventory_entries(tmp_path, [first_entry, second_entry])

    with pytest.raises(PytestExceptionPolicyError, match="ordered and contiguous"):
        validate_pytest_exception_policy(
            tmp_path,
            inventory_path=inventory,
            paths=[first, second],
            as_of=TODAY,
        )


def test_new_uninventoried_skip_fails_closed(tmp_path: Path):
    relative = "tests/test_sample.py"
    _write_source(tmp_path, relative, "import pytest\npytest.skip('first')\n")
    inventory = _write_inventory(tmp_path, _entry(tmp_path, relative))
    _write_source(
        tmp_path,
        relative,
        "import pytest\npytest.skip('first')\npytest.skip('unreviewed')\n",
    )

    with pytest.raises(PytestExceptionPolicyError, match="count drifted"):
        validate_pytest_exception_policy(
            tmp_path,
            inventory_path=inventory,
            paths=[relative],
            as_of=TODAY,
        )


def test_condition_helper_change_invalidates_source_fingerprint(tmp_path: Path):
    relative = "tests/test_sample.py"
    source = (
        "import pytest\n"
        "AVAILABLE = True\n"
        "pytestmark = pytest.mark.skipif(not AVAILABLE, reason='platform branch')\n"
    )
    _write_source(tmp_path, relative, source)
    inventory = _write_inventory(tmp_path, _entry(tmp_path, relative))
    _write_source(tmp_path, relative, source.replace("AVAILABLE = True", "AVAILABLE = False"))

    with pytest.raises(PytestExceptionPolicyError, match="source changed"):
        validate_pytest_exception_policy(
            tmp_path,
            inventory_path=inventory,
            paths=[relative],
            as_of=TODAY,
        )


def test_xfail_must_be_explicitly_strict(tmp_path: Path):
    relative = "tests/test_sample.py"
    _write_source(
        tmp_path,
        relative,
        "import pytest\npytestmark = pytest.mark.xfail(reason='known gap')\n",
    )
    entry = _entry(
        tmp_path,
        relative,
        required_environment="behavior-gap:known-gap",
        expires_on="2026-09-01",
        permanent_rationale=None,
    )
    inventory = _write_inventory(tmp_path, entry)

    with pytest.raises(PytestExceptionPolicyError, match="not explicitly strict"):
        validate_pytest_exception_policy(
            tmp_path,
            inventory_path=inventory,
            paths=[relative],
            as_of=TODAY,
        )


def test_expired_xfail_is_rejected(tmp_path: Path):
    relative = "tests/test_sample.py"
    _write_source(
        tmp_path,
        relative,
        "import pytest\npytestmark = pytest.mark.xfail(reason='known gap', strict=True)\n",
    )
    entry = _entry(
        tmp_path,
        relative,
        required_environment="behavior-gap:known-gap",
        expires_on="2026-08-03",
        permanent_rationale=None,
    )
    inventory = _write_inventory(tmp_path, entry)

    with pytest.raises(PytestExceptionPolicyError, match="is expired"):
        validate_pytest_exception_policy(
            tmp_path,
            inventory_path=inventory,
            paths=[relative],
            as_of=TODAY,
        )


def test_production_critical_dependency_skip_requires_enforced_lane(tmp_path: Path):
    relative = "packages/maverick-core/tests/test_audit_new.py"
    _write_source(
        tmp_path,
        relative,
        "import pytest\npytest.importorskip('cryptography')\n",
    )
    entry = _entry(
        tmp_path,
        relative,
        required_environment="base-dependency:cryptography",
    )
    inventory = _write_inventory(tmp_path, entry)

    with pytest.raises(PytestExceptionPolicyError, match="lacks an enforced lane"):
        validate_pytest_exception_policy(
            tmp_path,
            inventory_path=inventory,
            paths=[relative],
            as_of=TODAY,
        )


def test_production_critical_tool_skip_requires_enforced_lane(tmp_path: Path):
    relative = "packages/maverick-core/tests/test_production_tool.py"
    _write_source(
        tmp_path,
        relative,
        "import pytest\npytest.skip('helm is not installed')\n",
    )
    entry = _entry(
        tmp_path,
        relative,
        required_environment="tool:helm",
    )
    inventory = _write_inventory(tmp_path, entry)

    with pytest.raises(PytestExceptionPolicyError, match="lacks an enforced lane"):
        validate_pytest_exception_policy(
            tmp_path,
            inventory_path=inventory,
            paths=[relative],
            as_of=TODAY,
        )


def test_enforcement_lane_must_retain_every_required_token(tmp_path: Path):
    relative = "packages/maverick-core/tests/test_audit_new.py"
    _write_source(
        tmp_path,
        relative,
        "import pytest\npytest.importorskip('cryptography')\n",
    )
    _write_source(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n  test:\n    steps:\n      # install cryptography\n      - run: python -m pytest\n",
    )
    entry = _entry(
        tmp_path,
        relative,
        required_environment="base-dependency:cryptography",
        enforced_by={
            "workflow": ".github/workflows/ci.yml",
            "job": "test",
            "required_tokens": ["install cryptography", "python -m pytest"],
        },
    )
    inventory = _write_inventory(tmp_path, entry)

    with pytest.raises(PytestExceptionPolicyError, match="enforcement lane drifted"):
        validate_pytest_exception_policy(
            tmp_path,
            inventory_path=inventory,
            paths=[relative],
            as_of=TODAY,
        )


def test_repository_inventory_is_exact_and_current():
    root = Path(__file__).resolve().parents[3]

    summary = validate_pytest_exception_policy(root, as_of=TODAY)

    assert summary == {
        "schema": "lightwork.pytest-exceptions.v1",
        "reviewed_on": "2026-08-09",
        "occurrence_count": 444,
        "production_critical_count": 227,
        "strict_xfail_count": 2,
        "enforced_dependency_count": 164,
    }
