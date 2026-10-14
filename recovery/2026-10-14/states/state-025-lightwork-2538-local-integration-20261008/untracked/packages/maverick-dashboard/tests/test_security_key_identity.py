"""Security shows the same workspace-aware key selected by the runtime."""

from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from maverick.tenant import kms as tenant_kms
from maverick_dashboard import auth
from maverick_dashboard import product_operations as operations
from maverick_dashboard import product_operations_encryption as encryption
from maverick_dashboard import security_page_helpers as helpers


def test_workspace_key_identity_uses_the_runtime_tenant_precedence(monkeypatch):
    monkeypatch.setenv("MAVERICK_KMS_KEY_ID", "arn:aws:kms:region:account:key/server")
    monkeypatch.setattr(tenant_kms, "_kms_config_for", lambda tenant: {"provider": "aws"})
    monkeypatch.setattr(
        tenant_kms, "_tenant_own_key_id", lambda tenant: "arn:aws:kms:region:account:key/workspace"
    )
    monkeypatch.setattr(
        tenant_kms,
        "get_kms",
        lambda tenant: SimpleNamespace(key_id="arn:aws:kms:region:account:key/workspace"),
    )

    result = helpers.encryption_key_identity("workspace-7")

    assert result == {
        "status": "configured",
        "provider": "AWS KMS",
        "key_id": "arn:aws:kms:region:account:key/workspace",
        "source": "This workspace's settings",
    }


def test_installation_environment_key_is_labeled_only_when_runtime_selected_it(monkeypatch):
    key_id = "arn:aws:kms:region:account:key/server"
    monkeypatch.setenv("MAVERICK_KMS_KEY_ID", key_id)
    monkeypatch.setattr(tenant_kms, "_kms_config_for", lambda _tenant: {"provider": "aws"})
    monkeypatch.setattr(tenant_kms, "_tenant_own_key_id", lambda _tenant: None)
    monkeypatch.setattr(tenant_kms, "get_kms", lambda _tenant: SimpleNamespace(key_id=key_id))

    result = helpers.encryption_key_identity("workspace-7")

    assert result["key_id"] == key_id
    assert result["source"] == "The installation's environment setting"


def test_installation_saved_key_is_labeled_when_no_environment_key_is_set(monkeypatch):
    key_id = "arn:aws:kms:region:account:key/saved"
    monkeypatch.delenv("MAVERICK_KMS_KEY_ID", raising=False)
    monkeypatch.setattr(
        tenant_kms, "_kms_config_for", lambda _tenant: {"provider": "aws", "key_id": key_id}
    )
    monkeypatch.setattr(tenant_kms, "_tenant_own_key_id", lambda _tenant: None)
    monkeypatch.setattr(tenant_kms, "get_kms", lambda _tenant: SimpleNamespace(key_id=key_id))

    result = helpers.encryption_key_identity("workspace-7")

    assert result["key_id"] == key_id
    assert result["source"] == "The installation's saved settings"


def test_key_identity_is_unknown_when_runtime_and_config_disagree(monkeypatch):
    monkeypatch.setenv("MAVERICK_KMS_KEY_ID", "arn:aws:kms:region:account:key/server")
    monkeypatch.setattr(tenant_kms, "_kms_config_for", lambda _tenant: {"provider": "aws"})
    monkeypatch.setattr(tenant_kms, "_tenant_own_key_id", lambda _tenant: None)
    monkeypatch.setattr(
        tenant_kms,
        "get_kms",
        lambda _tenant: SimpleNamespace(key_id="arn:aws:kms:region:account:key/saved"),
    )

    result = helpers.encryption_key_identity("workspace-7")

    assert result["status"] == "unknown"
    assert result["key_id"] == ""


def test_unavailable_cloud_backend_never_reports_a_key_as_confirmed(monkeypatch):
    monkeypatch.setenv("MAVERICK_KMS_KEY_ID", "arn:aws:kms:region:account:key/server")
    monkeypatch.setattr(tenant_kms, "_kms_config_for", lambda _tenant: {"provider": "aws"})
    monkeypatch.setattr(tenant_kms, "_tenant_own_key_id", lambda _tenant: None)

    def unavailable(_tenant):
        raise RuntimeError("provider setup is incomplete")

    monkeypatch.setattr(tenant_kms, "get_kms", unavailable)

    result = helpers.encryption_key_identity("workspace-7")

    assert result == {
        "status": "unknown",
        "provider": "AWS KMS",
        "key_id": "",
        "source": "",
    }


