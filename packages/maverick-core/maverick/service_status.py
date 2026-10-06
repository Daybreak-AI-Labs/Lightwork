"""What the in-product service banner says when an AI service is paused (HLP-4).

The circuit breaker (:mod:`maverick.circuit_breaker`) pauses an AI provider
after repeated errors (``llm:<provider>`` keys) and lets one test request
through once its cooldown ends. This module turns that state into the three
things a person needs: what is affected, what happens to running tasks, and
when Lightwork checks again. It looks at every provider this workspace uses
(the role models and their backup models), so one healthy backup reads as
"reduced", not "down".

The breaker is held in memory by the process that runs the AI calls. Tasks
run inside the dashboard process by default, so the dashboard sees it;
separate worker processes keep their own and are not reflected here.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_PAUSED_STATES = {"open", "half_open"}
_NAMES = {"openai": "OpenAI", "xai": "xAI", "deepseek": "DeepSeek", "openrouter": "OpenRouter",
          "azure_openai": "Azure OpenAI", "codex_cli": "Codex", "tgi": "TGI", "vllm": "vLLM"}

#: What happens to a task whose next step needs a paused service (read from
#: llm._enforce_circuit and the runners' failure path): the call is refused at
#: once, a configured backup model is tried, and otherwise the task stops as
#: "Failed" with what it already finished kept.
TASKS_DOWN = ("Tasks that need an AI answer now stop and show “Failed”. "
              "What they already finished is kept; run them again once the service is back.")
TASKS_REDUCED = ("Steps that need {paused} move to a backup model where one is set up; "
                 "otherwise that task stops and shows “Failed”. Tasks that use {healthy} keep working.")
#: Another way to get the work done meanwhile (CTL-5: the fourth thing a
#: failure message says, after what broke, what is safe and when it returns).
ALTERNATIVE_DOWN = ("Meanwhile, an administrator can set up another AI service under Settings > AI models "
                    "so tasks can carry on, and urgent steps can be done by hand.")
ALTERNATIVE_REDUCED = "Meanwhile, start new work as usual: it uses {healthy}."


def provider_name(provider: str) -> str:
    key = str(provider or "").strip().lower()
    return _NAMES.get(key) or key.replace("_", " ").title() or "An AI service"


def _provider_of(spec: object) -> str:
    text = str(spec or "")
    return text.split(":", 1)[0].strip().lower() if ":" in text else ""


def providers_in_use() -> set[str]:
    """Providers named by this workspace's role models and backup chains."""
    from .config import get_role_model, load_config
    from .llm import ROLE_MODELS

    used = set()
    for role, default in ROLE_MODELS.items():
        try:
            used.add(_provider_of(get_role_model(role) or default))
        except Exception:  # noqa: BLE001 -- an unreadable role keeps its default
            used.add(_provider_of(default))
    try:
        chains = ((load_config() or {}).get("provider_failover") or {}).get("chains") or {}
    except Exception:  # noqa: BLE001 -- unreadable config: no backups known
        chains = {}
    if isinstance(chains, dict):
        for primary, backups in chains.items():
            used.add(_provider_of(primary))
            for backup in backups if isinstance(backups, list) else ():
                used.add(_provider_of(backup))
    return {name for name in used if name}


def current(*, breakers: list[dict] | None = None, in_use: set[str] | None = None) -> dict[str, Any] | None:
    """The banner's content, or ``None`` when no AI service is paused."""
    if breakers is None:
        from .circuit_breaker import snapshot
        breakers = snapshot()
    paused: dict[str, dict] = {}
    for row in breakers:
        key = str(row.get("key") or "")
        if key.startswith("llm:") and row.get("state") in _PAUSED_STATES:
            paused[key[4:].lower()] = row
    if not paused:
        return None
    if in_use is None:
        try:
            in_use = providers_in_use()
        except Exception:  # noqa: BLE001 -- unknown setup reads as "nothing else can serve"
            log.debug("service status: provider list unavailable", exc_info=True)
            in_use = set()
    healthy = sorted(in_use - set(paused))
    names = [provider_name(name) for name in sorted(paused)]
    affected = " and ".join(names)
    verb = "are" if len(names) > 1 else "is"
    checks = []
    for name, row in sorted(paused.items()):
        if row.get("state") == "half_open":
            checks.append({"provider": provider_name(name), "at": None})
        else:
            opened = float(row.get("opened_at") or 0)
            cooldown = float(row.get("cooldown_seconds") or 30)
            checks.append({"provider": provider_name(name), "at": opened + cooldown if opened else None})
    if healthy:
        healthy_names = " and ".join(provider_name(name) for name in healthy)
        return {"level": "reduced", "affected": affected,
                "title": f"{affected} {verb} paused after repeated errors. Lightwork is still working with {healthy_names}.",
                "tasks": TASKS_REDUCED.format(paused=affected, healthy=healthy_names),
                "alternative": ALTERNATIVE_REDUCED.format(healthy=healthy_names),
                "checks": checks, "healthy": [provider_name(name) for name in healthy]}
    return {"level": "down", "affected": affected,
            "title": f"{affected} {verb} paused after repeated errors, and no other AI service is set up to take over.",
            "tasks": TASKS_DOWN, "alternative": ALTERNATIVE_DOWN, "checks": checks, "healthy": []}


def safe_current() -> dict[str, Any] | None:
    """:func:`current` for page rendering: a fault never breaks a page."""
    try:
        return current()
    except Exception:  # noqa: BLE001 -- the banner is advisory
        log.debug("service status unavailable", exc_info=True)
        return None


__all__ = ["ALTERNATIVE_DOWN", "ALTERNATIVE_REDUCED", "TASKS_DOWN", "TASKS_REDUCED", "current", "provider_name", "providers_in_use", "safe_current"]
