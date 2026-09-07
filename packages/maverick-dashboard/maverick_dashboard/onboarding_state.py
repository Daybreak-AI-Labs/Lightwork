"""Read-only state for the dashboard's first-run checklist."""

from __future__ import annotations

from typing import Any


def selected_model_route_state() -> dict[str, Any]:
    """Return bounded evidence for the selected swarm model routes.

    This deliberately uses the same deterministic route resolver as goal
    preflight.  A credential for an unrelated provider (including an existing
    Codex CLI login) must not complete onboarding when the selected role models
    still route to Anthropic, OpenAI, or another unconfigured provider.

    ``configured`` proves only offline prerequisites.  It never claims that a
    provider accepted a credential or a live request; that belongs to
    ``maverick doctor`` and provider-specific readiness receipts.
    """

    try:
        from maverick.config import config_source_errors, load_config
        from maverick.operator_preflight import _routed_configuration_missing

        loaded = load_config()
        if config_source_errors(include_tenant=True):
            raise RuntimeError("active configuration sources are invalid")
        routed = _routed_configuration_missing(loaded)
        if not isinstance(routed, dict) or not routed:
            raise TypeError("selected model-route result is not a mapping")
        missing: dict[str, tuple[str, ...]] = {}
        for route, fields in routed.items():
            if isinstance(fields, (str, bytes)) or not isinstance(
                fields, (list, tuple, set, frozenset)
            ):
                raise TypeError("selected model-route fields are malformed")
            if fields:
                missing[str(route)] = tuple(str(field) for field in fields)
    except Exception as exc:
        return {
            "status": "failed",
            "configured": False,
            "detail": "Selected model-route configuration could not be evaluated safely.",
            "evidence": {
                "kind": "offline_configuration",
                "error_type": type(exc).__name__,
                "live_provider_verified": False,
            },
        }

    if missing:
        return {
            "status": "incomplete",
            "configured": False,
            "detail": (
                f"{len(missing)} selected model route(s) still have missing "
                "offline prerequisites."
            ),
            "evidence": {
                "kind": "offline_configuration",
                "routes_evaluated": len(routed),
                "routes_incomplete": len(missing),
                "live_provider_verified": False,
            },
        }
    return {
        "status": "configured",
        "configured": True,
        "detail": (
            "Every selected model route has its offline prerequisites. "
            "Live provider acceptance has not been inferred."
        ),
        "evidence": {
            "kind": "offline_configuration",
            "routes_evaluated": len(routed),
            "routes_incomplete": 0,
            "live_provider_verified": False,
        },
    }


def selected_model_routes_ready() -> bool:
    """Compatibility boolean for offline selected-route configuration."""

    return selected_model_route_state()["configured"] is True


def _has_user_template() -> bool:
    try:
        from maverick.templates import user_templates_dir

        return any(user_templates_dir().glob("*.md"))
    except OSError:
        return False


def _has_user_agent() -> bool:
    try:
        from maverick.domain_edit import list_agents

        return any(agent.get("is_override") for agent in list_agents())
    except Exception:
        return False


def _has_run(world: Any, owner: str | None = None) -> bool:
    try:
        return bool(world.list_goals(limit=1, owner=owner))
    except Exception:
        return False


def _has_automation() -> bool:
    try:
        from maverick.job_queue import JobQueue

        if any(
            (job.payload or {}).get("__cron__")
            for job in JobQueue().list(status="pending")
        ):
            return True
    except Exception:
        pass
    try:
        from . import triggers_store

        return bool(triggers_store.list_triggers())
    except Exception:
        return False


