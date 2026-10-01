"""Admin pages router: /settings and /users (pages + form handlers).

The first slice of decomposing ``app.py`` by nav group: everything on this
router is the site admin's control surface (RBAC "admin" permission via
``auth.require_permission`` for tenant-scoped administration and
``auth.require_global_permission`` for global control-plane writes),
same-origin-checked on every mutation, and rendered with the shared app
templates.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from . import _shared, auth, step_up

router = APIRouter()


def _app():
    """The dashboard app module, resolved lazily.

    This router is included while ``maverick_dashboard.app`` is still
    executing, so a top-level ``from .app import templates`` would race that
    module's initialization; by request time it is fully loaded.
    """
    from . import app as app_module
    return app_module


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = "") -> HTMLResponse:
    """Operator settings: appearance (theme/density/font/language) + which model
    Lightwork uses by default. Appearance is applied via the existing
    persist_theme middleware (the form GETs back here with the params); the
    model choice is saved to the dashboard-owned runtime overlay."""
    return _settings_response(request, saved)


def _api_keys_enabled() -> bool:
    try:
        from . import api_keys

        return api_keys.enabled()
    except Exception:
        return False


def _settings_response(request: Request, saved: str = "", *, notice: str = "",
                       status_code: int = 200) -> HTMLResponse:
    """The settings page; ``notice`` is a message shown instead of "Saved"
    (a change waiting for other administrators' approval)."""
    # Every form on this page changes deployment-wide process configuration.
    # A tenant-local admin must not see an apparently actionable console whose
    # POSTs all fail the correct global gate.
    auth.require_global_permission(request, "admin")
    import re as _re

    from maverick.llm import catalog_specs, model_for_role
    from maverick.runner import DEFAULT_MAX_DOLLARS
    from maverick.runtime_overrides import (
        allowed_models,
        budget_override,
        default_model_override,
        role_model_override,
        task_limits_override,
    )
    roles = [
        ("Orchestrator", "orchestrator"), ("Coder", "coder"),
        ("Researcher", "researcher"), ("Writer", "writer"),
        ("Analyst", "analyst"), ("Verifier", "verifier"),
        ("Summarizer", "summarizer"),
    ]
    role_models = [
        (label, role, model_for_role(role), role_model_override(role))
        for label, role in roles
    ]
    model_options = list(catalog_specs())
    seen = {s for s, _ in model_options}
    try:  # admins extend the picker via [models] catalog in config.toml
        from maverick.config import load_config
        for spec in (load_config().get("models", {}) or {}).get("catalog") or []:
            s = str(spec).strip()
            if s and s not in seen and _re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", s):
                model_options.append((s, "Custom (config)"))
                seen.add(s)
    except Exception:  # pragma: no cover -- config read fails soft
        pass
    # Admin allow-list: when set, every model picker is capped to it (and the
    # resolver enforces it as a hard cap). Empty = no restriction. The checkbox
    # section lets the admin pick which catalogue models everyone may use; the
    # default/per-role pickers then offer only those.
    allow = allowed_models()
    label_by_spec = dict(model_options)
    allow_options = [(s, label_by_spec.get(s, "Allowed")) for s in sorted(allow)]
    picker_options = allow_options if allow else model_options
    from maverick_dashboard import settings_store
    from maverick_dashboard.integration_readiness import (
        IntegrationConfigurationUnavailable,
        provider_readiness,
        require_readable_config,
    )

    try:
        require_readable_config()
        cfg_state = settings_store.state()
        if any(provider.get("source_errors") for provider in cfg_state["providers"]):
            raise IntegrationConfigurationUnavailable(
                "provider configuration source changed while it was read"
            )
    except IntegrationConfigurationUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="integration configuration is unavailable",
        ) from exc

    for provider in cfg_state["providers"]:
        provider["readiness"] = provider_readiness(
            provider["name"], configured=provider["configured"]
        )
    # The one truth about whether an AI service is connected: the header,
    # each provider row and every other page read the same verdict.
    from maverick_dashboard.provider_readiness import apply_to_rows, workspace_readiness

    workspace = workspace_readiness()
    apply_to_rows(cfg_state["providers"], workspace)
    # Grouped pickers: every model the configured keys can reach (cached live
    # discovery) plus the shipped catalog, capped by the allow-list.
    from maverick_dashboard import model_pickers
    pickers = model_pickers.build(cfg_state["providers"], allow)
    groups_by_name = {g["name"]: g for g in pickers["model_groups"]}
    from maverick_dashboard import plain_labels
    for provider in cfg_state["providers"]:
        provider["discovery"] = groups_by_name.get(provider["name"])
        provider["key_help"] = plain_labels.PROVIDER_KEY_HELP.get(provider["name"], "")
    for feature in cfg_state["features"]:
        feature["info"] = plain_labels.feature_help(feature["name"], feature.get("info") or "")
    saved_msg = {
        "appearance": "Appearance updated.",
        "models": "Default model updated.",
        "roles": "Role models updated.",
        "budget": "Task spending limit updated.",
        "allowed": "Approved models updated.",
        "discover": "Model lists refreshed from your provider keys.",
        "providers": "Provider keys updated.",
        "provider_test": "Connection test finished. The result is shown next to the provider.",
        "webhooks": "Secret for other apps updated.",
        "discovery": "Automatic model lists updated.",
        "capabilities": "Capabilities updated.",
        "features": "Features updated.",
        "visibility": "Page visibility updated.",
        "visibility_reset": "Page visibility reset to defaults.",
    }.get(saved, "")
    saved_section = {
        "appearance": "general",
        "budget": "general",
        "models": "models",
        "roles": "models",
        "allowed": "models",
        "discover": "models",
        "discovery": "models",
        "providers": "integrations",
        "provider_test": "integrations",
        "webhooks": "integrations",
        "capabilities": "controls",
        "features": "controls",
        "visibility": "access",
        "visibility_reset": "access",
    }.get(saved, "")
    from maverick_dashboard import rbac, ui_visibility

    from .webhook_settings import WebhookSettingsConflict, WebhookSettingsUnavailable
    from .webhook_settings import state as webhook_state

    # Render only the safe current state; the browser obtains a fresh reviewed
    # generation before it can submit any change.
    try:
        webhook_configured = webhook_state(
            principal=auth.caller_principal(request) or "local-operator",
            authorize=lambda: auth.require_global_permission(request, "admin"),
        )["configured"]
    except (WebhookSettingsConflict, WebhookSettingsUnavailable):
        webhook_configured = None
    return _app().templates.TemplateResponse(request, "settings.html", {
        "visibility_matrix": ui_visibility.matrix(),
        "visibility_roles": list(rbac.known_roles()),
        "role_models": role_models,
        "model_options": model_options,
        "picker_options": picker_options,
        "allowed_models": sorted(allow),
        "pinned_model": default_model_override(),
        **pickers,
        "providers": cfg_state["providers"],
        "workspace": workspace,
        "capabilities": cfg_state["capabilities"],
        "features": cfg_state["features"],
        "budget": budget_override(),
        "default_budget": DEFAULT_MAX_DOLLARS,
        "task_limits_enabled": (task_limits_override() if task_limits_override() is not None
                                else (load_config().get("budget") or {}).get("task_limits_enabled", True)),
        "discovery_locked": bool(__import__("os").environ.get("MAVERICK_MODEL_DISCOVERY", "").strip()),
        "webhook_configured": webhook_configured,
        # The static server token is the break-glass credential: it has no
        # person behind it and no scopes, so Settings says so while it is set.
        "static_token_set": bool(_shared.dashboard_token()),
        "api_keys_enabled": _api_keys_enabled(),
        "saved": saved_msg,
        "saved_section": saved_section,
        "notice": notice,
    }, status_code=status_code)


