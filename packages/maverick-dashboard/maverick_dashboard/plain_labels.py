"""Plain-language labels for administration pages.

Stored values such as model identifiers (``openai:gpt-5.4``), configuration
sources (``environment:ANTHROPIC_API_KEY``, ``config.toml``) and setting keys
(``code_exec``) are exact and must stay exact in forms and storage. This
module turns them into the words a person sees, so templates never print the
raw value. Every function is total: an unknown value still gets a readable
label rather than an error.
"""
from __future__ import annotations

import re
import unicodedata

_UPPER_WORDS = {"gpt": "GPT", "ai": "AI", "llm": "AI model", "vl": "VL", "api": "API",
                "sms": "SMS", "ros": "ROS", "ui": "UI", "pro": "Pro"}
_BRAND_WORDS = {"deepseek": "DeepSeek", "openai": "OpenAI", "xai": "xAI",
                "openrouter": "OpenRouter", "minimax": "MiniMax", "qwen3": "Qwen3",
                "qwen": "Qwen", "kimi": "Kimi", "llama": "Llama", "grok": "Grok",
                "gemini": "Gemini", "claude": "Claude", "moonshot": "Moonshot"}


def provider_name(provider: str) -> str:
    """Short provider name: ``anthropic`` -> ``Anthropic``."""
    try:
        from maverick.llm import PROVIDER_LABELS
        label = PROVIDER_LABELS.get(provider)
    except Exception:  # pragma: no cover -- labels are best effort
        label = None
    if not label:
        from . import settings_store
        label = next((p["label"] for p in settings_store.PROVIDERS if p["name"] == provider), "")
    if not label:
        label = _BRAND_WORDS.get(provider.lower(), provider.replace("_", " ").title())
    # "Anthropic (Claude)" -> "Anthropic"; "Ollama (self-hosted)" -> "Ollama".
    return re.sub(r"\s*\(.*\)\s*$", "", label).strip() or label


def _word(token: str) -> str:
    low = token.lower()
    if low in _UPPER_WORDS:
        return _UPPER_WORDS[low]
    if low in _BRAND_WORDS:
        return _BRAND_WORDS[low]
    if re.fullmatch(r"\d+(\.\d+)*[a-z]?", low):
        return token
    return token[:1].upper() + token[1:]


def _model_words(model_id: str) -> str:
    tail = model_id.rsplit("/", 1)[-1]
    if tail.lower().startswith("gpt-"):
        parts = tail.split("-")
        head = "GPT-" + parts[1] if len(parts) > 1 else "GPT"
        return " ".join([head] + [_word(p) for p in parts[2:]])
    parts = [p for p in re.split(r"[-_]", tail) if p]
    out: list[str] = []
    for part in parts:
        # Consecutive plain numbers are one version: claude-opus-4-8 -> 4.8.
        if part.isdigit() and out and re.fullmatch(r"\d+(\.\d+)*", out[-1]):
            out[-1] = out[-1] + "." + part
        else:
            out.append(_word(part))
    return " ".join(out) or model_id


def model_label(spec: object) -> str:
    """``openai:gpt-5.4`` -> ``GPT-5.4 · OpenAI``; ``claude-opus-4-8`` ->
    ``Claude Opus 4.8 · Anthropic``."""
    text = str(spec or "").strip()
    if not text:
        return ""
    provider, sep, model_id = text.partition(":")
    if not sep:
        provider, model_id = ("anthropic" if text.lower().startswith("claude") else ""), text
    words = _model_words(model_id)
    return f"{words} · {provider_name(provider)}" if provider else words


_SOURCE_LABELS = {
    "dashboard": "saved here in Settings",
    "config.toml": "set when Lightwork was installed",
    "operator overlay": "set by your organization's administrator",
    "tenant config": "set for this workspace",
}


def source_label(source: object) -> str:
    """Where a setting comes from, in words; never a file or variable name."""
    text = str(source or "").strip()
    if not text:
        return ""
    labels: list[str] = []
    for part in text.split(" + "):
        part = part.strip()
        if part.startswith("environment:"):
            label = "set on the computer running Lightwork"
        else:
            label = _SOURCE_LABELS.get(part, "set outside Lightwork")
        if label not in labels:
            labels.append(label)
    return " and ".join(labels)


CAPABILITY_LABELS = {
    "computer_use": "Control a desktop",
    "browser": "Browse websites",
    "web_search": "Search the web",
    "mobile_tools": "Use phones and tablets",
    "ros": "Work with connected robots",
    "code_exec": "Run code",
}

