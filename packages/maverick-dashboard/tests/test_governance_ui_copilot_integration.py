from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"
TEMPLATES = PACKAGE / "templates"
SHARED_ASSETS = {
    "/static/daybreak-logo.jpg",
    "/static/daybreak-mark.png",
    "/static/lightwork-copilot.js",
    "/static/lightwork-ui.js",
    "/static/lightwork-voice.js",
    "/static/lightwork.css",
}
GOVERNANCE_PAGES = {
    "/governance-agents": set(),
    "/ai-evidence-analysis": {
        "/static/lightwork-ai-evidence-analysis.css",
        "/static/lightwork-ai-evidence-analysis.js",
    },
    "/assess/evidence-autopopulation": {
        "/static/lightwork-assessment-evidence-autopopulation.css",
        "/static/lightwork-assessment-evidence-autopopulation.js",
    },
    "/administration/document-vault": {"/static/lightwork-governed-document-vault.js"},
    "/assurance/cloud-agents": set(),
    "/privacy/drop": {"/static/lightwork-privacy-drop-management.js"},
    "/privacy/data-discovery": {
        "/static/lightwork-privacy-data-discovery.css",
        "/static/lightwork-privacy-data-discovery.js",
    },
    "/governance/inventory": {
        "/static/lightwork-shared-governance-inventory.js",
    },
    "/ai-entity-tasks": {"/static/lightwork-ai-entity-tasks.js"},
    "/consent/custom-indexes": {"/static/lightwork-consent-custom-index.js"},
    "/consent/bulk-jobs": {"/static/lightwork-consent-bulk-jobs.js"},
    "/consent/publish-settings": {"/static/lightwork-consent-publish-settings.js"},
    "/consent/global-opt-out": {
        "/static/lightwork-consent-global-opt-out.css",
        "/static/lightwork-consent-global-opt-out.js",
    },
    "/third-party/contracts": {"/static/lightwork-third-party-contracts.js"},
    "/third-party/executive-reports": {"/static/lightwork-third-party-executive-reports.js"},
    "/compliance/continuous-evidence": set(),
}


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: set[str] = set()
        self.asset_counts: Counter[str] = Counter()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        candidate = attributes.get("href" if tag == "link" else "src")
        if candidate and candidate.startswith("/static/"):
            self.assets.add(candidate)
            self.asset_counts[candidate] += 1


def _effective_routes(routes: Iterable[object]) -> list[object]:
    """Flatten FastAPI's included-router wrappers without assuming ``.path``."""

    flattened: list[object] = []
    for route in routes:
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            flattened.extend(_effective_routes(candidates()))
        else:
            flattened.append(route)
    return flattened


def _registered_paths(app: object) -> set[str]:
    paths: set[str] = set()
    for route in _effective_routes(getattr(app, "routes", ())):
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)
    return paths


@pytest.fixture()
def dashboard_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> TestClient:
    monkeypatch.setenv("MAVERICK_TENANT", "governance-ui-integration")
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "absent-config.toml"))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)

    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app

    return TestClient(app, headers={"Origin": "http://testserver"})


@pytest.mark.parametrize(("page_path", "page_assets"), GOVERNANCE_PAGES.items())
def test_exposed_governance_pages_use_shared_shell_and_mounted_assets(
    dashboard_client: TestClient,
    page_path: str,
    page_assets: set[str],
) -> None:
    registered = _registered_paths(dashboard_client.app)
    assert page_path in registered

    response = dashboard_client.get(page_path)
    assert response.status_code == 200
    for shell_marker in (
        'class="skip-link"',
        'id="sidebar"',
        'id="content"',
        'id="mv-copilot"',
        'id="mv-copilot-fab"',
        'id="mv-copilot-egress"',
    ):
        assert shell_marker in response.text
    assert "may be sent to that provider" in response.text

    parser = _AssetParser()
    parser.feed(response.text)
    expected_assets = SHARED_ASSETS | page_assets
    assert expected_assets <= parser.assets
    assert parser.assets <= registered
    assert all(parser.asset_counts[asset] == 1 for asset in page_assets)

    for asset in parser.assets:
        asset_response = dashboard_client.get(asset)
        assert asset_response.status_code == 200, asset
        content_type = asset_response.headers["content-type"].split(";", 1)[0]
        if asset.endswith(".css"):
            assert content_type == "text/css", asset
        elif asset.endswith(".js"):
            assert content_type == "application/javascript", asset
        elif asset.endswith((".jpg", ".jpeg")):
            assert content_type == "image/jpeg", asset
        elif asset.endswith(".png"):
            assert content_type == "image/png", asset
        else:  # pragma: no cover - update this contract when adding an asset kind
            pytest.fail(f"unasserted static asset type: {asset}")