@router.post("/settings/models")
async def settings_set_model(request: Request) -> RedirectResponse:
    """Pin (or clear) the dashboard's default model via the runtime overlay.

    ``model`` is the picker value; when it is the custom marker the typed
    ``model_custom`` id is used instead. An empty value clears the pin,
    reverting to config.toml / built-in defaults. config.toml is never
    written."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick.runtime_overrides import (
        allowed_models,
        clear_default_model,
        set_default_model,
    )

    from maverick_dashboard.model_pickers import chosen
    model = chosen(await request.form(), "model")
    allow = await run_in_threadpool(allowed_models)
    if model and allow and model not in allow:
        raise HTTPException(status_code=400, detail="model not in the allowed list")
    try:
        if model:
            await run_in_threadpool(set_default_model, model)
        else:
            await run_in_threadpool(clear_default_model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid model id") from exc
    return RedirectResponse("/settings?saved=models", status_code=303)


@router.post("/settings/budget")
def settings_set_budget(request: Request, max_dollars: str = Form(""),
                        mode: str = Form("configured")) -> Response:
    """Set (or clear) the dashboard's per-goal spend cap via the runtime
    overlay. An empty value clears it, reverting to config.toml / defaults.
    config.toml is never written."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick.runtime_overrides import clear_budget, set_budget, set_task_limits
    val = (max_dollars or "").strip()
    try:
        wanted = _budget_choice(mode, val)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="spend cap must be a positive number",
        ) from exc
    # [safety] quorum_required: the per-task spending limit is a protected
    # setting by default, so a change waits for other administrators'
    # approval exactly as on All settings (settings_quorum).
    approved = None
    current = _budget_now()
    if wanted != current:
        from . import settings_quorum

        actor = auth.caller_principal(request) or "local administrator"
        change = [{"setting": _BUDGET_SETTING, "old": current, "new": wanted}]
        hold, approved = settings_quorum.review(change, actor, lambda _s, value: value)
        if hold is not None:
            return _held(request, hold)
    try:
        if mode == "disabled":
            set_task_limits(False)
        elif mode == "configured" and val:
            set_budget(float(val))
        else:
            # "default" is the page's "use your installation's standard
            # limits" choice: drop the Settings amount, keep limits on.
            clear_budget()
            set_task_limits(True)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="spend cap must be a positive number",
        ) from exc
    if approved is not None:
        from . import settings_quorum

        settings_quorum.settle(approved)
    return RedirectResponse("/settings?saved=budget", status_code=303)