FEATURE_LABELS = {
    "workflow_readiness": "Workflow catalog",
    "project_reviews": "Project reviews",
    "outcome_measurement": "Outcome measurement",
    "guided_onboarding": "Guided first steps",
    "action_center": "Action center",
    "draft_recovery": "Recover unsaved drafts",
    "support_center": "Support reports",
    "record_search": "Search across records",
    "workspace_recovery": "Workspace backups",
    "signing_identity_transition": "Change of software publisher",
    "skills": "Skills library",
    "world_model": "Shared memory across tasks",
    "streaming": "Show replies as they are written",
    "pack_editing": "Edit specialist teams",
    "role_editing": "Edit agent roles",
    "scheduling": "Scheduled workflows",
    "triggers": "Start workflows from other apps",
}

# Plain rewrites of the few help texts whose stored wording is technical.
FEATURE_HELP = {
    "workflow_readiness": "Show the ready-made workflows you can run and what each still needs before you rely on it.",
    "project_reviews": "Share finished task results with the reviewers you choose.",
    "outcome_measurement": "Compare how long work took before and after Lightwork, using your own numbers.",
    "guided_onboarding": "Offer a step-by-step path to your first finished piece of work.",
    "action_center": "Collect approvals, sign-offs and reviews waiting for you in one list.",
    "draft_recovery": "Keep a private, encrypted copy of unfinished work so it survives a closed tab.",
    "support_center": "Let administrators prepare a support report to send to the Lightwork team. Nothing is sent automatically.",
    "record_search": "Find vendors, evidence, assessments, tasks and reports by name.",
    "workspace_recovery": "Let administrators make, check and restore backups of this computer's workspaces.",
    "signing_identity_transition": "Allow administrators to approve a change to who publishes Lightwork updates. Only used during recovery.",
    "world_model": "Tasks share one memory, so what Lightwork learns in one task carries to the next.",
}

# "Where do I get a key?" links, by provider.
PROVIDER_KEY_HELP = {
    "anthropic": "https://console.anthropic.com/settings/keys",
    "openai": "https://platform.openai.com/api-keys",
    "gemini": "https://aistudio.google.com/apikey",
    "openrouter": "https://openrouter.ai/keys",
    "moonshot": "https://platform.moonshot.ai/console/api-keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "xai": "https://console.x.ai",
    "ollama": "https://ollama.com/download",
}


def capability_label(key: object) -> str:
    k = str(key or "")
    return CAPABILITY_LABELS.get(k) or k.replace("_", " ").capitalize()


def feature_label(key: object) -> str:
    k = str(key or "")
    return FEATURE_LABELS.get(k) or k.replace("_", " ").capitalize()


def feature_help(key: object, fallback: str = "") -> str:
    return FEATURE_HELP.get(str(key or ""), fallback)


def slugify(name: object, *, max_len: int = 48) -> str:
    """A stable identifier made from a display name: ``Acme — EU`` -> ``acme-eu``."""
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:max_len].strip("-")


def unique_slug(name: object, taken, *, fallback: str = "item", max_len: int = 48) -> str:
    """``slugify`` made unique against ``taken`` by appending -2, -3, ..."""
    base = slugify(name, max_len=max_len - 4) or fallback
    if not base[0].isalpha():
        base = f"{fallback}-{base}"[: max_len - 4]
    taken = set(taken or ())
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def money(value: object) -> str:
    """Dollars to two places; tiny non-zero amounts read as "under $0.01"."""
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "$0.00"
    if 0 < abs(amount) < 0.005:
        return "under $0.01"
    return f"${amount:,.2f}"


def size(num_bytes: object) -> str:
    """Bytes as KB / MB / GB."""
    try:
        n = float(num_bytes or 0)
    except (TypeError, ValueError):
        return "0 KB"
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "bytes":
                return f"{int(n)} bytes"
            return f"{n:,.1f} {unit}".replace(".0 ", " ")
        n /= 1024
    return f"{n:,.1f} TB"  # pragma: no cover


def register(env) -> None:
    """Install the label filters on a Jinja environment (idempotent)."""
    env.filters.setdefault("model_label", model_label)
    env.filters.setdefault("source_label", source_label)
    env.filters.setdefault("capability_label", capability_label)
    env.filters.setdefault("feature_label", feature_label)
    env.filters.setdefault("money", money)
    env.filters.setdefault("filesize", size)