def _preflight_state(profile: str) -> dict[str, Any]:
    from maverick.operator_preflight import collect

    try:
        payload = collect(profile).to_dict()
        if not isinstance(payload, dict) or not isinstance(payload.get("checks"), list):
            raise TypeError("preflight report contract is invalid")
    except Exception as exc:
        label = "Runtime" if profile == "run" else "Assurance"
        return {
            "ready": False,
            "status": "failed",
            "checks": [
                {
                    "status": "blocked",
                    "label": f"{label} preflight",
                    "detail": "Readiness evidence could not be evaluated safely.",
                    "remediation": "maverick preflight --json",
                }
            ],
            "next_action": "maverick preflight --json",
            "error_type": type(exc).__name__,
        }
    payload["ready"] = payload.get("ready") is True
    payload["status"] = "ready" if payload["ready"] else "incomplete"
    return payload


def build(
    world: Any,
    *,
    can_configure: bool = True,
    can_operate: bool = True,
    owner: str | None = None,
    reviewed_outcome: dict | None = None,
) -> dict[str, Any]:
    """Build the activation checklist and deterministic install preflight."""

    model_route_state = selected_model_route_state()
    provider_ok = model_route_state["configured"] is True
    activated = _has_run(world, owner)
    built = _has_user_template() or _has_user_agent() or activated
    if reviewed_outcome is None:
        from .guided_outcome import reviewed_goal

        reviewed_outcome = reviewed_goal(world, owner=owner)
    steps = [
        {
            "title": "Connect your AI service",
            "done": provider_ok,
            "body": (
                "Your selected AI services are configured. Test the connection "
                "in Settings before starting your first task."
                if provider_ok else
                "Choose an AI service in Settings, add its access key, and "
                "test the connection before starting your first task."
            ),
            "status": model_route_state["status"],
            "cta": "/settings",
            "cta_label": "Open Settings",
            "allowed": bool(can_configure),
            "readonly_label": "Ask your administrator to connect an AI service.",
            "command": "maverick preflight",
        },
        {
            "title": "Choose a workflow or agent",
            "done": built,
            "body": (
                "Choose an existing specialist or workflow, or create one in the builder. "
                "Using an existing specialist also completes this step."
            ),
            "status": "complete" if built else "incomplete",
            "cta": "/agents",
            "cta_label": "Choose a specialist",
            "allowed": bool(can_operate),
            "readonly_label": "An operator can choose or build the first workflow or agent.",
            "command": "",
        },
        {
            "title": "Start your first task",
            "done": activated,
            "body": (
                "Describe what you need in Chat. You can also schedule a "
                "saved workflow from Automations."
            ),
            "status": "complete" if activated else "incomplete",
            "cta": "/chat",
            "cta_label": "Start a goal",
            "allowed": bool(can_operate),
            "readonly_label": "An operator can start or automate the first run.",
            "command": "",
        },
    ]
    steps.append({
        "title": "Review and accept your first result", "done": reviewed_outcome.get("complete") is True,
        "body": reviewed_outcome["detail"], "status": reviewed_outcome["status"],
        "cta": reviewed_outcome.get("href", "/deliverables"), "cta_label": "Review the result",
        "allowed": bool(can_operate), "readonly_label": "An operator can review and accept the first result.",
        "command": "",
    })
    done_count = sum(step["done"] for step in steps)
    next_step = next((step for step in steps if not step["done"]), None)
    runtime_preflight = _preflight_state("run")
    assurance_preflight = _preflight_state("cockpit")
    from maverick.config import config_path

    return {
        "steps": steps,
        "done_count": done_count,
        "total": len(steps),
        "next_step": next_step,
        "model_route_state": model_route_state,
        "can_operate": bool(can_operate),
        "can_configure": bool(can_configure),
        "needs_initial_settings": not config_path().exists(),
        # Compatibility alias for existing template and API consumers.
        "preflight": runtime_preflight,
        "runtime_preflight": runtime_preflight,
        "assurance_preflight": assurance_preflight,
        "runtime_ready": runtime_preflight["ready"] is True,
        "assurance_ready": assurance_preflight["ready"] is True,
        "runtime_all_set": (
            done_count == len(steps) and runtime_preflight["ready"] is True
        ),
    }


__all__ = ["build", "selected_model_route_state", "selected_model_routes_ready"]