# The per-task spending limit as the quorum sees it: one protected setting
# (``budget.max_dollars`` in [safety] quorum_protected_keys), whatever the
# form's mode, so switching limits off is protected too.
_BUDGET_SETTING = SimpleNamespace(id="budget.max_dollars", label="Spending limit per task")


def _budget_choice(mode: str, amount: str) -> str:
    """What the form asks for, in the words approvers see."""
    if mode == "disabled":
        return "No limit for each task"
    if mode == "configured" and amount:
        value = float(amount)
        if not 0 < value < float("inf"):
            raise ValueError("spend cap must be positive")
        return f"Stop a task after ${value:,.2f}"
    if mode in ("configured", "default"):
        return "Your installation's standard limits"
    raise ValueError("unknown task limit policy")


def _budget_now() -> str:
    from maverick.config import load_config
    from maverick.runtime_overrides import budget_override, task_limits_override

    enabled = task_limits_override()
    if enabled is None:
        enabled = (load_config().get("budget") or {}).get("task_limits_enabled", True)
    if not enabled:
        return "No limit for each task"
    amount = budget_override()
    return (f"Stop a task after ${amount:,.2f}" if amount is not None
            else "Your installation's standard limits")


def _held(request: Request, hold: dict):
    """202: the change waits for approval. JSON for scripts, the settings
    page with the message for the browser form."""
    from fastapi.responses import JSONResponse

    if "application/json" in (request.headers.get("accept") or ""):
        return JSONResponse({"changed": [], "quorum": hold, "message": hold["message"]},
                            status_code=202, headers={"Cache-Control": "no-store"})
    return _settings_response(request, notice=hold["message"], status_code=202)