def test_all_copilot_contexts_have_registered_pages_and_new_role_nav_links(
    dashboard_client: TestClient,
) -> None:
    from maverick_dashboard import copilot_context, ui_visibility

    context_paths = {context.path for context in copilot_context._CONTEXTS}
    page_registry = {page["path"]: page for page in ui_visibility.PAGES}
    assert context_paths <= _registered_paths(dashboard_client.app)
    assert all(ui_visibility.page_for(path) is not None for path in context_paths)

    new_paths = set(GOVERNANCE_PAGES)
    expected_floors = {
        "/governance-agents": "view",
        "/ai-evidence-analysis": "view",
        "/assess/evidence-autopopulation": "view",
        "/administration/document-vault": "view",
        "/assurance/cloud-agents": "operate",
        "/privacy/drop": "view",
        "/privacy/data-discovery": "view",
        "/governance/inventory": "view",
        "/ai-entity-tasks": "view",
        "/consent/custom-indexes": "view",
        "/consent/bulk-jobs": "view",
        "/consent/publish-settings": "view",
        "/consent/global-opt-out": "view",
        "/third-party/contracts": "view",
        "/third-party/executive-reports": "view",
        "/compliance/continuous-evidence": "operate",
    }
    for path in new_paths:
        page = page_registry[path]
        assert page["floor"] == expected_floors[path]
        if path == "/administration/document-vault":
            expected_default = "admin"
        elif path in {
            "/consent/bulk-jobs",
            "/consent/publish-settings",
            "/consent/global-opt-out",
        }:
            expected_default = "audit"
        else:
            expected_default = expected_floors[path]
        assert page["default"] == expected_default
        assert page.get("in_nav", True) is True

    def nav_paths(role: str) -> set[str]:
        return {link["href"] for group in ui_visibility.nav_groups(role) for link in group["links"]}

    audit_only_paths = {
        "/consent/bulk-jobs",
        "/consent/publish-settings",
        "/consent/global-opt-out",
    }
    admin_only_paths = {"/administration/document-vault"}
    assert new_paths - audit_only_paths - admin_only_paths <= nav_paths("operator")
    assert new_paths <= nav_paths("admin")
    read_only_workbenches = {
        "/ai-evidence-analysis",
        "/assess/evidence-autopopulation",
        "/privacy/drop",
        "/privacy/data-discovery",
        "/governance/inventory",
        "/ai-entity-tasks",
        "/consent/custom-indexes",
        "/third-party/contracts",
        "/third-party/executive-reports",
    }
    assert read_only_workbenches <= nav_paths("auditor")
    assert read_only_workbenches <= nav_paths("viewer")
    assert "/consent/bulk-jobs" in nav_paths("auditor")
    assert "/consent/bulk-jobs" not in nav_paths("viewer")
    assert "/consent/publish-settings" in nav_paths("auditor")
    assert "/consent/publish-settings" not in nav_paths("viewer")
    assert "/consent/global-opt-out" in nav_paths("auditor")
    assert "/consent/global-opt-out" not in nav_paths("viewer")


def test_authenticated_ui_uses_resolved_shared_token_names() -> None:
    shared_css = (PACKAGE / "static" / "lightwork.css").read_text(encoding="utf-8")
    sources = {path: path.read_text(encoding="utf-8") for path in sorted(TEMPLATES.rglob("*.html"))}
    sources[PACKAGE / "static" / "lightwork.css"] = shared_css

    forbidden = re.compile(r"var\(\s*--(?:warning|font-display)\b")
    offenders = {
        str(path.relative_to(PACKAGE)): sorted(set(forbidden.findall(source)))
        for path, source in sources.items()
        if forbidden.search(source)
    }
    assert offenders == {}

    for token in ("--warn", "--font-serif", "--font-mono"):
        assert re.search(rf"{re.escape(token)}\s*:", shared_css), token
        assert any(f"var({token})" in source for source in sources.values()), token
