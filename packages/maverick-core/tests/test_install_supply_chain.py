"""Regression tests for first-party package installation trust boundaries.

The Lightwork distribution names are intentionally treated as untrusted on
public indexes until the organization has reserved and protected them. CI and
installer entry points must therefore use pinned local source or an explicit
private index, never an implicit public lookup.
"""

from __future__ import annotations

import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_composite_action_installs_first_party_packages_from_its_checkout():
    text = _read("deploy/github-action/action.yml")

    assert "GITHUB_ACTION_PATH" in text
    for local_project in (
        "packages/maverick-core[all]",
        "packages/maverick-shield",
        "packages/maverick-channels",
        "packages/maverick-dashboard",
        "packages/maverick-mcp",
        "apps/installer-cli",
    ):
        assert local_project in text

    assert not re.search(
        r"python\s+-m\s+pip\s+install\s+['\"]?maverick-agent",
        text,
    )


def test_reusable_pr_workflow_requires_immutable_runtime_source():
    text = _read(".github/workflows/agent-on-pr.yml")

    assert "lightwork_ref:" in text
    assert "Full 40-character commit SHA" in text
    assert "repository: Day-AI-Labs/Lightwork" in text
    assert "persist-credentials: false" in text
    assert '"$source_root/packages/maverick-core[all]"' in text
    assert "pip install 'maverick-agent[all]'" not in text


def test_gitlab_template_has_no_implicit_public_index_fallback():
    text = _read("deploy/gitlab-ci/maverick.gitlab-ci.yml")

    assert "MAVERICK_SOURCE_DIR" in text
    assert "MAVERICK_SOURCE_REF" in text
    assert "MAVERICK_PACKAGE_INDEX_URL" in text
    assert "python -m pip --isolated install" in text
    assert "Public PyPI is blocked" in text
    assert "status --porcelain --untracked-files=all" in text
    assert 'pip install "maverick-agent"' not in text


def test_desktop_installers_require_a_pinned_source_ref():
    shell = _read("deploy/desktop/install.sh")
    powershell = _read("deploy/desktop/install.ps1")

    for text in (shell, powershell):
        assert "MAVERICK_REF is required" in text
        assert "public-index fallback is disabled" in text
        assert "MAVERICK_ALLOW_UNPINNED" not in text
        assert "maverick-agent[installer]" not in text
        assert "status --porcelain --untracked-files=all" in text

    assert "rev-parse HEAD" in shell
    assert "rev-parse HEAD" in powershell


def test_vps_installer_requires_a_clean_immutable_checkout():
    text = _read("deploy/vps/install.sh")

    assert "MAVERICK_REF is required" in text
    assert "^[0-9a-f]{40}$" in text
    assert "rev-parse HEAD" in text
    assert "status --porcelain --untracked-files=all" in text
    assert "maverick.stage." in text
    assert "MAVERICK_VERSION" not in text
    assert "Lightwork/main/deploy/vps/install.sh" not in text


def test_native_installer_build_ref_is_a_full_commit():
    build_rs = _read("apps/installer-desktop/src-tauri/build.rs")
    readme = _read("apps/installer-desktop/README.md")

    assert "validate_install_ref" in build_rs
    assert "trimmed.len() == 40" in build_rs
    assert "lowercase, full 40-character commit SHA" in build_rs
    assert "lowercase-full-40-character-commit-sha" in readme
    assert "<commit-or-tag>" not in readme


def test_training_bootstrap_installs_only_from_verified_checkout():
    text = _read("scripts/train_runpod.sh")

    assert "MAVERICK_SOURCE_DIR" in text
    assert "MAVERICK_SOURCE_REF" in text
    assert "status --porcelain --untracked-files=all" in text
    assert '"$MAVERICK_SOURCE_DIR/packages/maverick-core[training]"' in text
    assert "pip install --quiet 'maverick-agent[training]'" not in text


def test_public_pypi_consumer_is_disabled_until_namespace_is_reserved():
    text = _read(".github/workflows/homebrew-bump.yml")

    assert "MAVERICK_PUBLIC_PYPI_ENABLED == 'true'" in text
    assert "disabled by default" in text


def test_external_github_actions_are_pinned_to_full_commits():
    files = sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))
    files.append(REPO_ROOT / "deploy" / "github-action" / "action.yml")

    mutable: list[str] = []
    for path in files:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            match = re.search(r"\buses:\s*([^\s#]+)", line)
            if match is None:
                continue
            target = match.group(1)
            if target.startswith(("./", "docker://")):
                continue
            if re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", target) is None:
                mutable.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {target}")

    assert mutable == [], "mutable external actions:\n" + "\n".join(mutable)