@router.post("/settings/models/roles")
async def settings_set_role_models(request: Request) -> RedirectResponse:
    """Set/clear per-role model pins from the settings page in one write. Each
    form field is named for a role; an empty value clears that role's pin."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick.runtime_overrides import allowed_models, set_role_models
    roles = ("orchestrator", "coder", "researcher", "writer",
             "analyst", "verifier", "summarizer")
    from maverick_dashboard.model_pickers import chosen
    form = await request.form()
    updates = {r: chosen(form, r) for r in roles}
    allow = await run_in_threadpool(allowed_models)
    if allow:
        for spec in updates.values():
            if spec and spec not in allow:
                raise HTTPException(
                    status_code=400, detail="model not in the allowed list")
    try:
        await run_in_threadpool(set_role_models, updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid model id") from exc
    return RedirectResponse("/settings?saved=roles", status_code=303)

@router.post("/settings/models/allowed")
async def settings_set_allowed_models(request: Request) -> RedirectResponse:
    """Set the admin model allow-list from the settings page. Each checked
    ``models`` field is an allowed spec; none checked clears the restriction
    (every model allowed again). Saved to the dashboard overlay, never
    config.toml; ``llm.model_for_role`` then caps every role to this set."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick.runtime_overrides import set_allowed_models
    form = await request.form()
    try:
        await run_in_threadpool(set_allowed_models, form.getlist("models"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid model id") from exc
    return RedirectResponse("/settings?saved=allowed", status_code=303)


@router.post("/settings/models/discover")
async def settings_discover_models(request: Request, provider: str = Form("")) -> RedirectResponse:
    """Ask every configured provider (or one) which models its key can reach and
    cache the answer for the pickers. Bounded live requests; nothing is pinned."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick import model_discovery
    from maverick.providers import KNOWN_PROVIDERS, _canonical
    name = _canonical(provider.strip()) if provider.strip() else ""
    if name and name not in KNOWN_PROVIDERS:
        raise HTTPException(status_code=400, detail="unknown provider")
    await run_in_threadpool(model_discovery.refresh, [name] if name else None)
    return RedirectResponse("/settings?saved=discover", status_code=303)


def _refresh_models_quietly(provider: str) -> None:
    """Refresh one provider's reachable-model list after its key changed; a
    discovery failure is recorded in the cache, never surfaced as a save error."""
    try:
        from maverick import model_discovery
        model_discovery.refresh([provider])
    except Exception:  # pragma: no cover -- best effort by design
        pass


def _webhook_operation(request: Request, operation):
    from .webhook_settings import WebhookSettingsConflict, WebhookSettingsUnavailable

    auth.require_global_permission(request, "admin")
    principal = auth.caller_principal(request) or "local-operator"
    try:
        return operation(principal, lambda: auth.require_global_permission(request, "admin"))
    except WebhookSettingsConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WebhookSettingsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/settings/webhooks")
def settings_get_webhooks(request: Request) -> dict:
    """Current effective ownership and an opaque, caller-bound review token."""
    from .webhook_settings import state
    return _webhook_operation(request, lambda principal, authorize:
        state(principal=principal, authorize=authorize))


@router.post("/settings/webhooks")
def settings_set_webhooks(
    request: Request,
    secret: str = Form(""),
    expected_generation: str = Form(""),
    action: str = Form("set"),
) -> dict:
    """Confirm a reviewed rotation against the actual runtime secret resolver."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick_dashboard import settings_store
    return _webhook_operation(request, lambda principal, authorize:
        settings_store.set_webhooks_secret(secret, action=action,
            expected_generation=expected_generation, principal=principal, authorize=authorize))


@router.post("/settings/models/discovery")
def settings_set_model_discovery(request: Request, enabled: str = Form("on")) -> RedirectResponse:
    """Turn automatic model lists on or off from the Models section (admin)."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    import os as _os

    from maverick_dashboard import settings_store
    if _os.environ.get("MAVERICK_MODEL_DISCOVERY", "").strip():
        raise HTTPException(
            status_code=409,
            detail="Automatic model lists are controlled by your installation, so this can't be changed here.",
        )
    try:
        settings_store.set_model_discovery(enabled == "on")
    except (OSError, settings_store.SecuritySuiteConfigUnavailable) as exc:
        raise HTTPException(
            status_code=503,
            detail="Settings are unavailable. Retry after storage is restored.",
        ) from exc
    try:
        from maverick import config as _config
        _config.reset_config_cache()
    except Exception:  # pragma: no cover -- cache reset is best effort
        pass
    return RedirectResponse("/settings?saved=discovery", status_code=303)


@router.post("/settings/providers")
async def settings_set_provider(
    request: Request,
    provider: str = Form(...),
    api_key: str = Form(""),
    base_url: str = Form(""),
) -> RedirectResponse:
    """Rotate only provider fields this dashboard can make effective.

    Tenant and operator overlays outrank the dashboard overlay. Refuse those
    rotations with the exact controlling source, then verify the effective
    credential generation before reporting success.
    """
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick_dashboard import settings_store
    provider_name = provider.strip()
    key_value = api_key.strip()
    base_value = base_url.strip()
    try:
        await run_in_threadpool(
            settings_store.rotate_provider,
            provider_name,
            api_key=key_value,
            base_url=base_value,
        )
    except settings_store.ProviderConfigurationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except settings_store.SecuritySuiteConfigUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except settings_store.ProviderRotationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await run_in_threadpool(_refresh_models_quietly, provider_name)
    return RedirectResponse("/settings?saved=providers", status_code=303)


@router.post("/settings/providers/clear")
def settings_clear_provider(request: Request, provider: str = Form(...)) -> RedirectResponse:
    """Remove a provider's dashboard-set key (config.toml / env unaffected)."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick_dashboard import settings_store
    try:
        settings_store.clear_provider(provider.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown provider") from exc
    return RedirectResponse("/settings?saved=providers", status_code=303)


@router.post("/settings/providers/test")
async def settings_test_provider(
    request: Request, provider: str = Form(...), next: str = Form("")
) -> RedirectResponse:
    """Run a bounded live provider probe and bind its receipt to this config."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick import config

    from maverick_dashboard.integration_readiness import test_provider

    name = provider.strip().lower()
    try:
        state = await run_in_threadpool(config.provider_configuration_state, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown provider") from exc
    if not state["configured"]:
        raise HTTPException(status_code=409, detail="provider is not configured")
    await run_in_threadpool(test_provider, name)
    await run_in_threadpool(_refresh_models_quietly, name)
    if next == "providers":  # the Providers page's own Test connection button
        return RedirectResponse("/providers?tested=1", status_code=303)
    return RedirectResponse("/settings?saved=provider_test", status_code=303)


@router.post("/settings/capabilities")
async def settings_set_capabilities(request: Request) -> RedirectResponse:
    """Activate/deactivate capabilities via the dashboard config overlay."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick_dashboard import settings_store
    form = await request.form()
    try:
        await run_in_threadpool(settings_store.set_toggles, "capabilities", {
            name: name in form for name in settings_store.CAPABILITY_DEFAULTS
        })
    except (OSError, settings_store.SecuritySuiteConfigUnavailable) as exc:
        raise HTTPException(
            status_code=503,
            detail="Settings are unavailable. Retry after storage is restored.",
        ) from exc
    return RedirectResponse("/settings?saved=capabilities", status_code=303)


@router.post("/settings/features")
async def settings_set_features(request: Request) -> RedirectResponse:
    """Activate/deactivate features via the dashboard config overlay."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick_dashboard import settings_store
    form = await request.form()
    selected = {name: name in form for name in settings_store.FEATURE_DEFAULTS}
    try:
        await run_in_threadpool(settings_store.set_toggles, "features", selected)
        await run_in_threadpool(
            settings_store.assert_effective_copilot_operations,
            selected["copilot_operations"],
        )
    except settings_store.FeatureSettingNotEffective as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, settings_store.SecuritySuiteConfigUnavailable) as exc:
        raise HTTPException(
            status_code=503,
            detail="Settings are unavailable. Retry after storage is restored.",
        ) from exc
    return RedirectResponse("/settings?saved=features", status_code=303)


@router.post("/settings/ui-visibility")
async def settings_set_ui_visibility(request: Request) -> RedirectResponse:
    """Save the page-visibility matrix (admin). Every eligible, unlocked
    page x role checkbox is submitted as ``cell=role:path``; an absent cell is
    hidden. Only differences from the defaults are stored (0600 JSON, never
    config.toml); a role can never be shown a page its permissions don't reach,
    and admins always keep Settings + Users."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick_dashboard import ui_visibility
    form = await request.form()
    await run_in_threadpool(ui_visibility.apply_selection, set(form.getlist("cell")))
    return RedirectResponse("/settings?saved=visibility#ui-visibility", status_code=303)


@router.post("/settings/ui-visibility/reset")
def settings_reset_ui_visibility(request: Request) -> RedirectResponse:
    """Reset every page x role cell to its default visibility (admin)."""
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick_dashboard import ui_visibility
    ui_visibility.clear_overrides()
    return RedirectResponse("/settings?saved=visibility_reset#ui-visibility", status_code=303)


def _invite_labels(all_invites) -> dict[str, str]:
    """Email per sign-in id for people who arrived through an accepted invite.

    An accepted invite is the one record that ties the address the owner typed
    to the federated subject the person actually signed in with, so the roster
    can read as people instead of provider id fragments."""
    labels: dict[str, str] = {}
    for inv in all_invites:
        if inv.used_by and inv.email:
            labels.setdefault(inv.used_by, inv.email)
    return labels


def _workspace_options(request: Request) -> tuple[list[dict], str]:
    """Active workspaces the caller may assign members to, and the one the
    Members section should start on (the caller's current selection when it is
    among them, else the first). Global admins see every active workspace;
    anyone else only the workspaces they hold a membership in."""
    principal = auth.caller_principal(request)
    options: list[dict] = []
    try:
        from maverick.tenant.registry import list_tenants

        from . import workspace_tenants
        visible = None
        if principal and not auth.has_global_permission(request, "admin"):
            visible = workspace_tenants.accessible_tenant_ids(principal)
        options = [
            {"id": rec.id, "name": (getattr(rec, "display_name", "") or rec.id)}
            for rec in list_tenants()
            if rec.active and (visible is None or rec.id in visible)
        ]
    except Exception:
        options = []
    try:
        from maverick.paths import current_tenant_id
        current = current_tenant_id() or ""
    except Exception:
        current = ""
    ids = [w["id"] for w in options]
    start = current if current in ids else (ids[0] if ids else "")
    return options, start


def _signed_in_principals(limit: int = 200) -> list[str]:
    """People who have signed in with company sign-in, most recent first."""
    try:
        from . import subject_directory
        index = subject_directory.load_index()
    except Exception:
        return []
    latest: dict[str, float] = {}
    for entry in index.values():
        sub = str(entry.get("sub") or "").strip()
        if sub:
            latest[sub] = max(latest.get(sub, 0.0), float(entry.get("ts") or 0.0))
    ordered = sorted(latest, key=lambda sub: latest[sub], reverse=True)[:limit]
    return [sub if sub.startswith("user:") else f"user:{sub}" for sub in ordered]


def _people(users: dict, labels: dict, extra) -> list[dict]:
    """Everyone the roster knows about, as choices for the people pickers.

    Sources: explicit role holders, accepted invites, department grants,
    installation administrators and people who have signed in. Each entry
    carries a readable label; the exact sign-in identity is only the value."""
    from .identity_format import humanize_principal
    seen: dict[str, dict] = {}
    for principal in [*users, *labels, *extra]:
        principal = str(principal or "").strip()
        if not principal.startswith("user:") or principal in seen:
            continue
        ident = humanize_principal(principal)
        name = labels.get(principal) or ident.name
        detail = "" if labels.get(principal) else ident.detail
        seen[principal] = {"principal": principal,
                           "label": f"{name} ({detail})" if detail and detail != "Email" else name,
                           "role": users.get(principal)}
    return sorted(seen.values(), key=lambda p: p["label"].lower())


def _users_context(request: Request) -> dict:
    """Template context for the /users page (shared with the invite handler)."""
    import os as _os

    from maverick.oidc import oidc_enabled
    from maverick.proxy_auth import proxy_auth_enabled

    from . import rbac
    auth_on = (auth.caller_principal(request) is not None
               or oidc_enabled() or proxy_auth_enabled())
    env = (_os.environ.get("MAVERICK_DASHBOARD_ADMINS") or "").strip()
    if env:
        bootstrap = [a.strip() for a in env.split(",") if a.strip()]
    else:
        try:
            from maverick.config import load_config
            raw = (load_config().get("dashboard", {}) or {}).get("admins", []) or []
            seq = raw if isinstance(raw, (list, tuple)) else [raw]
            bootstrap = [str(a).strip() for a in seq if str(a).strip()]
        except Exception:
            bootstrap = []
    saved = {"set": "Role updated.", "removed": "User removed.",
             "suites": "Department access updated.",
             "suites_removed": "Department access removed.",
             "invite_revoked": "Invitation revoked.",
             "role_saved": "Custom role saved.",
             "role_deleted": "Custom role deleted."}.get(
        request.query_params.get("saved", ""), "")
    from maverick.departments import department_title

    from . import invites, suite_grants
    default_suites = suite_grants.default_suites()
    try:
        custom_roles = sorted(rbac.list_custom_roles().items())
    except rbac.RbacStoreError:
        custom_roles = []
    try:
        all_invites = invites.list_invites()
    except Exception:
        all_invites = []
    workspace_options, wm_tenant = _workspace_options(request)
    role_map = rbac.list_users()
    labels = _invite_labels(all_invites)
    grants = suite_grants.list_grants()
    people = _people(role_map, labels, [*grants, *bootstrap, *_signed_in_principals()])
    from .identity_format import role_label
    defaults = set(default_suites) if default_suites is not None else None
    return {
        "people": people,
        "people_labels": {p["principal"]: p["label"] for p in people},
        "role_labels": {r: role_label(r) for r in rbac.known_roles()},
        "grant_can_use_default": {
            principal: defaults is not None and defaults.issubset(set(suites))
            for principal, suites in grants.items()
        },
        "users": sorted(role_map.items()),
        "roles": rbac.known_roles(),
        "builtin_roles": rbac.ROLES,
        "custom_roles": custom_roles,
        "permission_atoms": rbac.PERMISSION_ATOMS,
        "bootstrap_admins": sorted(bootstrap),
        "default_role": rbac.default_role(),
        "auth_on": auth_on,
        "you": auth.caller_principal(request),
        "saved": saved,
        "suite_grants": sorted(grants.items()),
        "all_suites": sorted(((k, department_title(k))
                              for k in suite_grants.known_suites()),
                             key=lambda kv: kv[1]),
        "default_suites": (sorted(default_suites)
                           if default_suites is not None else None),
        "invites_on": invites.invites_enabled(),
        "pending_invites": [i for i in all_invites if i.pending],
        "invite_labels": labels,
        "workspace_options": workspace_options,
        "wm_tenant": wm_tenant,
    }


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request) -> HTMLResponse:
    """Admin: manage dashboard user roles (admin / operator / auditor / viewer)."""
    auth.require_permission(request, "admin")
    return _app().templates.TemplateResponse(request, "users.html",
                                             _users_context(request))


@router.post("/users/set")
def users_set_role(
    request: Request, principal: str = Form(...), role: str = Form(...),
) -> RedirectResponse:
    """Assign a dashboard role to a user principal (admin only)."""
    auth.require_global_permission(request, "admin")
    _app()._require_same_origin(request)
    step_up.require_recent_mfa(request, "change_role")
    from . import rbac
    try:
        rbac.set_role(principal.strip(), role.strip(),
                      actor=auth.caller_principal(request) or "local")
    except rbac.RecoveryAuthorityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid principal or role") from exc
    return RedirectResponse("/users?saved=set", status_code=303)


@router.post("/users/remove")
def users_remove(request: Request, principal: str = Form(...)) -> RedirectResponse:
    """Remove a user's explicit role assignment (admin only). A bootstrap admin
    pinned in config is unaffected — it can't be removed here."""
    auth.require_global_permission(request, "admin")
    _app()._require_same_origin(request)
    step_up.require_recent_mfa(request, "change_role")
    from . import rbac
    try:
        rbac.remove_user(principal.strip(),
                         actor=auth.caller_principal(request) or "local")
    except rbac.RecoveryAuthorityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse("/users?saved=removed", status_code=303)


@router.post("/users/roles/save")
def users_save_custom_role(
    request: Request,
    name: str = Form(...),
    permissions: list[str] = Form([]),
    description: str = Form(""),
) -> RedirectResponse:
    """Create or update a custom role (admin only). Permission checkboxes come
    in as repeated form fields; "view" is always granted (rbac enforces it),
    so the form only offers the operate/audit/admin atoms on top."""
    auth.require_global_permission(request, "admin")
    _app()._require_same_origin(request)
    from . import rbac
    atoms = {str(p).strip() for p in permissions if str(p).strip()}
    atoms.add("view")
    # People type a role name ("Privacy officer"); the stored key is derived
    # from it (privacy-officer), so saving the same name again updates it.
    from .plain_labels import slugify
    key = slugify(name, max_len=32)
    if key and not key[0].isalpha():
        key = ("role-" + key)[:32]
    if len(key) < 2:
        raise HTTPException(status_code=400, detail="Give the role a name of at least two letters.")
    try:
        rbac.set_custom_role(
            key,
            sorted(atoms),
            description.strip(),
            actor=auth.caller_principal(request) or "local",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/users?saved=role_saved", status_code=303)


@router.post("/users/roles/delete")
def users_delete_custom_role(
    request: Request, name: str = Form(...)
) -> RedirectResponse:
    """Delete a custom role (admin only). Refused while anyone still holds it."""
    auth.require_global_permission(request, "admin")
    _app()._require_same_origin(request)
    from . import rbac
    try:
        rbac.delete_custom_role(
            name.strip(), actor=auth.caller_principal(request) or "local"
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/users?saved=role_deleted", status_code=303)


@router.post("/users/invite", response_class=HTMLResponse)
def users_invite(request: Request, email: str = Form(...),
                 role: str = Form(...)) -> HTMLResponse:
    """Mint a single-use invite link (admin only) and show it ONCE.

    The token is never stored (only its hash), so this response is the only
    place the link exists — the admin copies it into an email/chat to the
    invitee. Renders the users page directly instead of redirecting, because a
    redirect would drop the one-time link."""
    auth.require_global_permission(request, "admin")
    _app()._require_same_origin(request)
    from . import invites
    if not invites.invites_enabled():
        raise HTTPException(status_code=404, detail="invites are not enabled")
    invited_by = auth.caller_principal(request) or "local-admin"
    try:
        inv, token = invites.create_invite(email, role, created_by=invited_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    link = str(request.base_url).rstrip("/") + f"/auth/invite/{token}"
    # Best-effort email (fail-soft): the copyable link below is always the
    # fallback, so a broken SMTP account can never strand an invite.
    sent, send_note = invites.send_invite_email(inv, link, invited_by=invited_by)
    return _app().templates.TemplateResponse(request, "users.html", {
        **_users_context(request), "invite_link": link, "invite_email": inv.email,
        "invite_sent": sent, "invite_send_note": send_note,
    })


@router.post("/users/invite/revoke")
def users_invite_revoke(request: Request,
                       invite_id: str = Form(...)) -> RedirectResponse:
    """Revoke a pending invite link (admin only)."""
    auth.require_global_permission(request, "admin")
    _app()._require_same_origin(request)
    from . import invites
    invites.revoke_invite(invite_id.strip())
    return RedirectResponse("/users?saved=invite_revoked", status_code=303)


@router.post("/users/suites/set")
async def users_set_suites(request: Request) -> RedirectResponse:
    """Scope a user to a set of departments (global admin only).

    The grant store is GLOBAL control-plane data (like the role roster), so
    this requires the global admin role — a tenant-local admin may not scope
    users across the deployment. An empty selection is a valid grant meaning
    "no departments". Removing an override is allowed only when the configured
    default is no broader than the current grant."""
    auth.require_global_permission(request, "admin")
    _app()._require_same_origin(request)
    from . import suite_grants
    form = await request.form()
    principal = str(form.get("principal", "")).strip()
    suites = [str(s) for s in form.getlist("suites")]
    try:
        await run_in_threadpool(
            suite_grants.set_suites, principal, suites,
            actor=auth.caller_principal(request) or "local",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid principal or department") from exc
    return RedirectResponse("/users?saved=suites", status_code=303)


@router.post("/users/suites/remove")
def users_remove_suites(
    request: Request, principal: str = Form(...),
) -> RedirectResponse:
    """Remove a department override without broadening access."""
    auth.require_global_permission(request, "admin")
    _app()._require_same_origin(request)
    from . import suite_grants
    try:
        suite_grants.remove_grant(
            principal.strip(), actor=auth.caller_principal(request) or "local"
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse("/users?saved=suites_removed", status_code=303)



# -- Spending limits ---------------------------------------------------------

_LIMIT_SAVED = {
    "workspace": "Workspace limit saved.",
    "workspace_cleared": "The workspace no longer has a spending limit.",
    "default": "Limit for everyone saved.",
    "default_cleared": "People without their own limit can now spend without a limit.",
    "person": "Personal limit saved.",
    "person_cleared": "Personal limit removed.",
}
_LIMIT_ERRORS = {
    "amount": "Enter the limit as an amount in dollars, more than $0.",
    "period": "Choose how often the limit resets.",
    "person": "Choose a person.",
    "full": "There are too many personal limits to add another.",
    "unreadable": "The saved spending limits could not be read. Please try again.",
}


@router.get("/spending-limits", response_class=HTMLResponse)
def spending_limits_page(request: Request, saved: str = "", error: str = "") -> HTMLResponse:
    """Admin: workspace and per-person spending limits (none by default)."""
    auth.require_permission(request, "admin")
    from maverick import spending_limits
    from maverick.paths import current_tenant_id
    from maverick.quotas import UsageLedger

    from . import rbac
    people = set(rbac.list_users())
    try:
        people |= set(UsageLedger()._load())
    except Exception:  # noqa: BLE001 -- spend history is a convenience here
        pass
    people.discard("")
    load_error = ""
    try:
        view = spending_limits.overview(sorted(people))
    except Exception:  # noqa: BLE001 -- show a plain message, never a trace
        load_error = ("The saved spending limits could not be read, so paid work is "
                      "paused. Save the limits below again to repair them.")
        view = {"workspace": None, "default": None, "people": [],
                "workspace_today": 0.0, "workspace_month": 0.0}
    organization_cap = 0.0
    try:
        from maverick.tenant.registry import get_tenant
        record = get_tenant(current_tenant_id() or "")
        organization_cap = float(record.max_daily_dollars) if record else 0.0
    except Exception:  # noqa: BLE001 -- the organization cap is informational here
        pass
    return _app().templates.TemplateResponse(request, "spending_limits.html", {
        "view": view,
        "periods": [(p, spending_limits.PERIOD_LABELS[p]) for p in spending_limits.PERIODS],
        "workspace_periods": [(p, spending_limits.PERIOD_LABELS[p])
                              for p in spending_limits.WORKSPACE_PERIODS],
        "period_labels": spending_limits.PERIOD_LABELS,
        "organization_cap": organization_cap,
        "saved": _LIMIT_SAVED.get(saved, ""),
        "error": _LIMIT_ERRORS.get(error, ""),
        "load_error": load_error,
    })


def _limit_redirect(saved: str = "", error: str = "") -> RedirectResponse:
    from urllib.parse import urlencode
    query = urlencode({"saved": saved} if saved else {"error": error})
    return RedirectResponse(f"/spending-limits?{query}", status_code=303)


@router.post("/spending-limits/workspace")
def spending_limits_workspace(request: Request, choice: str = Form("none"),
                              amount: str = Form(""), period: str = Form("month")) -> RedirectResponse:
    auth.require_permission(request, "admin")
    _app()._require_same_origin(request)
    from maverick import spending_limits
    actor = auth.caller_principal(request) or "local"
    try:
        if choice == "none":
            step_up.require_recent_mfa(request, "remove_spending_limit")
            spending_limits.clear_workspace_limit(actor=actor)
            return _limit_redirect("workspace_cleared")
        spending_limits.set_workspace_limit(amount, period, actor=actor)
    except spending_limits.SpendingLimitError as exc:
        return _limit_redirect(error=exc.code)
    return _limit_redirect("workspace")


@router.post("/spending-limits/default")
def spending_limits_default(request: Request, choice: str = Form("none"),
                            amount: str = Form(""), period: str = Form("month")) -> RedirectResponse:
    auth.require_permission(request, "admin")
    _app()._require_same_origin(request)
    from maverick import spending_limits
    actor = auth.caller_principal(request) or "local"
    try:
        if choice == "none":
            step_up.require_recent_mfa(request, "remove_spending_limit")
            spending_limits.clear_default_limit(actor=actor)
            return _limit_redirect("default_cleared")
        spending_limits.set_default_limit(amount, period, actor=actor)
    except spending_limits.SpendingLimitError as exc:
        return _limit_redirect(error=exc.code)
    return _limit_redirect("default")


@router.post("/spending-limits/person")
def spending_limits_person(request: Request, principal: str = Form(""),
                           amount: str = Form(""), period: str = Form("month"),
                           action: str = Form("save")) -> RedirectResponse:
    auth.require_permission(request, "admin")
    _app()._require_same_origin(request)
    from maverick import spending_limits
    actor = auth.caller_principal(request) or "local"
    try:
        if action == "remove":
            step_up.require_recent_mfa(request, "remove_spending_limit")
            spending_limits.clear_user_limit(principal, actor=actor)
            return _limit_redirect("person_cleared")
        spending_limits.set_user_limit(principal, amount, period, actor=actor)
    except spending_limits.SpendingLimitError as exc:
        return _limit_redirect(error=exc.code)
    return _limit_redirect("person")
