"""Adversarial tests for the source-derived production QA assurance map."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "release" / "production-qa-assurance.json"
SCRIPT = ROOT / "scripts" / "verify_production_qa_assurance.py"
SPEC = importlib.util.spec_from_file_location("verify_production_qa_assurance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _load_workflow(path: Path) -> dict:
    return VERIFIER.yaml.load(
        path.read_text(encoding="utf-8"),
        Loader=VERIFIER._StrictWorkflowLoader,
    )


def _copy_workflows(tmp_path: Path) -> None:
    for relative in VERIFIER.WORKFLOW_BINDINGS:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)


def _copy_mapped_qa_inputs(tmp_path: Path) -> None:
    required = {
        "pyproject.toml",
        "conftest.py",
        "packages/maverick-core/tests/conftest.py",
        VERIFIER.CI_WORKFLOW,
        *VERIFIER.LOCAL_TEST_DISTRIBUTION_PYPROJECTS,
        *VERIFIER.REVIEWED_TEST_SUPPORT_SOURCE_SHA256,
    }
    for row in VERIFIER.FUZZ_PROPERTY_MAPPINGS:
        required.update(item["path"] for item in row["sources"])
        required.update(item["path"] for item in row["tests"])
    for relative in required:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)


def test_current_qa_assurance_contract_resolves_exact_repository_wiring() -> None:
    messages = VERIFIER.verify(ROOT)
    assert "verified 16 fuzz/property/state-machine mappings" in messages
    assert "verified 108 exact mapped test functions" in messages
    assert any("11 supply-chain controls" in message for message in messages)
    assert "release_eligible=false" in messages
    assert "live_external_status=required-not-verified-by-this-tool" in messages
    contract = _contract()
    for gate_id in ("QA-05", "QA-07", "QA-16"):
        assert contract["gate_statuses"][gate_id].startswith("repository-complete")
    assert len(contract["external_limitations"]) == 5


def test_mapping_cannot_silently_drop_a_fuzz_or_state_machine_boundary() -> None:
    contract = _contract()
    contract["fuzz_property_mappings"] = contract["fuzz_property_mappings"][1:]
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="fuzz/property mapping inventory drifted",
    ):
        VERIFIER.validate(ROOT, contract)


def test_draft_mapping_cannot_be_relabelled_as_certification() -> None:
    contract = _contract()
    contract["compliance_regimes"][0]["certified"] = True
    contract["compliance_regimes"][0]["claim_status"] = "certified"
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="compliance regime inventory drifted",
    ):
        VERIFIER.validate(ROOT, contract)


def test_repository_process_cannot_claim_live_staffing_or_drills() -> None:
    contract = _contract()
    contract["incident_vulnerability_disclosure"][0]["staffing_verified"] = True
    contract["incident_vulnerability_disclosure"][0]["live_drill_required"] = False
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="incident process inventory drifted",
    ):
        VERIFIER.validate(ROOT, contract)


def test_source_binding_refuses_reviewed_file_drift(tmp_path: Path) -> None:
    contract = _contract()
    contract["source_bindings"][0]["sha256"] = "0" * 64
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="QA source binding drifted",
    ):
        VERIFIER.validate(ROOT, contract)

    _copy_mapped_qa_inputs(tmp_path)
    helper = tmp_path / "lightwork_test_support/spawn.py"
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n# unreviewed harness change\n",
        encoding="utf-8",
    )
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="mapped test support differs from reviewed source",
    ):
        VERIFIER._verify_test_support_sources(tmp_path)


def test_external_limitations_cannot_be_shortened_or_reworded() -> None:
    contract = _contract()
    contract["external_limitations"][0] = "CI passed"
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="external limitations drifted or weakened",
    ):
        VERIFIER.validate(ROOT, contract)


def test_mapped_pytest_job_and_step_must_be_unconditional(tmp_path: Path) -> None:
    workflow = tmp_path / VERIFIER.CI_WORKFLOW
    workflow.parent.mkdir(parents=True, exist_ok=True)
    text = (ROOT / VERIFIER.CI_WORKFLOW).read_text(encoding="utf-8")
    marker = "      - name: Test (bounded, with diagnostics)\n"
    assert marker in text
    workflow.write_text(
        text.replace(marker, marker + "        if: ${{ always() }}\n", 1),
        encoding="utf-8",
    )
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="mapped test step is conditional",
    ):
        VERIFIER._workflow_step(
            tmp_path,
            workflow=VERIFIER.CI_WORKFLOW,
            job=VERIFIER.CI_TEST_JOB,
            step=VERIFIER.CI_TEST_STEP,
            require_unconditional=True,
        )

    document = _load_workflow(ROOT / VERIFIER.CI_WORKFLOW)
    document["jobs"]["disabled-prerequisite"] = {
        "if": "${{ false }}",
        "runs-on": "ubuntu-24.04",
        "steps": [{"run": "true"}],
    }
    document["jobs"][VERIFIER.CI_TEST_JOB]["needs"] = "disabled-prerequisite"
    workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="prerequisites drifted or are unreviewed",
    ):
        VERIFIER._workflow_step(
            tmp_path,
            workflow=VERIFIER.CI_WORKFLOW,
            job=VERIFIER.CI_TEST_JOB,
            step=VERIFIER.CI_TEST_STEP,
            require_unconditional=True,
            expected_job_needs=None,
        )

    workflow.write_text(
        text.replace(marker, marker + "        continue-on-error: true\n", 1),
        encoding="utf-8",
    )
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="workflow gate step permits failure",
    ):
        VERIFIER._workflow_step(
            tmp_path,
            workflow=VERIFIER.CI_WORKFLOW,
            job=VERIFIER.CI_TEST_JOB,
            step=VERIFIER.CI_TEST_STEP,
            require_unconditional=True,
        )


def test_mapped_test_function_cannot_skip_or_xfail(tmp_path: Path) -> None:
    path = tmp_path / "test_mapped.py"
    path.write_text(
        "import pytest\n\n@pytest.mark.skip(reason='disabled')\n"
        "def test_required_boundary():\n    assert True\n",
        encoding="utf-8",
    )
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="mapped test can skip or xfail",
    ):
        VERIFIER._verify_mapped_tests_are_unskippable(
            path,
            {"test_required_boundary"},
        )


def test_mapped_test_function_cannot_hide_from_collection_with_dunder_test(
    tmp_path: Path,
) -> None:
    path = tmp_path / "test_mapped.py"
    path.write_text(
        "def test_required_boundary():\n    raise AssertionError\n\n"
        "test_required_boundary.__test__ = False\n",
        encoding="utf-8",
    )
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="hidden from collection",
    ):
        VERIFIER._verify_mapped_tests_are_unskippable(
            path,
            {"test_required_boundary"},
        )


@pytest.mark.parametrize(
    "source",
    [
        (
            "import pytest as p\n\n@p.mark.skip(reason='disabled')\n"
            "def test_required_boundary():\n    raise AssertionError\n"
        ),
        (
            "from pytest import skip as s\n\n"
            "def test_required_boundary():\n    s('disabled')\n"
        ),
        (
            "import pytest\n\n"
            "def test_required_boundary():\n    getattr(pytest, 'skip')('disabled')\n"
        ),
        (
            "import unittest as u\n\n"
            "def test_required_boundary():\n    raise u.SkipTest('disabled')\n"
        ),
        (
            "import pytest\n\n"
            "def test_required_boundary():\n    raise pytest.skip.Exception('disabled')\n"
        ),
        (
            "import pytest\n\n"
            "@pytest.mark.parametrize('value', [pytest.param(1, "
            "marks=getattr(pytest.mark, 'skip'))])\n"
            "def test_required_boundary(value):\n    assert value\n"
        ),
    ],
)
def test_mapped_test_alias_and_dynamic_skip_paths_are_rejected(
    tmp_path: Path,
    source: str,
) -> None:
    path = tmp_path / "test_mapped.py"
    path.write_text(source, encoding="utf-8")

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="mapped test can skip or xfail",
    ):
        VERIFIER._verify_mapped_tests_are_unskippable(
            path,
            {"test_required_boundary"},
        )


@pytest.mark.parametrize(
    "suppression",
    [
        "globals()['__test__'] = False",
        "globals().update(__test__=False)",
        "vars()['__test__'] = False",
        "test_required_boundary.__dict__['__test__'] = False",
    ],
)
def test_mapped_test_dynamic_dunder_test_suppression_is_rejected(
    tmp_path: Path,
    suppression: str,
) -> None:
    path = tmp_path / "test_mapped.py"
    path.write_text(
        "def test_required_boundary():\n    raise AssertionError\n\n"
        f"{suppression}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="hidden from collection",
    ):
        VERIFIER._verify_mapped_tests_are_unskippable(
            path,
            {"test_required_boundary"},
        )


def test_supply_chain_fragment_in_comment_is_not_executable_evidence() -> None:
    command = "# cosign sign-blob --output-certificate\ntrue # pip-audit --strict"
    executable = VERIFIER._executable_text(command)
    assert "cosign sign-blob" not in executable
    assert "pip-audit" not in executable


def test_supply_chain_job_cannot_be_disabled_and_command_cannot_be_dead(
    tmp_path: Path,
) -> None:
    for relative in VERIFIER.WORKFLOW_BINDINGS:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)

    workflow = tmp_path / VERIFIER.CI_WORKFLOW
    document = _load_workflow(workflow)
    document["jobs"]["audit"]["if"] = "${{ false }}"
    workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="job condition drifted or is disabled",
    ):
        VERIFIER._verify_supply_chain(
            tmp_path,
            list(VERIFIER.SUPPLY_CHAIN_CONTROLS),
        )

    shutil.copyfile(ROOT / VERIFIER.CI_WORKFLOW, workflow)
    document = _load_workflow(workflow)
    document["jobs"]["disabled-prerequisite"] = {
        "if": "${{ false }}",
        "runs-on": "ubuntu-24.04",
        "steps": [{"run": "true"}],
    }
    document["jobs"]["audit"]["needs"] = "disabled-prerequisite"
    workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="prerequisites drifted or are unreviewed",
    ):
        VERIFIER._verify_supply_chain(
            tmp_path,
            list(VERIFIER.SUPPLY_CHAIN_CONTROLS),
        )

    shutil.copyfile(ROOT / VERIFIER.CI_WORKFLOW, workflow)
    document = _load_workflow(workflow)
    step = next(
        item
        for item in document["jobs"]["audit"]["steps"]
        if item.get("name") == "pip-audit (known CVEs in dependencies)"
    )
    step["run"] = (
        "if false; then\n"
        "  python -m pip install 'pip-audit==2.10.0'\n"
        "  pip-audit\n"
        "fi\n"
        "echo skipped\n"
    )
    workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="statically inert|full job semantics drifted",
    ):
        VERIFIER._verify_supply_chain(
            tmp_path,
            list(VERIFIER.SUPPLY_CHAIN_CONTROLS),
        )

    for inert_run in (
        (
            "dead_gate(){\n"
            "  python -m pip install 'pip-audit==2.10.0'\n"
            "  pip-audit\n"
            "}\n"
            "echo skipped\n"
        ),
        (
            "python -m pip install 'pip-audit==2.10.0'\n"
            "set +e\n"
            "pip-audit\n"
            "echo skipped\n"
        ),
    ):
        shutil.copyfile(ROOT / VERIFIER.CI_WORKFLOW, workflow)
        document = _load_workflow(workflow)
        step = next(
            item
            for item in document["jobs"]["audit"]["steps"]
            if item.get("name") == "pip-audit (known CVEs in dependencies)"
        )
        step["run"] = inert_run
        workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")
        with pytest.raises(
            VERIFIER.ProductionQaAssuranceError,
            match="command drifted from exact reviewed bytes|full job semantics drifted",
        ):
            VERIFIER._verify_supply_chain(
                tmp_path,
                list(VERIFIER.SUPPLY_CHAIN_CONTROLS),
            )


@pytest.mark.parametrize(
    "mutation",
    [
        "workflow-default-shell",
        "job-default-shell",
        "step-shell",
        "predecessor-path-poison",
        "self-hosted-runner",
        "zero-expansion-strategy",
    ],
)
def test_supply_chain_exact_execution_context_rejects_semantic_wrappers(
    tmp_path: Path,
    mutation: str,
) -> None:
    _copy_workflows(tmp_path)
    workflow = tmp_path / VERIFIER.CI_WORKFLOW
    document = _load_workflow(workflow)
    audit = document["jobs"]["audit"]
    if mutation == "workflow-default-shell":
        document["defaults"] = {
            "run": {"shell": "bash -c 'bash \"$1\" || true' -- {0}"}
        }
    elif mutation == "job-default-shell":
        audit["defaults"] = {
            "run": {"shell": "bash -c 'bash \"$1\" || true' -- {0}"}
        }
    elif mutation == "step-shell":
        lint_step = next(
            step
            for step in document["jobs"]["lint"]["steps"]
            if step.get("name")
            == "Secret scan (detect-secrets, no new secrets vs audited baseline)"
        )
        lint_step["shell"] = "bash -c 'bash \"$1\" || true' -- {0}"
    elif mutation == "predecessor-path-poison":
        audit["steps"].insert(
            0,
            {
                "name": "Poison command resolution",
                "run": "echo /tmp/fake-bin >> \"$GITHUB_PATH\"",
            },
        )
    elif mutation == "self-hosted-runner":
        audit["runs-on"] = "self-hosted"
    else:
        audit["strategy"] = {"matrix": {"slot": []}}
    workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="execution context drifted|full job semantics drifted|workflow semantics drifted",
    ):
        VERIFIER._verify_supply_chain(
            tmp_path,
            list(VERIFIER.SUPPLY_CHAIN_CONTROLS),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "workflow-env",
        "job-env",
        "step-env",
        "predecessor-github-env",
        "self-hosted-runner",
        "zero-expansion-strategy",
    ],
)
def test_mapped_pytest_exact_execution_context_rejects_deselection_paths(
    tmp_path: Path,
    mutation: str,
) -> None:
    _copy_mapped_qa_inputs(tmp_path)
    workflow = tmp_path / VERIFIER.CI_WORKFLOW
    document = _load_workflow(workflow)
    job = document["jobs"][VERIFIER.CI_TEST_JOB]
    step = next(item for item in job["steps"] if item.get("name") == VERIFIER.CI_TEST_STEP)
    deselect = "--deselect=packages/maverick-core/tests/test_parser_fuzz.py::test_capability_json_parser_never_crashes"
    if mutation == "workflow-env":
        document.setdefault("env", {})["PYTEST_ADDOPTS"] = deselect
    elif mutation == "job-env":
        job["env"] = {"PYTEST_ADDOPTS": deselect}
    elif mutation == "step-env":
        step.setdefault("env", {})["PYTEST_ADDOPTS"] = deselect
    elif mutation == "predecessor-github-env":
        target_index = job["steps"].index(step)
        job["steps"].insert(
            target_index,
            {
                "name": "Poison pytest collection",
                "run": f"echo 'PYTEST_ADDOPTS={deselect}' >> \"$GITHUB_ENV\"",
            },
        )
    elif mutation == "self-hosted-runner":
        job["runs-on"] = "self-hosted"
    else:
        job["strategy"] = {"fail-fast": False, "matrix": {"python-version": []}}
    workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="execution context drifted|full job semantics drifted",
    ):
        VERIFIER._verify_fuzz_mappings(
            tmp_path,
            list(VERIFIER.FUZZ_PROPERTY_MAPPINGS),
        )


@pytest.mark.parametrize(
    "mutation",
    ["disabled-job", "soft-failing-job", "disabled-first-step"],
)
def test_release_gate_rejects_poisoned_transitive_prerequisite(
    tmp_path: Path,
    mutation: str,
) -> None:
    _copy_workflows(tmp_path)
    workflow = tmp_path / ".github/workflows/release.yml"
    document = _load_workflow(workflow)
    prerequisite = document["jobs"]["validate-release-tag"]
    if mutation == "disabled-job":
        prerequisite["if"] = "${{ false }}"
    elif mutation == "soft-failing-job":
        prerequisite["continue-on-error"] = True
    else:
        prerequisite["steps"][0]["if"] = "${{ false }}"
    workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="critical workflow job execution context drifted|full job semantics drifted",
    ):
        VERIFIER._verify_supply_chain(
            tmp_path,
            list(VERIFIER.SUPPLY_CHAIN_CONTROLS),
        )


@pytest.mark.parametrize(
    "relative",
    [
        ".github/workflows/ci.yml",
        ".github/workflows/publish.yml",
        ".github/workflows/release.yml",
    ],
)
def test_release_critical_workflow_cannot_remove_its_trigger(
    tmp_path: Path,
    relative: str,
) -> None:
    _copy_workflows(tmp_path)
    workflow = tmp_path / relative
    document = _load_workflow(workflow)
    document["on"] = []
    workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="workflow execution context drifted|workflow semantics drifted",
    ):
        VERIFIER._verify_supply_chain(
            tmp_path,
            list(VERIFIER.SUPPLY_CHAIN_CONTROLS),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate-trigger",
        "duplicate-job",
        "duplicate-runner",
        "duplicate-target-shell",
        "duplicate-workflow-env",
        "merge-key",
        "anchor-alias",
    ],
)
def test_strict_workflow_parser_rejects_ambiguous_yaml(
    tmp_path: Path,
    mutation: str,
) -> None:
    _copy_workflows(tmp_path)
    workflow = tmp_path / VERIFIER.CI_WORKFLOW
    text = workflow.read_text(encoding="utf-8")
    if mutation == "duplicate-trigger":
        text = "on: []\n" + text
    elif mutation == "duplicate-job":
        text = text.replace(
            "jobs:\n",
            "jobs:\n  audit:\n    runs-on: self-hosted\n    steps: []\n",
            1,
        )
    elif mutation == "duplicate-runner":
        text = text.replace(
            "  audit:\n    runs-on: ubuntu-24.04\n",
            "  audit:\n    runs-on: self-hosted\n    runs-on: ubuntu-24.04\n",
            1,
        )
    elif mutation == "duplicate-target-shell":
        text = text.replace(
            (
                "      - name: Test (bounded, with diagnostics)\n"
                "        env:\n"
                "          PYTHONFAULTHANDLER: \"1\"\n"
                "        shell: bash\n"
            ),
            (
                "      - name: Test (bounded, with diagnostics)\n"
                "        env:\n"
                "          PYTHONFAULTHANDLER: \"1\"\n"
                "        shell: bash -c 'bash \"$1\" || true' -- {0}\n"
                "        shell: bash\n"
            ),
            1,
        )
    elif mutation == "duplicate-workflow-env":
        text = text.replace(
            "env:\n",
            "env:\n  PYTEST_ADDOPTS: --collect-only\nenv:\n",
            1,
        )
    elif mutation == "merge-key":
        text = text.replace(
            "  audit:\n",
            "  audit:\n    <<: {runs-on: self-hosted}\n",
            1,
        )
    else:
        text = "x-poison: &poison {runs-on: self-hosted}\n" + text
        text = text.replace("  audit:\n", "  audit:\n    <<: *poison\n", 1)
    workflow.write_text(text, encoding="utf-8")

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="not parseable|anchors and aliases are not admitted",
    ):
        VERIFIER._workflow(tmp_path, VERIFIER.CI_WORKFLOW)


@pytest.mark.parametrize(
    "coerced_key",
    ["true", "True", "TRUE", "yes", "Yes", "YES", "ON"],
)
def test_workflow_parser_requires_exact_yaml12_on_key(
    tmp_path: Path,
    coerced_key: str,
) -> None:
    _copy_workflows(tmp_path)
    workflow = tmp_path / VERIFIER.CI_WORKFLOW
    text = workflow.read_text(encoding="utf-8")
    assert "on:\n" in text
    workflow.write_text(
        text.replace("on:\n", f"{coerced_key}:\n", 1),
        encoding="utf-8",
    )

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="not parseable|exact GitHub trigger key",
    ):
        VERIFIER._workflow(tmp_path, VERIFIER.CI_WORKFLOW)


def test_mapped_pytest_command_cannot_be_echo_or_dead_function(tmp_path: Path) -> None:
    required = {
        "pyproject.toml",
        "conftest.py",
        "packages/maverick-core/tests/conftest.py",
        VERIFIER.CI_WORKFLOW,
        *VERIFIER.LOCAL_TEST_DISTRIBUTION_PYPROJECTS,
        *VERIFIER.REVIEWED_TEST_SUPPORT_SOURCE_SHA256,
    }
    for row in VERIFIER.FUZZ_PROPERTY_MAPPINGS:
        required.update(item["path"] for item in row["sources"])
        required.update(item["path"] for item in row["tests"])
    for relative in required:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    workflow = tmp_path / VERIFIER.CI_WORKFLOW
    for run in (
        "echo python -m pytest\n",
        "dead_tests(){ python -m pytest; }; echo skipped\n",
    ):
        document = _load_workflow(ROOT / VERIFIER.CI_WORKFLOW)
        step = next(
            item
            for item in document["jobs"][VERIFIER.CI_TEST_JOB]["steps"]
            if item.get("name") == VERIFIER.CI_TEST_STEP
        )
        step["run"] = run
        workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")
        with pytest.raises(
            VERIFIER.ProductionQaAssuranceError,
            match="pytest command drifted from exact reviewed bytes",
        ):
            VERIFIER._verify_fuzz_mappings(
                tmp_path,
                list(VERIFIER.FUZZ_PROPERTY_MAPPINGS),
            )


def test_collection_hook_cannot_deselect_mapped_tests(tmp_path: Path) -> None:
    _copy_mapped_qa_inputs(tmp_path)
    conftest = tmp_path / "conftest.py"
    conftest.write_text(
        conftest.read_text(encoding="utf-8")
        + "\ndef pytest_collection_modifyitems(items):\n    items[:] = []\n",
        encoding="utf-8",
    )
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="can disable mapped tests",
    ):
        VERIFIER._verify_collection_controls(tmp_path)

    shutil.copyfile(ROOT / "conftest.py", conftest)
    conftest.write_text(
        conftest.read_text(encoding="utf-8")
        + (
            "\n@pytest.fixture(autouse=True)\n"
            "def skip_required_boundary(request):\n"
            "    if request.node.name == 'test_required_boundary':\n"
            "        pytest.skip('disabled')\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="can disable mapped tests",
    ):
        VERIFIER._verify_collection_controls(tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        (
            "\ndef hidden_deselect(session, config, items):\n    items[:] = []\n"
            "pytest_collection_modifyitems = hidden_deselect\n"
        ),
        (
            "\nimport pytest as p\n\n@p.fixture(autouse=True)\n"
            "def hidden_skip():\n    getattr(p, 'skip')('disabled')\n"
        ),
        "\nglobals()['pytest_plugins'] = ['evil_plugin']\n",
    ],
)
def test_dynamic_conftest_collection_suppression_is_rejected(
    tmp_path: Path,
    payload: str,
) -> None:
    _copy_mapped_qa_inputs(tmp_path)
    conftest = tmp_path / "conftest.py"
    conftest.write_text(
        conftest.read_text(encoding="utf-8") + payload,
        encoding="utf-8",
    )

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="can disable mapped tests|differs from reviewed source",
    ):
        VERIFIER._verify_collection_controls(tmp_path)


@pytest.mark.parametrize(
    "relative",
    [
        "pytest.ini",
        ".pytest.ini",
        "pytest.toml",
        ".pytest.toml",
        "tox.ini",
        "setup.cfg",
        "sitecustomize.py",
        "usercustomize.py",
    ],
)
def test_alternate_pytest_and_startup_controls_are_rejected(
    tmp_path: Path,
    relative: str,
) -> None:
    _copy_mapped_qa_inputs(tmp_path)
    (tmp_path / relative).write_text("[pytest]\naddopts = --collect-only\n", encoding="utf-8")

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="unreviewed pytest or Python startup control",
    ):
        VERIFIER._verify_collection_controls(tmp_path)


@pytest.mark.parametrize(
    "relative",
    [
        "packages/conftest.py",
        "packages/maverick-core/conftest.py",
        "packages/maverick-dashboard/conftest.py",
        "packages/maverick-dashboard/tests/conftest.py",
    ],
)
def test_unreviewed_mapped_test_ancestor_conftest_is_rejected(
    tmp_path: Path,
    relative: str,
) -> None:
    _copy_mapped_qa_inputs(tmp_path)
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("pytest_plugins = ['evil_plugin']\n", encoding="utf-8")

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="unreviewed ancestor conftest",
    ):
        VERIFIER._verify_collection_controls(tmp_path)


def test_editable_package_startup_module_and_pytest11_plugin_are_rejected(
    tmp_path: Path,
) -> None:
    _copy_mapped_qa_inputs(tmp_path)
    package_root = tmp_path / "packages/maverick-core"
    startup = package_root / "sitecustomize.py"
    startup.write_text("raise RuntimeError('startup poison')\n", encoding="utf-8")
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="editable-installed package exposes",
    ):
        VERIFIER._verify_collection_controls(tmp_path)

    startup.unlink()
    pyproject = package_root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        + "\n[project.entry-points.pytest11]\n"
        + "lightwork_qa_poison = 'maverick.qa_poison'\n",
        encoding="utf-8",
    )
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="unreviewed pytest plugin",
    ):
        VERIFIER._verify_collection_controls(tmp_path)


def test_release_critical_action_tag_cannot_replace_commit_pin(tmp_path: Path) -> None:
    for relative in VERIFIER.WORKFLOW_BINDINGS:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)

    workflow = tmp_path / VERIFIER.WORKFLOW_BINDINGS[0]
    text = workflow.read_text(encoding="utf-8")
    text = text.replace(
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/checkout@v7",
        1,
    )
    workflow.write_text(text, encoding="utf-8")

    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="release-critical action is not commit-pinned",
    ):
        VERIFIER._verify_action_pins(tmp_path)


def test_external_reusable_workflow_must_be_commit_pinned(tmp_path: Path) -> None:
    for relative in VERIFIER.WORKFLOW_BINDINGS:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    workflow = tmp_path / VERIFIER.WORKFLOW_BINDINGS[0]
    document = _load_workflow(workflow)
    document["jobs"]["unreviewed-reusable"] = {
        "uses": "owner/repo/.github/workflows/gate.yml@main",
    }
    workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="reusable workflow is not commit-pinned",
    ):
        VERIFIER._verify_action_pins(tmp_path)


def test_local_reusable_workflow_is_rejected_until_transitively_inventoried(
    tmp_path: Path,
) -> None:
    for relative in VERIFIER.WORKFLOW_BINDINGS:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    local = tmp_path / ".github/workflows/unreviewed.yml"
    local.write_text(
        "jobs:\n  gate:\n    steps:\n      - uses: actions/checkout@main\n",
        encoding="utf-8",
    )
    workflow = tmp_path / VERIFIER.WORKFLOW_BINDINGS[0]
    document = _load_workflow(workflow)
    document["jobs"]["unreviewed-reusable"] = {
        "uses": "./.github/workflows/unreviewed.yml",
    }
    workflow.write_text(VERIFIER.yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="outside the reviewed inventory",
    ):
        VERIFIER._verify_action_pins(tmp_path)


def test_contract_copy_is_independent_of_verifier_constants() -> None:
    contract = _contract()
    cloned = copy.deepcopy(contract)
    cloned["gate_statuses"]["QA-07"] = "complete"
    assert contract["gate_statuses"]["QA-07"] != "complete"
    with pytest.raises(
        VERIFIER.ProductionQaAssuranceError,
        match="QA assurance identity or gate boundary drifted",
    ):
        VERIFIER.validate(ROOT, cloned)