def test_markdown_workflow_examples_do_not_recommend_mutable_action_refs():
    mutable: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*.md")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = re.search(r"\buses:\s*([^\s#]+)", line)
            if match is None:
                continue
            target = match.group(1)
            ref = target.rpartition("@")[2]
            if not ref or "full-40-character-commit-sha" in ref:
                continue
            if re.fullmatch(r"[0-9a-f]{40}", ref) is None:
                mutable.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {target}")

    assert mutable == [], "mutable Markdown action refs:\n" + "\n".join(mutable)


def test_public_publish_workflow_uses_an_explicit_package_allowlist():
    text = _read(".github/workflows/publish.yml")

    for public_project in (
        "packages/maverick-core",
        "packages/maverick-shield",
        "packages/maverick-channels",
        "packages/maverick-dashboard",
        "packages/maverick-mcp",
        "packages/maverick-evolve",
        "packages/maverick-knowledge",
        "apps/installer-cli",
    ):
        assert public_project in text
    assert "apps/vendor-console" not in text
    assert "for d in packages/* apps/*" not in text
    # OIDC is granted only on the publish/sign jobs, never the dependency-
    # installing build job through a workflow-wide permission.
    top_permissions = text.split("jobs:", 1)[0]
    assert "id-token: write" not in top_permissions


def test_active_runtime_guidance_never_resolves_first_party_from_public_index():
    active_files = (
        "packages/maverick-core/maverick/cli/__init__.py",
        "packages/maverick-core/maverick/health.py",
        "packages/maverick-core/maverick/deployment.py",
        "packages/maverick-core/maverick/queue_dispatcher.py",
    )
    unsafe = re.compile(
        r"pip(?:3|x)?\s+(?:install|inject)[^\n]*(?:maverick-agent|"
        r"maverick-dashboard|maverick-mcp-server|maverick-channels|"
        r"maverick-shield|maverick-installer)",
        re.IGNORECASE,
    )
    for path in active_files:
        assert unsafe.search(_read(path)) is None, path


def test_localized_getting_started_pages_use_reviewed_source_not_public_names():
    for path in sorted((REPO_ROOT / "docs" / "i18n").glob("*/getting-started.md")):
        text = path.read_text(encoding="utf-8")
        assert "pipx install 'maverick-agent[installer]'" not in text
        assert "git checkout --detach <reviewed-full-40-character-commit-sha>" in text


def _metadata(relative_path: str) -> dict:
    return tomllib.loads(_read(relative_path))


def _assert_floor(requirements: list[str], package: str, floor: str) -> None:
    matches = [Requirement(item) for item in requirements
               if Requirement(item).name.casefold() == package.casefold()]
    assert matches, f"{package} is not constrained"
    lower_bounds = [
        Version(spec.version)
        for requirement in matches
        for spec in requirement.specifier
        if spec.operator == ">="
    ]
    assert lower_bounds and max(lower_bounds) >= Version(floor), (
        f"{package} must be constrained to >= {floor}: {matches}"
    )


def test_security_dependency_floors_cover_current_fixed_releases():
    core = _metadata("packages/maverick-core/pyproject.toml")
    core_deps = core["project"]["dependencies"]
    core_extras = core["project"]["optional-dependencies"]
    _assert_floor(core_deps, "click", "8.3.3")
    _assert_floor(core_deps, "cryptography", "48.0.1")
    _assert_floor(core_extras["langchain"], "langchain-core", "1.3.3")
    _assert_floor(core_extras["langchain"], "langsmith", "0.8.18")
    _assert_floor(core_extras["computer-use"], "pillow", "12.3.0")
    _assert_floor(core_extras["pdf"], "pypdf", "6.13.3")
    _assert_floor(core_extras["dev"], "python-multipart", "0.0.31")
    _assert_floor(core_extras["dev"], "starlette", "1.3.1")

    dashboard = _metadata("packages/maverick-dashboard/pyproject.toml")
    _assert_floor(dashboard["project"]["dependencies"], "python-multipart", "0.0.31")
    _assert_floor(dashboard["project"]["dependencies"], "starlette", "1.3.1")

    channels = _metadata("packages/maverick-channels/pyproject.toml")
    channel_extras = channels["project"]["optional-dependencies"]
    for extra in ("discord", "matrix", "all"):
        _assert_floor(channel_extras[extra], "aiohttp", "3.14.1")
    for extra in ("whatsapp", "sms", "all"):
        _assert_floor(channel_extras[extra], "python-multipart", "0.0.31")
        _assert_floor(channel_extras[extra], "starlette", "1.3.1")

    knowledge = _metadata("packages/maverick-knowledge/pyproject.toml")
    knowledge_extras = knowledge["project"]["optional-dependencies"]
    _assert_floor(knowledge_extras["parsers"], "pypdf", "6.13.3")
    _assert_floor(knowledge_extras["vision"], "pillow", "12.3.0")


def test_all_python_builds_require_patched_setuptools_floor():
    for path in sorted(REPO_ROOT.rglob("pyproject.toml")):
        metadata = tomllib.loads(path.read_text(encoding="utf-8"))
        build = metadata.get("build-system")
        if build is None:
            continue
        requirements = build.get("requires", [])
        if any(
            Requirement(item).name.casefold() == "setuptools"
            for item in requirements
        ):
            _assert_floor(requirements, "setuptools", "83.0.0")
