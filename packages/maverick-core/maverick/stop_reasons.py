"""Plain-language explanations for a run that stopped.

The kernel records WHY a run stopped as a string on the goal (``goal.result``
for a blocked goal): a quota refusal, a paused workspace, a provider outage,
an internal error. Those strings are written for logs -- they name tenant
ids, principals and exception classes. This module is the one place that
turns them into what a person reading the chat needs: what happened, the
next step, and where to take it. Raw text is kept only as ``technical``
detail for administrators.

Classification is by the wording the kernel writes (see the ``_RULES`` table);
the order matters -- the tenant refusal starts with ``over quota:`` and must
not be read as a spending problem.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Most raw text an explanation carries for administrators.
RAW_TEXT_LIMIT = 1000
_MESSAGE_LIMIT = 600

_GENERIC_MESSAGE = "The run stopped before finishing."
_GENERIC_ACTION = "Try again. If it keeps stopping, ask an administrator to check the logs."


@dataclass(frozen=True)
class StopReason:
    """What a person reads when a run stops."""

    kind: str
    message: str
    action: str
    link: str | None = None
    link_label: str | None = None
    technical: str = ""

    def as_dict(self, *, include_technical: bool = False) -> dict:
        out = {"kind": self.kind, "message": self.message, "action": self.action,
               "link": self.link, "link_label": self.link_label}
        if include_technical:
            out["technical"] = self.technical
        return out


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.IGNORECASE)


# (kind, pattern, message, action, link, link_label) -- first match wins.
_RULES: tuple[tuple[str, re.Pattern[str], str, str, str | None, str | None], ...] = (
    (
        "workspace_paused",
        _rx(r"tenant is suspended or unknown", r"tenant policy unavailable",
            r"tenant registry", r"TenantSuspended", r"TenantRegistryError",
            r"queued tenant policy", r"workspace is paused"),
        "This workspace is paused or hasn't been set up. Open Workspaces to resume it.",
        "Open Workspaces, resume this workspace, then try again.",
        "/workspaces", "Open Workspaces",
    ),
    (
        "halted",
        _rx(r"^halted$", r"HALT file", r"is halted"),
        "Lightwork is stopped by an emergency stop.",
        "An administrator can clear the emergency stop; then try again.",
        None, None,
    ),
    (
        "unpriced_model",
        _rx(r"^unpriced model:", r"\bUnpricedModelError\b"),
        "This AI model needs a price before Lightwork can continue.",
        "Ask an administrator to choose a priced model or enter this model's "
        "current spending ceiling in Settings.",
        "/settings#settings-model-prices", "Review model prices",
    ),
    (
        "spending_limit",
        _rx(r"over its daily (spend|token) quota", r"spend cap", r"spending limit",
            r"spending allowance", r"budget exhausted", r"of \$[\d.,]+ cap",
            r"BudgetExceeded", r"over quota"),
        "This task reached a spending limit.",
        "Set a spending limit for this attempt, or ask an administrator to raise "
        "the workspace's limit.",
        "/spending-limits", "Open Spending limits",
    ),
    (
        "provider_not_ready",
        _rx(r"api[_ ]?key", r"provider", r"ConnectError", r"connection (refused|reset|error)",
            r"could not (reach|connect)", r"model route", r"AuthenticationError",
            r"timed out", r"ReadTimeout"),
        "Lightwork couldn't reach the AI service.",
        "Connect an AI service, then try again.",
        "/connections", "Connect an AI service",
    ),
    (
        "permission_denied",
        _rx(r"permission", r"insufficient role", r"not allowed", r"forbidden",
            r"execution principal", r"may not use"),
        "You don't have permission to run this.",
        "A workspace administrator can grant you access on the People page.",
        "/users", "Open People",
    ),
    (
        "policy_blocked",
        _rx(r"rejected by Shield", r"input blocked", r"brief blocked", r"blocked by hook"),
        "A safety or company rule stopped this request.",
        "Reword the request, or ask an administrator which rule applied.",
        None, None,
    ),
    (
        "internal_error",
        _rx(r"^internal error", r"Traceback", r"\b\w+(Error|Exception)\b",
            r"dispatch (failed|unavailable)"),
        "Something went wrong inside Lightwork.",
        _GENERIC_ACTION,
        None, None,
    ),
)

# Text a person should never have to read: identifiers, paths, code.
_TECHNICAL = _rx(r"\b\w+(Error|Exception)\b", r"Traceback", r"'[^' ]+'", r"\"[^\" ]+\"",
                 r"(^|[\s(])/[\w.-]+/", r"[A-Za-z]:\\", r"\btenant\b", r"\bprincipal\b",
                 r"\buser:[\w.@-]+", r"<[^>]+>", r"\b0x[0-9a-f]+\b")


def _bounded(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def explain_stop(raw: str | None, *, status: str | None = None) -> StopReason:
    """Map the kernel's stop text to a :class:`StopReason`.

    ``status`` is accepted for callers that know the goal's state; a raw text
    that reads as plain prose (no identifiers, paths or exception names) is
    passed through as the message so the kernel's own sentences still show.
    """
    del status  # every stopped status is explained the same way today
    text = (raw or "").strip()
    technical = _bounded(text, RAW_TEXT_LIMIT)
    if not text:
        return StopReason("other", _GENERIC_MESSAGE, _GENERIC_ACTION)
    for kind, pattern, message, action, link, label in _RULES:
        if pattern.search(text):
            return StopReason(kind, message, action, link, label, technical)
    if _TECHNICAL.search(text):
        return StopReason("internal_error", "Something went wrong inside Lightwork.",
                          _GENERIC_ACTION, None, None, technical)
    return StopReason("other", _bounded(text, _MESSAGE_LIMIT), "Try again when you are ready.",
                      None, None, technical)


def stop_payload(status: str | None, raw: str | None, *,
                 include_technical: bool = False) -> dict | None:
    """The ``stop`` field for goal payloads: set for a stopped goal, else None."""
    if status not in ("blocked", "failed"):
        return None
    return explain_stop(raw, status=status).as_dict(include_technical=include_technical)