def test_local_key_view_never_exposes_the_server_cloud_key(monkeypatch):
    monkeypatch.setenv("MAVERICK_KMS_KEY_ID", "arn:aws:kms:region:account:key/server")
    monkeypatch.setattr(tenant_kms, "_kms_config_for", lambda _tenant: {"provider": "local"})

    result = helpers.encryption_key_identity("workspace-7")

    assert result["status"] == "local"
    assert result["key_id"] == ""
    assert "server" not in str(result)


def test_security_page_displays_the_key_and_links_to_settings(monkeypatch):
    import importlib

    from maverick import security_ops
    from maverick_dashboard import immune_actions_api

    dashboard = importlib.import_module("maverick_dashboard.app")
    monkeypatch.setattr(dashboard, "require_permission", lambda *_args: None)
    monkeypatch.setattr(
        dashboard,
        "_workspace_ctx",
        lambda *_args, **_kwargs: {
            "sessions": [],
            "worklist": [],
            "stats": {
                "total": 0,
                "pending": 0,
                "needs_more": 0,
                "high_residual": 0,
                "review_due": 0,
                "mine": 0,
                "unassigned": 0,
                "oldest_open_days": None,
            },
            "department": "security",
            "precedents": [],
            "templates": [],
        },
    )
    monkeypatch.setattr(dashboard, "has_global_permission", lambda *_args: False)
    monkeypatch.setattr(security_ops, "enabled", lambda: False)
    monkeypatch.setattr(immune_actions_api, "people_choices", list)
    monkeypatch.setattr(
        helpers,
        "encryption_key_identity",
        lambda _tenant: {
            "status": "configured",
            "provider": "AWS KMS",
            "key_id": "arn:aws:kms:region:account:key/workspace",
            "source": "This workspace's settings",
        },
    )

    response = TestClient(dashboard.app).get("/security")

    assert response.status_code == 200
    assert "Workspace data protection" in response.text
    assert "arn:aws:kms:region:account:key/workspace" in response.text
    assert "/settings/all?group=security" in response.text


def _copilot_client(monkeypatch, require_permission):
    from maverick import paths

    monkeypatch.setattr(operations, "_same_origin", lambda _request: None)
    monkeypatch.setattr(operations, "_read_gate", lambda _request: None)
    monkeypatch.setattr(operations, "_context", lambda _request: "a" * 64)
    monkeypatch.setattr(operations, "enabled_families", lambda: {"preferences"})
    monkeypatch.setattr(
        operations,
        "adapters",
        lambda: {entry.id: entry for entry in encryption.entries()},
    )
    monkeypatch.setattr(auth, "require_permission", require_permission)
    monkeypatch.setattr(paths, "current_tenant_id", lambda: "workspace-7")
    monkeypatch.setattr(
        helpers,
        "encryption_key_identity",
        lambda _tenant: {
            "status": "configured",
            "provider": "AWS KMS",
            "key_id": "arn:aws:kms:region:account:key/workspace",
            "source": "This workspace's settings",
        },
    )
    app = FastAPI()
    app.include_router(operations.router)
    return TestClient(app)


def test_copilot_can_read_the_workspace_key_only_with_operate_access(monkeypatch):
    checked_permissions = []

    def require(_request, permission):
        checked_permissions.append(permission)

    client = _copilot_client(monkeypatch, require)
    with client:
        catalog = client.post(
            "/api/v1/copilot/product-operations/catalog",
            json={"page": "/security", "selection": {}},
        )
        assert catalog.status_code == 200, catalog.text
        action = next(
            item
            for item in catalog.json()["operations"]
            if item["id"] == "preferences.encryption_workspace_key"
        )
        assert action["available"] is True
        assert action["confirmation_required"] is False

        prepared = client.post(
            "/api/v1/copilot/product-operations/prepare",
            json={
                "page": "/security",
                "selection": {},
                "operation": action["id"],
                "context": catalog.json()["context"],
                "revision": action["revision"],
                "arguments": {},
            },
        )
        assert prepared.status_code == 200, prepared.text
        assert prepared.json()["read_only"] is True
        assert prepared.json()["details"]["Key selected for this workspace"].endswith(
            "key/workspace"
        )
        assert prepared.json()["details"]["Key service"] == "AWS KMS"
        assert prepared.json()["details"]["Provider check"].startswith("Not performed")
        assert "does not confirm" in prepared.json()["summary"]

    assert checked_permissions.count("operate") >= 3


def test_copilot_hides_workspace_key_from_users_without_operate_access(monkeypatch):
    def require(_request, permission):
        if permission == "operate":
            raise HTTPException(403, "operate access required")

    client = _copilot_client(monkeypatch, require)
    response = client.post(
        "/api/v1/copilot/product-operations/catalog",
        json={"page": "/security", "selection": {}},
    )

    assert response.status_code == 200
    assert "preferences.encryption_workspace_key" not in response.text
    assert "arn:aws:kms:region:account:key/workspace" not in response.text
