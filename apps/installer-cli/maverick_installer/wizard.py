"""Lightwork interactive installer.

Configures Lightwork for a fresh install. Sets up:
  - AI providers and per-role models
  - channels (Telegram, Discord, Slack, Signal, WhatsApp, SMS, Email,
    Matrix, iMessage)
  - safety profile
  - sandbox backend
  - budget caps
  - API keys (stored beside config.toml, referenced from it via ${VAR})

Writes ``$MAVERICK_CONFIG`` when explicitly selected, otherwise
``$MAVERICK_HOME/config.toml`` (default ``~/.maverick/config.toml``), plus a
sibling ``.env``. The agent reads from the same resolution contract.

v0.1.1 additions (council UX feedback):
  - Preflight: Python version, write perms, optional docker check
  - API key validation: pings Anthropic with the entered key before save
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover -- Py 3.10 CI matrix
    import tomli as tomllib  # type: ignore[no-redef]

from rich.console import Console
from rich.panel import Panel

try:
    import questionary
except ImportError:  # pragma: no cover
    questionary = None  # type: ignore

from . import models as catalog


def _resolve_config_paths() -> tuple[Path, Path, Path]:
    """Resolve exactly the paths used by :mod:`maverick.config`.

    The previous import-time ``Path.home() / '.maverick'`` ignored the
    documented ``MAVERICK_HOME``/``MAVERICK_CONFIG`` overrides.  The wizard
    could therefore overwrite the real user config and then smoke-test a
    different, still-empty location.  Environment is fixed before a CLI
    process imports this module, so resolving once here keeps the existing
    monkeypatchable constants while aligning both sides of the handoff.
    """
    explicit = os.environ.get("MAVERICK_CONFIG", "").strip()
    if explicit:
        config_file = Path(explicit).expanduser()
        config_dir = config_file.parent
    else:
        config_dir = Path(os.environ.get("MAVERICK_HOME", "~/.maverick")).expanduser()
        config_file = config_dir / "config.toml"
    return config_dir, config_file, config_dir / ".env"


CONFIG_DIR, CONFIG_FILE, ENV_FILE = _resolve_config_paths()


def _prior_consent_salt() -> str:
    """The existing consent subject_salt, or a fresh one on first enable.

    Consent subject digests are sha256(salt:subject), so regenerating the
    salt on a wizard re-run would orphan every stored consent record.
    Mirror the MAVERICK_RED_TEAM_COMMITMENT_KEY preservation: read the
    current config.toml (which the wizard is about to rewrite) and keep
    the salt that the existing records were digested under.
    """

    try:
        prior = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        salt = str((prior.get("consent_registry") or {}).get("subject_salt") or "")
        if 16 <= len(salt.strip()) <= 256:
            return salt.strip()
    except (OSError, ValueError):
        pass
    return secrets.token_urlsafe(24)

console = Console()


# Channel catalog: (id, label, env_vars_needed)
CHANNELS: list[tuple[str, str, list[str]]] = [
    ("telegram", "Telegram bot (free, easiest)", ["TELEGRAM_BOT_TOKEN"]),
    ("discord", "Discord bot (Gateway WS)", ["DISCORD_BOT_TOKEN"]),
    ("slack", "Slack (Socket Mode)", ["SLACK_APP_TOKEN", "SLACK_BOT_TOKEN"]),
    ("signal", "Signal (via signal-cli)", []),
    ("email", "Email (IMAP/SMTP, stdlib only)", ["EMAIL_USER", "EMAIL_APP_PASSWORD"]),
    ("matrix", "Matrix (federated)", ["MATRIX_ACCESS_TOKEN"]),
    ("bluesky", "Bluesky (AT Protocol)", ["BLUESKY_HANDLE", "BLUESKY_PASSWORD"]),
    ("mastodon", "Mastodon (any instance)", ["MASTODON_ACCESS_TOKEN"]),
    ("irc", "IRC (channels + DMs)", []),
    # Voice API key is provider-specific (VAPI/RETELL/BLAND), resolved in the
    # voice block below; only the webhook token is static here.
    ("voice", "Voice (Vapi/Retell/Bland)", ["VAPI_WEBHOOK_TOKEN"]),
    ("whatsapp", "WhatsApp (Twilio, needs webhook)", ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"]),
    (
        "whatsapp_cloud",
        "WhatsApp (Meta Cloud API, needs webhook)",
        [
            "WHATSAPP_CLOUD_ACCESS_TOKEN",
            "WHATSAPP_CLOUD_PHONE_NUMBER_ID",
            "WHATSAPP_CLOUD_VERIFY_TOKEN",
            "WHATSAPP_CLOUD_APP_SECRET",
        ],
    ),
    ("sms", "SMS (Twilio, needs webhook)", ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"]),
    ("imessage", "iMessage (macOS only)", []),
    ("threads", "Threads (Meta, polling)", ["THREADS_ACCESS_TOKEN", "THREADS_USER_ID"]),
    (
        "rcs",
        "RCS (Google RBM, approved agents only)",
        ["RCS_AGENT_ID", "RCS_SERVICE_ACCOUNT_JSON", "RCS_WEBHOOK_TOKEN"],
    ),
]


# Channels that are scaffolds: they ship runtime code but can't work
# end-to-end from a default install — whatsapp/sms need a Twilio account
# plus a public webhook (maverick_channels documents both as "scaffold"),
# and imessage is macOS-only and needs Full Disk Access. Offering them in
# the default checkbox is dishonest: users pick them, then hit a dead end.
# Gate them behind an explicit opt-in (see pick_channels).
EXPERIMENTAL_CHANNELS: set[str] = {
    "whatsapp",
    "whatsapp_cloud",
    "sms",
    "imessage",
    "threads",
    "rcs",
}


# Ordered advanced-flow steps, mirroring the pick_* sequence in run().
# Purely a progress-bar aid: changing this list never changes the config.
STEPS: list[tuple[str, str]] = [
    ("deployment", "Deployment"),
    ("providers", "Providers"),
    ("role_models", "Models"),
    ("channels", "Channels"),
    ("safety", "Safety"),
    ("signed_skills", "Signed skills"),
    ("budget", "Budget"),
    ("sandbox", "Sandbox"),
    ("capabilities", "Capabilities"),
    ("self_learning", "Self-learning"),
    ("ekko", "Ekko work discovery"),
    ("automation_import", "Automation import"),
    ("event_triggers", "Event triggers"),
    ("flows", "Flow engine"),
    ("knowledge", "Knowledge RAG"),
    ("oauth_vault", "OAuth token vault"),
    ("governed_connectors", "Governed connectors"),
    ("durable", "Durable execution"),
    ("journal", "Workspace journal"),
    ("finance", "Finance suite"),
    ("value", "Savings assumptions"),
    ("assessments", "Assessment assists"),
    ("security_suite", "Security & GRC"),
    ("advanced", "Advanced reasoning"),
    ("web_search", "Web search"),
    ("mcp_servers", "MCP servers"),
    ("plugins", "Plugins"),
    ("tool_acl", "Tool ACL"),
    ("rate_limits", "Rate limits"),
    ("retention", "Retention"),
    ("analytics", "Analytics"),
    ("persona", "Persona"),
    ("notifications", "Notifications"),
    ("webhooks", "Webhooks"),
    ("a2a", "A2A"),
]


def _step_indicator(index: int, *, done: list[str] | None = None) -> str:
    """Format the ``Step N/M`` progress line for the ``index``-th step
    (1-based), optionally trailed by a breadcrumb of completed labels.

    Returns plain text (no Rich markup): styling is applied by the caller
    via ``console.print(..., style=...)`` so the literal "Step N/M" text
    stays contiguous in rendered output instead of being fragmented by
    inline ANSI codes. Defined as a pure helper so tests can assert the
    formatting without driving the whole wizard.
    """
    total = len(STEPS)
    label = STEPS[index - 1][1] if 1 <= index <= total else ""
    line = f"Step {index}/{total} {label}"
    if done:
        line += f"  ({' > '.join(done)})"
    return line


def _safe_int(s: str, *, default: int) -> int:
    """``int()`` that doesn't crash on whitespace, empty, or junk input."""
    try:
        return int(str(s or "").strip())
    except (TypeError, ValueError):
        return default


def _csv_list(raw: str, *, lower: bool = False) -> list[str]:
    """Split a comma-separated prompt answer into trimmed, non-empty items."""
    items = [x.strip() for x in str(raw or "").split(",") if x.strip()]
    return [x.lower() for x in items] if lower else items


def _safe_float(s: str, *, default: float) -> float:
    """``float()`` that doesn't crash on whitespace, empty, or junk input."""
    try:
        return float(str(s or "").strip())
    except (TypeError, ValueError):
        return default


# ---------- prompt primitives ----------


def _ask(question: Any) -> Any:
    """Run a questionary prompt, treating a ``None`` answer as an abort.

    questionary's ``.ask()`` returns ``None`` when the user presses
    Ctrl-C / Ctrl-D or when there's no interactive TTY (e.g. stdin is a
    pipe). Every call site then did ``.split()`` / ``.strip()`` on the
    result and crashed with an opaque ``AttributeError``. Convert that
    to ``KeyboardInterrupt`` so the entry point prints a clean "Aborted"
    message and exits 130 instead of dumping a traceback.
    """
    answer = question.ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer


def _q_select(message: str, choices: list[str], default: str | None = None) -> str:
    if questionary is None:
        print(message)
        for i, c in enumerate(choices):
            marker = "*" if default == c else " "
            print(f"  {marker} {i + 1}) {c}")
        while True:
            choice = input("> ").strip()
            if not choice and default:
                return default
            if choice.isdigit() and 1 <= int(choice) <= len(choices):
                return choices[int(choice) - 1]
    return _ask(questionary.select(message, choices=choices, default=default))


def _q_text(message: str, default: str = "") -> str:
    if questionary is None:
        val = input(f"{message} [{default}]: ").strip()
        return val or default
    return _ask(questionary.text(message, default=default))


def _q_secret(message: str) -> str:
    if questionary is None:
        import getpass

        return getpass.getpass(f"{message}: ").strip()
    # Route through _ask so Ctrl-C / Ctrl-D / non-TTY (questionary returns
    # None) raises KeyboardInterrupt and aborts the wizard, like every other
    # prompt. The old `.ask() or ""` swallowed the abort into "", which
    # callers read as "skip this key" -- so Ctrl-C silently continued.
    return _ask(questionary.password(message)) or ""


def _q_checkbox(message: str, choices: list[str], default: list[str] | None = None) -> list[str]:
    if questionary is None:
        print(f"{message} (comma-separated numbers, blank = none)")
        for i, c in enumerate(choices):
            marker = "*" if default and c in default else " "
            print(f"  {marker} {i + 1}) {c}")
        raw = input("> ").strip()
        if not raw:
            return default or []
        picks = [c.strip() for c in raw.split(",")]
        return [choices[int(p) - 1] for p in picks if p.isdigit() and 1 <= int(p) <= len(choices)]
    # questionary.checkbox ignores `default` (it's documented "not used by
    # checkbox"). To actually pre-select the defaults, wrap them as
    # pre-checked Choice objects whose value is the title string -- so
    # callers still get back the same strings they passed in.
    default_set = set(default or [])
    q_choices: Any = (
        [questionary.Choice(c, checked=c in default_set) for c in choices]
        if default_set
        else choices
    )
    return _ask(questionary.checkbox(message, choices=q_choices))


def _q_confirm(message: str, default: bool = True) -> bool:
    if questionary is None:
        val = input(f"{message} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        if not val:
            return default
        return val.startswith("y")
    return _ask(questionary.confirm(message, default=default))


# ---------- preflight ----------


def preflight() -> bool:
    """Check the environment before asking any questions.

    Returns True if all critical checks pass. Warnings are shown but
    don't block the wizard.
    """
    console.print("\n[dim]Checking your environment...[/dim]")
    all_ok = True

    # Python version
    if sys.version_info < (3, 10):
        console.print(f"[red]✗[/red] Python 3.10+ required (you have {sys.version.split()[0]})")
        all_ok = False
    else:
        console.print(f"[green]✓[/green] Python {sys.version.split()[0]}")

    # Config dir writable
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        test_file = CONFIG_DIR / ".write-test"
        test_file.write_text("ok")
        test_file.unlink()
        console.print(f"[green]✓[/green] {CONFIG_DIR} is writable")
    except (PermissionError, OSError) as e:
        console.print(f"[red]✗[/red] Can't write to {CONFIG_DIR}: {e}")
        all_ok = False

    # Docker (advisory only -- only matters if user picks docker sandbox)
    if shutil.which("docker"):
        try:
            subprocess.run(
                ["docker", "version"],
                capture_output=True,
                timeout=5,
                check=True,
            )
            console.print("[green]✓[/green] Docker is running")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            console.print(
                "[yellow]![/yellow] Docker installed but daemon isn't responding "
                "(only matters if you pick the docker sandbox)"
            )
    else:
        console.print(
            "[yellow]![/yellow] Docker not installed (only matters if you pick the docker sandbox)"
        )

    return all_ok


# ---------- validators ----------


def _sdk_import_skip_message(name: str, exc: ImportError) -> str:
    """Say WHY validation is skipped. A corrupt install raises ImportError
    exactly like an absent package; reporting "not installed" while pip lists
    the package sent a user the wrong fix (field finding: the cure was
    --force-reinstall). Only a ModuleNotFoundError for the package itself is
    "not installed"."""
    if isinstance(exc, ModuleNotFoundError) and exc.name == name:
        return f"{name} SDK not installed -- skipping validation"
    return (
        f"{name} SDK failed to import ({exc}) -- skipping validation; "
        f"try: pip install --force-reinstall {name}"
    )


def _validate_anthropic_key(key: str) -> tuple[bool, str]:
    """Ping Anthropic with the key. Returns (ok, message).

    Skip the prefix check: Anthropic now ships admin keys, batch keys,
    and several legacy formats. The API ping handles whatever shape
    the key takes.
    """
    if not key.strip():
        return False, "empty key"
    try:
        import anthropic
    except ImportError as e:
        return True, _sdk_import_skip_message("anthropic", e)
    try:
        client = anthropic.Anthropic(api_key=key, timeout=5.0)
        # Minimal call -- list available models is enough to verify auth.
        list(client.models.list(limit=1))
        return True, "validated"
    except anthropic.AuthenticationError:
        return False, "API rejected the key"
    except Exception as e:
        return True, f"validation skipped: {type(e).__name__}"


def _validate_openai_key(key: str) -> tuple[bool, str]:
    if not key.strip():
        return False, "empty key"
    # Azure OpenAI keys are 32-char hex with no prefix; OpenAI ships
    # sk-, sk-proj-, sk-svcacct-. Just ping the API and let it tell us.
    try:
        from openai import AuthenticationError, OpenAI
    except ImportError as e:
        return True, _sdk_import_skip_message("openai", e)
    try:
        client = OpenAI(api_key=key, timeout=5.0)
        list(client.models.list().data[:1])
        return True, "validated"
    except AuthenticationError:
        return False, "API rejected the key"
    except Exception as e:
        return True, f"validation skipped: {type(e).__name__}"


def _validate_openai_compat_key(key: str, base_url: str, label: str) -> tuple[bool, str]:
    """For openai-compatible endpoints (Moonshot, DeepSeek, xAI, Gemini)."""
    if not key:
        return False, "empty key"
    try:
        from openai import AuthenticationError, OpenAI
    except ImportError as e:
        return True, _sdk_import_skip_message("openai", e)
    try:
        client = OpenAI(api_key=key, base_url=base_url, timeout=5.0)
        list(client.models.list().data[:1])
        return True, f"validated against {label}"
    except AuthenticationError:
        return False, f"{label} rejected the key"
    except Exception as e:
        # Network / route errors are non-fatal -- saving still useful.
        return True, f"validation skipped: {type(e).__name__}"


def _validate_moonshot_key(key: str) -> tuple[bool, str]:
    return _validate_openai_compat_key(
        key,
        "https://api.moonshot.ai/v1",
        "Moonshot",
    )


def _validate_deepseek_key(key: str) -> tuple[bool, str]:
    return _validate_openai_compat_key(
        key,
        "https://api.deepseek.com/v1",
        "DeepSeek",
    )


def _validate_xai_key(key: str) -> tuple[bool, str]:
    return _validate_openai_compat_key(
        key,
        "https://api.x.ai/v1",
        "xAI",
    )


def _validate_gemini_key(key: str) -> tuple[bool, str]:
    return _validate_openai_compat_key(
        key,
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "Gemini",
    )


def _validation_marker(ok: bool, msg: str) -> str:
    """Status marker for a key-validation result. A SKIPPED check (SDK missing /
    couldn't reach the API) is NOT a confirmed key -- render it distinctly so a
    green "ok" never appears next to "validation skipped"."""
    if not ok:
        return "[red]x[/red]"
    if "skipped" in msg.lower():
        return "[yellow]?[/yellow]"
    return "[green]ok[/green]"


_VALIDATORS = {
    "ANTHROPIC_API_KEY": _validate_anthropic_key,
    "OPENAI_API_KEY": _validate_openai_key,
    "MOONSHOT_API_KEY": _validate_moonshot_key,
    "DEEPSEEK_API_KEY": _validate_deepseek_key,
    "XAI_API_KEY": _validate_xai_key,
    "GEMINI_API_KEY": _validate_gemini_key,
    # Channel tokens validated when 'maverick serve' starts (less time-critical).
}


# ---------- validation cache ----------

VALIDATION_CACHE_PATH = CONFIG_DIR / "validation-cache.json"
_VALIDATION_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def _key_fingerprint(env_name: str, key: str) -> str:
    import hashlib

    digest = hashlib.sha256(f"{env_name}\x00{key}".encode()).hexdigest()
    return digest[:32]


def _load_validation_cache() -> dict[str, Any]:
    try:
        import json as _json

        return _json.loads(VALIDATION_CACHE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save_validation_cache(cache: dict[str, Any]) -> None:
    import json as _json

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        VALIDATION_CACHE_PATH.write_text(_json.dumps(cache, default=str))
        try:
            os.chmod(VALIDATION_CACHE_PATH, 0o600)
        except OSError:
            pass
    except OSError:
        pass


def _cached_validation(env_name: str, key: str) -> tuple[bool, str] | None:
    """Return cached (ok, msg) if the same key was validated within the TTL."""
    import time as _time

    if not key.strip():
        return None
    cache = _load_validation_cache()
    fp = _key_fingerprint(env_name, key)
    entry = cache.get(fp)
    if not entry:
        return None
    ts = float(entry.get("ts", 0))
    if (_time.time() - ts) > _VALIDATION_TTL_SECONDS:
        return None
    return bool(entry.get("ok", False)), str(entry.get("msg", "cached"))


def _remember_validation(env_name: str, key: str, ok: bool, msg: str) -> None:
    import time as _time

    if not key.strip():
        return
    cache = _load_validation_cache()
    cache[_key_fingerprint(env_name, key)] = {
        "ts": _time.time(),
        "ok": ok,
        "msg": msg,
    }
    _save_validation_cache(cache)


# ---------- error UI ----------


def show_bad_key_error(env_name: str, msg: str) -> None:
    """Council UX seat error screen #1: provider rejected the key."""
    console.print()
    console.print(
        Panel.fit(
            f"[bold]That {env_name.split('_')[0].title()} key didn't work.[/bold]\n\n"
            f"{msg}\n\n"
            "Common causes:\n"
            "  1. Typo (the secret is long; copy/paste, don't retype).\n"
            "  2. The key was deleted from your account.\n"
            "  3. Billing isn't set up on the provider.",
            border_style="red",
        )
    )


def show_network_error(provider: str, exception_type: str) -> None:
    """Council UX seat error screen #2: validator couldn't reach the provider."""
    console.print()
    console.print(
        Panel.fit(
            f"[bold]Couldn't reach {provider} to check the key ({exception_type}).[/bold]\n\n"
            "Usually a network block or proxy. Your key is saved either way;\n"
            "if it's wrong the first goal you run will say so.",
            border_style="yellow",
        )
    )


def show_install_failure(exc: BaseException) -> None:
    """Council UX seat error screen #3: catch-all for unexpected setup failures."""
    console.print()
    console.print(
        Panel.fit(
            "[bold]Setup hit a problem and stopped.[/bold]\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Nothing was changed. Try again, or report the issue with the\n"
            "diagnostic output of [bold]maverick doctor[/bold].",
            border_style="red",
        )
    )


# ---------- wizard steps ----------


def welcome() -> None:
    console.print(
        Panel.fit(
            "[bold]Lightwork installer[/bold]\n\n"
            "Next you'll pick a setup mode: a quick consumer flow (a few\n"
            "questions, safe defaults) or advanced (configure every model,\n"
            "channel, safety level, and budget). Re-run any time with\n"
            "[bold]maverick init[/bold].",
            border_style="cyan",
        )
    )


def pick_deployment() -> str:
    choices = [
        "desktop  - This computer (recommended for first-time users)",
        "docker   - Local Docker container (isolated, easy to remove)",
        "vps      - Remote server you own (always-on)",
        "phone    - Phone companion (Lightwork runs on desktop/VPS; phone is a frontend)",
    ]
    # Default to the previously recorded target on a re-run of `maverick init`
    # (what [deployment] type was written for).
    default = None
    try:
        from maverick.config import get_deployment

        prior = get_deployment().get("type", "")
        default = next((c for c in choices if c.split()[0] == prior), None)
    except Exception:  # pragma: no cover -- never block the wizard
        default = None
    pick = _q_select("Where will Lightwork run?", choices, default=default)
    return pick.split()[0]


def pick_providers() -> list[str]:
    choices = []
    for prov_id, info in catalog.PROVIDERS.items():
        tag = "[ready]" if info["status"] == "ready" else "[v0.2]"
        choices.append(f"{prov_id:10} {tag} - {info['label']}")

    picks = _q_checkbox(
        "Which AI providers do you want to use?",
        choices,
        default=[choices[0]],
    )
    return [p.split()[0] for p in picks]


_LOCAL_FIRST_PROVIDERS = frozenset({"ollama", "tgi"})


def _local_first_model(providers: list[str]) -> str | None:
    """Return the default local model spec for the selected providers."""
    for prov in providers:
        if prov not in _LOCAL_FIRST_PROVIDERS:
            continue
        info = catalog.PROVIDERS.get(prov)
        models = (info or {}).get("models") or []
        if models:
            return f"{prov}:{models[0]['id']}"
    return None


def pick_models_per_role(providers: list[str]) -> dict[str, str]:
    console.print()
    if _q_confirm(
        "Use the default model for each role?",
        default=True,
    ):
        return {}

    console.print()
    console.print(
        "[bold]Pick a model for each agent role.[/bold] "
        "Large models (orchestrator, revisor) suit big roles; "
        "cheap roles (summarizer) can use smaller ones.\n"
    )

    role_models: dict[str, str] = {}
    for role, hint in catalog.ROLES:
        choices: list[str] = []
        for prov in providers:
            info = catalog.PROVIDERS.get(prov)
            if not info:
                continue
            tag = "" if info["status"] == "ready" else " [v0.2]"
            for m in info["models"]:
                choices.append(f"{prov}:{m['id']}{tag}  - {m['notes']}")
        choices.append("[skip - use default]")

        default_spec = catalog.default_for_role(role)
        default_choice = next((c for c in choices if c.startswith(default_spec)), choices[0])

        pick = _q_select(f"  {role}: {hint}", choices, default=default_choice)
        if pick.startswith("[skip"):
            continue
        role_models[role] = pick.split()[0]
    return role_models


# Inbound channels enforce a sender allowlist (fail-closed): only these
# IDs can drive the agent and spend budget. The wizard must collect it or
# the channel refuses to start. See maverick_channels.base.is_allowed.
_ALLOWLIST_CHANNELS = {
    "telegram",
    "discord",
    "slack",
    "signal",
    "email",
    "matrix",
    "bluesky",
    "mastodon",
    "irc",
    "imessage",
    "sms",
    "whatsapp",
    "whatsapp_cloud",
    "threads",
    "rcs",
}
_ALLOWLIST_HINT = {
    "telegram": "numeric Telegram user IDs",
    "discord": "numeric Discord user IDs",
    "slack": "Slack user IDs, e.g. U01ABC",
    "signal": "phone numbers, e.g. +12345550199",
    "email": "email addresses",
    "matrix": "MXIDs, e.g. @you:matrix.org",
    "bluesky": "handles or DIDs",
    "mastodon": "acct names, e.g. you@instance",
    "irc": "authenticated IRC account names (requires IRCv3 account-tag)",
    "imessage": "phone numbers or emails",
    "sms": "phone numbers, e.g. +14155551234",
    "whatsapp": "senders as Twilio sends them, e.g. whatsapp:+14155551234",
    "whatsapp_cloud": "bare wa_id digits, e.g. 14155551234",
    "threads": "Threads usernames of allowed authors",
    "rcs": "E.164 MSISDNs, e.g. +14155551234",
}


def _channel_base_cfg(ch_id: str, envs: set[str]) -> dict[str, Any]:
    """Build the channel-specific config for ``ch_id`` (excluding allowlists).

    May add provider-specific env vars to ``envs`` (e.g. voice keys).
    """
    cfg: dict[str, Any] = {"enabled": True}

    if ch_id == "telegram":
        cfg["bot_token"] = "${TELEGRAM_BOT_TOKEN}"
    elif ch_id == "discord":
        cfg["bot_token"] = "${DISCORD_BOT_TOKEN}"
    elif ch_id == "slack":
        cfg["app_token"] = "${SLACK_APP_TOKEN}"
        cfg["bot_token"] = "${SLACK_BOT_TOKEN}"
    elif ch_id == "signal":
        cfg["phone_number"] = _q_text("  Signal phone number (e.g., +12345550199)", default="")
    elif ch_id == "email":
        cfg["imap_host"] = _q_text("  IMAP server", default="imap.gmail.com")
        cfg["smtp_host"] = _q_text("  SMTP server", default="smtp.gmail.com")
        cfg["smtp_port"] = _safe_int(_q_text("  SMTP port", default="465"), default=465)
        cfg["imap_user"] = "${EMAIL_USER}"
        cfg["imap_password"] = "${EMAIL_APP_PASSWORD}"
        cfg["smtp_user"] = "${EMAIL_USER}"
        cfg["smtp_password"] = "${EMAIL_APP_PASSWORD}"
        cfg["poll_interval"] = 30
    elif ch_id == "matrix":
        cfg["homeserver"] = _q_text("  Matrix homeserver URL", default="https://matrix.org")
        cfg["user_id"] = _q_text("  Matrix user ID (e.g., @you:matrix.org)", default="")
        cfg["access_token"] = "${MATRIX_ACCESS_TOKEN}"
    elif ch_id == "bluesky":
        cfg["handle"] = "${BLUESKY_HANDLE}"
        cfg["password"] = "${BLUESKY_PASSWORD}"
        cfg["poll_interval"] = 60
    elif ch_id == "mastodon":
        cfg["instance"] = _q_text(
            "  Mastodon instance URL",
            default="https://mastodon.social",
        )
        cfg["access_token"] = "${MASTODON_ACCESS_TOKEN}"
        cfg["poll_interval"] = 30
    elif ch_id == "voice":
        provider = (
            _q_text(
                "  Voice provider (vapi, retell, bland)",
                default="vapi",
            )
            .strip()
            .lower()
            or "vapi"
        )
        cfg["provider"] = provider
        key_env = {
            "vapi": "VAPI_API_KEY",
            "retell": "RETELL_API_KEY",
            "bland": "BLAND_API_KEY",
        }.get(provider, "VAPI_API_KEY")
        # Collect the provider-specific key so the wizard actually prompts
        # for it; otherwise a retell/bland config references ${RETELL_API_KEY}
        # / ${BLAND_API_KEY} that the user was never asked to enter.
        envs.add(key_env)
        cfg["api_key"] = "${" + key_env + "}"
        # Inbound webhook auth is Vapi-shaped today; keep the token ref.
        cfg["webhook_token"] = "${VAPI_WEBHOOK_TOKEN}"
        cfg["phone_number"] = _q_text(
            "  Phone number (E.164, optional)",
            default="",
        )
        cfg["assistant_id"] = _q_text(
            "  Assistant/agent ID (optional)",
            default="",
        )
        cfg["port"] = _safe_int(
            _q_text("  Webhook port", default="8770"),
            default=8770,
        )
    elif ch_id == "whatsapp":
        cfg["account_sid"] = "${TWILIO_ACCOUNT_SID}"
        cfg["auth_token"] = "${TWILIO_AUTH_TOKEN}"
        cfg["from_number"] = _q_text("  WhatsApp 'from' (e.g., whatsapp:+14155238886)", default="")
        cfg["port"] = _safe_int(_q_text("  Webhook port", default="8765"), default=8765)
    elif ch_id == "sms":
        cfg["account_sid"] = "${TWILIO_ACCOUNT_SID}"
        cfg["auth_token"] = "${TWILIO_AUTH_TOKEN}"
        cfg["from_number"] = _q_text("  SMS 'from' number (e.g., +14155551234)", default="")
        cfg["port"] = _safe_int(_q_text("  Webhook port", default="8766"), default=8766)
    elif ch_id == "imessage":
        cfg["poll_interval"] = 5

    return cfg


def _channel_allowlist(ch_id: str, cfg: dict[str, Any]) -> None:
    """Prompt for and apply per-channel sender allowlists in-place on ``cfg``."""
    if ch_id in _ALLOWLIST_CHANNELS:
        hint = _ALLOWLIST_HINT.get(ch_id, "sender IDs")
        raw_ids = _q_text(
            f"  Allowed senders, comma-separated ({hint}) — only these can drive the agent",
            default="",
        )
        ids = _csv_list(raw_ids)
        if ids:
            cfg["allowed_user_ids"] = ids
        else:
            env_name = (
                "IRC_ALLOWED_ACCOUNTS" if ch_id == "irc" else ch_id.upper() + "_ALLOWED_USER_IDS"
            )
            console.print(
                "  [yellow]No allowlist set — this channel will refuse "
                f"all senders until you set {env_name} "
                "or add allowed_user_ids to config.[/yellow]"
            )
    elif ch_id == "voice":
        raw = _q_text(
            "  Allowed caller numbers (E.164, comma-separated; blank = any authenticated caller)",
            default="",
        )
        callers = _csv_list(raw)
        if callers:
            cfg["allowed_callers"] = callers


def pick_channels(deployment: str) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Returns (channels_config, env_vars_needed)."""
    console.print()
    if deployment == "desktop":
        if not _q_confirm(
            "Enable any messaging channels (Telegram, Discord, Signal, etc.) for remote access?",
            default=False,
        ):
            return {}, set()
    elif deployment == "phone":
        console.print("[bold]Phone-companion mode:[/bold] pick the channels your phone will use.\n")

    selectable = [c for c in CHANNELS if c[0] not in EXPERIMENTAL_CHANNELS]
    if _q_confirm(
        "Show experimental/unfinished channels (WhatsApp, SMS, iMessage)? "
        "These are scaffolds and may not work end-to-end.",
        default=False,
    ):
        selectable += [
            (ch_id, f"{label} [experimental]", envs)
            for ch_id, label, envs in CHANNELS
            if ch_id in EXPERIMENTAL_CHANNELS
        ]

    choices = [f"{ch_id:9} - {label}" for ch_id, label, _ in selectable]
    picked = _q_checkbox("Which channels do you want to enable?", choices)
    picked_ids = [p.split()[0] for p in picked]

    channels: dict[str, dict[str, Any]] = {}
    envs: set[str] = set()

    for ch_id in picked_ids:
        info = next((c for c in CHANNELS if c[0] == ch_id), None)
        if info is None:
            continue
        envs.update(info[2])

        cfg = _channel_base_cfg(ch_id, envs)
        _channel_allowlist(ch_id, cfg)

        channels[ch_id] = cfg

    return channels, envs


def pick_safety() -> dict[str, Any]:
    pick = _q_select(
        "Safety profile:",
        [
            "strict     - Block on any medium+ threat. Best for sensitive use.",
            "balanced   - Block on high+ threats. Recommended default.",
            "permissive - Block only on critical threats. For research/experimentation.",
            "off        - No safety scanning. NOT recommended.",
        ],
        default="balanced   - Block on high+ threats. Recommended default.",
    )
    profile = pick.split()[0]
    threshold = {
        "strict": "medium",
        "balanced": "high",
        "permissive": "critical",
        "off": "critical",
    }[profile]
    # Agent compartments: when one agent's scan blocks a threat, record its
    # signature so the rest of the swarm is immune to the same attack for the
    # run. Moot with safety off (nothing scans). Off by default.
    compartments = False
    if profile != "off":
        compartments = _q_confirm(
            "  Enable agent compartments (one agent's blocked threat immunizes "
            "the rest of the swarm for the run)?",
            default=False,
        )
    return {
        "profile": profile,
        "block_threshold": threshold,
        "scan_input": profile != "off",
        "scan_tool_calls": profile != "off",
        "scan_output": profile != "off",
        "compartments": compartments,
    }


def pick_signed_skills() -> dict[str, Any]:
    """Optional Ed25519 signing policy for installed skills.

    Returns a dict written under ``[skills]``. Defaults keep current
    behavior (no trusted publishers, unsigned skills allowed)."""
    console.print()
    console.print(
        "[dim]Signed skills: a publisher can sign a SKILL.md with an Ed25519 "
        "key. Paste trusted publisher public keys (hex) to verify against; "
        "leave blank to skip.[/dim]"
    )
    raw = _q_text("  Trusted skill publisher pubkeys (comma-separated hex)", default="")
    trusted = _csv_list(raw)
    require = _q_confirm(
        "  Reject unsigned skills (only install signed + trusted ones)?",
        default=False,
    )
    require_catalog = _q_confirm(
        "  Require a verified signature for catalog installs (even with no trusted keys above)?",
        default=False,
    )
    return {
        "trusted_pubkeys": trusted,
        "require_signed": require,
        "require_signed_catalog": require_catalog,
    }


def pick_budget() -> dict[str, Any]:
    console.print()
    console.print("[dim]Per-run caps. Edit later in ~/.maverick/config.toml.[/dim]")
    budget: dict[str, Any] = {
        "max_dollars": _safe_float(
            _q_text("  Max $ per run", default="5.0"),
            default=5.0,
        ),
        "max_wall_seconds": _safe_float(
            _q_text("  Max wall-clock seconds per run", default="3600"),
            default=3600.0,
        ),
        "max_tool_calls": _safe_int(
            _q_text("  Max tool calls per run", default="500"),
            default=500,
        ),
    }
    # Billing-grade accounting is the safe default. Preserve an explicit false
    # for legacy/custom gateways, where estimates remain provenance-labelled.
    budget["strict_pricing"] = _q_confirm(
        "  Strict pricing? Fail closed if a model has no verified price "
        "(recommended; choose No only for estimate-only custom gateways)",
        default=True,
    )
    return budget


def pick_capabilities() -> dict[str, bool]:
    """Opt-in to high-impact tools that ship disabled.

    Computer-use, browser, ROS robotics, and code-exec tools have real safety
    side effects (mouse/keyboard control, arbitrary navigation, robot/simulator
    commands, or sandboxed tool orchestration), so they default to off until you
    explicitly enable them.
    """
    console.print()
    use_computer = _q_confirm(
        "Enable computer-use? Lets the agent see your screen and drive the mouse/keyboard.",
        default=False,
    )
    use_browser = _q_confirm(
        "Enable browser? Lets the agent navigate the web via Playwright.",
        default=False,
    )
    use_ros = _q_confirm(
        "Enable ROS robotics? Lets the agent publish topics or call services "
        "against ROS_BRIDGE_URL over rosbridge. Only enable for trusted robot/sim "
        "operators.",
        default=False,
    )
    use_code_exec = _q_confirm(
        "Enable code_exec? Lets the agent run a sandboxed Python script that "
        "orchestrates several tool calls in one turn (keeps large intermediate "
        "outputs out of context). Runs code in the sandbox, like the shell tool.",
        default=False,
    )
    # The embedded-device tool (JTAG/I2C) is always registered, but its
    # DESTRUCTIVE ops (flash write, target reset) stay refused until the
    # operator opts in here -> [embedded] allow_flash. Default off.
    embedded_flash = _q_confirm(
        "Allow embedded-device flashing? The JTAG tool can erase/reflash YOUR "
        "OWN connected device's firmware (OpenOCD). Off = it refuses flash/reset.",
        default=False,
    )
    deferred_tools = _q_confirm(
        "Use deferred tool loading? The model sees the core toolset and "
        "discovers the 400+ SaaS connectors on demand via find_tools -- "
        "cuts per-call token cost ~60%. Disable only if you want every "
        "connector schema offered on every turn.",
        default=True,
    )
    jd_hiring = _q_confirm(
        "Enable JD hiring in the Agent Factory? HR can upload a job "
        "description to match it against the specialist roster or draft a "
        "new specialist from it (drafts stay envelope-clamped and require "
        "human approval to save).",
        default=True,
    )
    return {
        "computer_use": use_computer,
        "browser": use_browser,
        "ros": use_ros,
        "code_exec": use_code_exec,
        "embedded_flash": embedded_flash,
        "deferred_tools": deferred_tools,
        "jd_hiring": jd_hiring,
    }


def pick_self_learning() -> dict[str, Any]:
    """Configure default-on governed capability learning.

    Catalog skills, API discovery, pack provisioning and local distillation are
    on by default. Generating executable tools remains a separate trust choice.
    Returns a dict written under ``[self_learning]``.

    MCP-server acquisition (#422) is a separate, even-higher-trust knob: it
    re-enables the capability #392 disabled, but only for curated, hash-pinned
    catalog servers AND only after explicit operator approval. It ships OFF
    independently of the self-learning master switch.
    """
    console.print()
    console.print(
        "[dim]Self-learning lets the agent close capability gaps on its own: "
        "install skills, discover REST APIs, even write & run new tools. "
        "Governed learning is ON by default; executable tool generation and "
        "external MCP processes remain separate opt-ins.[/dim]"
    )
    enable = _q_confirm("Enable governed self-learning?", default=True)
    if not enable:
        return {"enable": False}
    create_tools = _q_confirm(
        "  Allow the agent to GENERATE and run new tools (full autonomy)?",
        default=False,
    )
    preflight = _q_confirm(
        "  Pre-acquire likely skills before each run (local catalog pass)?",
        default=True,
    )
    allow_provider_egress = _q_confirm(
        "  Allow learning helpers to make EXTRA model calls with redacted task "
        "or result text (may use another configured provider)?",
        default=False,
    )
    provision_packs = _q_confirm(
        "  Equip newly onboarded packs: install the catalog skills and "
        "synthesize the tools a pack's workflow needs, at approval time?",
        default=True,
    )
    console.print(
        "[dim]  MCP acquisition: the agent may PROPOSE adding a curated, "
        "hash-pinned catalog MCP server (never a free-text command). Each one "
        "still needs your explicit approval before it starts.[/dim]"
    )
    allow_mcp = _q_confirm(
        "  Allow agent to propose catalog MCP servers (operator-approved)?",
        default=False,
    )
    distill_local = _q_confirm(
        "  Distill successful runs into local skills? After a successful run, "
        "save a reusable skill under ~/.maverick/learned-skills.",
        default=True,
    )
    return {
        "enable": True,
        "preflight": preflight,
        "create_tools": create_tools,
        "provision_packs": provision_packs,
        "allow_mcp_acquisition": allow_mcp,
        "allow_provider_egress": allow_provider_egress,
        "distill_local": distill_local,
        "max_acquisitions": 5,
    }


_EKKO_BLOCKED_APPS = [
    "email",
    "outlook",
    "gmail",
    "chat",
    "teams",
    "slack",
    "crm",
    "salesforce",
    "erp",
    "sap",
    "database",
]


def pick_ekko() -> dict[str, Any]:
    """Configure explicit enrollment for the Ekko work-discovery sensor.

    The wizard never starts a collector or asks the operating system for
    monitoring permissions. It only writes policy; the client enrolls a named
    device and starts a reviewed collector separately with ``maverick ekko``.
    """
    console.print()
    console.print(
        "[dim]Ekko finds repeatable work that Lightwork could improve or "
        "automate. It records authorized application names and timing only by "
        "default -- never screen pixels, window titles, clipboard, keystrokes, "
        "URLs, or document contents. Data stays local and recommendations "
        "remain drafts until a person approves them.[/dim]"
    )
    if not _q_confirm(
        "Enable Ekko policy for explicitly enrolled devices?",
        default=False,
    ):
        return {"enable": False}

    requested_apps = _csv_list(
        _q_text(
            "  Canonical applications Ekko may observe (for example excel, "
            "powerpoint, chrome; blank = deny all)",
            default="",
        ),
        lower=True,
    )[:128]
    from maverick.work_discovery import KNOWN_APPS

    allowed_apps = [app for app in requested_apps if app in KNOWN_APPS]
    dropped_apps = sorted(set(requested_apps) - set(allowed_apps))
    if dropped_apps:
        console.print(
            "[yellow]  Ignored unknown application IDs: "
            + ", ".join(dropped_apps)
            + ". Platform collectors map executable/bundle IDs to the "
            "canonical names before capture.[/yellow]"
        )
    if not allowed_apps:
        console.print(
            "[yellow]  No applications authorized. Ekko remains unable to "
            "enroll or record until [ekko].allowed_apps is configured.[/yellow]"
        )
    capture_pick = _q_select(
        "  Capture mode",
        [
            "application_metadata - application identity + timing only",
            "guided - accept user/integration supplied action labels",
        ],
        default="application_metadata - application identity + timing only",
    )
    capture_level = capture_pick.split()[0]
    if capture_level not in {"application_metadata", "guided"}:
        capture_level = "application_metadata"

    retention_days = max(
        2,
        min(
            30,
            _safe_int(
                _q_text(
                    "  Raw event retention in days (2-30)",
                    default="14",
                ),
                default=14,
            ),
        ),
    )
    min_occurrences = max(
        2,
        min(
            100,
            _safe_int(
                _q_text(
                    "  Repetitions before Ekko suggests a process (2-100)",
                    default="3",
                ),
                default=3,
            ),
        ),
    )
    min_distinct_days = max(
        2,
        min(
            retention_days,
            _safe_int(
                _q_text(
                    "  Distinct days required for a suggestion (2-30)",
                    default="2",
                ),
                default=2,
            ),
        ),
    )
    return {
        "enable": True,
        "retention_days": retention_days,
        "enrollment_days": 30,
        "min_occurrences": min_occurrences,
        "min_distinct_days": min_distinct_days,
        "poll_interval_seconds": 5,
        "capture_level": capture_level,
        "allowed_apps": allowed_apps,
        "blocked_apps": list(_EKKO_BLOCKED_APPS),
        # Reserved for a future audited provider-summary path. Local discovery
        # has no egress implementation and this must remain false.
        "provider_egress": False,
    }


def pick_automation_import() -> dict[str, Any]:
    """Opt-in to importing clients' existing automations into Lightwork.

    Off by default. When on, ``maverick import`` can pull workflow definitions
    from platforms that expose them (n8n/Make/Workato/Power Automate/UiPath) and
    turn each into a Lightwork template, plus connect-and-trigger for Zapier/
    Notion. It reaches out to third-party platforms and writes user templates,
    so it ships disabled. Returns a dict written under ``[automation_import]``.
    """
    console.print()
    console.print(
        "[dim]Automation import pulls workflows your clients already built "
        "(n8n/Make/Workato/Power Automate/UiPath) into Lightwork templates, and "
        "lets Zapier/Notion trigger Lightwork. It calls third-party APIs and "
        "writes templates, so it's OFF by default.[/dim]"
    )
    enable = _q_confirm("Enable automation import?", default=False)
    if not enable:
        return {"enable": False}
    create_schedules = _q_confirm(
        "  Auto-create Lightwork schedules for imported cron triggers? "
        "(off = import the template, you activate the schedule yourself)",
        default=False,
    )
    return {"enable": True, "create_schedules": create_schedules}


def pick_event_triggers() -> dict[str, Any]:
    """Opt-in to polled event triggers ("when X appears, run this workflow").

    Off by default. When on, the dashboard can bind a workflow to an event
    source (e.g. a JSON REST feed or an RSS feed) and the app polls it on a
    background tick, firing the workflow per new item. It reaches out to
    third-party endpoints on a schedule, so it ships disabled. Returns a dict
    written under ``[event_triggers]``.
    """
    console.print()
    console.print(
        "[dim]Event triggers poll a source (a JSON API or RSS feed) and run a "
        "workflow for each new item -- the 'when a new lead/row/post appears' "
        "half of automation. The app polls on a background tick. It calls "
        "third-party endpoints on a schedule, so it's OFF by default.[/dim]"
    )
    enable = _q_confirm("Enable event triggers?", default=False)
    return {"enable": bool(enable)}


def pick_flows() -> dict[str, Any]:
    """Opt-in to the flow engine (multi-step visual workflows).

    Off by default. When on, the dashboard's visual designer can build and run
    flows -- a deterministic graph of agent/action/branch/loop/approval nodes,
    schedulable on a cron, with self-rewrite + rollback. When a flow pauses for
    approval it can post signed Approve/Reject links to a channel; those links
    need the dashboard's externally-reachable base URL, collected here and
    written as ``[flows] public_url``. Optionally lets the loop self-correct
    (``[flows] auto_evolve``). Returns a dict written under ``[flows]``.
    """
    console.print()
    console.print(
        "[dim]The flow engine runs multi-step visual workflows (agent + action + "
        "branch + loop + approval), schedulable on a cron, with self-rewrite and "
        "rollback. OFF by default.[/dim]"
    )
    if not _q_confirm("Enable the flow engine?", default=False):
        return {"enable": False}
    out: dict[str, Any] = {"enable": True}
    console.print(
        "[dim]Retries can recover transient agent/tool failures, but every retry "
        "can spend provider budget or repeat side effects. Keep the per-node cap "
        "small unless operations explicitly need more.[/dim]"
    )
    retry_cap = _q_text("Max retries per flow node", default="5").strip()
    try:
        out["max_node_retries"] = max(0, min(50, int(retry_cap or "5")))
    except ValueError:
        out["max_node_retries"] = 5
    console.print(
        "[dim]For actionable channel approvals (clickable Approve/Reject links), "
        "the dashboard needs its externally-reachable base URL. Leave blank to "
        "skip -- approvals then happen from the dashboard.[/dim]"
    )
    url = _q_text(
        "Public base URL for approval links (e.g. https://ops.acme.com)", default=""
    ).strip()
    if url:
        out["public_url"] = url
    console.print(
        "[dim]Self-correction lets a scheduled pass autonomously revert a node "
        "rewrite whose grounded outcomes measurably regressed -- the loop undoes "
        "its own bad changes. Off by default; surfaced proposals + human apply "
        "work without it.[/dim]"
    )
    if _q_confirm(
        "Let flows self-correct (autonomous revert of a regressed rewrite)?", default=False
    ):
        out["auto_evolve"] = True
        # Full autonomy: also APPLY a proposal forward (soften a failing action to
        # an agent), not just revert. Strictly opt-in on top of self-correction,
        # since a bad apply is auto-reverted only if the revert side is on.
        if _q_confirm(
            "Also let flows autonomously APPLY a learned rewrite (not just revert)?", default=False
        ):
            out["auto_apply"] = True
    return out


def pick_knowledge() -> dict[str, Any]:
    """Opt-in to per-domain vector RAG (the maverick-knowledge package).

    Off by default; the kernel never requires maverick-knowledge. When on,
    ``embedder`` selects the embedding provider (hosted Voyage / local /
    deterministic) and ``store`` selects the vector backend (sqlite / pgvector).
    Returns a dict written under ``[knowledge]``.
    """
    console.print()
    console.print(
        "[dim]Knowledge RAG gives the agent per-domain document recall via a "
        "vector store (the knowledge_search tool). Needs the maverick-knowledge "
        "package + an embedder. OFF by default.[/dim]"
    )
    if not _q_confirm("Enable per-domain knowledge RAG?", default=False):
        return {"enable": False}
    out: dict[str, Any] = {"enable": True}
    embedder = _q_select(
        "  Embedder:",
        [
            "hosted        - Voyage AI (needs VOYAGE_API_KEY)",
            "local         - on-box sentence-transformers (no API key)",
            "deterministic - hashing stub (offline; low quality, for testing)",
        ],
        default="hosted        - Voyage AI (needs VOYAGE_API_KEY)",
    ).split()[0]
    out["embedder"] = embedder
    store = _q_select(
        "  Vector store:",
        [
            "sqlite   - single-file, local (default; fine to a few million chunks)",
            "pgvector - Postgres + pgvector (enterprise: one DB to encrypt/back up/audit)",
            "qdrant   - Qdrant (scale / dedicated retrieval infra)",
        ],
        default="sqlite   - single-file, local (default; fine to a few million chunks)",
    ).split()[0]
    out["store"] = store
    if store == "pgvector":
        dsn = _q_text("  pgvector DSN (blank = MAVERICK_KNOWLEDGE_DSN env)", default="").strip()
        if dsn:
            out["dsn"] = dsn
    elif store == "qdrant":
        url = _q_text("  Qdrant URL (blank = QDRANT_URL env)", default="").strip()
        if url:
            out["url"] = url
    return out


def pick_oauth_vault() -> dict[str, Any]:
    """Opt-in to sealing captured OAuth tokens in the per-tenant vault.

    Off by default. When on, the OAuth helper seals access/refresh tokens
    encrypted-at-rest under the tenant's DEK (one data key per tenant) instead
    of writing them to a plaintext file. Returns a dict written under
    ``[oauth]``.
    """
    console.print()
    console.print(
        "[dim]The OAuth token vault seals captured access/refresh tokens "
        "encrypted-at-rest under each tenant's own key (no plaintext token "
        "files, no cross-tenant readability). Recommended for hosted/multi-"
        "tenant deploys. OFF by default.[/dim]"
    )
    return {"vault": _q_confirm("Seal OAuth tokens in the per-tenant vault?", default=False)}


def pick_governed_connectors() -> dict[str, Any]:
    """Opt-in to routing live system-of-record writes through governed Actions.

    Off by default. When on, a selected enterprise connector (Salesforce,
    ServiceNow) is registered as a typed governed Action: a write previews its
    effect, hits the approval floor (``[actions] require_approval_at``), and
    records a tamper-evident lineage link -- instead of a bare confirm-gated
    tool call. Returns a dict written under ``[governed_connectors]``.
    """
    try:
        from maverick.governed_rest import available_rest_connectors

        choices = available_rest_connectors()
    except Exception:  # pragma: no cover -- never block the wizard
        choices = ["salesforce", "servicenow"]
    console.print()
    console.print(
        "[dim]Governed connectors route a live system-of-record write "
        f"({', '.join(choices)}) through simulate -> approve -> commit -> "
        "lineage: the write hits the approval floor and is recorded in a "
        "tamper-evident chain. OFF by default.[/dim]"
    )
    if not _q_confirm("Enable governed system-of-record connectors?", default=False):
        return {"enable": False}
    selected = [
        c for c in choices if _q_confirm(f"  Register {c} as a governed connector?", default=False)
    ]
    # Standing approver of record: when these connectors are wrapped in the live
    # tool path, a write is approval-gated against this identity (the agent can't
    # self-approve). Blank = writes are previewed but refused until an operator
    # commits them out of band.
    approver = _q_text(
        "  Approver of record for governed writes (blank = refuse agent writes "
        "without out-of-band approval):",
        default="",
    ).strip()
    # Restore points: one read-only GET before an in-place update, so the write
    # carries a real undo instead of an asserted one. Without it every update is
    # irreversible and routes to a human -- safe, but nothing ever earns
    # autonomy. Off is the right answer for a write-only service account.
    console.print(
        "[dim]  Restore points read a record's prior values before changing "
        "them (one read-only GET), so the write ships a genuine undo. Off = "
        "every update is treated as irreversible.[/dim]"
    )
    restore_points = _q_confirm("  Capture restore points before in-place updates?", default=True)
    return {
        "enable": True,
        "connectors": selected,
        "approver": approver,
        "restore_points": restore_points,
    }


def pick_durable() -> dict[str, Any]:
    """Opt-in to durable execution (crash-resume via checkpoints).

    Off by default. When on, a long-running goal checkpoints its loop state at
    each step so ``maverick resume`` continues from where a crash left off
    instead of re-running from the start. Returns a dict written under
    ``[durable]``.
    """
    console.print()
    console.print(
        "[dim]Durable execution checkpoints a goal's progress so a crash or "
        "restart resumes from the last step instead of starting over. Adds a "
        "small write per step; OFF by default.[/dim]"
    )
    if not _q_confirm("Enable durable execution (crash-resume)?", default=False):
        return {"enabled": False}
    return {"enabled": True, "keep_last": 5}


def pick_workspace_journal() -> dict[str, Any]:
    """Opt-in workspace mutation journal (pre-images + ``maverick journal
    revert``).

    Off by default. Returns keys for the ``[workspace]`` table (``journal``,
    ``journal_strict``); written only when enabled.
    """
    console.print()
    console.print(
        "[dim]The workspace journal captures every file the agents overwrite "
        "or create through the file tools — BEFORE the mutation — so "
        "`maverick journal revert` can restore a goal's workspace to its "
        "pre-run state without a prior snapshot. Shell-driven mutations "
        "(rm/mv/redirects) are not covered — the audit trail records those "
        "commands. Bounded disk per goal; OFF by default.[/dim]"
    )
    if not _q_confirm(
            "Enable the workspace mutation journal (undoable file edits)?",
            default=False):
        return {"journal": False}
    strict = _q_confirm(
        "Strict mode: REFUSE any write whose pre-image cannot be journaled? "
        "(makes reversibility a guarantee; a full journal then blocks writes)",
        default=False)
    out: dict[str, Any] = {"journal": True}
    if strict:
        out["journal_strict"] = True
    return out


def pick_assessments() -> dict[str, Any]:  # noqa: C901 - independent product prompts
    """Assessment-flow assists (doc discovery + learning), both default ON.

    Doc discovery searches connected sources (Microsoft Graph / Slack /
    Google Drive) for the SOW/contract/DPA related to an assessment subject;
    it only activates when a source has credentials. Learning suggests
    answers from the org's OWN past assessments -- advisory only. Returns a
    dict written under ``[assessments]`` only on divergence from defaults.
    """
    console.print()
    console.print(
        "[dim]Assessment assists: doc discovery finds the SOW/contract/DPA in "
        "your connected Microsoft 365 / Slack / Google Drive when someone "
        "fills an assessment; learning suggests answers from your own past "
        "assessments (a human still reviews everything). Both ON by default; "
        "discovery stays inert until a source is connected.[/dim]"
    )
    discovery = _q_confirm("Enable assessment doc discovery?", default=True)
    learn = _q_confirm("Enable assessment learning (suggest from past assessments)?", default=True)
    ops = _q_confirm(
        "Enable the privacy ops record types (DPA review, AI registry, RoPA, DSAR tracker)?",
        default=True,
    )
    consent_preferences = _q_confirm(
        "Enable consumer Consent & Preferences records, receipts, tracker "
        "governance, and fail-closed enforcement?",
        default=False,
    )
    consent_product: dict[str, Any] | None = None
    consent_publishing = False
    consent_public_api = False
    consent_identity = False
    consent_trust_center = False
    consent_trust_center_branding = False
    consent_native_sdk: dict[str, Any] | None = None
    if consent_preferences:
        consent_publishing = _q_confirm(
            "Enable versioned property, policy, notice, translation, and publication governance?",
            default=True,
        )
        consent_web_publish_settings = consent_publishing and _q_confirm(
            "Enable governed website publish settings for SDK version, "
            "geolocation reconsent, analytics, cookie scope, SPA reload, CSP "
            "nonce requirements, and automatic refresh?",
            default=False,
        )
        consent_public_api = consent_publishing and _q_confirm(
            "Enable the external consumer consent API? Property/origin "
            "bindings are still required before it serves traffic.",
            default=False,
        )
        consent_identity = consent_public_api and _q_confirm(
            "Enable verified consumer identity federation and one-time "
            "cross-domain/device pairing? OIDC keys and subject-digest custody "
            "must still be injected by the deployment.",
            default=False,
        )
        consent_identity_config: dict[str, Any] | None = None
        if consent_identity:
            consent_identity_config = {
                "enable": True,
                "issuer": _q_text(
                    "  Consumer OIDC issuer (exact signed iss value)",
                    default="",
                ).strip(),
                "audience": _q_text(
                    "  Consumer OIDC audience (exact app/API audience)",
                    default="",
                ).strip(),
                "jwks_origin": _q_text(
                    "  Pinned HTTPS JWKS origin (origin only; keys are injected at runtime)",
                    default="",
                ).strip(),
                "token_ttl_seconds": 600,
                "link_lifecycle_retention_seconds": 31_536_000,
            }
        consent_scanner = _q_confirm(
            "Enable scheduled website tracker scanning? Only properties "
            "explicitly enrolled by the privacy owner are eligible.",
            default=False,
        )
        consent_scan_scheduler = consent_scanner and _q_confirm(
            "Run the bounded consent scan scheduler automatically with the "
            "dashboard? Enrolled schedules remain the only work authority.",
            default=False,
        )
        consent_scan_notice_proposals = consent_scanner and _q_confirm(
            "Enable independently reviewed scan-to-cookie/privacy disclosure "
            "metadata proposals? This creates structured local proposals only; "
            "it does not generate legal copy or publish a notice or site change.",
            default=False,
        )
        consent_application_scanner = _q_confirm(
            "Enable bounded mobile and OTT/CTV application manifest analysis? "
            "Operators must supply already-extracted manifests; this does not "
            "execute APKs/IPAs, inspect devices, or classify trackers or law.",
            default=False,
        )
        consent_signals = _q_confirm(
            "Enable GPC and DNT signal interpretation?",
            default=True,
        )
        consent_adtech_standards = _q_confirm(
            "Enable advertising interoperability (GPP, TCF, and Google "
            "Consent Mode) after its conformance vectors are installed?",
            default=False,
        )
        consent_native_enabled = (
            consent_publishing
            and consent_public_api
            and _q_confirm(
                "Enable the native mobile and OTT/CTV consent SDK runtime? "
                "Production readiness requires an injected platform attestation "
                "verifier; GPP/TCF also requires an injected standards-wire verifier.",
                default=False,
            )
        )
        if consent_native_enabled:
            receipt_key_id = (
                _q_text(
                    "  Native receipt signing key id",
                    default="native-consent-receipt",
                ).strip()
                or "native-consent-receipt"
            )
            receipt_key_secret_ref = (
                _q_text(
                    "  Environment variable holding the native receipt root secret",
                    default="MAVERICK_NATIVE_CONSENT_RECEIPT_KEY",
                ).strip()
                or "MAVERICK_NATIVE_CONSENT_RECEIPT_KEY"
            )
            require_profile_linking = consent_identity and _q_confirm(
                "Require each native consent request to link to a verified consumer profile? "
                "The deployment must inject the profile-linking callback.",
                default=False,
            )
            consent_native_sdk = {
                "enable": True,
                "receipt_key_id": receipt_key_id,
                "receipt_key_revision": 1,
                "receipt_key_secret_ref": receipt_key_secret_ref,
                "max_applications": 1_000,
                "max_devices": 100_000,
                "max_requests": 1_000_000,
                "requests_per_minute": 120,
                "require_profile_linking": require_profile_linking,
                "require_rights_requests": False,
            }
        consent_delivery = _q_confirm(
            "Enable downstream preference delivery? Destinations remain "
            "disabled until separately enrolled.",
            default=False,
        )
        consent_destination_config: dict[str, Any] | None = None
        if consent_delivery and _q_confirm(
            "Configure named Salesforce, Marketo, Adobe Experience Platform, "
            "or Snowflake consent destinations? Exact destination enrollment, "
            "signing keys, deployment-held credentials, and an independent "
            "reconciliation authority are still required.",
            default=False,
        ):
            destination_providers = _q_checkbox(
                "Select named consent delivery destinations",
                [
                    "salesforce",
                    "marketo",
                    "adobe_experience_platform",
                    "snowflake",
                ],
                default=[],
            )
            destination_tables: dict[str, dict[str, Any]] = {}

            def destination_text(label: str, default: str) -> str:
                return _q_text(f"  {label}", default=default).strip() or default

            def credential_reference(label: str, default: str) -> str:
                name = destination_text(label, default)
                if name.startswith("$"):
                    return name
                return "${" + name + "}"

            if "salesforce" in destination_providers:
                destination_tables["salesforce"] = {
                    "destination_id": destination_text(
                        "Salesforce destination id", "salesforce"
                    ),
                    "origin": destination_text(
                        "Salesforce exact HTTPS origin",
                        "https://tenant.my.salesforce.com",
                    ),
                    "credential_secret_ref": credential_reference(
                        "Environment variable holding the Salesforce OAuth token",
                        "MAVERICK_SALESFORCE_CONSENT_TOKEN",
                    ),
                    "credential_revision": 1,
                    "api_version": destination_text(
                        "Salesforce REST API version", "v65.0"
                    ),
                    "object_api_name": destination_text(
                        "Salesforce consent-event object API name",
                        "Lightwork_Consent_Event__c",
                    ),
                    "external_id_field": destination_text(
                        "Salesforce external-id field",
                        "Lightwork_Message_Id__c",
                    ),
                    "payload_digest_field": destination_text(
                        "Salesforce payload-digest field",
                        "Lightwork_Payload_SHA256__c",
                    ),
                }
            if "marketo" in destination_providers:
                destination_tables["marketo"] = {
                    "destination_id": destination_text(
                        "Marketo destination id", "marketo"
                    ),
                    "origin": destination_text(
                        "Marketo exact HTTPS origin",
                        "https://123-ABC-456.mktorest.com",
                    ),
                    "credential_secret_ref": credential_reference(
                        "Environment variable holding the Marketo OAuth token",
                        "MAVERICK_MARKETO_CONSENT_TOKEN",
                    ),
                    "credential_revision": 1,
                    "custom_object_name": destination_text(
                        "Marketo consent custom-object API name",
                        "lightworkConsentEvent",
                    ),
                    "dedupe_field": destination_text(
                        "Marketo dedupe field", "lightworkMessageId"
                    ),
                    "message_id_field": destination_text(
                        "Marketo standalone searchable message-id field",
                        "lightworkMessageId",
                    ),
                    "payload_digest_field": destination_text(
                        "Marketo payload-digest field", "payloadSha256"
                    ),
                }
            if "adobe_experience_platform" in destination_providers:
                destination_tables["adobe_experience_platform"] = {
                    "destination_id": destination_text(
                        "Adobe Experience Platform destination id", "aep"
                    ),
                    "origin": destination_text(
                        "Adobe Experience Platform exact streaming origin",
                        "https://dcs.adobedc.net",
                    ),
                    "credential_secret_ref": credential_reference(
                        "Environment variable holding the AEP credential JSON bundle",
                        "MAVERICK_AEP_CONSENT_CREDENTIAL",
                    ),
                    "credential_revision": 1,
                    "connection_id": destination_text(
                        "AEP streaming connection id", "replace_connection_id"
                    ),
                    "flow_id": destination_text(
                        "AEP dataflow id", "replace_flow_id"
                    ),
                    "dataset_id": destination_text(
                        "AEP dataset id", "replace_dataset_id"
                    ),
                    "schema_id": destination_text(
                        "AEP XDM schema id", "replace_schema_id"
                    ),
                    "ims_org_id": destination_text(
                        "AEP IMS organization id", "replace@AdobeOrg"
                    ),
                }
            if "snowflake" in destination_providers:
                destination_tables["snowflake"] = {
                    "destination_id": destination_text(
                        "Snowflake destination id", "snowflake"
                    ),
                    "origin": destination_text(
                        "Snowflake exact SQL API origin",
                        "https://account.region.snowflakecomputing.com",
                    ),
                    "credential_secret_ref": credential_reference(
                        "Environment variable holding the Snowflake OAuth token",
                        "MAVERICK_SNOWFLAKE_CONSENT_TOKEN",
                    ),
                    "credential_revision": 1,
                    "database": destination_text(
                        "Snowflake database", "LIGHTWORK"
                    ),
                    "schema": destination_text(
                        "Snowflake schema", "CONSENT"
                    ),
                    "table": destination_text(
                        "Snowflake consent-event table", "PREFERENCE_EVENTS"
                    ),
                    "warehouse": destination_text(
                        "Snowflake warehouse", "LIGHTWORK_WH"
                    ),
                    "role": destination_text(
                        "Snowflake least-privilege role", "LIGHTWORK_CONSENT_WRITER"
                    ),
                }
            if destination_tables:
                consent_destination_config = {
                    "enable": True,
                    "enabled_providers": list(destination_tables),
                    "timeout_seconds": 10.0,
                    "max_response_bytes": 65_536,
                    **destination_tables,
                }
        consent_analytics = _q_confirm(
            "Enable privacy-safe aggregate consent analytics?",
            default=False,
        )
        consent_experiments = _q_confirm(
            "Enable privacy-preserving consent A/B experiments? Legal signals, "
            "required purposes, accessibility, and symmetry of choice remain "
            "non-experimental safety floors.",
            default=False,
        )
        consent_profile_data = _q_confirm(
            "Enable governed volunteered profile data and progressive consent "
            "journeys? Values remain closed-schema and purpose-bound; raw free "
            "text and protected-trait inference are not supported.",
            default=False,
        )
        consent_custom_index = consent_profile_data and _q_confirm(
            "Enable governed custom data-element indexes and exact profile search? "
            "Search values are HMAC-tokenized under a deployment-custody key; "
            "indexes fail closed when their source changes.",
            default=False,
        )
        consent_bulk_jobs = _q_confirm(
            "Enable governed bulk consent exports and lifecycle jobs? Jobs are "
            "tenant-scoped, credit-limited, cancellable, and retain bounded "
            "artifacts; operators must still hold the matching consent authority.",
            default=False,
        )
        consent_trust_center = consent_public_api and _q_confirm(
            "Enable the unified consumer Trust Center? It requires separately "
            "approved capability grants before consumer data is shown.",
            default=False,
        )
        consent_trust_center_branding = consent_trust_center and _q_confirm(
            "Enable governed Trust Center logos and exact custom-domain claims? "
            "PNG/JPEG assets require independent review; DNS/TLS/CDN deployment "
            "authority and the encryption key must still be injected.",
            default=False,
        )
        consent_product = {
            "enable": True,
            "publishing": {
                "enable": consent_publishing,
                "require_signed_publications": True,
                **(
                    {
                        "web_settings": {
                            "enable": True,
                            "retention_seconds": 31_536_000,
                            "max_history": 250,
                        }
                    }
                    if consent_web_publish_settings
                    else {}
                ),
            },
            "public_api": {"enable": consent_public_api},
            **(
                {"identity": consent_identity_config} if consent_identity_config is not None else {}
            ),
            "scanner": {
                "enable": consent_scanner,
                "notice_proposals": {"enable": consent_scan_notice_proposals},
                "scheduler": {
                    "enable": consent_scan_scheduler,
                    "poll_seconds": 60,
                    "enqueue_limit": 100,
                    "claim_limit": 4,
                    "tenant_limit": 1_000,
                },
            },
            **(
                {
                    "application_scanner": {
                        "enable": True,
                        "max_records": 25_000,
                        "retention_seconds": 31_536_000,
                    }
                }
                if consent_application_scanner
                else {}
            ),
            "standards": {
                "gpc": consent_signals,
                "dnt": consent_signals,
                "gpp": consent_adtech_standards,
                "tcf": consent_adtech_standards,
                "google_consent_mode": consent_adtech_standards,
            },
            **({"native_sdk": consent_native_sdk} if consent_native_sdk is not None else {}),
            "delivery": {
                "enable": consent_delivery,
                **(
                    {"destinations": consent_destination_config}
                    if consent_destination_config is not None
                    else {}
                ),
            },
            "analytics": {"enable": consent_analytics},
            "experiments": {"enable": consent_experiments},
            "profile_data": {
                "enable": consent_profile_data,
                "custom_index": {
                    "enable": consent_custom_index,
                    "key_secret_ref": "MAVERICK_CONSENT_CUSTOM_INDEX_KEY",
                    "key_version": 1,
                    "max_indexes": 256,
                    "max_page_size": 100,
                    "retention_seconds": 31_536_000,
                },
            },
            **(
                {
                    "bulk_jobs": {
                        "enable": True,
                        "initial_credits": 10_000,
                        "max_jobs": 10_000,
                        "max_items_per_job": 10_000,
                        "max_artifact_bytes": 16_777_216,
                        "lease_seconds": 60,
                        "retention_seconds": 31_536_000,
                        "worker_batch_size": 100,
                    }
                }
                if consent_bulk_jobs
                else {}
            ),
            "trust_center": {
                "enable": consent_trust_center,
                **(
                    {
                        "branding": {
                            "enable": True,
                            "max_asset_bytes": 2_097_152,
                            "max_assets": 1_000,
                            "max_width": 4_096,
                            "max_height": 4_096,
                            "max_pixels": 8_388_608,
                            "max_domains": 100,
                            "challenge_ttl_seconds": 3_600,
                            "verification_ttl_seconds": 604_800,
                            "retention_seconds": 7_776_000,
                            "key_ref": "env:MAVERICK_TRUST_CENTER_BRAND_ASSET_KEY",
                            "key_revision": 1,
                        }
                    }
                    if consent_trust_center_branding
                    else {}
                ),
            },
        }
    privacy_data_discovery = _q_confirm(
        "Enable Privacy Automation structured and unstructured personal-data "
        "discovery? Sources remain inert until separately enrolled and provider "
        "adapters are attached.",
        default=False,
    )
    privacy_discovery_adapter_providers: list[str] = []
    privacy_discovery_remediation = False
    privacy_discovery_data_map = False
    privacy_discovery_scheduler = False
    if privacy_data_discovery and _q_confirm(
        "Enable the bounded built-in privacy discovery source adapters? "
        "Each source still requires an exact tenant enrollment and deployment-held credential.",
        default=False,
    ):
        privacy_discovery_adapter_providers = _q_checkbox(
            "Select built-in privacy discovery adapters",
            [
                "aws_s3",
                "azure_blob",
                "google_cloud_storage",
                "snowflake_metadata",
                "servicenow_cmdb",
                "entra_assets",
            ],
            default=[],
        )
        if privacy_discovery_adapter_providers:
            privacy_discovery_remediation = _q_confirm(
                "Enable independently approved discovery-remediation outbox jobs and "
                "the digest-only governed data-map target? Provider writes remain "
                "disabled until a separate tenant authority is enrolled.",
                default=False,
            )
            privacy_discovery_data_map = privacy_discovery_remediation
            privacy_discovery_scheduler = (
                privacy_discovery_remediation
                and _q_confirm(
                    "Enable the managed privacy discovery remediation scheduler? "
                    "It runs bounded tenant-local digest-only retry and ambiguity "
                    "reconciliation; it never runs provider discovery or proves completeness.",
                    default=False,
                )
            )
    privacy_regulatory_intelligence = _q_confirm(
        "Enable Privacy Automation regulatory research, collections, alerts, "
        "comparisons, and cited reports? No proprietary corpus is bundled; "
        "sources remain unavailable until separately licensed and enrolled.",
        default=False,
    )
    privacy_rights_orchestration = _q_confirm(
        "Enable provider-neutral Privacy Automation rights-request orchestration? "
        "Raw subject references are HMACed; callbacks remain worker intent with "
        "no provider transport or response delivery.",
        default=False,
    )
    privacy_rights_source_adapters: dict[str, Any] | None = None
    if privacy_rights_orchestration and _q_confirm(
        "Enable production stock privacy-rights source adapters with encrypted "
        "immutable-target custody and reconciliation-only deletes? Provider "
        "credentials and raw subjects remain server-owned.",
        default=False,
    ):
        source_choices = _q_checkbox(
            "Select privacy-rights source providers",
            [
                "Microsoft Graph users",
                "Google Workspace users",
                "Salesforce contacts",
                "ServiceNow users",
            ],
            default=[],
        )
        provider_specs = {
            "Microsoft Graph users": (
                "microsoft_graph_users",
                "directory-graph",
                "https://graph.microsoft.com",
                "env:GRAPH_RIGHTS_TOKEN",
            ),
            "Google Workspace users": (
                "google_workspace_users",
                "directory-google",
                "https://admin.googleapis.com",
                "env:GOOGLE_WORKSPACE_RIGHTS_TOKEN",
            ),
            "Salesforce contacts": (
                "salesforce_contacts",
                "crm-salesforce",
                "https://tenant.salesforce.com",
                "env:SALESFORCE_RIGHTS_TOKEN",
            ),
            "ServiceNow users": (
                "servicenow_users",
                "itsm-servicenow",
                "https://tenant.service-now.com",
                "env:SERVICENOW_RIGHTS_TOKEN",
            ),
        }
        selected_providers: dict[str, Any] = {}
        for choice in source_choices:
            provider, source_id, origin, credential_ref = provider_specs[choice]
            selected_providers[provider] = {
                "enable": True,
                "source_id": source_id,
                "origin": origin,
                "credential_ref": credential_ref,
                "capabilities": [
                    "discover",
                    "retrieve",
                    "delete",
                    "verify_delete",
                ],
            }
        privacy_rights_source_adapters = {
            "enable": bool(selected_providers),
            "timeout_seconds": 15,
            "max_response_bytes": 1_048_576,
            "max_retrieval_bytes": 8_388_608,
            "max_records": 200,
            "batch_size": 10,
            "lease_seconds": 60,
            "retry_delay_seconds": 30,
            "claim_history_retention_seconds": 86_400,
            "target_custody_retention_seconds": 2_592_000,
            "providers": selected_providers,
        }
    privacy_rights_consumer_delivery = privacy_rights_orchestration and _q_confirm(
        "Enable one-time consumer delivery for closed, redacted rights-response "
        "packages? Production use still requires the governed document vault, "
        "pinned key references, independent public reauthentication, and an "
        "IdP-backed operator-to-consumer mapping for operator minting.",
        default=False,
    )
    privacy_rights_consumer_intake = (
        privacy_rights_orchestration
        and consent_publishing
        and consent_public_api
        and consent_trust_center
        and _q_confirm(
            "Enable authenticated consumer privacy-rights intake in the Trust "
            "Center? Requests remain profile-bound, identity evidence is "
            "independently resolved, and provider retrieval/deletion stays "
            "disabled until separately authorized.",
            default=False,
        )
    )
    if consent_native_sdk is not None and privacy_rights_consumer_intake:
        consent_native_sdk["require_rights_requests"] = _q_confirm(
            "Require native SDK privacy-rights requests? The deployment must inject "
            "the tenant-bound rights-request callback.",
            default=False,
        )
    consent_global_opt_out = privacy_rights_consumer_intake and _q_confirm(
        "Enable the governed Global Opt-Out mobile and email experience? "
        "Public submissions create a real profile-bound opt-out rights request; "
        "live device, service, customer-estate, and legal acceptance remain separate.",
        default=False,
    )
    if consent_global_opt_out and consent_product is not None:
        consent_product["global_opt_out"] = {
            "enable": True,
            "retention_seconds": 31_536_000,
            "submission_ttl_seconds": 900,
            "max_records": 10_000,
        }
    privacy_ott_ctv_rights = (
        privacy_rights_orchestration
        and consent_identity
        and consent_trust_center
        and _q_confirm(
            "Enable the selector-free OTT/CTV service-client rights bridge? "
            "This is a local access/deletion control path only; it provides no "
            "native provider SDK, live deletion, or estate-completeness proof.",
            default=False,
        )
    )
    privacy_drop_management = privacy_rights_orchestration and _q_confirm(
        "Enable California DROP deletion and permanent opt-out management? "
        "DROP records, subject bindings, DSAR submission and terminal outcome "
        "evidence must be independently enrolled; this does not enable a live "
        "California provider connection or make a legal-compliance claim.",
        default=False,
    )
    privacy_drop_key_ref = "env:MAVERICK_PRIVACY_RIGHTS_DIGEST_KEY"
    privacy_drop_scheduler = False
    if privacy_drop_management:
        privacy_drop_key_ref = (
            _q_text(
                "DROP principal-binding secret-provider reference (never key material)",
                default=privacy_drop_key_ref,
            ).strip()
            or privacy_drop_key_ref
        )
        privacy_drop_scheduler = _q_confirm(
            "Enable the managed DROP reconciliation and retention scheduler? "
            "This runs bounded tenant-local maintenance; California delivery and "
            "registered-broker interoperability remain separately operated.",
            default=False,
        )
    privacy_notice_governance = _q_confirm(
        "Enable governed Privacy Automation notice approvals and publication "
        "intents? Notice content stays external and publication remains local "
        "worker intent with no site, app, or consent-provider write.",
        default=False,
    )
    privacy_notice_publication_runtime = False
    privacy_notice_publication_scheduler = False
    privacy_notice_publication_binding: dict[str, Any] | None = None
    if privacy_notice_governance and consent_publishing:
        privacy_notice_publication_runtime = _q_confirm(
            "Enable the local privacy-notice publication runtime? This consumes "
            "approved intents into the tenant-private Consent Publishing store "
            "only; it does not update a website, SDK, tag manager, or provider.",
            default=False,
        )
    if privacy_notice_publication_runtime:
        binding_tenant = (
            _q_text(
                "  Exact active tenant ID for the first publication binding",
                default=os.environ.get("MAVERICK_TENANT", "default"),
            ).strip()
            or "default"
        )
        binding_id = (
            _q_text("  Publication binding name", default="primary").strip()
            or "primary"
        )
        privacy_notice_publication_binding = {
            "binding_id": binding_id,
            "tenant_id": binding_tenant,
            "notice_id": (
                _q_text("  Exact notice release ID", default="privacy-main").strip()
                or "privacy-main"
            ),
            "consent_publication_ref": (
                _q_text(
                    "  Exact notice release consent-publication reference",
                    default="consent-binding:1",
                ).strip()
                or "consent-binding:1"
            ),
            "property_id": (
                _q_text("  Exact Consent Publishing property ID", default="property-main").strip()
                or "property-main"
            ),
            "rule_group_id": (
                _q_text("  Exact consent rule-group ID", default="production-rules").strip()
                or "production-rules"
            ),
            "rule_group_version": max(
                1,
                _safe_int(
                    _q_text("  Exact consent rule-group version", default="1"),
                    default=1,
                ),
            ),
            "notice_resource_id": (
                _q_text("  Exact consent notice-resource ID", default="privacy-notice").strip()
                or "privacy-notice"
            ),
            "notice_resource_version": max(
                1,
                _safe_int(
                    _q_text("  Exact consent notice-resource version", default="1"),
                    default=1,
                ),
            ),
        }
        privacy_notice_publication_scheduler = _q_confirm(
            "Run the bounded local notice-publication scheduler with the dashboard? "
            "Only the exact active tenant and target binding above are eligible.",
            default=False,
        )
    data_use_governance = _q_confirm(
        "Enable the Data Use Governance authority and policy compiler? "
        "Provider writes remain disabled until a native provider is enrolled.",
        default=False,
    )
    data_use_native: dict[str, Any] | None = None
    data_use_provider_audit = False
    data_use_provider_audit_scheduler = False
    if data_use_governance and _q_confirm(
        "Configure a native Snowflake or Databricks enforcement runtime? "
        "This enables external writes only after a binding is enrolled and an "
        "authorized decision is explicitly applied.",
        default=False,
    ):
        native_provider = _q_select(
            "Native data provider",
            ["snowflake", "databricks"],
            default="snowflake",
        )
        native_origin = _q_text(
            f"Exact HTTPS {native_provider} account/workspace origin",
            default=(
                "https://account.snowflakecomputing.com"
                if native_provider == "snowflake"
                else "https://workspace.cloud.databricks.com"
            ),
        ).strip()
        native_secret_ref = _q_text(
            "Secret-provider reference containing the bearer token (for example "
            "$LIGHTWORK_DATA_USE_PROVIDER_TOKEN)",
            default="$LIGHTWORK_DATA_USE_PROVIDER_TOKEN",
        ).strip()
        native_revision = _safe_int(
            _q_text(
                "Credential authority revision (increment on rotation)",
                default="1",
            ),
            default=1,
        )
        data_use_native = {
            "provider": native_provider,
            "section": {
                "enable": True,
                "origin": native_origin,
                "credential_secret_ref": native_secret_ref,
                "credential_revision": max(1, native_revision),
                "timeout_seconds": 10,
                "max_response_bytes": 65_536,
            },
        }
        data_use_provider_audit = _q_confirm(
            "Enable native provider audit-log ingestion and last-mile "
            "reconciliation? Provider writes will then require a current, "
            "independently enrolled read capability and use the built-in typed "
            "audit transport; audit-log completeness and live provider acceptance "
            "remain separate.",
            default=False,
        )
        if data_use_provider_audit:
            data_use_provider_audit_scheduler = _q_confirm(
                "Run the bounded cross-tenant provider-audit recovery and "
                "reconciliation scheduler in this dashboard process?",
                default=False,
            )
    console.print()
    console.print(
        "[dim]Vendor-paper review compares a vendor's own DPA or privacy "
        "addendum against your standard positions and produces a Word "
        "tracked-changes redline. Which clauses fall short is always decided "
        "deterministically from the clause checklists; a model is used only to "
        "re-word YOUR required clause to match the vendor's drafting, and any "
        "draft that weakens your position is discarded. Say no and the redline "
        "uses your template language verbatim -- no model sees the "
        "contract.[/dim]"
    )
    draft = _q_confirm(
        "Let a model re-word your standard clauses to match vendor drafting?", default=True
    )
    console.print()
    console.print(
        "[dim]When a vendor signs YOUR paper, Lightwork drafts your template "
        "DPA/addendum as a Word document with the vendor-specific values "
        "filled in red for counsel to verify. Your legal entity name goes on "
        "the party line; leave blank to keep a red placeholder.[/dim]"
    )
    org_name = _q_text(
        "Legal entity name for drafted agreements (blank = placeholder)", default=""
    ).strip()
    console.print()
    console.print(
        "[dim]The entity graph links your records into one lineage: every "
        "vendor, reviewer, document, and clause becomes one identity, and "
        "every review/decision becomes a dated, citable edge — so you can ask "
        "'why did we approve this vendor?', 'what did we know on the day we "
        "signed?', and 'this clause turned out bad — what relied on it?'. "
        "Derived and read-only: it is rebuilt from your records and never "
        "stores anything they don't say.[/dim]"
    )
    graph = _q_confirm("Enable the entity graph (lineage & dossier queries)?", default=True)
    out: dict[str, Any] = {}
    if not discovery:
        out["doc_discovery"] = False
    if not learn:
        out["learn"] = False
    if not ops:
        out["privacy_ops"] = False
    if consent_product is not None:
        out["consent_preferences"] = consent_product
    if (
        privacy_data_discovery
        or privacy_regulatory_intelligence
        or privacy_rights_orchestration
        or privacy_rights_consumer_delivery
        or privacy_rights_consumer_intake
        or privacy_ott_ctv_rights
        or privacy_drop_management
        or privacy_notice_governance
    ):
        data_discovery_config: dict[str, Any] = {"enable": privacy_data_discovery}
        if privacy_discovery_adapter_providers:
            data_discovery_config["adapters"] = {
                "enable": True,
                "enabled_providers": privacy_discovery_adapter_providers,
                "timeout_seconds": 15.0,
                "max_response_bytes": 8_388_608,
                "page_size": 250,
                "max_pages": 500,
                "max_attempts": 3,
                "max_polls": 8,
            }
            data_discovery_config["remediation"] = {
                "enable": privacy_discovery_remediation,
                "max_attempts": 5,
                "retention_seconds": 2_592_000,
                "data_map": {
                    "enable": privacy_discovery_data_map,
                    "retention_seconds": 31_536_000,
                },
                "scheduler": {
                    "enable": privacy_discovery_scheduler,
                    "interval_seconds": 60,
                    "jitter_seconds": 10,
                    "retry_max_seconds": 300,
                    "tenant_limit": 1_000,
                    "worker_limit": 10,
                    "reconcile_limit": 10,
                    "lease_seconds": 60,
                },
            }
        out["privacy_automation"] = {
            "enable": True,
            "data_discovery": data_discovery_config,
            "regulatory_intelligence": {
                "enable": privacy_regulatory_intelligence,
            },
            "rights_orchestration": {
                "enable": privacy_rights_orchestration,
                **(
                    {"source_adapters": privacy_rights_source_adapters}
                    if privacy_rights_source_adapters is not None
                    else {}
                ),
            },
            "rights_consumer_delivery": {
                "enable": privacy_rights_consumer_delivery,
                "digest_key_ref": "env:MAVERICK_PRIVACY_RIGHTS_DELIVERY_DIGEST_KEY",
                "export_signing_key_ref": (
                    "env:MAVERICK_PRIVACY_RIGHTS_DELIVERY_EXPORT_KEY"
                ),
                "package_encryption_key_ref": "env:MAVERICK_ENCRYPTION_KEY",
                "server_grant_ttl_seconds": 30,
                "max_package_bytes": 16_777_216,
            },
            "rights_consumer_intake": {
                "enable": privacy_rights_consumer_intake,
                "request_ttl_seconds": 86_400,
                "retention_seconds": 31_536_000,
                "max_active_requests": 1_000,
                "max_active_requests_per_subject": 25,
            },
            "ott_ctv_rights": {
                "enable": privacy_ott_ctv_rights,
            },
            "drop_management": {
                "enable": privacy_drop_management,
                "principal_binding_key_ref": privacy_drop_key_ref,
                "retention_seconds": 31_536_000,
                "scheduler_enable": privacy_drop_scheduler,
                "scheduler_interval_seconds": 3_600,
                "scheduler_jitter_seconds": 60,
                "scheduler_retry_max_seconds": 900,
            },
            "notice_governance": {
                "enable": privacy_notice_governance,
                **(
                    {
                        "publication_runtime": {
                            "enable": True,
                            "scheduler_enable": privacy_notice_publication_scheduler,
                            "active_tenants": [
                                str(privacy_notice_publication_binding["tenant_id"])
                            ],
                            "poll_seconds": 30,
                            "retry_max_seconds": 300,
                            "tenant_limit": 100,
                            "lease_seconds": 60,
                            "retry_delay_seconds": 60,
                            "bindings": {
                                str(privacy_notice_publication_binding["binding_id"]): {
                                    key: value
                                    for key, value in privacy_notice_publication_binding.items()
                                    if key != "binding_id"
                                }
                            },
                        }
                    }
                    if privacy_notice_publication_binding is not None
                    else {}
                ),
            },
        }
    if data_use_governance:
        data_use_product: dict[str, Any] = {
            "enable": True,
            "compiler": {"enable": True},
        }
        if data_use_native is not None:
            data_use_product["enforcement"] = {
                "enable": True,
                "require_four_eyes": True,
                "max_attempts": 3,
                "retry_base_seconds": 30,
                "retry_max_seconds": 300,
            }
            data_use_product[str(data_use_native["provider"])] = data_use_native["section"]
            provider_audit_config: dict[str, Any] = {
                "enable": data_use_provider_audit,
                "audit_window_seconds": (
                    14_400
                    if data_use_provider_audit and data_use_native["provider"] == "snowflake"
                    else 3_600
                ),
                "retention_seconds": 31_536_000,
                "max_attempts": 5,
                "cursor_overlap_seconds": 300,
                "max_pages_per_read": 20,
                "scheduler": {
                    "enable": data_use_provider_audit_scheduler,
                    "poll_seconds": 60,
                    "claim_limit": 10,
                    "tenant_limit": 1_000,
                    "lease_seconds": 120,
                    "event_limit": 100,
                    "retry_backoff_seconds": 30,
                    "purge_limit": 100,
                },
            }
            if data_use_provider_audit and data_use_native["provider"] == "snowflake":
                provider_audit_config["snowflake_latency_watermark_seconds"] = 10_800
            data_use_product["provider_audit"] = provider_audit_config
        out["data_use_governance"] = data_use_product
    if not draft:
        out["paper_review_use_model"] = False
    if org_name:
        out["paper_review_org"] = org_name
    if not graph:
        out["entity_graph"] = False
    return out


def _pick_evidence_local_parsers(enabled: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ai_evidence_ocr": False,
        "ai_evidence_ocr_executable": "",
        "ai_evidence_ocr_sha256": "",
        "ai_evidence_ocr_tesseract_version": "",
        "ai_evidence_ocr_traineddata_path": "",
        "ai_evidence_ocr_traineddata_sha256": "",
        "ai_evidence_ocr_runtime_library_paths": [],
        "ai_evidence_ocr_runtime_libraries_sha256": "",
        "ai_evidence_ocr_pillow_version": "",
        "ai_evidence_ocr_pillow_manifest_sha256": "",
        "ai_evidence_legacy_xls": False,
        "ai_evidence_xlrd_manifest_sha256": "",
        "ai_evidence_olefile_manifest_sha256": "",
        "ai_evidence_parser_sandbox": False,
        "ai_evidence_parser_sandbox_launcher": "",
        "ai_evidence_parser_sandbox_sha256": "",
        "ai_evidence_parser_sandbox_version": "",
    }
    if not enabled:
        return result
    ocr_enabled = _q_confirm(
        "Configure the governed offline JPEG/PNG OCR runtime now? It remains "
        "disabled unless an absolute Tesseract path, exact reviewed binary "
        "SHA-256, exact Tesseract version, and exact Pillow version are supplied.",
        default=False,
    )
    if ocr_enabled:
        result["ai_evidence_ocr_executable"] = _q_text(
            "  Absolute Tesseract executable path", default=""
        ).strip()
        result["ai_evidence_ocr_sha256"] = _q_text(
            "  Reviewed Tesseract executable SHA-256 (64 lowercase hex)",
            default="",
        ).strip().lower()
        result["ai_evidence_ocr_tesseract_version"] = _q_text(
            "  Exact `tesseract --version` version token", default=""
        ).strip()
        result["ai_evidence_ocr_traineddata_path"] = _q_text(
            "  Absolute eng.traineddata path", default=""
        ).strip()
        result["ai_evidence_ocr_traineddata_sha256"] = _q_text(
            "  Reviewed eng.traineddata SHA-256 (64 lowercase hex)", default=""
        ).strip().lower()
        raw_runtime_paths = _q_text(
            "  Absolute Tesseract runtime library paths (comma-separated)", default=""
        )
        result["ai_evidence_ocr_runtime_library_paths"] = [
            value.strip() for value in raw_runtime_paths.split(",") if value.strip()
        ]
        result["ai_evidence_ocr_runtime_libraries_sha256"] = _q_text(
            "  Reviewed runtime-library manifest SHA-256", default=""
        ).strip().lower()
        result["ai_evidence_ocr_pillow_version"] = _q_text(
            "  Exact installed Pillow distribution version", default="12.3.0"
        ).strip()
        result["ai_evidence_ocr_pillow_manifest_sha256"] = _q_text(
            "  Reviewed Pillow package-file manifest SHA-256", default=""
        ).strip().lower()
        digests = (
            result["ai_evidence_ocr_sha256"],
            result["ai_evidence_ocr_traineddata_sha256"],
            result["ai_evidence_ocr_runtime_libraries_sha256"],
            result["ai_evidence_ocr_pillow_manifest_sha256"],
        )
        digests_valid = all(
            len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
            for digest in digests
        )
        versions_valid = all(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}", value)
            for value in (
                result["ai_evidence_ocr_tesseract_version"],
                result["ai_evidence_ocr_pillow_version"],
            )
        )
        absolute_paths = [
            result["ai_evidence_ocr_executable"],
            result["ai_evidence_ocr_traineddata_path"],
            *result["ai_evidence_ocr_runtime_library_paths"],
        ]
        ocr_enabled = bool(
            absolute_paths
            and all(Path(path).is_absolute() for path in absolute_paths)
            and digests_valid
            and versions_valid
        )
        if not ocr_enabled:
            console.print(
                "[yellow]Governed OCR remains disabled until every exact "
                "runtime attestation is supplied.[/yellow]"
            )
    result["ai_evidence_ocr"] = ocr_enabled
    xls_enabled = _q_confirm(
        "Enable the isolated, offline legacy BIFF8 XLS subset parser? It requires "
        "the evidence-analysis extra with exact xlrd 2.0.2 and olefile 0.47, and "
        "rejects formulas, macros, external references, and unknown records.",
        default=False,
    )
    if xls_enabled:
        result["ai_evidence_xlrd_manifest_sha256"] = _q_text(
            "  Reviewed xlrd package-file manifest SHA-256", default=""
        ).strip().lower()
        result["ai_evidence_olefile_manifest_sha256"] = _q_text(
            "  Reviewed olefile package-file manifest SHA-256", default=""
        ).strip().lower()
        xls_enabled = all(
            len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
            for digest in (
                result["ai_evidence_xlrd_manifest_sha256"],
                result["ai_evidence_olefile_manifest_sha256"],
            )
        )
    result["ai_evidence_legacy_xls"] = xls_enabled
    if ocr_enabled or xls_enabled:
        if os.name != "posix":
            console.print(
                "[yellow]Governed OCR/XLS remains disabled on this platform: "
                "the stock runtime requires a POSIX Bubblewrap network namespace.[/yellow]"
            )
            result["ai_evidence_ocr"] = False
            result["ai_evidence_legacy_xls"] = False
            return result
        result["ai_evidence_parser_sandbox_launcher"] = _q_text(
            "  Absolute Bubblewrap launcher path", default="/usr/bin/bwrap"
        ).strip()
        result["ai_evidence_parser_sandbox_sha256"] = _q_text(
            "  Reviewed Bubblewrap launcher SHA-256", default=""
        ).strip().lower()
        result["ai_evidence_parser_sandbox_version"] = _q_text(
            "  Exact `bwrap --version` version token", default=""
        ).strip()
        sandbox_digest = result["ai_evidence_parser_sandbox_sha256"]
        result["ai_evidence_parser_sandbox"] = bool(
            Path(result["ai_evidence_parser_sandbox_launcher"]).is_absolute()
            and len(sandbox_digest) == 64
            and all(character in "0123456789abcdef" for character in sandbox_digest)
            and re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}",
                result["ai_evidence_parser_sandbox_version"],
            )
        )
        if not result["ai_evidence_parser_sandbox"]:
            result["ai_evidence_ocr"] = False
            result["ai_evidence_legacy_xls"] = False
            console.print(
                "[yellow]Governed OCR/XLS remains disabled until the exact "
                "Bubblewrap sandbox attestation is supplied.[/yellow]"
            )
    return result


def pick_security_suite() -> dict[str, Any]:
    """Configure the Security/GRC suite and both defensive hunters.

    The record-only GRC workspace follows Privacy's default-on posture.  The
    hunters are explicit opt-ins because they consume operational telemetry.
    Environment response execution is a second, independent opt-in and still
    requires a human-approved playbook at runtime.
    """
    console.print()
    console.print(
        "[dim]Security & GRC maintains control, evidence, risk, POA&M, policy, "
        "vendor, incident, and audit records locally. The platform hunter "
        "defensively scans Lightwork's signed telemetry; the environment "
        "hunter reads configured customer telemetry. Hunters are OFF until "
        "enabled. Approved response execution is a separate opt-in.[/dim]"
    )
    customer_sandbox_lifecycle = _q_confirm(
        "Enable the global-admin managed customer sandbox tenant-copy control "
        "plane? It copies only a closed safe configuration projection into a "
        "separate isolated local sandbox workspace; production data and credentials are "
        "never copied.",
        default=False,
    )
    security_ops = _q_confirm(
        "Enable Security & GRC records and readiness workspace?", default=True
    )
    evidence_graph = _q_confirm(
        "Enable the review-gated evidence graph? It stores bounded evidence "
        "metadata and hashes, never raw telemetry.",
        default=False,
    )
    model_risk_assurance = _q_confirm(
        "Enable the Model Risk & AI Assurance Officer? Legal applicability, "
        "risk acceptance, and deployment decisions remain human-owned.",
        default=False,
    )
    if model_risk_assurance:
        evidence_graph = True
    evidence_gateway = _q_confirm(
        "Enable the AI Evidence-Ready Gateway? It stores signed hash-only "
        "interaction receipts and cited regulatory-impact records, never raw "
        "prompts or generated text.",
        default=False,
    )
    if evidence_gateway:
        evidence_graph = True
        model_risk_assurance = True
    model_improvement = _q_confirm(
        "Enable governed specialist-model improvement plumbing? Training and "
        "weight promotion remain separately approval-gated.",
        default=False,
    )
    allow_hosted_training = False
    if model_improvement:
        evidence_graph = True
        model_risk_assurance = True
        allow_hosted_training = _q_confirm(
            "Allow hosted training only for cases with an exact hosted-training "
            "consent scope? Cross-tenant training remains refused.",
            default=False,
        )
    ai_inventory = _q_confirm(
        "Enable read-only discovery of customer-owned AI assets across cloud AI, "
        "model registries, data platforms, agent builders, and model hubs such as "
        "Bedrock, SageMaker, Azure AI Foundry, Vertex AI, Databricks, Copilot "
        "Studio, MLflow, Hugging Face, OpenAI, Replicate, Snowflake, or "
        "Salesforce Agentforce? "
        "Credentials are configured separately and are never stored in the "
        "inventory ledger.",
        default=False,
    )
    ai_entity_tasks = _q_confirm(
        "Enable governed task management for AI inventory entities? Tasks are "
        "tenant-bound, revision-pinned, approval-audited, and require the AI "
        "inventory authority.",
        default=False,
    )
    ai_inventory = ai_inventory or ai_entity_tasks
    inventory_connectors: list[str] = []
    if ai_inventory:
        evidence_graph = True
        model_risk_assurance = True
        connector_labels = {
            "AWS Bedrock": "bedrock",
            "AWS SageMaker (stock contract)": "aws_sagemaker",
            "Amazon SageMaker account registry": "sagemaker",
            "Azure AI Foundry": "azure_ai_foundry",
            "Google Vertex AI": "vertex_ai",
            "Databricks Unity Catalog": "databricks_unity_catalog",
            "Microsoft Copilot Studio": "copilot_studio",
            "MLflow Model Registry": "mlflow",
            "Snowflake Cortex": "snowflake_cortex",
            "Snowflake Model Registry": "snowflake",
            "Salesforce Agentforce (stock contract)": "salesforce_agentforce",
            "Salesforce Agentforce account": "agentforce",
            "Hugging Face Hub (stock contract)": "hugging_face",
            "Hugging Face Hub organization": "huggingface",
            "OpenAI platform (org models + fine-tunes)": "openai",
            "Replicate (account deployments)": "replicate",
        }
        selected = _q_checkbox(
            "Prepare which read-only AI inventory connectors?",
            list(connector_labels),
            default=[],
        )
        inventory_connectors = [
            connector_labels[label] for label in selected if label in connector_labels
        ]
    ai_vendor_risk = _q_confirm(
        "Enable governed AI-specific vendor assessments with cited document "
        "drafting and independent review before model-risk projection?",
        default=False,
    )
    ai_vendor_risk_portal = False
    if ai_vendor_risk:
        evidence_graph = True
        model_risk_assurance = True
        ai_vendor_risk_portal = _q_confirm(
            "Enable the external AI-vendor questionnaire portal? Invitation "
            "tokens and vendor submissions remain bounded, tenant-scoped, and "
            "separately governed.",
            default=False,
        )
    portfolio_risk = _q_confirm(
        "Enable transparent AI portfolio-risk rollups and immutable quarterly "
        "snapshots? Scores remain decision support, not compliance verdicts.",
        default=False,
    )
    policy_packs = _q_confirm(
        "Enable signed, versioned AI policy packs with human activation and "
        "evidence-staleness tracking? Packs do not certify compliance.",
        default=False,
    )
    grc_studio = _q_confirm(
        "Enable the tenant-private no-code GRC configuration studio for closed "
        "objects, fields, forms, rules, collector bindings and reports? "
        "Publishing requires independent review and approval; runtime writes "
        "remain deployment-owned.",
        default=False,
    )
    policy_publication_links = _q_confirm(
        "Enable Tech Risk & Compliance evergreen public/private policy links? "
        "Publication remains review-gated; private capabilities, expiry, "
        "revocation, and scheduled changes fail closed.",
        default=False,
    )
    compliance_scoping = _q_confirm(
        "Enable local Tech Risk & Compliance questionnaire scoping? Closed "
        "rules use opaque references and independent review; no licensed "
        "framework text, legal advice, discovery, or certification is included.",
        default=False,
    )
    compliance_scope_actions = compliance_scoping and _q_confirm(
        "Enable deterministic control and evidence-task plans from independently "
        "finalized compliance scopes? Plans use reviewed opaque mapping catalogs, "
        "four-eyes approval, and a separately injected local apply callback; no "
        "licensed framework text or provider write is included.",
        default=False,
    )
    continuous_evidence = _q_confirm(
        "Enable Tech Risk & Compliance continuous evidence collection? The "
        "built-in local collector and seven read-only stock provider adapters are "
        "digest-only; external estates still require exact approved definitions, "
        "enrollments, credentials, and acceptance.",
        default=False,
    )
    stock_collector_labels = {
        "AWS Security Hub": "aws_security_hub_v1",
        "AWS Config posture": "aws_config_posture_v1",
        "Azure Defender": "azure_defender_v1",
        "Azure Policy posture": "azure_policy_posture_v1",
        "Google Cloud Security Command Center": "gcp_security_command_center_v1",
        "GitHub security streams": "github_security_v1",
        "Okta policy posture": "okta_posture_v1",
    }
    selected_stock_collector_labels = (
        _q_checkbox(
            "Prepare which read-only Tech Risk stock evidence adapters? Exact "
            "tenant source manifests are added separately after independent approval.",
            list(stock_collector_labels),
            default=[],
        )
        if continuous_evidence
        else []
    )
    stock_collector_ids = [
        stock_collector_labels[label]
        for label in selected_stock_collector_labels
        if label in stock_collector_labels
    ]
    continuous_evidence_scheduler = continuous_evidence and _q_confirm(
        "Enable the continuous-evidence scheduler with leased retries, recovery, "
        "and bounded tenant processing?",
        default=False,
    )
    ai_evidence_analysis = _q_confirm(
        "Enable bounded multimodal AI Evidence Analysis for PDF, JPEG/PNG, "
        "Excel, and text evidence? Results remain evidence-pinned drafts with "
        "transparent scoring and mandatory human review, never compliance or "
        "legal conclusions.",
        default=False,
    )
    if ai_evidence_analysis:
        evidence_gateway = True
    evidence_local_parsers = _pick_evidence_local_parsers(ai_evidence_analysis)
    assessment_evidence_autopopulation = ai_evidence_analysis and _q_confirm(
        "Enable evidence-assisted assessment answer proposals from released AI "
        "Evidence Analysis results? Proposals remain cited drafts and require a "
        "different human reviewer.",
        default=False,
    )
    assessment_evidence_apply = assessment_evidence_autopopulation and _q_confirm(
        "Allow a separately approved proposal to fill only unanswered assessment "
        "questions under revision-CAS? Existing answers are never overwritten and "
        "final assessment review remains required.",
        default=False,
    )
    third_party_intelligence = _q_confirm(
        "Enable the provider-neutral Third-Party Management intelligence, "
        "screening, continuous-monitoring, EDD, exchange, and bounded "
        "low-risk workflow plane? Commercial feeds remain inert until "
        "separately licensed and enrolled.",
        default=False,
    )
    third_party_intelligence_providers = third_party_intelligence and _q_confirm(
        "Enable the bounded third-party intelligence provider runtime for "
        "RiskRecon, SecurityScorecard, HackNotice, SupplyWisdom, and ISS Corporate "
        "Solutions? Licensed credentials and live provider acceptance remain "
        "separate deployment requirements.",
        default=False,
    )
    third_party_supplywisdom = third_party_intelligence_providers and _q_confirm(
        "Enable the SupplyWisdom normalized reference contract? This requires the "
        "exact licensed HTTPS gateway hostname for this deployment.",
        default=False,
    )
    third_party_supplywisdom_host = (
        _q_text(
            "  Exact SupplyWisdom gateway hostname (no scheme, port, path, or query)",
            default="",
        )
        .strip()
        .lower()
        if third_party_supplywisdom
        else ""
    )
    third_party_iss_corporate_solutions = (
        third_party_intelligence_providers
        and _q_confirm(
            "Enable the ISS Corporate Solutions normalized reference contract? This "
            "requires the exact licensed HTTPS gateway hostname for this deployment.",
            default=False,
        )
    )
    third_party_iss_corporate_solutions_host = (
        _q_text(
            "  Exact ISS Corporate Solutions gateway hostname "
            "(no scheme, port, path, or query)",
            default="",
        )
        .strip()
        .lower()
        if third_party_iss_corporate_solutions
        else ""
    )
    third_party_intelligence_scheduler = third_party_intelligence_providers and _q_confirm(
        "Enable scheduled third-party provider collection with retries, "
        "revocation handling, and tenant-bounded processing?",
        default=False,
    )
    third_party_lifecycle = _q_confirm(
        "Enable provider-neutral conditional third-party assessments, lifecycle "
        "transitions, and governed offboarding? Callbacks remain local intent; "
        "no transport worker or provider write is included.",
        default=False,
    )
    third_party_contracts = _q_confirm(
        "Enable first-class third-party Contract and Engagement records with "
        "revisioned schema metadata, redaction state, and governed tombstones? "
        "External contract systems and legal acceptance remain separate.",
        default=False,
    )
    third_party_executive_reports = _q_confirm(
        "Enable role-aware, brandable third-party executive PDF reports from "
        "exact revision-pinned local records? PDFs use closed branding and "
        "one-time downloads; live portfolio completeness remains separate.",
        default=False,
    )
    # The stock composition resolves exact vendor authority through the local
    # intelligence registry; commercial provider transports stay independent.
    third_party_intelligence = third_party_intelligence or third_party_contracts
    third_party_collaboration_portal = third_party_lifecycle and _q_confirm(
        "Enable the external third-party collaboration portal for invitation-"
        "bound assessments, attestations, evidence manifests, and follow-up? "
        "No email transport, raw document ingestion, or model-authored answer "
        "is enabled by this setting.",
        default=False,
    )
    governance_agents = _q_confirm(
        "Enable the packaged Breach Response, Privacy Risk Assessment, Program "
        "Copilot, Knowledgebase Governance, and Third-Party Risk agents? Agent "
        "outputs remain evidence-bound recommendations and all mutations require "
        "separate human approval.",
        default=False,
    )
    if governance_agents:
        evidence_gateway = True
    ai_regulatory_feed = _q_confirm(
        "Enable the governed AI regulatory-change bridge? It maps cited alerts "
        "to reviewed policy/evidence impacts and requires Finance Operations plus "
        "the AI evidence gateway.",
        default=False,
    )
    red_team_assurance = _q_confirm(
        "Enable governed red-team campaigns for enrolled external agents? "
        "Raw target output stays transient; only keyed commitments and bounded metrics "
        "enter review-pending evidence.",
        default=False,
    )
    eu_ai_incidents = _q_confirm(
        "Enable the human-routed EU AI Act Article 73 incident clock and report "
        "workflow? Lightwork records decisions and receipts but does not file or "
        "make legal determinations.",
        default=False,
    )
    grc_outbound_sync = _q_confirm(
        "Enable governed semantic writeback of findings, risk acceptances, and AI "
        "incidents to allowlisted ServiceNow IRM or Archer targets? Credentials "
        "remain separately configured.",
        default=False,
    )
    ai_literacy = _q_confirm(
        "Enable privacy-minimising AI-literacy requirements, completion receipts, "
        "reviewed waivers, and evidence exports?",
        default=False,
    )
    ai_business_intake = _q_confirm(
        "Enable the bounded AI business-intake workflow that creates or links an "
        "inventory asset and routes a proposed declaration and risk assessment "
        "for human review?",
        default=False,
    )
    ai_cost_roi = _q_confirm(
        "Enable governed multi-provider AI cost allocation and assumption-labelled "
        "ROI reporting? Provider billing collectors and credentials are configured "
        "separately.",
        default=False,
    )
    ai_dynamic_risk = _q_confirm(
        "Enable explainable dynamic AI risk scoring from normalized telemetry, "
        "policy, privacy, security, and evidence signals? Scores remain human-"
        "reviewed decision support and never certify compliance.",
        default=False,
    )
    ai_policy_management = _q_confirm(
        "Enable governed operational AI policies with human activation, exact "
        "applicability, strictest-wins decisions, four-eyes expiring exceptions, "
        "and revalidation-aware attestations?",
        default=False,
    )
    ai_runtime_guardrails = _q_confirm(
        "Enable transient prompt, output, and action guardrails that compose "
        "Shield, secret/PII redaction, operational policy, and digest-only "
        "runtime receipts? Raw runtime content is never persisted by this plane.",
        default=False,
    )
    ai_cloud_agent_governance = _q_confirm(
        "Enable named AWS Bedrock, Azure AI Foundry, and Google Vertex AI agent "
        "inventory, observability, and policy-enforcement intents? This enables "
        "the required inventory, policy, and runtime-guardrail authorities but "
        "does not install credentials or claim provider writes.",
        default=False,
    )
    databricks_ai_security_framework = False
    if ai_cloud_agent_governance:
        ai_inventory = True
        ai_policy_management = True
        ai_runtime_guardrails = True
        databricks_ai_security_framework = _q_confirm(
            "Enable the content-free, versioned Databricks AI Security Framework "
            "mapping pack? It requires human activation and does not include "
            "licensed framework prose or certification.",
            default=False,
        )
        if databricks_ai_security_framework:
            policy_packs = True
    ai_governance_loop = (
        ai_inventory
        and ai_dynamic_risk
        and ai_policy_management
        and ai_runtime_guardrails
        and _q_confirm(
            "Enable the durable AI governance closed loop that correlates "
            "inventory, risk, policy, control, monitoring, escalation, and "
            "evidence? It remains digest-only and performs no provider mutation.",
            default=False,
        )
    )
    ai_explainability = _q_confirm(
        "Enable governed, digest-only native/SHAP/LIME/Captum/Alibi and "
        "counterfactual explanation receipts with exact model binding and "
        "independent human review? Raw feature values are never admitted.",
        default=False,
    )
    ai_conformity_workflow = _q_confirm(
        "Enable the EU AI Act conformity-readiness workflow for FRIA, Annex IV, "
        "Article 43, post-market, draft declaration, and CE-readiness evidence? "
        "It does not make legal determinations, certify, sign, or affix a CE mark.",
        default=False,
    )
    ai_insurability = _q_confirm(
        "Enable exact-evidence insurance-assurance profiles with measurements, "
        "limitations, verifier instructions, and four-eyes export? Profiles do "
        "not establish coverage, pricing impact, or insurer acceptance.",
        default=False,
    )
    ai_regulated_verticals = _q_confirm(
        "Enable aggregate credit, fair-lending, NYC Local Law 144, and NAIC "
        "decision-support evidence packages? Packages do not make individual "
        "decisions, legal conclusions, filings, audits, or certifications.",
        default=False,
    )
    ai_bank_mrm = _q_confirm(
        "Enable governed bank model-risk evidence packages with nine closed "
        "review sections and four-eyes package-integrity approval? This does not "
        "approve a model or establish regulatory acceptance.",
        default=False,
    )
    ai_benchmark_exchange = _q_confirm(
        "Enable voluntary, aggregate-only benchmark contribution packages and "
        "externally signed cohort-report admission? This does not pool tenant "
        "records, prove anonymity, or independently verify differential privacy.",
        default=False,
    )
    ai_discovery_sensors = _q_confirm(
        "Enable the privacy-safe discovery-sensor control plane for approved, "
        "digest-bound normalized observations? This does not deploy sensors or "
        "establish complete AI discovery.",
        default=False,
    )
    ai_media_provenance = _q_confirm(
        "Enable governed digest-only C2PA adapter receipts and four-eyes media "
        "release review? A real verifier or signer remains separately required; "
        "Lightwork does not simulate cryptography or certify media.",
        default=False,
    )
    ai_media_provenance_c2patool = False
    ai_media_provenance_c2patool_sha256 = ""
    if ai_media_provenance:
        ai_media_provenance_c2patool = _q_confirm(
            "Select the separately installed c2patool verification adapter? "
            "This enables verification only; signing credentials and trust "
            "material are never generated by the installer.",
            default=False,
        )
        if ai_media_provenance_c2patool:
            candidate = (
                _q_text(
                    "  Independently approved c2patool executable SHA-256 "
                    "(64 lowercase hex; blank leaves the adapter unselected)",
                    default="",
                )
                .strip()
                .lower()
            )
            if len(candidate) == 64 and all(
                character in "0123456789abcdef" for character in candidate
            ):
                ai_media_provenance_c2patool_sha256 = candidate
            else:
                ai_media_provenance_c2patool = False
                console.print(
                    "[yellow]c2patool verification remains unselected until an "
                    "exact reviewed executable SHA-256 is supplied.[/yellow]"
                )
    ai_identity_governance_connectors = _q_confirm(
        "Enable bounded read-only Entra, Okta, and Purview identity/data-governance "
        "snapshots? Provider tenant IDs, secret references, and endpoints remain "
        "separately configured, and this does not claim complete discovery.",
        default=False,
    )
    ai_identity_governance_scheduler = False
    ai_identity_governance_controls = False
    ai_identity_change_coupling = False
    ai_identity_federation = False
    ai_identity_conditional_access = False
    ai_identity_purview_writeback = False
    ai_identity_production_composition = False
    if ai_identity_governance_connectors:
        ai_identity_governance_scheduler = _q_confirm(
            "Enable the bounded identity-governance scheduler supervisor? "
            "Every provider connector and provider schedule remains disabled "
            "until separately configured.",
            default=False,
        )
        ai_identity_governance_controls = _q_confirm(
            "Prepare the separately gated identity control plane? Every "
            "control remains independently disabled until selected, configured, "
            "and accepted against test tenants.",
            default=False,
        )
        if ai_identity_governance_controls:
            ai_identity_change_coupling = _q_confirm(
                "Enable digest-only identity-change coupling to dynamic risk and "
                "alerts? Sink wiring and replay acceptance remain required.",
                default=False,
            )
            ai_identity_federation = _q_confirm(
                "Enable pseudonymous Entra/Okta agent-principal federation? "
                "Provider trust and test-tenant acceptance remain required.",
                default=False,
            )
            if ai_identity_federation:
                ai_identity_conditional_access = _q_confirm(
                    "Enable fresh conditional-access enforcement for federated "
                    "agent principals? Provider policy acceptance remains required.",
                    default=False,
                )
                ai_identity_purview_writeback = _q_confirm(
                    "Enable default-deny Purview classification writeback for "
                    "federated agent principals? This is a mutating control and "
                    "requires separate test-tenant consent.",
                    default=False,
                )
            ai_identity_production_composition = _q_confirm(
                "Enable the lazy per-tenant identity-governance production "
                "composition? Provider transports and enrolled pseudonymous "
                "authorities remain separately required; local readiness does "
                "not claim live provider acceptance.",
                default=False,
            )
    if (
        portfolio_risk
        or policy_packs
        or red_team_assurance
        or ai_regulatory_feed
        or eu_ai_incidents
        or grc_outbound_sync
        or ai_literacy
        or ai_business_intake
        or ai_cost_roi
        or ai_dynamic_risk
        or ai_policy_management
        or ai_runtime_guardrails
        or ai_explainability
        or ai_conformity_workflow
        or ai_insurability
        or ai_regulated_verticals
        or ai_bank_mrm
        or ai_benchmark_exchange
        or ai_discovery_sensors
        or ai_media_provenance
    ):
        evidence_graph = True
        model_risk_assurance = True
    if ai_regulatory_feed:
        evidence_gateway = True
    threat_hunt = _q_confirm(
        "Enable the defensive Lightwork platform threat hunter?", default=False
    )
    env_hunt = _q_confirm("Enable the defensive customer-environment threat hunter?", default=False)
    response_execution = False
    connectors: dict[str, dict[str, bool]] = {}
    enrichment_sources: list[str] = []
    poll_seconds = 300
    if env_hunt:
        response_execution = _q_confirm(
            "Allow execution of human-approved response playbooks?",
            default=False,
        )
        connector_labels = {
            "AWS CloudTrail": "cloudtrail",
            "AWS GuardDuty": "guardduty",
            "Syslog": "syslog",
            "Endpoint detection and response (EDR)": "edr",
            "Splunk": "splunk",
            "Elastic": "elastic",
            "Microsoft Sentinel": "sentinel",
            "Kubernetes audit logs": "kubernetes_audit",
            "Okta": "okta",
            "Microsoft Entra ID": "entra",
        }
        console.print(
            "[dim]Choose the read-only telemetry connector types to prepare. "
            "Credentials and vendor-specific transports are configured after "
            "installation; selecting a type here does not grant access.[/dim]"
        )
        selected_labels = _q_checkbox(
            "Prepare which read-only environment connectors?",
            list(connector_labels),
            default=[],
        )
        push_labels = (
            _q_checkbox(
                "Allow API push ingestion for which selected connector types?",
                selected_labels,
                default=[],
            )
            if selected_labels
            else []
        )
        pivot_labels = (
            _q_checkbox(
                "Allow bounded read-only investigation pivots for which selected connectors?",
                selected_labels,
                default=[],
            )
            if selected_labels
            else []
        )
        connectors = {
            name: {
                "enable": label in selected_labels,
                "push_enable": label in push_labels,
                "pivot_enable": label in pivot_labels,
            }
            for label, name in connector_labels.items()
        }
        enrichment_sources = [
            value.strip().lower()
            for value in _q_text(
                "Allowlisted enrichment adapter names (comma-separated; optional)",
                default="",
            ).split(",")
            if value.strip()
        ]
        poll_seconds = max(
            30,
            min(
                3600,
                int(
                    _safe_float(
                        _q_text("Hunter poll interval (seconds)", default="300"),
                        default=300.0,
                    )
                ),
            ),
        )
    # Keep these additive administration prompts at the end of the established
    # security-suite questionnaire so scripted/resumed answers for existing
    # controls retain their historical ordering.
    administration_document_access = _q_confirm(
        "Enable governed short-lived document and attachment access tokens? "
        "Document authority, object storage, and delivery grants remain deployment-owned.",
        default=False,
    )
    governed_document_vault = administration_document_access and _q_confirm(
        "Enable the stock tenant-private encrypted document vault as the local "
        "document authority and one-time delivery backend? External object-store, "
        "backup, HSM/KMS, and live workflow acceptance remain separate.",
        default=False,
    )
    incident_attachment_metadata = administration_document_access and _q_confirm(
        "Enable governed metadata-only attachments on privacy and security incidents? "
        "No document bytes, filenames, paths, or object-store credentials are stored.",
        default=False,
    )
    result = {
        "customer_sandbox_lifecycle": customer_sandbox_lifecycle,
        "security_ops": security_ops,
        "administration_document_access": administration_document_access,
        "governed_document_vault": governed_document_vault,
        "incident_attachment_metadata": incident_attachment_metadata,
        "evidence_graph": evidence_graph,
        "model_risk_assurance": model_risk_assurance,
        "evidence_gateway": evidence_gateway,
        "model_improvement": model_improvement,
        "allow_hosted_training": allow_hosted_training,
        "ai_inventory": ai_inventory,
        "ai_entity_tasks": ai_entity_tasks,
        "inventory_connectors": inventory_connectors,
        "ai_vendor_risk": ai_vendor_risk,
        "ai_vendor_risk_portal": ai_vendor_risk_portal,
        "portfolio_risk": portfolio_risk,
        "policy_packs": policy_packs,
        "governance_agents": governance_agents,
        "ai_evidence_analysis": ai_evidence_analysis,
        **evidence_local_parsers,
        "assessment_evidence_autopopulation": assessment_evidence_autopopulation,
        "assessment_evidence_apply": assessment_evidence_apply,
        "tech_risk_compliance": {
            "enable": (
                policy_publication_links
                or compliance_scoping
                or compliance_scope_actions
                or continuous_evidence
                or grc_studio
            ),
            "grc_studio": {
                "enable": grc_studio,
                "max_studios": 10_000,
                "retention_seconds": 31_536_000,
                "max_import_bytes": 2_097_152,
            },
            "policy_publication_links": {"enable": policy_publication_links},
            "scoping": {"enable": compliance_scoping},
            "scope_actions": {"enable": compliance_scope_actions},
            "continuous_evidence": {
                "enable": continuous_evidence,
                "enabled_collectors": (
                    ["local_soc2_v1", *stock_collector_ids]
                    if continuous_evidence
                    else []
                ),
                "stock_collectors": {
                    "enable": bool(stock_collector_ids),
                    "sources": [],
                },
                "scheduler": {
                    "enable": continuous_evidence_scheduler,
                    "poll_seconds": 60,
                    "enqueue_limit": 50,
                    "claim_limit": 10,
                    "tenant_limit": 1_000,
                    "lease_seconds": 120,
                    "retry_backoff_seconds": 30,
                },
            },
        },
        "third_party_management": {
            "enable": (
                third_party_intelligence
                or third_party_lifecycle
                or third_party_contracts
                or third_party_executive_reports
            ),
            "contracts": {
                "enable": third_party_contracts,
                "max_records": 100_000,
                "retention_seconds": 31_536_000,
                "authority_max_age_seconds": 604_800,
            },
            "executive_reports": {
                "enable": third_party_executive_reports,
                "max_reports": 10_000,
                "max_sources": 100,
                "max_pdf_bytes": 524_288,
                "retention_seconds": 31_536_000,
                "download_token_seconds": 300,
            },
            "intelligence": {
                "enable": third_party_intelligence,
                "providers": {
                    "enable": third_party_intelligence_providers,
                    "enabled_providers": (
                        [
                            "riskrecon",
                            "securityscorecard",
                            "hacknotice",
                            *(
                                ["supplywisdom"]
                                if third_party_supplywisdom
                                else []
                            ),
                            *(
                                ["iss_corporate_solutions"]
                                if third_party_iss_corporate_solutions
                                else []
                            ),
                        ]
                        if third_party_intelligence_providers
                        else []
                    ),
                    **(
                        {"supplywisdom_host": third_party_supplywisdom_host}
                        if third_party_supplywisdom
                        else {}
                    ),
                    **(
                        {
                            "iss_corporate_solutions_host": (
                                third_party_iss_corporate_solutions_host
                            )
                        }
                        if third_party_iss_corporate_solutions
                        else {}
                    ),
                    "timeout_seconds": 20,
                    "scheduler": {
                        "enable": third_party_intelligence_scheduler,
                        "poll_seconds": 900,
                    },
                },
            },
            "lifecycle": {
                "enable": third_party_lifecycle,
                "portal": {
                    "enable": third_party_collaboration_portal,
                    "invitation_ttl_seconds": 86_400,
                    "retention_seconds": 31_536_000,
                    "max_messages": 100,
                    "max_artifacts": 20,
                    "max_artifact_bytes": 10_485_760,
                },
            },
        },
        "ai_regulatory_feed": ai_regulatory_feed,
        "red_team_assurance": red_team_assurance,
        "eu_ai_incidents": eu_ai_incidents,
        "grc_outbound_sync": grc_outbound_sync,
        "ai_literacy": ai_literacy,
        "ai_business_intake": ai_business_intake,
        "ai_cost_roi": ai_cost_roi,
        "ai_dynamic_risk": ai_dynamic_risk,
        "ai_policy_management": ai_policy_management,
        "ai_runtime_guardrails": ai_runtime_guardrails,
        "ai_cloud_agent_governance": ai_cloud_agent_governance,
        "databricks_ai_security_framework": databricks_ai_security_framework,
        "ai_governance_loop": ai_governance_loop,
        "ai_explainability": ai_explainability,
        "ai_conformity_workflow": ai_conformity_workflow,
        "ai_insurability": ai_insurability,
        "ai_regulated_verticals": ai_regulated_verticals,
        "ai_bank_mrm": ai_bank_mrm,
        "ai_benchmark_exchange": ai_benchmark_exchange,
        "ai_discovery_sensors": ai_discovery_sensors,
        "ai_media_provenance": ai_media_provenance,
        "ai_media_provenance_c2patool": ai_media_provenance_c2patool,
        "ai_media_provenance_c2patool_sha256": (ai_media_provenance_c2patool_sha256),
        "ai_identity_governance_connectors": ai_identity_governance_connectors,
        "ai_identity_governance_scheduler": ai_identity_governance_scheduler,
        "ai_identity_governance_controls": ai_identity_governance_controls,
        "ai_identity_change_coupling": ai_identity_change_coupling,
        "ai_identity_federation": ai_identity_federation,
        "ai_identity_conditional_access": ai_identity_conditional_access,
        "ai_identity_purview_writeback": ai_identity_purview_writeback,
        "ai_identity_production_composition": ai_identity_production_composition,
        "threat_hunt": threat_hunt,
        "env_hunt": env_hunt,
        "response_execution": response_execution,
    }
    if env_hunt:
        result["connectors"] = connectors
        result["enrichment_sources"] = list(dict.fromkeys(enrichment_sources))
        result["poll_seconds"] = poll_seconds
    return result


def pick_value() -> dict[str, Any]:
    """The savings (ROI) report's cost/value assumptions -- the CLIENT's own
    numbers, editable later on the dashboard Savings page.

    The Savings dashboard compares real completed work against the typical
    human cost: ``(tasks x hours per task x hourly rate) - agent spend``.
    Defaults are deliberately conservative ($75/h, 2h/task). Returns a dict
    written under ``[value]``.
    """
    console.print()
    console.print(
        "[dim]Savings report: the dashboard's Savings page compares real "
        "completed work against what the same work would cost a human -- "
        "using YOUR numbers. Set them now or tune them later on the page "
        "itself. Conservative defaults keep the claim defensible.[/dim]"
    )
    if not _q_confirm("Set your human-cost assumptions now?", default=True):
        return {}
    rate = _safe_float(
        _q_text("  Fully-loaded human hourly rate ($/hour)", default="75"), default=75.0
    )
    hours = _safe_float(
        _q_text("  Human hours one comparable task takes", default="2"), default=2.0
    )
    return {"hourly_rate": max(0.0, rate), "hours_per_task": max(0.0, hours)}


def pick_finance() -> dict[str, Any]:
    """Opt-in to the finance suite governance (finance-agent-suite §5/§8).

    Off by default. When enabled, governance pauses money movement for a human,
    you pick the compliance regimes to enforce (strictest-wins), set the
    delegation-of-authority dollar tiers, and point at an OFAC SDN list. The
    optional finance-operations module adds deterministic regulatory feeds,
    anomaly rules, governed screening cases, and scheduled GRC evidence work.
    Returns a dict written under ``[governance]`` / ``[finance]`` /
    ``[screening]`` / ``[finance_operations]``.
    """
    console.print()
    console.print(
        "[dim]Finance suite: the CFO-office governance wrapper -- segregation of "
        "duties, maker-checker, dollar-threshold approvals, and a signed book of "
        "record. Enabling pauses every money movement for a human and lets you "
        "enforce compliance regimes. OFF by default.[/dim]"
    )
    if not _q_confirm("Enable the finance suite governance?", default=False):
        return {"enable": False}
    regimes = _q_checkbox(
        "Compliance regimes to enforce (strictest-wins union):",
        [
            "sox",
            "coso",
            "gaap",
            "pci",
            "glba",
            "aml",
            "sec",
            "irs",
            "dora",
            "basel_iii",
            "ifrs_17",
        ],
        default=["sox", "gaap"],
    )
    require_human_above = _safe_float(
        _q_text("  Pause money movement above $ (DoA threshold; 0 = pause all)", default="5000"),
        default=5000.0,
    )
    deny_above = _safe_float(
        _q_text("  Hard-deny money movement above $ (0 = no hard ceiling)", default="0"),
        default=0.0,
    )
    require_fresh = _q_confirm(
        "  Require a FRESH human approval each time a paused action runs "
        "(ignore any prior 'remember this' grant)?",
        default=False,
    )
    sdn_path = _q_text(
        "  OFAC SDN list path for sanctions screening (blank to set later)",
        default="",
    ).strip()
    operations_enable = _q_confirm(
        "  Enable deterministic regulatory monitoring, anomaly cases, governed "
        "AML screening, and scheduled GRC control tests?",
        default=True,
    )
    operations: dict[str, Any] = {"operations_enable": operations_enable}
    if operations_enable:
        operations["federal_register_enable"] = _q_confirm(
            "  Poll the official Federal Register API?",
            default=True,
        )
        operations["texas_register_enable"] = _q_confirm(
            "  Poll the official Texas Register RSS and queue each issue for review?",
            default=False,
        )
        operations["eur_lex_enable"] = _q_confirm(
            "  Poll EUR-Lex (all EU Parliament and Council legislation) for review?",
            default=False,
        )
        operations["edpb_enable"] = _q_confirm(
            "  Poll the EDPB news feed (GDPR guidance and decisions) for review?",
            default=False,
        )
        operations["regulatory_domains"] = _q_checkbox(
            "  Regulatory domains to route into the review queue:",
            ["finance", "money_transmitter", "insurance_producer", "lending"],
            default=["finance", "money_transmitter", "insurance_producer"],
        )
        regulatory_minutes = max(
            5,
            min(
                10_080,
                _safe_int(
                    _q_text("  Regulatory feed poll interval (minutes)", default="60"),
                    default=60,
                ),
            ),
        )
        control_hours = max(
            1,
            min(
                744,
                _safe_int(
                    _q_text("  Finance-to-GRC control test interval (hours)", default="24"),
                    default=24,
                ),
            ),
        )
        operations.update(
            {
                "regulatory_poll_seconds": regulatory_minutes * 60,
                "control_test_interval_seconds": control_hours * 3600,
                "anomaly_enable": True,
                "sanctions_max_age_hours": 72,
                "control_owner": _q_text(
                    "  Finance control/evidence owner",
                    default="Finance Control Owner",
                ).strip()
                or "Finance Control Owner",
            }
        )
    return {
        "enable": True,
        "regimes": regimes,
        "require_human_above": require_human_above,
        "deny_above": deny_above,
        "require_fresh_human_approval": require_fresh,
        "sdn_path": sdn_path,
        **operations,
    }


def pick_oidc() -> dict[str, Any]:
    """Opt-in to OIDC ID-token verification for `maverick serve` (SSO).

    Off by default. When enabled, the server verifies an OpenID-Connect ID
    token (RS256/ES256 only — never HMAC/none) against the issuer + audience
    you configure, and maps the verified ``sub`` to a ``user:<sub>`` principal.
    Returns a dict written under ``[auth.oidc]``; ``{"enabled": False}`` when
    declined (so the writer emits nothing).
    """
    console.print(
        "[dim]OIDC SSO lets users authenticate to `maverick serve` with your "
        "identity provider (Okta, Auth0, Entra, Google, ...). Tokens are "
        "verified with the IdP's public keys; only RS256/ES256 are accepted "
        "(HMAC/none are rejected to prevent algorithm-confusion). OFF by "
        "default.[/dim]"
    )
    if not _q_confirm("Enable OIDC SSO token verification?", default=False):
        return {"enabled": False}
    issuer = _q_text(
        "  Issuer URL (the IdP's 'iss', e.g. https://example.okta.com)",
        default="",
    ).strip()
    audience = _q_text(
        "  Audience (your app's client_id / API audience)",
        default="",
    ).strip()
    jwks_uri = _q_text(
        "  JWKS URI (the IdP's signing-key endpoint, e.g. https://example.okta.com/oauth2/v1/keys)",
        default="",
    ).strip()
    result: dict[str, Any] = {
        "enabled": True,
        "issuer": issuer,
        "audience": audience,
        "jwks_uri": jwks_uri,
    }
    # Optional: the built-in browser-login (authorization-code) flow. The
    # default path is bearer-token verification only (API clients) or a reverse
    # proxy for browser SSO; this self-contained flow is for deployments that
    # can't run an auth proxy. OFF unless the operator opts in and supplies the
    # OAuth client + a session-signing secret.
    console.print(
        "[dim]Optional: built-in browser login. Lets the dashboard run the "
        "OAuth2 authorization-code flow itself (browser SSO without a separate "
        "auth proxy). Needs an OAuth client_id/secret registered with your IdP "
        "and a redirect URI of <dashboard-url>/auth/callback. OFF by "
        "default.[/dim]"
    )
    if _q_confirm("  Also enable the built-in browser login flow?", default=False):
        client_id = _q_text(
            "    OAuth client_id (this dashboard's registered client)",
            default="",
        ).strip()
        client_secret = _q_text(
            "    OAuth client_secret",
            default="",
        ).strip()
        redirect_uri = _q_text(
            "    Redirect URI (must be <dashboard-url>/auth/callback)",
            default="",
        ).strip()
        session_secret = _q_text(
            "    Session-signing secret (a long random string; keep it secret)",
            default="",
        ).strip()
        result.update(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "session_secret": session_secret,
            }
        )
    return result


def _ask_external_agent_followups(advanced: dict[str, Any]) -> None:
    """The external-agent gateway's follow-up questions (gateway already on).

    Governed execution rides on the gateway: naming a connector is the
    operator's explicit decision to let foreign agents ACT through
    Lightwork's egress-guarded, receipted connector path. Blank keeps the
    gateway screen-only; the kernel intersects with its reference factories
    anyway, so the filter here just surfaces a typo at answer time instead
    of silently at runtime.
    """
    raw = _q_text(
        "  Governed connectors external agents may EXECUTE through "
        "(comma-separated: salesforce, servicenow; blank = screen-only)",
        default="",
    )
    connectors = []
    for name in dict.fromkeys(_csv_list(raw, lower=True)):
        if name in ("salesforce", "servicenow"):
            connectors.append(name)
        else:
            console.print(
                f"  [yellow]unknown connector '{name}' — skipped "
                "(valid: salesforce, servicenow)[/yellow]"
            )
    if connectors:
        advanced["external_connectors"] = connectors
    # Both hardening switches below are strict, tightening booleans in the
    # kernel (a malformed config value engages them, never disables).
    if _q_confirm(
        "  Require the strong credential? An agent whose trust entry pins "
        "an Ed25519 key or names a JWT issuer must present it -- its "
        "minted bearer alone stops authenticating (token-only agents are "
        "unaffected). Off by default.",
        default=False,
    ):
        advanced["external_require_signed"] = True
    if _q_confirm(
        "  Step-up approval on credential minting? Each mint/rotation "
        "parks a dashboard approval a decision-maker must approve before "
        "the token is issued (one approval mints exactly one credential). "
        "Off by default.",
        default=False,
    ):
        advanced["external_mint_approval"] = True


def _ask_governed_execution_followups(advanced: dict[str, Any]) -> None:
    """Follow-ups for the two default-off governed-execution planes.

    The session kernel's containment IS the sandbox backend, which was already
    chosen several steps back, so the note points back at that answer and
    names the backends that can host a session at all: the kernel writes its
    driver, statement and result file into the sandbox workdir, which ssh and
    kubernetes do not share with the host.

    Self-refinement's approval gate is asked separately because it is the
    whole reason an agent may write its own instructions at all; declining it
    is a real choice, so it is written explicitly rather than defaulted.
    """
    if advanced.get("repl"):
        console.print(
            "[dim]  The kernel runs model-written Python through "
            "sandbox.exec, so the sandbox backend you picked earlier is its "
            "containment boundary: it should be docker/gvisor/podman -- "
            "'local' runs that code on this machine. ssh and kubernetes "
            "cannot host a session (the kernel needs a workdir shared with "
            "the host); edit [sandbox] backend if you chose one of those."
            "[/dim]"
        )
    if advanced.get("harness_refine") and not _q_confirm(
        "  Require a human approval before a refinement is applied? Each "
        "proposal parks a dual-control approval bound to that one target and "
        "name, spent once. Declining lets an applied refinement rewrite the "
        "agent's own instructions with no human decision. ON by default.",
        default=True,
    ):
        advanced["harness_refine_require_approval"] = False


def pick_advanced() -> dict[str, Any]:
    """Configure advanced reasoning, learning, and governance features.

    Governed learning choices default on; higher-authority or infrastructure
    features retain explicit opt-ins. All are editable later in
    ~/.maverick/config.toml.
    """
    console.print()
    advanced: dict[str, Any] = {
        "cost_aware": _q_confirm(
            "Cost-aware routing? Use the cheapest capable model per role to cut spend.",
            default=False,
        ),
        "tree_of_thought": _q_confirm(
            "Tree-of-thought planning? Draft a few plans and let a critic pick the "
            "best before working (more tokens up front, fewer dead ends).",
            default=False,
        ),
        "compact_history": _q_confirm(
            "Compact long conversations? Keep the most relevant older turns under a "
            "token budget instead of just the last few.",
            default=False,
        ),
        "model_scaled_context": _q_confirm(
            "Model-scaled context? Size history windows, compaction targets, and "
            "routing thresholds to the driving model's real context window (a "
            "1M-token model gets 1M-token bounds) instead of fixed constants. "
            "Recommended; ON by default.",
            default=True,
        ),
        "voice_commands": _q_confirm(
            "Dashboard voice commands? A mic button on the chat composer records "
            "a spoken goal and transcribes it (OpenAI/Groq Whisper key or the "
            "built-in local Whisper; falls back to browser speech recognition). "
            "ON by default.",
            default=True,
        ),
        "voice_local_stt": _q_confirm(
            "  Built-in local speech-to-text? Auto-fetch a checksum-verified "
            "Whisper model (~148 MB, at dashboard startup) so the mic works "
            "offline with no provider key — the engine ships with the "
            "dashboard. ON by default (egress-locked deployments stay off).",
            default=True,
        ),
        "attach_any_mime": _q_confirm(
            "Accept attachments of ANY declared type? Text, images, audio, video, "
            "and document formats are always accepted; this also admits anything "
            "else EXCEPT executables/archives (magic-byte deny stays). OFF by "
            "default.",
            default=False,
        ),
        "attachment_understanding": _q_confirm(
            "Attachment understanding? Auto-transcribe incoming audio/video and "
            "extract text from Office documents into the goal context (audio "
            "needs an STT key: OPENAI_API_KEY/GROQ_API_KEY or local "
            "faster-whisper). ON by default.",
            default=True,
        ),
        "compaction_strategy": _q_select(
            "  Compaction strategy? (default = simple shrink)",
            [
                "default     - keep recent + trim big tool outputs",
                "learned     - LLM summary, self-tuning prompt picker",
                "multimodal  - stub heavy image/audio blocks to text",
                "streaming   - incremental running summary (long chats)",
                "graph       - entity-relation digest",
            ],
            default="default     - keep recent + trim big tool outputs",
        ).split()[0],
        "reflexion": _q_confirm(
            "Reflexion learning? Remember lessons from failed runs and recall them "
            "on the next similar goal.",
            default=True,
        ),
        "self_harness": _q_confirm(
            "Self-harness? Learn a MODEL-SPECIFIC operating-guidance addendum from "
            "recurring failures -- mined, validated on held-out cases, and gated "
            "through the same promotion ladder as every other learned change -- "
            "then recalled into that model's system prompt. Inspect or roll it "
            "back any time with `maverick self-harness`. ON by default.",
            default=True,
        ),
        "fleet_memory": _q_confirm(
            "Fleet memory? Let EXTERNAL agents (Agentforce, Copilot, custom) "
            "deposit experience into and recall from Lightwork's governed "
            "memory -- Shield-scanned, provenance-tagged, audited reads. "
            "An explicit trust decision; OFF by default.",
            default=False,
        ),
        "cortex": _q_confirm(
            "Enable Cortex, the governed company memory over knowledge, skills, "
            "lessons, the Operating Record and fleet memory (recommended)? One "
            "recall/deposit surface, tenant-exact, one audit row per read and "
            "per write; each source's own toggle still applies. Inspect it with "
            "`maverick cortex status`. ON by default.",
            default=True,
        ),
        "external_agents": _q_confirm(
            "External-agent gateway? Govern agents built on OTHER platforms "
            "(Salesforce Agentforce, AWS Bedrock, custom runtimes): enroll "
            "them, credential them, screen their actions, and account their "
            "runs on the Operating Record. An explicit trust decision; OFF "
            "by default.",
            default=False,
        ),
        "repl": _q_confirm(
            "Governed session kernel? Let an agent write PYTHON against a live "
            "namespace instead of composing fixed tool calls -- every "
            "statement is hashed, injection-screened, receipted on the "
            "tamper-evident chain and audited. It admits ARBITRARY CODE "
            "EXECUTION, so run it on a container sandbox backend "
            "(docker/gvisor/podman), never on 'local'. OFF by default.",
            default=False,
        ),
        "harness_refine": _q_confirm(
            "Governed self-refinement? Let an agent PROPOSE a change to its "
            "own operating instructions (prompt / skill / memory) from a "
            "failure it observed. Nothing self-applies: a proposal parks a "
            "human approval, applying is snapshotted and reversible, and both "
            "ends land in the signed learning audit. OFF by default.",
            default=False,
        ),
        "session_tree": _q_confirm(
            "Run forking? Branch a run at a decision point so Oversight can "
            "read what the agent chose beside the alternative, under one root "
            "on the Run Tree page. Lineage only -- forking re-executes "
            "nothing and spends no tokens. ON by default.",
            default=True,
        ),
        "memory_guard": _q_confirm(
            "Memory Guard (OWASP ASI06)? Screen every stored fact for prompt-"
            "injection/poisoning, stamp it with provenance + a trust tier, and "
            "keep low-trust memory out of the agent's standing brief (trust-aware "
            "retrieval). Every decision is audited. OFF by default.",
            default=False,
        ),
        "temporal_memory": _q_confirm(
            "Temporal memory? Keep a bitemporal history of every fact (validity "
            "windows) instead of overwriting -- answer 'what did we believe on "
            "date X, and why' for the Operating Record. OFF by default.",
            default=False,
        ),
        "fairness_monitor": _q_confirm(
            "Continuous fairness monitoring (ISO 42001 A.6.2.6)? Watch decision "
            "outcomes over a rolling window and raise a signed FAIRNESS_ALERT when "
            "the four-fifths rule is breached or fairness drifts below baseline. A "
            "deployment opts in by feeding the monitor its outcomes. OFF by default.",
            default=False,
        ),
        "drift_monitoring": _q_confirm(
            "Continuous model drift monitoring? Keep governed aggregate baselines "
            "for feature/prediction distributions, embeddings, and delayed-ground-"
            "truth performance; emit bound assurance evidence and a signed alert "
            "when configured PSI, KL, cosine, or decay thresholds are crossed. Raw "
            "rows and vectors are not retained. OFF by default.",
            default=False,
        ),
        "ai_incident_reporting": _q_confirm(
            "EU AI Act Art. 73 serious-incident reporting? Classify an "
            "assurance incident under Art. 3(49), start the tiered 2/10/15-day "
            "clock from awareness, draft the report for the routed Member-State "
            "authority, and record human-gated submissions. Clocks and drafts "
            "only -- nothing is ever transmitted. OFF by default.",
            default=False,
        ),
        "ai_regulatory_feed": _q_confirm(
            "AI regulatory-change impact bridge? Run reviewed regulatory "
            "alerts through explicit operator rules to create policy-impact "
            "records, exact evidence-staleness flags, and idempotent customer "
            "notices. Requires finance operations and the AI evidence gateway. "
            "Decision support, never a legal applicability opinion. OFF by "
            "default.",
            default=False,
        ),
        "eu_conformity": _q_confirm(
            "EU AI Act conformity workflows? Human-gated registers for the "
            "Art. 27 fundamental-rights impact assessment, the Annex IV "
            "technical-documentation checklist, and the Art. 43 conformity-"
            "assessment route with forbidden combinations refused. Drafting "
            "and tracking assistance, never a conformity assessment or legal "
            "advice. OFF by default.",
            default=False,
        ),
        "explainability": _q_confirm(
            "Model-agnostic explainability? Seeded permutation importance, "
            "partial dependence, Spearman global sensitivity, and "
            "standardized-deviation reason codes, recordable as governed "
            "evidence. Attribution for your own review, never a fairness "
            "certification. OFF by default.",
            default=False,
        ),
        "content_provenance": _q_confirm(
            "Signed content provenance (EU AI Act Art. 50(2), applicable "
            "2 Dec 2026)? Machine-readable sidecar manifests binding "
            "generated content to the generating system, Ed25519-signed and "
            "verifiable against an external key registry. Marking assistance "
            "only, never a content-authenticity certification. OFF by "
            "default.",
            default=False,
        ),
        "llm_gateway": _q_confirm(
            "Governed LLM gateway? Agents built anywhere point their "
            "OpenAI- or Anthropic-compatible base URL at Lightwork; every "
            "call crosses a governed checkpoint (own bearer, model allowlist, "
            "prompt/completion redaction, injection screen, bounded bodies, "
            "team budget, and egress-guarded upstream) and unknown callers "
            "land in a shadow-AI ledger. Requires reviewed upstream URLs and "
            "tokens after setup. OFF by default.",
            default=False,
        ),
        "vendor_management": _q_confirm(
            "Third-party vendor register? Governed vendor records with "
            "criticality tiers, questionnaire campaigns served over the "
            "public form endpoint, monitoring notes, links to assessments "
            "and RoPA entries, and a OneTrust vendor-CSV importer. A "
            "register and audit trail, never a vendor risk rating service. "
            "OFF by default.",
            default=False,
        ),
        "consent_registry": _q_confirm(
            "Consent receipts and preferences registry? Per-subject consent "
            "receipts (purpose, policy version, channel) with salted "
            "subject digests, a current-preference projection, audit "
            "export, and a OneTrust consent-CSV importer. The governed "
            "record behind a consent front end, never a cookie banner or "
            "TCF-certified CMP. OFF by default.",
            default=False,
        ),
        "data_map": _q_confirm(
            "Data map and discovery? A governed register of data assets "
            "with a column-name classifier, a deterministic CONTENT "
            "classifier for pasted data samples (Luhn/mod-97/structural "
            "validation, never an ML guess), an information_schema CSV "
            "importer for warehouses, a SharePoint site census via "
            "Microsoft Graph, and a Snowflake masking-policy DDL export. "
            "Triage for a human data-mapping exercise, never a crawler. "
            "OFF by default.",
            default=False,
        ),
        "breach_notification": _q_confirm(
            "Breach-notification planner? Fourteen regimes' verified "
            "who/when/whether rules (GDPR, HIPAA, six US states, LGPD, "
            "PIPEDA, Australia, Singapore, Japan, Korea) turned into "
            "per-incident obligations with computed deadlines and "
            "citations. Decision support for your counsel, never legal "
            "advice, and nothing is ever transmitted. OFF by default.",
            default=False,
        ),
        "dsar_fulfillment": _q_confirm(
            "DSAR fulfillment pipeline? Execute subject requests as a "
            "governed pipeline: recorded identity verification, collection "
            "logging, deterministic third-party redaction, and a "
            "token-gated response link assembled at delivery time (no "
            "second copy of personal data stored). OFF by default.",
            default=False,
        ),
        "ai_literacy": _q_confirm(
            "EU AI Act Art. 4 AI-literacy register? Named humans set which "
            "trainings each role requires, record completions with evidence "
            "references, and export gap and evidence snapshots for auditors. "
            "Decision support, never a certification of competence. OFF by "
            "default.",
            default=False,
        ),
        "grc_sync": _q_confirm(
            "Outbound GRC synchronization? Idempotently mirror model-risk "
            "findings, accepted risks, and incidents into your ServiceNow, "
            "Archer, or other REST GRC system as governed pointer records "
            "with a round-trip link. Evidence never leaves Lightwork. Paths "
            "and connector are configured in [grc_sync]. OFF by default.",
            default=False,
        ),
        "agent_topology": _q_confirm(
            "Agent dependency and MCP blast-radius governance? Project each "
            "enabled agent's tools, MCP servers, hosts, paths, knowledge, and model "
            "routes with the trust boundary at every hop; promote configured MCP "
            "servers into the assurance inventory without retaining credentials. "
            "OFF by default.",
            default=False,
        ),
        "governance_inventory": _q_confirm(
            "Enable the shared governance inventory? Build a tenant-scoped, "
            "revisioned projection across the stock governance suites and entity "
            "graph with closed retention, record, relationship, export, and "
            "snapshot bounds. OFF by default.",
            default=False,
        ),
        "specialist_discipline": _q_confirm(
            "Specialist operating discipline? Append each business suite's "
            "professional guardrails (finance maker-checker, legal privilege, "
            "HR PII-minimization, ...) to every domain pack's persona at "
            "spawn. Recommended; prompts only, hard limits stay enforced by "
            "capabilities/governance.",
            default=True,
        ),
        "allow_pack_editing": _q_confirm(
            "Allow editing agents (domain packs) from the dashboard? Operators "
            "can fork/tweak a specialist's persona, tools, and workflow per "
            "client; an edit is validated before it saves, so it can never "
            "weaken the safety envelope. Turn off to lock the agent roster.",
            default=True,
        ),
        "allow_role_editing": _q_confirm(
            "Allow editing the core roles (orchestrator, coder, ...) from the "
            "dashboard? Operators can add a per-client system-prompt addendum "
            "to a role. Turn off to lock role behavior. (Model/effort routing "
            "is configured separately.)",
            default=True,
        ),
        "dreaming_llm_consolidation": _q_confirm(
            "  └ LLM-enriched consolidation? Have the cheap summarizer model "
            "rewrite each consolidated failure into a transferable lesson "
            "(instead of the deterministic template). Inputs+output are "
            "shield-scanned and the spend is budgeted; fails open to the "
            "deterministic text. Costs a few tokens per dream cycle.",
            default=False,
        ),
        "dreaming": _q_confirm(
            "Dreaming (offline consolidation)? `maverick dream` replays recent "
            "runs while idle, distills recurring wins into skills per department, "
            "turns repeated failures into recalled insights (promoting patterns "
            "shared across departments), retires skills whose track record "
            "decayed, and prunes stale lessons. Deterministic by default "
            "(opt-in LLM enrichment + rehearsal runs are separate and budgeted).",
            default=True,
        ),
        "verify_ensemble": _q_confirm(
            "Ensemble verification? Cross-check final answers with a panel of models "
            "(slower, stronger).",
            default=False,
        ),
        "risk_proportional_verify": _q_confirm(
            "Risk-proportional verification? Skip the verifier on trivial, low-risk "
            "answers (short, prose-only, no tools or code) to save tokens and latency.",
            default=False,
        ),
        "autonomy_gate": _q_confirm(
            "Autonomy gate? When sub-agents disagree, cross-check the answer with a "
            "model panel AND hold irreversible (high-risk) actions until the "
            "disagreement is resolved or a human approves.",
            default=False,
        ),
        "headless_assume": _q_confirm(
            "Autonomous (headless) mode? When no human is available to answer, the "
            "agent states a reasonable assumption and continues instead of stalling "
            "on a clarifying question. Best for batch / unattended runs.",
            default=False,
        ),
        "governed_actions": _q_confirm(
            "Governed actions? Record a tamper-evident lineage of every consequential "
            "agent action (writes, shell) so a run's actions are auditable end-to-end.",
            default=False,
        ),
        "workforce_levels": _q_confirm(
            "Per-agent autonomy levels? Treat each agent like a hire with a level of "
            "authority you set -- observe / suggest / request-approval / autonomous, "
            "per action risk -- starting supervised (onboarding) and graduating on a "
            "clean record. Off by default, every agent stages actions for human "
            "execution. Per-agent overrides go under [workforce.agents].",
            default=False,
        ),
        "workforce_data_grounding": _q_confirm(
            "Primary-source data grounding? Give each analyst pack its suite's "
            "public/government data connectors (SEC EDGAR, FRED, openFDA, "
            "USAspending, weather, ...) so it grounds work in primary sources. "
            "GET-only, low-risk, deferred (no context cost), and inert without "
            "each source's API key. On by default; turn off to withhold them.",
            default=True,
        ),
        "calibration_enforce": _q_confirm(
            "Calibration interlock? Freeze self-improvement (trajectory donation) "
            "if the verifier stops telling correct answers from incorrect ones on "
            "your labeled set, so the system never learns from a drifted evaluator.",
            default=False,
        ),
        "donate_trajectories": _q_confirm(
            "Donate run trajectories for training? Write scrubbed records of your "
            "runs to ~/.maverick/outbox/ so you can train the self-learning loop "
            "(PRM + DPO) on your OWN data -- nothing is uploaded, the records stay "
            "on this machine until you choose to ingest/train. Metadata-only by "
            "default; raw text stays local unless you also set [telemetry] "
            "donate_text. Off by default. See docs/self-learning-runbook.md.",
            default=False,
        ),
        "adaptive_compute": _q_confirm(
            "Adaptive test-time compute? Concentrate effort on uncertain sub-tasks "
            "and spend less when the swarm agrees (cheaper, focused).",
            default=False,
        ),
        "best_of_n": _q_confirm(
            "Best-of-N answers? Sample a few candidate answers and keep the one the "
            "verifier scores highest (stronger, more tokens).",
            default=False,
        ),
        "structured_verifier_off": _q_confirm(
            "Turn OFF the structured rubric verifier? It's ON by default: the "
            "verifier scores each FINAL on a rubric (correctness/completeness/"
            "grounding/safety) and a failing safety facet vetoes even a high score, "
            "with the rubric recorded for the learning audit (maverick.reasoning_reward). "
            "Costs ~more tokens per verify; say yes to fall back to the scalar verifier.",
            default=False,
        ),
        "jit_rl_off": _q_confirm(
            "Turn OFF JitRL test-time adaptation? It's ON by default but a no-op until "
            "experience accumulates: it records verifier-guided best-of-N outcomes and "
            "steers candidate selection toward what has verified well (maverick.jit_rl, "
            "no weight updates). Say yes to disable it entirely.",
            default=False,
        ),
        "audit_rewards": _q_confirm(
            "Sign verification rewards into the audit chain? Provable learning: write a "
            "tamper-evident, signed row for every structured reward the system learns "
            "from -- the per-dimension rubric, the score, and where a facet vetoed "
            "(maverick.reasoning_reward). On by default (adds audit volume); recommended "
            "for regulated deployments.",
            default=False,
        ),
        "reward_laundering": _q_confirm(
            "Enforce reward-laundering resistance? Beyond freezing on verifier DRIFT, "
            "also freeze learning when the judge stays sharp on normal traffic but its "
            "edge on adversarial probes collapses -- i.e. it's being GAMED "
            "(maverick.calibration). Off by default; enables the calibration interlock "
            "and needs adversarial probes recorded to bite.",
            default=False,
        ),
        "signed_approval": _q_confirm(
            "Require cryptographic approval for code/weights self-changes? Replace the "
            "self-settable approval boolean with an Ed25519 signature over the exact "
            "change, verified against operator-held keys, so a self-modifying agent "
            "cannot approve itself (maverick.approval_signing). Off by default; you "
            "provide approver public keys (fails closed until you do).",
            default=False,
        ),
        "self_modify": _q_confirm(
            "Enable RESEARCH-ONLY code evolution? The workforce may propose diffs to a "
            "narrow editable allowlist, evaluate them in isolated no-egress containers, "
            "and archive development telemetry (maverick.self_modify). It NEVER adopts "
            "code: production adoption remains disabled until an external one-shot "
            "evaluator and PREPARE/CAS/COMMIT integration exist. Off by default and inert "
            "until editable_paths plus at least two discriminating eval_tests are set.",
            default=False,
        ),
        "adapter_rung": _q_confirm(
            "Enable GOVERNED in-tenant weights adaptation? Train LoRA adapters for a "
            "LOCAL open-weights base model on your own corrections and traces -- frontier-"
            "model output is refused by default (distillation guard) -- and promote them "
            "through the weights rung: held-out evidence, no new authority, Ed25519 human "
            "signature, one-step rollback (maverick.adapter_rung). Nothing leaves the "
            "tenant. Off by default AND inert until you set base_model; needs Ollama (or "
            "compatible) serving and the [training] extra for real tuning.",
            default=False,
        ),
        "skill_synthesis": _q_confirm(
            "Test-time skill synthesis? Write a short task-specific cheat-sheet for "
            "each goal before working on it.",
            default=True,
        ),
        "experience_guidance": _q_confirm(
            "Experience-guided orchestration? Steer planning with how similar past "
            "goals turned out (what worked, what failed).",
            default=True,
        ),
        "credit_assignment": _q_confirm(
            "Counterfactual credit assignment? After a swarm answers, work out which "
            "sub-agent actually helped (ablate + re-verify) to improve learning and "
            "routing. Costs extra verifier calls per swarm.",
            default=True,
        ),
        "causal_promotion": _q_confirm(
            "Counterfactual promotion? When governed self-improvement is on, promote a "
            "learned change (tool/prompt/policy) only if its confounder-adjusted CAUSAL "
            "effect on outcomes clears the bar -- not just a correlation that co-occurred "
            "with success. Each promotion records the effect, its confidence interval, and "
            "what it adjusted for. Requires self-improvement enabled.",
            default=True,
        ),
        "factory_learning": _q_confirm(
            "Self-improving agent factory? Mine recurring pack-generation gaps (a tool a "
            "draft kept omitting, a skill its workflow kept needing) into proposer "
            "corrections and promote them through the self-improvement gate, so future "
            "packs are drafted better. Guidance text only -- never widens an envelope. "
            "Requires self-improvement enabled.",
            default=True,
        ),
        "evaluator_evolution": _q_confirm(
            "Evaluator co-evolution? When the learned judge stops discriminating, don't "
            "just FREEZE learning -- promote a better judge: a challenger evaluator "
            "replaces the incumbent only when its agreement with a fixed, checksum-locked "
            "ground-truth ANCHOR beats it (on the dedicated 'evaluator' rung). The anchor "
            "is immutable so a weak judge can't launder drift. Requires self-improvement "
            "enabled; an evaluator swap needs human approval until you raise max_auto_rung "
            "to 'evaluator'.",
            default=True,
        ),
        "rehearsal": _q_confirm(
            "Pre-execution rehearsal? Before a risky plan runs, simulate it against the "
            "learned world-model of your environment and gate on the prediction: proceed "
            "when the model is confident it's safe, BLOCK a confidently-poor outcome, and "
            "ESCALATE to a human when the model is unsure or has never seen the move. "
            "Governance that lets agents be bolder where it's earned; on by default.",
            default=True,
        ),
        "speculative": _q_confirm(
            "Speculative execution? On turns where the world-model is highly confident "
            "what comes next (a well-trodden, near-deterministic step), draft with a cheap "
            "model instead of the frontier one -- reserving the expensive model for novel "
            "or uncertain turns. Cuts cost/latency on repetitive workflows; off by default "
            "and a no-op until you set a draft model.",
            default=False,
        ),
        "data_engine": _q_confirm(
            "Cognitive Data Engine? The Tesla-style improvement flywheel: production "
            "failures are triaged by CAUSAL impact on real outcomes (fix what moves "
            "reality most, not what's merely frequent), then mined, validated in the "
            "world-model, and promoted through the safety ladder. The workforce compounds "
            "from its own experience; on by default.",
            default=True,
        ),
        "operations_scientist": _q_confirm(
            "Operations Scientist? An agent that DISCOVERS a better process and proves it: "
            "it pairs a harmful action with the beneficial habit that should replace it, "
            "validates the swap in the world-model, then runs a real causal experiment and "
            "ships the proven win. Discovery, not just labour; on by default.",
            default=True,
        ),
        "consequence": _q_confirm(
            "Consequence Engine? Ground the workforce's learning in REAL outcomes instead "
            "of a model's self-graded proxy: when a downstream result lands (an invoice "
            "paid, a ticket reopened), it overrides the proxy reward so the data engine "
            "learns from reality. Reality is the reward signal; on by default.",
            default=True,
        ),
        "earned_autonomy": _q_confirm(
            "Earned autonomy? Agents earn the right to act, action type by action type, "
            "by proving they predict consequences correctly: each rehearsed high-stakes "
            "action pins a consequence card, reality grades it, and a proven streak "
            "graduates the action from 'a human approves' to 'policy auto-approves' -- "
            "with instant demotion on one miss and a guaranteed undo until it's earned. "
            "Enabling only records evidence; graduation stays off until you also arm "
            "auto_graduate. Off by default.",
            default=False,
        ),
        "flows": _q_confirm(
            "Flow engine? A deterministic skeleton -- branch, switch, loop, parallel, "
            "approval, delay, wait-for-event, try/catch -- over steps that are either a "
            "fixed tool call or a full agentic goal. Migrated workflows keep their "
            "structure and auditability, while any node can still be an agent; each "
            "node's outcome trains which steps should be deterministic vs. agentic. "
            "Off by default.",
            default=False,
        ),
        "connections": _q_confirm(
            "Named connections? Store a connector's base URL + API token under a name, "
            "sealed at rest, so a connector can be wired from the dashboard without "
            "setting <NAME>_TOKEN env vars (an existing env var still wins). Off by "
            "default.",
            default=False,
        ),
        "emergent_protocol": _q_confirm(
            "Emergent coordination shorthand? Swarms evolve short codes for the boilerplate "
            "they repeat (cheaper coordination), while every code decodes EXACTLY back to "
            "English -- the auditable translation layer, so nothing is ever hidden from the "
            "Shield or a human. Off by default; a no-op until a codebook is learned.",
            default=False,
        ),
        "emergent_codec": _q_confirm(
            "Measure the token-aware codec on live coordination? The codec that saves "
            "actual frontier TOKENS (not just bytes), via byte-stuffed cheap codes. When "
            "on, the blackboard measures -- never changes -- what it would compress real "
            "traffic to, so you can confirm the savings before agents ever read codes. "
            "Off by default; pure telemetry, the audit/Shield path is untouched.",
            default=False,
        ),
        "enforce_capabilities": _q_confirm(
            "Enforce agent capabilities? Each agent runs under a scoped grant and "
            "spawned sub-agents can only narrow it, never exceed it (least privilege).",
            default=False,
        ),
        "per_call_token_exchange": _q_confirm(
            "Per-call token exchange? Each tool call trades the run-long grant for "
            "a freshly minted, single-tool, short-lived signed token (zero-trust; "
            "shrinks the blast radius of a mid-run compromise). Needs capability "
            "enforcement.",
            default=False,
        ),
        "enforce_quotas": _q_confirm(
            "Enforce per-principal usage quotas? Track spend (dollars + tokens) per "
            "user per day and refuse to start a new goal once the daily cap is hit "
            "(chargeback / cost governance across runs, beyond the per-run budget).",
            default=False,
        ),
        "tenant_by_user": _q_confirm(
            "Isolate each user into their own tenant? Per-user cross-session memory "
            "is kept separate — recommended for multi-user servers.",
            default=False,
        ),
        "client_id": _q_text(
            "Client/tenant id for THIS deployment (one Lightwork per enterprise "
            "client). All data (world DB, audit, memory, fleet) is isolated under "
            "this id — leave blank only for a personal/single-user install. "
            'Letters/digits/._- e.g. "acme-corp".',
            default="",
        ),
        "enterprise": _q_confirm(
            "Enterprise mode (private/sensitive data)? Pin every LLM call to a "
            "local/self-hosted model so data never leaves your boundary, gate "
            "destructive actions, and enforce per-agent capabilities. Recommended "
            "when the agent handles PHI/PII/financial data.",
            default=False,
        ),
        "agent_trust": _q_confirm(
            "Govern which OUTSIDE agents your agents may talk to? Engages the Agent "
            "Trust Plane: external agents (federation peers, A2A callers, fleet "
            "agents) are default-DENIED unless listed in [agent_trust] agents with a "
            "pinned key, direction, and tool/budget/data ceiling. Auto-on under "
            "enterprise mode; recommended at the company boundary.",
            default=False,
        ),
        "control_plane": _q_confirm(
            "Turn on the Agent Control Plane? One console (dashboard page + "
            "`maverick control-plane`) over the running/revoked native principals "
            "and enrolled external agents this deployment can resolve: suspend / "
            "resume / contain / attenuate a principal, or operate HALT. Principal "
            "verbs land in enforcement the runtime already consults and attempt a "
            "control-plane audit row. Ordinary audit outages are marked in the "
            "result; a configured audit refusal propagates after any already-landed "
            "change. CLI HALT covers this local deployment; dashboard HALT reaches "
            "other replicas only with a configured shared authority. Reads remain "
            "available while the verbs are off and fail closed when a required "
            "authority is unreadable. Recommended wherever more than one person "
            "operates agents.",
            default=False,
        ),
        "anonymous_logs": _q_confirm(
            "Anonymous mode? Scrub user-identifying content (goal text, user/channel "
            "ids, home paths, emails/phones) from logs and audit events — hashes or "
            "sentinels instead of raw values. Good for shared/regulated environments.",
            default=False,
        ),
        "encrypt_at_rest": _q_confirm(
            "Encrypt sensitive local stores at rest? Seals the cross-session "
            "memory store with AES-256-GCM (key in ~/.maverick/keys, chmod 600). "
            "Implied by enterprise mode; recommended for PHI/PII/financial data.",
            default=False,
        ),
        "encrypt_per_tenant": _q_confirm(
            "Per-tenant encryption keys? Each tenant gets its own data key "
            "(wrapped by a KMS KEK) so one tenant's key never opens another's "
            "data — the posture a hosted multi-tenant store needs. Requires "
            "at-rest encryption; reads of existing data stay transparent. "
            "Off by default (single-tenant boxes don't need it).",
            default=False,
        ),
        "pg_rls": _q_confirm(
            "Database-enforced tenant isolation (Postgres Row-Level Security)? "
            "Only for the shared Postgres backend with MULTIPLE tenants: the DB "
            "itself rejects cross-tenant rows as defense-in-depth over the "
            "app-layer scoping. REQUIRES one-time prep first — assign legacy rows "
            "with `maverick tenant backfill --tenant <id>` and verify with "
            "`maverick tenant rls-preflight`, or pre-tenancy rows become invisible. "
            "Off by default; leave off for SQLite or single-tenant installs.",
            default=False,
        ),
        "audit_sign": _q_confirm(
            "Sign the audit log for tamper-evidence? Ed25519 hash-chains every "
            "audit row (plus a signed cross-file ledger) so `maverick audit verify` "
            "can prove the log was not altered — the basis for SOC 2 evidence. "
            "Needs the [audit-signing] extra; falls back to unsigned if absent.",
            default=False,
        ),
        "audit_worm": _q_confirm(
            "Export closed audit day-files to a write-once (WORM) store? Beyond "
            "tamper-EVIDENCE, this makes the historical log un-alterable: each "
            "closed day-file is shipped with a retention lock so it can't be "
            "rewritten or deleted (S3 Object-Lock for regulator-grade WORM, or a "
            "local read-only mirror). Run `maverick audit worm push` (e.g. nightly "
            "cron). Defaults to a local mirror; edit [audit.worm] for S3.",
            default=False,
        ),
        "unified_audit": _q_confirm(
            "Project all six governance suites into one tenant-bound signed audit "
            "view? Each source stays authoritative while a bounded, digest-only "
            "projection exposes explicit coverage, lag, retry, revocation and "
            "integrity status. The managed runtime also stages independently "
            "verifiable checkpoints; WORM remains separately opt-in. This enables "
            "audit signing. OFF by default.",
            default=False,
        ),
        "saml": _q_confirm(
            "Enable SAML 2.0 SSO (alongside or instead of OIDC)? For enterprises "
            "whose IdP (Okta, Entra/Azure AD, ADFS) mandates SAML over OIDC. "
            "Writes a [auth.saml] template you fill in with your SP/IdP details, "
            "then hand /saml/metadata to the IdP. Needs the [saml] extra (pysaml2) "
            "and the browser-login session secret. Off by default.",
            default=False,
        ),
        "security_autofix": _q_confirm(
            "Let the security assessor auto-fix low-risk gaps? With enterprise mode "
            "on, `maverick remediate --apply` may auto-apply reversible, in-boundary "
            "config fixes (enable audit signing, set retention); anything "
            "behaviour-changing stays gated for a human. Off by default; every fix "
            "is audited and reversible.",
            default=False,
        ),
        "dual_approval": _q_confirm(
            "Require two-person approval for risky actions (N-of-M dual control)? "
            "A high/critical-risk action then needs 2 DISTINCT approvers in the "
            "dashboard queue, and the requester can't approve their own request — "
            "the segregation-of-duties control SOX / SOC 2 / HIPAA auditors test. "
            "Writes [security] approvals_required = 2. Off by default.",
            default=False,
        ),
        "deferred_tools": _q_confirm(
            "Deferred tool loading? Show the model a small core toolset plus a "
            "find_tools search tool, loading the long tail (80+ integrations, MCP) "
            "on demand. Big context savings when many tools are enabled.",
            default=False,
        ),
        "shield_updates": _q_confirm(
            "Pull signed shield-rule updates? Fetches a publisher-signed rules "
            "bundle ([shield] update_url + update_pubkey; Ed25519-verified, "
            "downgrades refused) and stages it for the shield. Off by default.",
            default=False,
        ),
        "ebpf_monitor": _q_confirm(
            "Enable the eBPF syscall monitor? An operator-run bpftrace "
            "supervisor tracing execve/connect/openat for the agent's PID tree "
            "(needs root + bpftrace at runtime). Off by default.",
            default=False,
        ),
        "local_runtime": _q_confirm(
            "Manage a local model server (vLLM / TGI / llama.cpp)? Writes "
            "[local_runtime] so `maverick local-runtime plan` composes the "
            "right batching / KV-cache / precision flags for your engine; "
            "configure the engine + model in config.toml after the wizard. "
            "Off by default.",
            default=False,
        ),
        "output_cache": _q_confirm(
            "Cache tool outputs? Memoize side-effect-free (read-only) tool calls "
            "within a run so a repeated read isn't re-done. Off by default.",
            default=False,
        ),
        "hardware_sensors": _q_confirm(
            "Enable host hardware sensors? Lets agents read this machine's "
            "temperatures, fans, and battery via the [sensors] extra. Off by "
            "default because it exposes host telemetry to tool calls.",
            default=False,
        ),
        "local_first": _q_confirm(
            "Local-first models? When a configured local model's server is "
            "reachable, prefer it over a remote provider (privacy + cost). Only "
            "applies when you haven't pinned a model. Off by default.",
            default=False,
        ),
        "energy_aware": _q_confirm(
            "Energy-aware routing? On a laptop, downgrade to a cheaper/faster "
            "model when the battery is low. Off by default.",
            default=False,
        ),
        "effort": _q_confirm(
            "Per-role reasoning effort? Keep the orchestrator/coder at high effort "
            "but run bulk roles (researcher/verifier/writer) at lower effort — the "
            "biggest cost/latency lever on Opus 4.7/4.8. Off by default.",
            default=False,
        ),
        "cache_prewarm": _q_confirm(
            "Pre-warm the prompt cache at start? A max_tokens=0 prefill writes the "
            "system+tools cache so the first turn doesn't pay the cold-write "
            "latency (best for interactive use). Off by default.",
            default=False,
        ),
        "hedge_requests": _q_confirm(
            "Hedge slow LLM requests? If a call hasn't returned within ~1.5s, fire "
            "a backup request and take whichever finishes first (tightens p99 on a "
            "provider with variable latency). Costs extra on slow calls. Off by "
            "default.",
            default=False,
        ),
    }
    # The governed sidecar is an extension of the external-agent boundary: its
    # clients authenticate through per-agent MCP credentials and then inherit
    # the same trust/revocation/control-plane posture. Keep the deployment
    # prompt behind that explicit higher-level opt-in so a normal wizard run is
    # unchanged and the operator sees the dependency before enabling egress.
    if advanced.get("external_agents"):
        sidecar = pick_governed_mcp_sidecar()
        if sidecar:
            advanced["mcp_sidecar"] = sidecar
    # Federated insight exchange rides on dreaming: trusted peer keys are
    # only worth asking for when the loop that produces/consumes insights is
    # on. Imports are fail-closed without them.
    if advanced.get("dreaming") and _q_confirm(
        "  Exchange consolidated insights with trusted peer instances? "
        "(signed bundles via `maverick insights-export/-import`)",
        default=False,
    ):
        raw = _q_text(
            "  Trusted peer insight pubkeys (comma-separated hex Ed25519)",
            default="",
        )
        advanced["insight_pubkeys"] = [k.strip() for k in raw.split(",") if k.strip()]
    # Autonomous self-correction only makes sense once flows are on. Human apply
    # + surfaced proposals work without it; this governs whether the loop may
    # UNDO a change on its own.
    if advanced.get("flows") and _q_confirm(
        "  Let flows self-correct? A scheduled pass reverts a node rewrite whose "
        "grounded outcomes measurably regressed -- the loop undoes its own bad "
        "changes. Off by default.",
        default=False,
    ):
        advanced["flows_auto_evolve"] = True
    # Signed channel Approve/Reject links need the dashboard's externally-
    # reachable base URL; blank keeps approvals on the dashboard itself.
    if advanced.get("flows"):
        flows_url = _q_text(
            "  Public base URL for flow approval links (e.g. https://ops.acme.com; "
            "blank = approve from the dashboard)",
            default="",
        ).strip()
        if flows_url:
            advanced["flows_public_url"] = flows_url
    if advanced.get("external_agents"):
        _ask_external_agent_followups(advanced)
    # Both governed-execution planes are off by default, so the helper guards
    # each of its own questions rather than gating the call.
    _ask_governed_execution_followups(advanced)
    # Self-harness has a handful of optional paths beyond the validation floors
    # written by default. Only worth asking once the loop itself is on; each
    # stays at its historical default unless the operator opts in here.
    if advanced.get("self_harness") and _q_confirm(
        "  Tune self-harness advanced paths? (eval corpus, per-department "
        "scoping, semantic mining, best-of-N, efficacy review, auto-retire)",
        default=False,
    ):
        corpus = _q_text(
            "  Eval-corpus path for the live A/B (lets a scheduled pass actually "
            "promote; blank = dry pass)",
            default="",
        ).strip()
        if corpus:
            advanced["self_harness_eval_corpus"] = corpus
            # A live A/B spends real provider dollars unattended, so cap it by
            # default; explicit 0 opts out.
            advanced["self_harness_eval_budget"] = max(
                0.0,
                _safe_float(
                    _q_text(
                        "    Spend cap per auto-evaluated cycle, in dollars (0 = uncapped)",
                        default="5",
                    ),
                    default=5.0,
                ),
            )
        if _q_confirm("    Mine and scope guidance per department (domain)?", default=False):
            advanced["self_harness_bucket_domain"] = True
        advanced["self_harness_semantic_mining"] = _q_confirm(
            "    Semantic mining? Cluster failures by embedding similarity when "
            "embeddings are available (else deterministic Jaccard).",
            default=False,
        )
        advanced["self_harness_efficacy_review"] = _q_confirm(
            "    Efficacy review? Re-measure each line's live A/B lift every cycle "
            "and demote dead weight (needs the eval corpus above).",
            default=False,
        )
        advanced["self_harness_canary"] = _q_confirm(
            "    Canary rollout? Stage each newly learned line on probation and "
            "graduate or pull it from real run outcomes.",
            default=False,
        )
        advanced["self_harness_auto_run"] = _q_confirm(
            "    Auto-run? Run the governed learning cycle for every role model "
            "as part of `maverick dream` (no second cron entry).",
            default=True,
        )
        advanced["self_harness_metamorphic"] = _q_confirm(
            "    Metamorphic validation? Paraphrase held-out cases and require a "
            "candidate's lift to survive the rewording (needs the live A/B).",
            default=False,
        )
        advanced["self_harness_relapse"] = _q_confirm(
            "    Relapse watch? Put a learned line back on probation when half "
            "or more of its recent run outcomes are failures.",
            default=False,
        )
        advanced["self_harness_calibrate_judge"] = _q_confirm(
            "    Judge calibration? Feed the evaluation judge's verdicts into "
            "the verifier-drift freeze so a drifting judge pauses learning.",
            default=False,
        )
        advanced["self_harness_transfer_auto"] = _q_confirm(
            "    Nightly cross-model transfer? Try each model's proven guidance "
            "on the rest of the fleet (gated, on probation, one-shot per pair).",
            default=False,
        )
        harvest_mode = _q_select(
            "    Corpus bootstrapping from run history?",
            [
                "off     - don't harvest",
                "propose - stage mined cases for your review",
                "auto    - merge mined cases straight into the corpus",
            ],
            default="off     - don't harvest",
        ).split()[0]
        if harvest_mode in ("propose", "auto"):
            advanced["self_harness_corpus_harvest"] = harvest_mode
        # Both nightly paths ride the dream beat: without auto_run (or, for
        # harvesting, a corpus) they are configured but never executed --
        # say so NOW, not silently at 3am.
        if not advanced.get("self_harness_auto_run") and (
            advanced.get("self_harness_transfer_auto") or harvest_mode in ("propose", "auto")
        ):
            console.print(
                "    [yellow]Note: nightly transfer/harvesting only run when "
                "auto-run is on -- enable it above (or run them manually via "
                "`maverick self-harness transfer` / `corpus harvest`).[/yellow]"
            )
        if harvest_mode in ("propose", "auto") and not corpus:
            console.print(
                "    [yellow]Note: corpus bootstrapping needs the eval-corpus "
                "path above; without one the harvest is a no-op.[/yellow]"
            )
        store_pick = _q_select(
            "    Learning-store backend?",
            [
                "files - per-host JSON under ~/.maverick (default)",
                "world - shared world database (a multi-host fleet learns as one)",
            ],
            default="files - per-host JSON under ~/.maverick (default)",
        ).split()[0]
        if store_pick == "world":
            advanced["self_harness_store"] = "world"
        advanced["self_harness_candidates"] = max(
            1,
            _safe_int(
                _q_text("    Candidate lines per weakness (best-of-N; 1 = single)", default="1"),
                default=1,
            ),
        )
        advanced["self_harness_retire_days"] = max(
            0,
            _safe_int(
                _q_text("    Auto-retire lines unused this many days (0 = never)", default="0"),
                default=0,
            ),
        )
    # Tax-constants content channel: law changes ship as SIGNED bundles
    # (fail-closed against the publisher keys), auto-applied by
    # `maverick tax prepare` / `maverick tax update`.
    if _q_confirm(
        "  Auto-update tax computation constants from a signed publisher "
        "channel? (new tax law as a content release, not a code release)",
        default=False,
    ):
        advanced["tax_update_url"] = _q_text("  Constants update URL", default="").strip()
        raw = _q_text(
            "  Trusted publisher pubkeys (comma-separated hex Ed25519)",
            default="",
        )
        advanced["tax_pubkeys"] = [k.strip() for k in raw.split(",") if k.strip()]
    # Regulated-deployment posture: data-residency region + a compliance
    # disclosure line. These map to the independent [residency]/[compliance]
    # scalar tables a regulated deployment needs; previously only hand-editable.
    # (Action-level [governance] guards stay config-only: they share the
    # [governance] table the finance step already owns.)
    if _q_confirm(
        "  Set regulated-deployment knobs (data residency, compliance disclosure)?",
        default=False,
    ):
        region = _q_text(
            "  Data-residency region (e.g. us / eu / us-east-1; blank = none)", default=""
        ).strip()
        if region:
            advanced["residency_region"] = region
        disclosure = _q_text(
            "  Compliance disclosure line shown to users (blank = none)", default=""
        ).strip()
        if disclosure:
            advanced["compliance_disclosure_text"] = disclosure
    # Regulatory enforcement is independent of the optional residency and
    # disclosure fields above. Always expose it so answering "no" to that
    # unrelated bundle cannot make the controls undiscoverable.
    profiles = _q_checkbox(
        "  Regulatory obligations to compile into the action gate "
        "(strictest-wins)",
        _regulatory_profile_choices(),
        default=[],
    )
    if profiles:
        advanced["compliance_profiles"] = [str(p) for p in profiles]
    # OIDC SSO is a string-bearing toggle (issuer/audience/jwks_uri), so it has
    # its own prompt; the result is nested under the "oidc" key and the writer
    # emits a single [auth.oidc] table for it.
    advanced["oidc"] = pick_oidc()
    # Department (job-function) scoping only bites once sign-in is on, so it is
    # offered right after — writes under [dashboard]. Returns {} when declined.
    advanced["department_access"] = pick_department_access(bool(advanced["oidc"].get("enabled")))
    return advanced


def _regulatory_profile_choices() -> list[str]:
    """The installed compiler's released profile registry, not a local copy."""
    from maverick.regulatory_compiler import shipped_profile_keys

    return list(shipped_profile_keys())


def pick_governed_mcp_sidecar() -> dict[str, Any]:
    """Collect a reviewed, no-secret sidecar manifest for external MCP clients.

    The prompt deliberately asks for environment-variable *names*, never an
    upstream bearer value.  Tool schemas remain conservative ``object`` roots;
    operators can add a more restrictive reviewed JSON Schema later instead of
    the wizard inventing one from a tool name.
    """
    console.print(
        "[dim]A governed MCP sidecar fronts reviewed tools from one upstream "
        "MCP server for external clients. It needs per-agent MCP credentials, "
        "the Agent Trust Plane, and a Shield installation; it does not discover "
        "or proxy arbitrary upstream tools.[/dim]"
    )
    if not _q_confirm(
        "  Configure a governed MCP sidecar for external clients now?",
        default=False,
    ):
        return {}
    upstream = _q_text(
        "  Upstream Streamable HTTP MCP URL (https; loopback http allowed for development)",
        default="",
    ).strip()
    if not (
        upstream.startswith("https://")
        or upstream.startswith("http://localhost")
        or upstream.startswith("http://127.0.0.1")
        or upstream.startswith("http://[::1]")
    ):
        console.print(
            "[yellow]No valid sidecar URL supplied; leaving the sidecar disabled."
            "[/yellow]"
        )
        return {}
    raw_tools = _q_text(
        "  Reviewed upstream tools (comma-separated name[:low|medium|high])",
        default="",
    )
    tools: dict[str, dict[str, Any]] = {}
    for entry in _split_csv(raw_tools):
        name, separator, risk = entry.partition(":")
        name = name.strip()
        risk = risk.strip().lower() if separator else "high"
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) or "__" in name:
            console.print(f"[yellow]Skipping invalid MCP tool name: {name!r}[/yellow]")
            continue
        if risk not in {"low", "medium", "high"}:
            console.print(
                f"[yellow]Skipping {name!r}: risk must be low, medium, or high.[/yellow]"
            )
            continue
        tools.setdefault(
            name,
            {
                "risk": risk,
                "description": "Reviewed governed MCP tool",
                "input_schema": {"type": "object", "properties": {}},
            },
        )
    if not tools:
        console.print(
            "[yellow]No reviewed tool bindings supplied; leaving the sidecar disabled."
            "[/yellow]"
        )
        return {}
    token_env = _q_text(
        "  Upstream bearer environment variable (blank = no static bearer)",
        default="MAVERICK_MCP_SIDECAR_UPSTREAM_TOKEN",
    ).strip()
    if token_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", token_env):
        console.print("[yellow]Invalid environment variable name; no bearer will be configured.[/yellow]")
        token_env = ""
    max_calls = max(
        0,
        _safe_int(
            _q_text("  Per-principal call budget per hour (0 = no count ceiling)", default="1000"),
            default=1000,
        ),
    )
    port = min(
        65_535,
        max(
            1,
            _safe_int(_q_text("  Sidecar listen port", default="8787"), default=8787),
        ),
    )
    out: dict[str, Any] = {
        "enable": True,
        "upstream_url": upstream,
        "require_shield": True,
        "max_calls_per_window": min(max_calls, 1_000_000),
        "budget_window_seconds": 3_600,
        "listen_host": "127.0.0.1",
        "listen_port": port,
        "tools": tools,
    }
    if token_env:
        out["upstream_token_env"] = token_env
    return out


def _split_csv(text: str) -> list[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def _collect_group_pairs(prompt: str, *, csv_value: bool) -> dict[str, Any]:
    """Collect ``Group Name = value`` lines until a blank entry. ``csv_value``
    parses the right side as a comma-separated list (departments) instead of a
    single token (role)."""
    out: dict[str, Any] = {}
    while True:
        line = _q_text(prompt, default="").strip()
        if not line or "=" not in line:
            break
        name, _, rhs = line.partition("=")
        name, rhs = name.strip(), rhs.strip()
        if not name or not rhs:
            continue
        out[name] = _split_csv(rhs) if csv_value else rhs
    return out


def pick_department_access(sso_enabled: bool) -> dict[str, Any]:
    """Opt-in department (job-function) scoping for authenticated users.

    Only meaningful once sign-in is on (it keys on the authenticated
    principal), so it is skipped entirely when SSO is off. Collects the
    deny-by-default department set and optional SCIM-group -> role / department
    mappings, written under ``[dashboard]``. Returns ``{}`` when declined, so a
    default install is unchanged.
    """
    if not sso_enabled:
        return {}
    console.print(
        "[dim]Department scoping limits which specialist teams each signed-in "
        "user can see and run — a finance analyst gets the Finance department, "
        "not Legal. OFF by default (everyone sees every department); you can "
        "always assign per-user access later on the dashboard Users page.[/dim]"
    )
    if not _q_confirm("Restrict users to specific departments by default?", default=False):
        return {}
    result: dict[str, Any] = {
        "default_suites": _split_csv(
            _q_text(
                "  Departments a new user may use until granted more (comma-separated "
                "suite keys, e.g. finance,tax; blank = none)",
                default="",
            )
        ),
    }
    console.print(
        "[dim]Optional: if your IdP pushes groups via SCIM, map a group to a "
        "role and/or departments so access follows team membership. Enter one "
        "'Group Name = value' per line; blank line to finish.[/dim]"
    )
    if _q_confirm("  Map IdP (SCIM) groups to roles/departments now?", default=False):
        roles = _collect_group_pairs(
            "    Group -> role (e.g. Finance Team=operator)", csv_value=False
        )
        suites = _collect_group_pairs(
            "    Group -> departments (e.g. Finance Team=finance,tax)", csv_value=True
        )
        if roles:
            result["group_roles"] = roles
        if suites:
            result["group_suites"] = suites
    return result


def _docker_available() -> bool:
    """Return True iff the `docker` binary is on PATH AND the daemon
    responds. Used to pick a safe sandbox default in consumer mode and
    to choose the wizard's default in dev mode."""
    if not shutil.which("docker"):
        return False
    try:
        subprocess.run(
            ["docker", "version"],
            capture_output=True,
            timeout=2,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


# Container backends pick their image from the coding language (see
# sandbox._IMAGE_BY_LANGUAGE). local/ssh run model shell on the host toolchain
# and devcontainer reuses the user's own image, so the language hint only
# changes anything for these three.
_LANGUAGE_BACKENDS = {"docker", "gvisor", "podman", "kubernetes"}


def pick_sandbox() -> dict[str, Any]:
    # Security-first default: keep Docker selected by default regardless
    # of current daemon reachability to avoid silently falling back to
    # the least isolated local backend.
    docker_default = "docker - Throwaway Docker container (recommended)"
    pick = _q_select(
        "Sandbox backend (where the agent runs shell commands):",
        [
            "local  - Subprocess on this machine (fastest, least isolated)",
            "docker - Throwaway Docker container (recommended)",
            "gvisor - Docker + gVisor runsc kernel (strongest isolation)",
            "podman - Throwaway Podman container (rootless)",
            "devcontainer - Reuse a .devcontainer config",
            "kubernetes - Pod-per-command in a cluster (kubectl)",
            "ssh    - Remote machine",
        ],
        default=docker_default,
    )
    backend = pick.split()[0]
    workdir = _q_text("  Workspace directory", default=str(Path.home() / "maverick-workspace"))
    cfg: dict[str, Any] = {"backend": backend, "workdir": workdir, "timeout": 60}
    # Non-Python coders get a toolchain image that can actually run their tests
    # (cargo/go test, the JS runner, ...). Python is the default image, so we
    # only write [sandbox] language when it's something else -- existing and
    # Python configs stay byte-identical.
    if backend in _LANGUAGE_BACKENDS:
        languages = [
            "python     - python:3.12-slim (default)",
            "javascript - node:22 (JavaScript / TypeScript)",
            "go         - golang:1",
            "rust       - rust:1",
            "java       - eclipse-temurin:21 (Java / Kotlin)",
            "ruby       - ruby:3",
        ]
        lang = _q_select(
            "  What do you mostly code in? (sets the container's toolchain)",
            languages,
            default=languages[0],
        ).split()[0]
        if lang != "python":
            cfg["language"] = lang
    return cfg


# ---------- new wizard steps (council parity pass) ----------


def pick_web_search() -> tuple[bool, list[str]]:
    """Enable web search + pick a default backend. Returns (enabled, env_vars_needed)."""
    if not _q_confirm(
        "Enable web search? (Tavily / Brave / SerpAPI / DuckDuckGo)",
        default=True,
    ):
        return False, []
    pick = _q_select(
        "  Default backend:",
        [
            "tavily   - best quality, free tier ~1k/mo, BYOK",
            "brave    - generous free tier, BYOK",
            "serpapi  - paid, covers more engines",
            "ddg      - no key, rate-limited",
        ],
        default="tavily   - best quality, free tier ~1k/mo, BYOK",
    )
    backend = pick.split()[0]
    envs = {
        "tavily": ["TAVILY_API_KEY"],
        "brave": ["BRAVE_API_KEY"],
        "serpapi": ["SERPAPI_API_KEY"],
        "ddg": [],
    }[backend]
    os.environ["MAVERICK_SEARCH_BACKEND"] = backend  # picked up by web_search tool
    return True, envs


def pick_mcp_servers() -> dict[str, dict[str, Any]]:
    """Configure MCP servers the agent will consume as tools.

    MCP servers expose their own tools (filesystem, GitHub, etc.) via a
    JSON-RPC protocol. The agent calls them as ``mcp_<name>__<tool>``.
    Skip if you don't know what MCP is.
    """
    if not _q_confirm(
        "Add MCP servers? (extensibility hook; skip if unsure)",
        default=False,
    ):
        return {}
    servers: dict[str, dict[str, Any]] = {}
    console.print(
        "[dim]Example: name 'filesystem', command 'npx', "
        "args '-y @modelcontextprotocol/server-filesystem /tmp'.[/dim]"
    )
    while True:
        name = _q_text("  Name (blank to finish)", default="").strip()
        if not name:
            break
        cmd = _q_text(f"  {name}: command", default="").strip()
        if not cmd:
            console.print("  [yellow]skipped (no command)[/yellow]")
            continue
        args_raw = _q_text(f"  {name}: args (space-separated)", default="").strip()
        args = args_raw.split() if args_raw else []
        servers[name] = {"command": cmd, "args": args}
        if not _q_confirm("  Add another?", default=False):
            break
    return servers


def pick_plugins() -> list[str]:
    """Allowlist for pip-installed plugin packages.

    Plugins are loaded only when listed in ``[plugins].enabled``. We
    scan installed entry-points and offer a checkbox; if nothing is
    installed, the step is a no-op.
    """
    discovered: set[str] = set()
    try:
        from maverick.plugins import _entry_points  # type: ignore[attr-defined]

        for group in (
            "maverick.tools",
            "maverick.channels",
            "maverick.skills",
            "maverick.personas",
        ):
            for ep in _entry_points(group):
                discovered.add(ep.name)
    except Exception as e:
        console.print(
            f"[yellow]Plugin discovery skipped: {e}[/yellow] "
            "(no plugins will be offered; re-run the wizard to retry)"
        )
        return []
    if not discovered:
        return []
    console.print()
    console.print(
        "[bold]Plugins discovered via entry_points:[/bold] " + ", ".join(sorted(discovered))
    )
    if not _q_confirm(
        "Enable any of these? (allow-listed for security; skip is safe)",
        default=False,
    ):
        return []
    return _q_checkbox("Enable plugins:", sorted(discovered))


def pick_ts_plugins() -> list[list[str]]:
    """TypeScript (NDJSON stdio) plugin commands — writes ``[plugins].ts``.

    Each entry is the argv that serves the plugin (e.g.
    ``node /path/to/plugin.js``); Lightwork discovers its tools via
    ``--describe`` at boot. Skipped by default — most setups have none.
    """
    if not _q_confirm(
        "Add any TypeScript plugins? (commands like: node /path/plugin.js)",
        default=False,
    ):
        return []
    commands: list[list[str]] = []
    while True:
        raw = _q_text("  Plugin command (blank to finish)", default="")
        if not raw.strip():
            break
        commands.append(raw.split())
    return commands


def pick_plugin_permissions() -> tuple[list[str], bool]:
    """Grants + enforcement for enabled plugins (writes ``[plugins].grant`` /
    ``enforce_permissions``).

    A plugin declares the permissions it needs (network / fs_write / subprocess)
    in its manifest. By default an ungranted request is loaded with a warning;
    granting here silences it, and enforcing *skips* a plugin that requests
    something ungranted. Returns ``(grant, enforce)``; only asked when at least
    one plugin is enabled.
    """
    grant = _q_checkbox(
        "Permissions enabled plugins may use "
        "(ungranted requests warn, or are skipped if you enforce next):",
        ["network", "fs_write", "subprocess"],
        default=[],
    )
    enforce = _q_confirm(
        "Skip plugins that request a permission you didn't grant? "
        "(recommended; off = load with a warning)",
        default=False,
    )
    return grant, enforce


def pick_tool_acl(channels: dict[str, Any]) -> dict[str, Any]:
    """Optional per-tool / per-channel allow/deny lists.

    Common pattern: a Telegram channel may chat but shouldn't run
    shell. Power users only; defaults to no restriction.
    """
    if not _q_confirm(
        "Restrict tools the agent may run? (skip for full access)",
        default=False,
    ):
        return {}
    acl: dict[str, Any] = {}
    common = ["shell", "write_file", "computer", "browser", "http_fetch", "apply_patch"]
    denied = _q_checkbox(
        "Deny these tools globally (rare; usually empty):",
        common,
        default=[],
    )
    if denied:
        acl["denied_tools"] = denied
    for ch_id in channels:
        if not _q_confirm(f"  Restrict tools available over {ch_id}?", default=False):
            continue
        ch_denied = _q_checkbox(
            f"    Deny over {ch_id}:",
            common,
            default=["shell", "computer"],
        )
        acl.setdefault("channels", {})[ch_id] = {"denied_tools": ch_denied}
    return acl


def pick_rate_limits(channels: dict[str, Any]) -> dict[str, str]:
    """Per-tool sliding-window rate caps."""
    default = bool(channels)  # default ON when exposing via channels
    if not _q_confirm(
        "Cap call rate per tool? (recommended when exposing via channels)",
        default=default,
    ):
        return {}
    limits: dict[str, str] = {}
    proposed = [
        ("web_search", "10/60"),
        ("http_fetch", "30/60"),
        ("shell", "30/60"),
        ("mcp_*", "60/60"),
    ]
    for name, spec_default in proposed:
        spec = _q_text(
            f"  {name} (N/seconds, blank to skip)",
            default=spec_default,
        ).strip()
        if spec:
            limits[name] = spec
    return limits


def pick_retention() -> dict[str, int]:
    """Auto-prune audit logs and world-model rows."""
    if not _q_confirm(
        "Auto-prune audit logs + old episodes after N days?",
        default=True,
    ):
        return {}
    return {
        "audit_days": _safe_int(_q_text("  Audit log retention days", default="90"), default=90),
        "episodes_days": _safe_int(_q_text("  Episode retention days", default="365"), default=365),
        "events_days": _safe_int(
            _q_text("  Goal-event retention days", default="180"), default=180
        ),
    }


def pick_analytics() -> dict[str, Any]:
    """Consent step for MCP-client language analytics. OFF by default.

    When granted, the MCP server tallies a coarse language bucket from each
    client's User-Agent (typescript / go / rust / c# / java / python) into a
    local counts file — no request content, no identifiers, nothing leaves
    the machine. The tally feeds the Q1-2027 language-bindings gate
    (``maverick.mcp_analytics.non_python_share()``). Returns a dict written
    under ``[analytics]``.
    """
    console.print()
    console.print(
        "[dim]Optional: count which languages drive this agent over MCP "
        "(a coarse bucket from each client's User-Agent). Counts stay in a "
        "local file — no request content, no identifiers, nothing is "
        "uploaded. The tally feeds the decision on funding native client "
        "libraries; OFF by default.[/dim]"
    )
    if not _q_confirm("Count MCP client languages locally?", default=False):
        return {}
    return {"mcp_client_language": True}


def pick_persona() -> dict[str, str]:
    """Agent identity: name + voice."""
    if not _q_confirm(
        "Customise the agent's name and style? (skip for defaults)",
        default=False,
    ):
        return {}
    name = _q_text("  Agent name", default="Lightwork").strip() or "Lightwork"
    style_pick = _q_select(
        "  Style:",
        [
            "concise   - terse, direct",
            "balanced  - default",
            "verbose   - explains its reasoning",
        ],
        default="balanced  - default",
    )
    return {"name": name, "style": style_pick.split()[0]}


def pick_notifications() -> tuple[dict[str, Any], list[str]]:
    """Run-end notification webhook. Returns (config, env_vars_needed)."""
    if not _q_confirm(
        "Get pinged when long runs finish? (ntfy / Pushover / Slack / Discord)",
        default=False,
    ):
        return {}, []
    pick = _q_select(
        "  Backend:",
        [
            "ntfy      - free, no signup, push to phone via ntfy.sh",
            "pushover  - one-time $5, phone push",
            "slack     - incoming webhook",
            "discord   - webhook URL",
        ],
        default="ntfy      - free, no signup, push to phone via ntfy.sh",
    )
    backend = pick.split()[0]
    if backend == "ntfy":
        topic = _q_text(
            "  ntfy topic (any unique string; treat as a password)",
            default="",
        ).strip()
        return ({"backend": "ntfy", "topic": topic}, []) if topic else ({}, [])
    if backend == "pushover":
        return (
            {
                "backend": "pushover",
                "user_key": "${PUSHOVER_USER_KEY}",
                "app_token": "${PUSHOVER_APP_TOKEN}",
            },
            ["PUSHOVER_USER_KEY", "PUSHOVER_APP_TOKEN"],
        )
    if backend == "slack":
        return (
            {"backend": "slack", "webhook_url": "${SLACK_NOTIFY_WEBHOOK}"},
            ["SLACK_NOTIFY_WEBHOOK"],
        )
    if backend == "discord":
        return (
            {"backend": "discord", "webhook_url": "${DISCORD_NOTIFY_WEBHOOK}"},
            ["DISCORD_NOTIFY_WEBHOOK"],
        )
    return {}, []


def pick_webhooks() -> tuple[dict[str, Any], list[str]]:
    """Outbound run-lifecycle webhooks. Returns (config, env_vars_needed).

    Distinct from pick_notifications (a single run-end ping): these are
    signed POSTs fired on every lifecycle event (goal_created,
    goal_finished, episode_finished, final_emitted) to one or more
    endpoints, for integrations (Zapier, custom receivers, dashboards).
    """
    if not _q_confirm(
        "POST run events to your own endpoint(s)? (signed lifecycle webhooks)",
        default=False,
    ):
        return {}, []
    raw = _q_text(
        "  Endpoint URL(s), comma-separated",
        default="",
    ).strip()
    urls = _csv_list(raw)
    if not urls:
        return {}, []
    cfg: dict[str, Any] = {"outbound": urls}
    envs: list[str] = []
    if _q_confirm("  Sign payloads with an HMAC secret?", default=True):
        cfg["secret"] = "${MAVERICK_WEBHOOK_SECRET}"
        envs.append("MAVERICK_WEBHOOK_SECRET")
    return cfg, envs


def pick_deliverable_handoff() -> tuple[dict[str, Any], list[str]]:
    """System-of-record hand-off for APPROVED deliverables. Returns
    (config, env_vars_needed).

    Distinct from lifecycle webhooks: this fires only when a human signs off a
    gated deliverable (a forecast, a CECL memo), POSTing it to a downstream
    system (treasury / GL / Jira) so an approved result lands there instead of
    being re-keyed by hand. Signed with the same [webhooks] HMAC secret."""
    if not _q_confirm(
        "POST approved deliverables to a system-of-record endpoint?",
        default=False,
    ):
        return {}, []
    url = _q_text("  System-of-record endpoint URL", default="").strip()
    if not url:
        return {}, []
    return {"handoff_webhook": url}, []


def pick_persona_roles() -> dict[str, Any]:
    """Bind the operator to persona consumer role(s) for the deliverables inbox.

    Sets ``[personas] default`` so the signed-in user lands on their own
    deliverables ("my forecasts") instead of the full list. Per-user mappings
    (``[personas]`` keyed by principal) are added later like RBAC roles; this is
    the single-user default."""
    if not _q_confirm(
        "Default the deliverables inbox to your own role(s)?",
        default=False,
    ):
        return {}
    raw = _q_text(
        "  Your primary role(s) (e.g. fpa_analyst, controller; space/comma separated)",
        default="",
    ).strip()
    roles = [r.strip() for r in raw.replace(",", " ").split() if r.strip()]
    return {"default": roles} if roles else {}


def pick_connectors() -> dict[str, str]:
    """Collect credentials for enterprise connectors (ServiceNow, Salesforce,
    Snowflake, SAP, ...).

    Connectors are always registered in the kernel; they only need their
    BASE_URL/TOKEN env vars set to work. Returns ``{ENV_NAME: value}`` for the
    systems the user chose to connect now, merged into ~/.maverick/.env. The
    catalog (and ``docs/connectors.md``) come from ``connector_catalog()`` in
    maverick-core, so this stays in sync as connectors are added. Secrets
    collected here are never persisted to the partial-state file.
    """
    try:
        from maverick.tools.enterprise_connectors import connector_catalog

        entries = connector_catalog()
    except Exception as e:  # maverick-core not importable / catalog moved
        console.print(f"[yellow]Connector catalog unavailable: {e}[/yellow]")
        return {}
    if not entries:
        return {}
    console.print()
    console.print(
        f"[dim]Lightwork ships {len(entries)} enterprise connectors "
        "(ServiceNow, Salesforce, Snowflake, SAP, Workday, Datadog, ...). "
        "Full list: docs/connectors.md. Connect any now, or add them later in "
        "~/.maverick/.env.[/dim]"
    )
    if not _q_confirm("Connect any enterprise systems now?", default=False):
        return {}
    by_name = {e["name"]: e for e in entries}
    raw = _q_text(
        "  Which systems? (comma-separated names, e.g. servicenow, snowflake)",
        default="",
    )
    picked = _csv_list(raw, lower=True)
    keys: dict[str, str] = {}
    for name in dict.fromkeys(picked):  # dedupe, preserve order
        entry = by_name.get(name)
        if entry is None:
            console.print(
                f"  [yellow]unknown connector '{name}' — skipped "
                "(see docs/connectors.md for valid names)[/yellow]"
            )
            continue
        console.print(f"  [bold]{entry['label']}[/bold]")
        for env_name, is_secret in entry["env"]:
            current = os.environ.get(env_name, "")
            if is_secret:
                masked = (current[:4] + "...") if current else "(none)"
                val = _q_secret(f"    {env_name} [current: {masked}] (blank = keep current)")
                if not val and current:
                    val = current
            else:
                val = _q_text(f"    {env_name}", default=current)
            if val:
                keys[env_name] = val
    return keys


def collect_api_keys(providers: list[str], channel_envs: set[str]) -> dict[str, str]:
    keys: dict[str, str] = {}
    needed: list[str] = []

    for prov in providers:
        info = catalog.PROVIDERS.get(prov, {})
        env_name = info.get("env")
        if env_name:
            needed.append(env_name)
        needed.extend(info.get("env_vars", []))

    needed.extend(sorted(channel_envs))

    if not needed:
        return keys

    console.print()
    console.print("[bold]API keys / tokens[/bold] (stored in ~/.maverick/.env, chmod 600)")
    for env_name in dict.fromkeys(needed):  # dedupe preserving order
        current = os.environ.get(env_name, "")
        masked = (current[:7] + "...") if current else "(none)"
        val = _q_secret(f"  {env_name} [current: {masked}] (leave blank to keep current)")
        if not val:
            if current:
                keys[env_name] = current
            continue

        # Validate when we know how, with a 7-day cache so re-runs of
        # the wizard don't burn an API round-trip on every key.
        validator = _VALIDATORS.get(env_name)
        if validator:
            cached = _cached_validation(env_name, val)
            if cached is not None:
                ok, msg = cached
                console.print(f"    {_validation_marker(ok, msg)} {msg} (cached)")
            else:
                ok, msg = validator(val)
                console.print(f"    {_validation_marker(ok, msg)} {msg}")
                _remember_validation(env_name, val, ok, msg)
            if not ok and not _q_confirm("Save anyway?", default=False):
                continue
        keys[env_name] = val
    return keys


# ---------- write + verify ----------


def _toml_str(v: Any) -> str:
    """Render a value as a TOML basic string with proper escaping.

    Windows paths (e.g. a sandbox workdir ``C:\\Users\\x\\ws``) contain
    backslashes; emitted raw into a ``"..."`` basic string, ``\\U`` is parsed
    as a unicode escape and the config.toml the wizard just wrote can't be read
    back (``TOMLDecodeError: Invalid hex value``). Escape backslashes and
    double-quotes so the round-trip holds on every platform.
    """
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _emit_kv(lines: list[str], k: str, v: Any) -> None:
    """Append one TOML key=value line, type-dispatched."""
    if isinstance(v, bool):
        lines.append(f"{k} = {str(v).lower()}")
    elif isinstance(v, (int, float)):
        lines.append(f"{k} = {v}")
    elif isinstance(v, list):
        rendered = ", ".join(_toml_str(x) for x in v)
        lines.append(f"{k} = [{rendered}]")
    else:
        lines.append(f"{k} = {_toml_str(v)}")


def pick_a2a() -> tuple[dict[str, Any], list[str]]:
    """Expose Lightwork to other agents over A2A. Returns (config, envs).

    Off by default: A2A is an outward-facing surface (other agents can
    discover this instance and delegate budget-spending goals to it). When
    enabled we require a bearer token (MAVERICK_A2A_TOKEN) so the task
    endpoint isn't open; the agent card + task endpoint mount on the
    dashboard at /a2a/v1.
    """
    if not _q_confirm(
        "Expose this agent over A2A so other agents can delegate goals to it?",
        default=False,
    ):
        return {}, []
    console.print(
        "  [dim]A2A serves an agent card at /.well-known/agent-card.json and a "
        "task endpoint at /a2a/v1 (on `maverick dashboard`). Budget is clamped "
        "to operator caps; a bearer token is required. A2A goals run under a "
        "tool ceiling (max_risk=medium by default -- edit [a2a].max_risk to "
        'tighten to "low" or open to "high"/"none").[/dim]'
    )
    return {"enabled": True, "max_risk": "medium"}, ["MAVERICK_A2A_TOKEN"]


# Business-function agent suites the factory can spawn from (domain packs under
# maverick/domains/). The kernel's enabled_domains() honors the [suites] table
# this writes; suites are ON by default (opt-out), so writing nothing keeps all.
AGENT_SUITES: list[tuple[str, str]] = [
    ("operations", "Operations / Supply Chain"),
    ("legal", "Legal (General Counsel)"),
    ("finance", "Finance"),
    ("it_grc", "IT / GRC / Security / Privacy / AI-Governance"),
    ("sales_gtm", "Sales / GTM"),
    ("hr", "HR / People"),
    ("product_engineering", "Product & Engineering"),
    ("strategy", "Strategy / Corp Dev / Exec"),
    ("customer_experience", "Customer Experience / Support"),
    ("marketing", "Marketing / Communications"),
    ("procurement", "Procurement / Sourcing"),
    ("data_analytics", "Data & Analytics"),
    ("security_ops", "Security Operations"),
    ("executive_office", "Executive Office / Chief of Staff"),
    ("facilities_ehs", "Facilities / EHS"),
    ("healthcare", "Healthcare (RCM / Payer Ops)"),
    ("insurance", "Insurance (Claims / Underwriting Support)"),
    ("banking", "Banking / Credit Union Ops"),
    ("retail", "Retail / E-commerce"),
    ("manufacturing_vertical", "Manufacturing (Vertical)"),
    ("construction", "Construction / AEC"),
    ("logistics", "Logistics / Transportation"),
    ("professional_services", "Professional Services"),
    ("government_contracting", "Government Contracting"),
    ("education_nonprofit", "Education / Nonprofit"),
    ("tax", "Tax Preparation (CPA Firms)"),
    # Council-expansion verticals (2026).
    ("utilities", "Energy / Utilities"),
    ("real_estate", "Real Estate / Property Management"),
    ("pharma_lifesciences", "Pharma / Life Sciences"),
    ("telecom_media", "Telecom / Media & Entertainment"),
    ("hospitality", "Hospitality / Travel"),
    ("capital_markets", "Capital Markets / Asset Management"),
    # New industry suites (2026 build-out).
    ("oil_gas", "Oil & Gas / Energy (Upstream-Downstream)"),
    ("automotive", "Automotive (OEM / Dealership / Mobility)"),
    ("public_sector", "Public Sector (State & Local Government Operations)"),
    ("agriculture", "Agriculture / Agribusiness"),
    ("aerospace_defense", "Aerospace & Defense"),
    ("maritime", "Maritime / Shipping & Ports"),
    ("travel_aviation", "Travel / Airlines & Aviation"),
    ("mining_metals", "Mining & Metals"),
    ("crypto_digital_assets", "Crypto & Digital Assets"),
    ("chemicals", "Chemicals (Bulk / Specialty / Petrochemical)"),
    ("food_beverage_cpg", "Food, Beverage & CPG"),
    ("medical_devices", "Medical Devices & Diagnostics"),
    ("private_equity_vc", "Private Equity & Venture Capital"),
    ("water_utilities", "Water & Wastewater Utilities"),
    ("renewables_cleantech", "Renewables & Clean Energy"),
    ("semiconductors", "Semiconductors & Electronics"),
    # Horizontal-function suites.
    ("esg_sustainability", "ESG & Sustainability"),
    ("enterprise_risk", "Enterprise Risk & Corporate Insurance"),
    ("knowledge_management", "Knowledge Management"),
    ("trust_safety", "Trust & Safety"),
    ("process_automation", "Process Automation & Workflows"),
]


def pick_suites() -> dict[str, bool]:
    """Which business-function agent suites to enable. All on unless customized.

    Returns a ``suite -> bool`` map for the ``[suites]`` config table (empty when
    the operator keeps the default, so the kernel enables every suite)."""
    console.print()
    console.print(
        "[bold]Agent suites[/bold] — the business functions the agent "
        "factory can spawn (finance, operations, legal, ...)."
    )
    console.print(
        "[dim]All enabled by default. A disabled suite's agents can't be "
        "spawned. Editable later in ~/.maverick/config.toml under "
        "[suites].[/dim]"
    )
    if not _q_confirm("  Customize which suites are enabled? (No = keep all on)", default=False):
        return {}
    out: dict[str, bool] = {}
    for key, label in AGENT_SUITES:
        out[key] = _q_confirm(f"    Enable the {label} suite?", default=True)
    return out


def pick_license() -> dict[str, Any]:
    """The ``[license]`` section — paid-feature entitlement enforcement.

    Default **OFF** (a community/dev box runs everything, matching the
    fail-open kernel). A licensed production deployment turns enforcement on and
    points at its signed license file; paid features then gate on the license.
    Returns a config map for ``[license]`` (empty when the operator keeps the
    fail-open default, so nothing is emitted and nothing gates)."""
    console.print()
    console.print(
        "[bold]Licensing[/bold] — enforce paid-feature entitlements "
        "(Gold/Platinum features, add-on suites like fleet)."
    )
    console.print(
        "[dim]Fail-open by default: the kernel runs with no license. "
        "Turn enforcement on only for a licensed production deployment. "
        "A missing/expired license never stops the core — only paid "
        "add-ons gate off. Editable later under [license] in "
        "~/.maverick/config.toml.[/dim]"
    )
    if not _q_confirm("  Enforce paid-feature entitlements on this deployment?", default=False):
        return {}
    out: dict[str, Any] = {"enforce": True}
    path = _q_text("    Signed license file path (blank = ~/.maverick/license.json)", default="")
    if path.strip():
        out["license_file"] = path.strip()
    pub = _q_text(
        "    Trusted publisher pubkey(s), comma-separated hex (blank = keys embedded in the build)",
        default="",
    )
    keys = _csv_list(pub, lower=True)
    if keys:
        out["publisher_pubkeys"] = keys
    api = _q_text(
        "    Entitlement API URL for connected refresh (blank = offline license file only)",
        default="",
    )
    if api.strip():
        out["api_url"] = api.strip()
        # The API token is a secret: reference an env var, never inline it
        # (config.toml interpolates ${VAR}; the value lives in ~/.maverick/.env).
        out["api_token"] = "${MAVERICK_LICENSE_API_TOKEN}"
        # Near-instant propagation: the dashboard polls the API on this
        # interval so an upgrade issued in the vendor console lights up
        # without a redeploy. 0 disables; the kernel floors it at 30s.
        interval = _q_text(
            "    Auto-refresh interval in seconds (blank = 60, 0 = manual refresh only)", default=""
        )
        if interval.strip():
            try:
                out["refresh_interval_seconds"] = int(interval.strip())
            except ValueError:
                pass  # keep the 60s default rather than fail the wizard
    return out


def _cfg_deployment(deployment: str | None) -> list[str]:
    if not deployment:
        return []
    # Record the chosen deployment topology (laptop / vps / ...) for
    # provenance + so a later `maverick init` can default to it.
    return [
        "[deployment]",
        f"type = {_toml_str(str(deployment))}",
        "",
    ]


def _cfg_governance(
    profile: str | None,
    finance: dict[str, Any] | None = None,
) -> list[str]:
    values: list[str] = []
    # Record the governance level chosen at onboarding. This is a label for
    # humans + dashboards: the enforceable knobs it expanded to ([audit],
    # [quotas], [self_improvement], retention, ...) are written explicitly
    # elsewhere in this file, so hand-edits keep working. Re-run
    # `maverick init` to switch levels.
    if profile:
        values.append(
            f"profile = {_toml_str(str(profile))}   "
            "# essentials | standard | regulated | custom"
        )
    if finance:
        # Finance governance scalars share this table with the onboarding
        # profile. Keeping a single owner prevents a profile + finance install
        # from declaring [governance] twice and invalidating the whole config.
        values.append('require_human_min_risk = "high"')
        if finance.get("require_fresh_human_approval"):
            # A prior persistent consent grant won't satisfy the Art-14 gate --
            # each paused action needs a fresh human decision.
            values.append("require_fresh_human_approval = true")
    if not values:
        return []
    return ["[governance]", *values, ""]


def _cfg_providers(providers: list[str]) -> list[str]:
    lines: list[str] = []
    for prov in providers:
        info = catalog.PROVIDERS.get(prov, {})
        lines.append(f"[providers.{prov}]")
        env_name = info.get("env")
        if env_name:
            lines.append(f'api_key = "${{{env_name}}}"')
        if prov == "ollama":
            lines.append('base_url = "http://localhost:11434"')
        if prov == "openai_compatible":
            lines.append('base_url = "${OPENAI_COMPATIBLE_BASE_URL}"')
        lines.append("")
    return lines


def _cfg_role_models(role_models: dict[str, str]) -> list[str]:
    if not role_models:
        return []
    lines = ["[models]"]
    for role, spec in role_models.items():
        lines.append(f'{role} = "{spec}"')
    lines.append("")
    return lines


def _cfg_channels(channels: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for ch_id, cfg in channels.items():
        lines.append(f"[channels.{ch_id}]")
        for k, v in cfg.items():
            # _emit_kv handles lists (e.g. the allowed_user_ids array) and
            # escapes string values; the old inline branch emitted a list as
            # a quoted string and didn't escape backslash paths.
            _emit_kv(lines, k, v)
        lines.append("")
    return lines


def _cfg_core(
    budget: dict[str, Any],
    safety: dict[str, Any],
    sandbox: dict[str, Any],
) -> list[str]:
    lines = ["[budget]"]
    for k, v in budget.items():
        _emit_kv(lines, k, v)
    lines.append("")
    lines.append("[safety]")
    for k, v in safety.items():
        _emit_kv(lines, k, v)
    lines.append("")
    lines.append("[sandbox]")
    for k, v in sandbox.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_skills(skills: dict[str, Any] | None) -> list[str]:
    if not skills:
        return []
    # Signed-skill policy. trusted_pubkeys = hex Ed25519 publisher keys
    # a signed SKILL.md must match; require_signed rejects unsigned ones.
    lines = ["", "[skills]"]
    for k, v in skills.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_self_learning(self_learning: dict[str, Any] | None) -> list[str]:
    if not self_learning:
        return []
    # Self-learning. enable gates the whole feature; sub-toggles let the
    # agent install skills, add MCP servers, and generate+run new tools.
    lines = ["", "[self_learning]"]
    for k, v in self_learning.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_ekko(ekko: dict[str, Any] | None) -> list[str]:
    if not ekko:
        return []
    # Policy only. The installer never starts/enrolls a workstation collector.
    lines = ["", "[ekko]"]
    for k, v in ekko.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_automation_import(automation_import: dict[str, Any] | None) -> list[str]:
    if not automation_import:
        return []
    # Automation import. enable gates the whole feature; create_schedules lets a
    # recovered cron trigger auto-create a Lightwork schedule on import.
    lines = ["", "[automation_import]"]
    for k, v in automation_import.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_event_triggers(event_triggers: dict[str, Any] | None) -> list[str]:
    if not event_triggers:
        return []
    # Event triggers. enable gates the whole feature; the app polls bound
    # sources on a background tick and fires the workflow per new item.
    lines = ["", "[event_triggers]"]
    for k, v in event_triggers.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_flows(flows: dict[str, Any] | None) -> list[str]:
    if not flows:
        return []
    # Flow engine. enable gates the visual multi-step workflow designer/runner;
    # public_url (optional) is the base URL for signed channel-approval links.
    lines = ["", "[flows]"]
    for k, v in flows.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_knowledge(knowledge: dict[str, Any] | None) -> list[str]:
    if not knowledge:
        return []
    # Per-domain vector RAG (maverick-knowledge). enable gates the whole
    # feature; embedder/store/dsn are read by config.get_knowledge.
    lines = ["", "[knowledge]"]
    for k, v in knowledge.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_oauth(oauth: dict[str, Any] | None) -> list[str]:
    if not (oauth and oauth.get("vault")):
        return []
    # Seal captured OAuth tokens in the per-tenant vault (encrypted at rest).
    return ["", "[oauth]", "vault = true"]


def _cfg_governed_connectors(governed_connectors: dict[str, Any] | None) -> list[str]:
    if not (governed_connectors and governed_connectors.get("enable")):
        return []
    # Governed system-of-record connectors. enable turns on the governed write
    # path; connectors selects which reference REST connectors to register.
    lines = ["", "[governed_connectors]"]
    for k, v in governed_connectors.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_durable(durable: dict[str, Any] | None) -> list[str]:
    if not (durable and durable.get("enabled")):
        return []
    # Durable execution: checkpoint loop state so `maverick resume`
    # continues from the last step after a crash. Off unless opted in.
    lines = ["", "[durable]"]
    for k, v in durable.items():
        _emit_kv(lines, k, v)
    return lines


def _append_nested_product_config(
    lines: list[str],
    section: str,
    values: dict[str, Any],
) -> None:
    def append_table(path: str, table: dict[str, Any]) -> None:
        lines.extend(("", f"[{path}]"))
        for key, value in table.items():
            if not isinstance(value, dict):
                _emit_kv(lines, key, value)
        for subsystem, nested in table.items():
            if isinstance(nested, dict):
                append_table(f"{path}.{subsystem}", nested)

    append_table(section, values)


def _cfg_workspace(workspace: dict[str, Any] | None) -> list[str]:
    if not (workspace and workspace.get("journal")):
        return []
    # Workspace mutation journal: pre-image every file-tool write so
    # `maverick journal revert` can restore a goal's workspace. Off unless
    # opted in; journal_strict refuses writes it cannot journal.
    lines = ["", "[workspace]"]
    for k, v in workspace.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_assessments(assessments: dict[str, Any] | None) -> list[str]:
    if not assessments:
        return []
    # Assessment assists + privacy ops are default-on; write tables only when
    # the operator opted OUT of something (write-only-on-divergence).
    body = {
        k: v
        for k, v in assessments.items()
        if k
        not in (
            "privacy_ops",
            "privacy_automation",
            "consent_preferences",
            "data_use_governance",
            "paper_review_use_model",
            "paper_review_org",
            "entity_graph",
        )
    }
    lines: list[str] = []
    if body:
        lines += ["", "[assessments]"]
        for k, v in body.items():
            _emit_kv(lines, k, v)
    if "privacy_ops" in assessments:
        lines += ["", "[privacy_ops]"]
        _emit_kv(lines, "enable", assessments["privacy_ops"])
    consent = assessments.get("consent_preferences")
    if consent is True:
        lines += ["", "[consent_preferences]"]
        _emit_kv(lines, "enable", True)
    elif isinstance(consent, dict):
        _append_nested_product_config(lines, "consent_preferences", consent)
    data_use = assessments.get("data_use_governance")
    if isinstance(data_use, dict):
        _append_nested_product_config(lines, "data_use_governance", data_use)
    privacy_automation = assessments.get("privacy_automation")
    if isinstance(privacy_automation, dict):
        _append_nested_product_config(lines, "privacy_automation", privacy_automation)
    if "paper_review_use_model" in assessments or "paper_review_org" in assessments:
        lines += ["", "[paper_review]"]
        if "paper_review_use_model" in assessments:
            _emit_kv(lines, "use_model", assessments["paper_review_use_model"])
        if "paper_review_org" in assessments:
            _emit_kv(lines, "org_name", assessments["paper_review_org"])
    if "entity_graph" in assessments:
        lines += ["", "[entity_graph]"]
        _emit_kv(lines, "enable", assessments["entity_graph"])
    return lines


def _cfg_security_suite(security_suite: dict[str, Any] | None) -> list[str]:
    if security_suite is None:
        return []
    allowed_inventory_connectors = {
        "agentforce",
        "azure_ai_foundry",
        "aws_sagemaker",
        "bedrock",
        "copilot_studio",
        "databricks_unity_catalog",
        "hugging_face",
        "huggingface",
        "mlflow",
        "openai",
        "replicate",
        "salesforce_agentforce",
        "sagemaker",
        "snowflake",
        "snowflake_cortex",
        "vertex_ai",
    }
    inventory_connectors = list(
        dict.fromkeys(
            str(value).strip().lower()
            for value in (security_suite.get("inventory_connectors") or [])
            if str(value).strip().lower() in allowed_inventory_connectors
        )
    )
    ai_entity_tasks_enabled = bool(security_suite.get("ai_entity_tasks", False))
    governance_agents_enabled = bool(security_suite.get("governance_agents", False))
    evidence_analysis_enabled = bool(security_suite.get("ai_evidence_analysis", False))
    evidence_ocr_enabled = bool(
        evidence_analysis_enabled and security_suite.get("ai_evidence_ocr", False)
    )
    evidence_legacy_xls_enabled = bool(
        evidence_analysis_enabled and security_suite.get("ai_evidence_legacy_xls", False)
    )
    evidence_parser_sandbox_enabled = bool(
        (evidence_ocr_enabled or evidence_legacy_xls_enabled)
        and security_suite.get("ai_evidence_parser_sandbox", False)
    )
    evidence_media_types = ["pdf", "jpeg", "png", "xlsx", "text"]
    if evidence_legacy_xls_enabled:
        evidence_media_types.insert(3, "xls")
    rendered_evidence_media_types = ", ".join(
        _toml_str(value) for value in evidence_media_types
    )
    assessment_evidence_enabled = bool(
        security_suite.get("assessment_evidence_autopopulation", False)
        and evidence_analysis_enabled
    )
    assessment_evidence_apply_enabled = bool(
        assessment_evidence_enabled and security_suite.get("assessment_evidence_apply", False)
    )
    cloud_agent_governance_enabled = bool(security_suite.get("ai_cloud_agent_governance", False))
    databricks_framework_enabled = bool(
        cloud_agent_governance_enabled
        and security_suite.get("databricks_ai_security_framework", False)
    )
    if cloud_agent_governance_enabled:
        inventory_connectors = list(
            dict.fromkeys([*inventory_connectors, "bedrock", "azure_ai_foundry", "vertex_ai"])
        )
    rendered_inventory_connectors = ", ".join(_toml_str(value) for value in inventory_connectors)
    vendor_portal_enabled = bool(
        security_suite.get("ai_vendor_risk", False)
        and security_suite.get("ai_vendor_risk_portal", False)
    )
    c2patool_executable_sha256 = (
        str(security_suite.get("ai_media_provenance_c2patool_sha256", "")).strip().lower()
    )
    c2patool_digest_valid = len(c2patool_executable_sha256) == 64 and all(
        character in "0123456789abcdef" for character in c2patool_executable_sha256
    )
    c2patool_verification_enabled = bool(
        security_suite.get("ai_media_provenance", False)
        and security_suite.get("ai_media_provenance_c2patool", False)
        and c2patool_digest_valid
    )
    identity_scheduler_enabled = bool(
        security_suite.get("ai_identity_governance_connectors", False)
        and security_suite.get("ai_identity_governance_scheduler", False)
    )
    identity_controls_enabled = bool(
        security_suite.get("ai_identity_governance_connectors", False)
        and security_suite.get("ai_identity_governance_controls", False)
    )
    identity_federation_enabled = bool(
        identity_controls_enabled and security_suite.get("ai_identity_federation", False)
    )
    identity_production_enabled = bool(
        identity_controls_enabled
        and security_suite.get("ai_identity_production_composition", False)
    )
    lines = [
        "",
        "[customer_sandbox_lifecycle]",
        "enable = "
        f"{'true' if security_suite.get('customer_sandbox_lifecycle', False) else 'false'}",
        "source_revision = 1",
        "",
        "[security_ops]",
        f"enable = {'true' if security_suite.get('security_ops', True) else 'false'}",
        "",
        "[administration_document_access]",
        "enable = "
        f"{'true' if security_suite.get('administration_document_access', False) else 'false'}",
        "default_ttl_seconds = 300",
        "max_ttl_seconds = 900",
        "max_uses = 1",
        "delivery_grant_ttl_seconds = 60",
        "retention_seconds = 31536000",
        "",
        "[governed_document_vault]",
        f"enable = {'true' if security_suite.get('governed_document_vault', False) else 'false'}",
        "max_document_bytes = 26214400",
        "max_documents = 100000",
        "retention_seconds = 31536000",
        "grant_ttl_seconds = 60",
        "claim_lease_seconds = 30",
        "stream_chunk_bytes = 65536",
        'allowed_media_types = ["application/json", "application/pdf", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "image/jpeg", "image/png", "image/webp", "text/csv", "text/plain"]',
        "",
        "[incident_attachment_metadata]",
        "enable = "
        f"{'true' if security_suite.get('incident_attachment_metadata', False) else 'false'}",
        "retention_seconds = 31536000",
        "attachment_ttl_seconds = 604800",
        "max_attachments = 100000",
        'allowed_media_types = ["application/json", "application/pdf", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "image/jpeg", "image/png", "image/webp", "text/csv", "text/plain"]',
        "",
        "[governed_records]",
        'backend = "auto"',
        "",
        "[evidence_graph]",
        f"enable = {'true' if security_suite.get('evidence_graph', False) else 'false'}",
        "",
        "[model_risk_assurance]",
        f"enable = {'true' if security_suite.get('model_risk_assurance', False) else 'false'}",
        "gate_promotions = "
        f"{'true' if security_suite.get('model_risk_assurance', False) else 'false'}",
        "",
        "[evidence_gateway]",
        "enable = "
        f"{'true' if security_suite.get('evidence_gateway', False) or governance_agents_enabled or evidence_analysis_enabled else 'false'}",
        "",
        "[ai_inventory]",
        "enable = "
        f"{'true' if security_suite.get('ai_inventory', False) or ai_entity_tasks_enabled or cloud_agent_governance_enabled else 'false'}",
        f"connectors = [{rendered_inventory_connectors}]",
        "page_cap = 100",
        "record_cap = 5000",
        "retry_attempts = 3",
        "timeout_seconds = 30",
        "missing_grace_runs = 2",
        "poll_seconds = 900",
        "",
        "[ai_entity_tasks]",
        f"enable = {'true' if ai_entity_tasks_enabled else 'false'}",
        "max_tasks = 100000",
        "retention_seconds = 31536000",
        "authority_max_age_seconds = 604800",
        "",
        "[ai_vendor_risk]",
        f"enable = {'true' if security_suite.get('ai_vendor_risk', False) else 'false'}",
        f"portal_enable = {'true' if vendor_portal_enabled else 'false'}",
        "",
        "[portfolio_risk]",
        f"enable = {'true' if security_suite.get('portfolio_risk', False) else 'false'}",
        "",
        "[policy_packs]",
        "enable = "
        f"{'true' if security_suite.get('policy_packs', False) or databricks_framework_enabled else 'false'}",
        "",
        "[governance_agents]",
        f"enable = {'true' if governance_agents_enabled else 'false'}",
        'enabled_agents = ["breach_response", "privacy_risk_assessment", "program_copilot", "knowledgebase_governance", "third_party_risk"]',
        f"context_projection_enable = {'true' if governance_agents_enabled else 'false'}",
        "context_projection_max_records_per_source = 100",
        "context_projection_max_context_items = 32",
        "context_projection_max_total_bytes = 2097152",
        "context_projection_max_record_bytes = 16384",
        "max_context_items = 100",
        "max_context_bytes = 1048576",
        "max_output_bytes = 262144",
        "retention_seconds = 31536000",
        "",
        "[ai_evidence_analysis]",
        f"enable = {'true' if evidence_analysis_enabled else 'false'}",
        f"enabled_media_types = [{rendered_evidence_media_types}]",
        "max_artifact_bytes = 52428800",
        "max_artifacts_per_case = 20",
        "max_authority_records = 100000",
        "max_extracted_text_bytes = 1048576",
        "retention_seconds = 31536000",
        "",
        "[ai_evidence_analysis.parser_sandbox]",
        f"enable = {'true' if evidence_parser_sandbox_enabled else 'false'}",
        'mode = "bubblewrap"',
        "launcher = "
        f"{_toml_str(str(security_suite.get('ai_evidence_parser_sandbox_launcher', '')))}",
        "launcher_sha256 = "
        f"{_toml_str(str(security_suite.get('ai_evidence_parser_sandbox_sha256', '')).lower())}",
        "launcher_version = "
        f"{_toml_str(str(security_suite.get('ai_evidence_parser_sandbox_version', '')))}",
        "",
        "[ai_evidence_analysis.ocr]",
        f"enable = {'true' if evidence_ocr_enabled else 'false'}",
        "executable = "
        f"{_toml_str(str(security_suite.get('ai_evidence_ocr_executable', '')))}",
        "executable_sha256 = "
        f"{_toml_str(str(security_suite.get('ai_evidence_ocr_sha256', '')).lower())}",
        "tesseract_version = "
        f"{_toml_str(str(security_suite.get('ai_evidence_ocr_tesseract_version', '')))}",
        "traineddata_path = "
        f"{_toml_str(str(security_suite.get('ai_evidence_ocr_traineddata_path', '')))}",
        "traineddata_sha256 = "
        f"{_toml_str(str(security_suite.get('ai_evidence_ocr_traineddata_sha256', '')).lower())}",
        "runtime_library_paths = ["
        + ", ".join(
            _toml_str(str(value))
            for value in security_suite.get("ai_evidence_ocr_runtime_library_paths", [])
        )
        + "]",
        "runtime_libraries_sha256 = "
        f"{_toml_str(str(security_suite.get('ai_evidence_ocr_runtime_libraries_sha256', '')).lower())}",
        "pillow_version = "
        f"{_toml_str(str(security_suite.get('ai_evidence_ocr_pillow_version', '')))}",
        "pillow_manifest_sha256 = "
        f"{_toml_str(str(security_suite.get('ai_evidence_ocr_pillow_manifest_sha256', '')).lower())}",
        "timeout_seconds = 30",
        "max_pixels = 40000000",
        "max_output_bytes = 1048576",
        "max_memory_bytes = 536870912",
        "",
        "[ai_evidence_analysis.legacy_xls]",
        f"enable = {'true' if evidence_legacy_xls_enabled else 'false'}",
        'xlrd_version = "2.0.2"',
        "xlrd_manifest_sha256 = "
        f"{_toml_str(str(security_suite.get('ai_evidence_xlrd_manifest_sha256', '')).lower())}",
        'olefile_version = "0.47"',
        "olefile_manifest_sha256 = "
        f"{_toml_str(str(security_suite.get('ai_evidence_olefile_manifest_sha256', '')).lower())}",
        "timeout_seconds = 15",
        "max_workbook_stream_bytes = 33554432",
        "max_record_bytes = 8388608",
        "max_records = 500000",
        "max_sheets = 200",
        "max_rows_per_sheet = 200000",
        "max_columns_per_sheet = 256",
        "max_cells = 200000",
        "max_output_bytes = 1048576",
        "max_memory_bytes = 536870912",
        "",
        "[assessment_evidence_autopopulation]",
        f"enable = {'true' if assessment_evidence_enabled else 'false'}",
        f"apply_enabled = {'true' if assessment_evidence_apply_enabled else 'false'}",
        "retention_seconds = 31536000",
        "max_records = 10000",
        "max_evidence_records_per_proposal = 32",
        "max_citations_per_answer = 32",
        "min_confidence_bps = 7000",
        "",
        "[ai_regulatory_feed]",
        f"enable = {'true' if security_suite.get('ai_regulatory_feed', False) else 'false'}",
        "",
        "[red_team_assurance]",
        f"enable = {'true' if security_suite.get('red_team_assurance', False) else 'false'}",
        'commitment_secret_ref = "MAVERICK_RED_TEAM_COMMITMENT_KEY"',
        "max_cases = 12",
        "max_response_bytes = 65536",
        "campaign_ttl_seconds = 604800",
        "evidence_validity_seconds = 7776000",
        "scheduler_poll_seconds = 30",
        "",
        "[eu_ai_incidents]",
        f"enable = {'true' if security_suite.get('eu_ai_incidents', False) else 'false'}",
        "authorities = []",
        "",
        "[grc_outbound_sync]",
        f"enable = {'true' if security_suite.get('grc_outbound_sync', False) else 'false'}",
        'targets = ["servicenow_irm", "archer"]',
        "max_attempts_per_revision = 8",
        "",
        "[grc_outbound_sync.servicenow_irm]",
        'credential_secret_ref = "SERVICENOW_TOKEN"',
        "timeout_seconds = 15",
        "max_response_bytes = 262144",
        "",
        "[grc_outbound_sync.archer]",
        'credential_secret_ref = "ARCHER_GRC_TOKEN"',
        "timeout_seconds = 15",
        "max_response_bytes = 262144",
        "",
        "[ai_literacy]",
        f"enable = {'true' if security_suite.get('ai_literacy', False) else 'false'}",
        "",
        "[ai_business_intake]",
        f"enable = {'true' if security_suite.get('ai_business_intake', False) else 'false'}",
        "",
        "[ai_cost_roi]",
        f"enable = {'true' if security_suite.get('ai_cost_roi', False) else 'false'}",
        "collector_poll_seconds = 86400",
        "collector_lookback_days = 1",
        "",
        "[ai_cost_roi.collector_limits]",
        "timeout_seconds = 20",
        "page_cap = 20",
        "record_cap = 1000",
        "max_period_days = 93",
        "page_size = 200",
        "max_bytes_billed = 1000000000",
        "",
        "[ai_cost_roi.aws]",
        "enable = false",
        "",
        "[ai_cost_roi.azure]",
        "enable = false",
        "",
        "[ai_cost_roi.gcp]",
        "enable = false",
        "",
        "[ai_cost_roi.openai]",
        "enable = false",
        "",
        "[ai_dynamic_risk]",
        f"enable = {'true' if security_suite.get('ai_dynamic_risk', False) else 'false'}",
        "",
        "[ai_policy_management]",
        "enable = "
        f"{'true' if security_suite.get('ai_policy_management', False) or cloud_agent_governance_enabled else 'false'}",
        "",
        "[ai_runtime_guardrails]",
        "enable = "
        f"{'true' if security_suite.get('ai_runtime_guardrails', False) or cloud_agent_governance_enabled else 'false'}",
        "",
        "[ai_cloud_agent_governance]",
        f"enable = {'true' if cloud_agent_governance_enabled else 'false'}",
        'enabled_providers = ["aws_bedrock", "azure_ai_foundry", "google_vertex_ai"]',
        f"databricks_framework_enable = {'true' if databricks_framework_enabled else 'false'}",
        "max_records_per_sync = 10000",
        "max_events_per_batch = 5000",
        "retention_seconds = 31536000",
        "",
        "[ai_governance_loop]",
        f"enable = {'true' if security_suite.get('ai_governance_loop', False) else 'false'}",
        "poll_seconds = 300",
        "batch_size = 100",
        "lease_seconds = 120",
        "max_attempts = 5",
        "retry_base_seconds = 30",
        "max_lag_seconds = 900",
        "",
        "[ai_explainability]",
        f"enable = {'true' if security_suite.get('ai_explainability', False) else 'false'}",
        "",
        "[ai_conformity_workflow]",
        f"enable = {'true' if security_suite.get('ai_conformity_workflow', False) else 'false'}",
        "",
        "[ai_insurability]",
        f"enable = {'true' if security_suite.get('ai_insurability', False) else 'false'}",
        "",
        "[ai_regulated_verticals]",
        f"enable = {'true' if security_suite.get('ai_regulated_verticals', False) else 'false'}",
        "",
        "[ai_bank_mrm]",
        f"enable = {'true' if security_suite.get('ai_bank_mrm', False) else 'false'}",
        "",
        "[ai_benchmark_exchange]",
        f"enable = {'true' if security_suite.get('ai_benchmark_exchange', False) else 'false'}",
        "",
        "[ai_discovery_sensors]",
        f"enable = {'true' if security_suite.get('ai_discovery_sensors', False) else 'false'}",
        "",
        "[ai_media_provenance]",
        f"enable = {'true' if security_suite.get('ai_media_provenance', False) else 'false'}",
        *(
            [
                'verification_adapter = "c2patool"',
                f"c2patool_executable_sha256 = {_toml_str(c2patool_executable_sha256)}",
            ]
            if c2patool_verification_enabled
            else []
        ),
        "",
        "[ai_identity_governance_connectors]",
        "enable = "
        f"{'true' if security_suite.get('ai_identity_governance_connectors', False) else 'false'}",
        "",
        "[ai_identity_governance_connectors.entra]",
        "enable = false",
        'tenant_id = ""',
        'base_url = "https://graph.microsoft.com"',
        'credential_secret_ref = "ENTRA_IDENTITY_GOVERNANCE_TOKEN"',
        "",
        "[ai_identity_governance_connectors.okta]",
        "enable = false",
        'tenant_id = ""',
        'base_url = "https://replace-me.okta.com"',
        'credential_secret_ref = "OKTA_IDENTITY_GOVERNANCE_TOKEN"',
        "",
        "[ai_identity_governance_connectors.purview]",
        "enable = false",
        'tenant_id = ""',
        'base_url = "https://replace-me.purview.azure.com"',
        'credential_secret_ref = "PURVIEW_IDENTITY_GOVERNANCE_TOKEN"',
        "",
        "[ai_identity_governance_connectors.scheduler]",
        f"enable = {'true' if identity_scheduler_enabled else 'false'}",
        "poll_seconds = 60",
        "",
        "[ai_identity_governance_connectors.scheduler.providers.entra]",
        "enable = false",
        "interval_seconds = 3600",
        "retry_base_seconds = 30",
        "retry_max_seconds = 900",
        "max_attempts_per_slot = 3",
        "lease_seconds = 3600",
        "history_limit = 8",
        "max_snapshot_items = 5000",
        "max_delta_digests = 256",
        "",
        "[ai_identity_governance_connectors.scheduler.providers.okta]",
        "enable = false",
        "interval_seconds = 3600",
        "retry_base_seconds = 30",
        "retry_max_seconds = 900",
        "max_attempts_per_slot = 3",
        "lease_seconds = 3600",
        "history_limit = 8",
        "max_snapshot_items = 5000",
        "max_delta_digests = 256",
        "",
        "[ai_identity_governance_connectors.scheduler.providers.purview]",
        "enable = false",
        "interval_seconds = 3600",
        "retry_base_seconds = 30",
        "retry_max_seconds = 900",
        "max_attempts_per_slot = 3",
        "lease_seconds = 3600",
        "history_limit = 8",
        "max_snapshot_items = 5000",
        "max_delta_digests = 256",
        "",
        "[ai_identity_governance_connectors.controls]",
        f"enable = {'true' if identity_controls_enabled else 'false'}",
        "",
        "[ai_identity_governance_connectors.controls.change_coupling]",
        "enable = "
        f"{'true' if identity_controls_enabled and security_suite.get('ai_identity_change_coupling', False) else 'false'}",
        "dynamic_risk = "
        f"{'true' if identity_controls_enabled and security_suite.get('ai_identity_change_coupling', False) else 'false'}",
        "alerts = "
        f"{'true' if identity_controls_enabled and security_suite.get('ai_identity_change_coupling', False) else 'false'}",
        "",
        "[ai_identity_governance_connectors.controls.federation]",
        f"enable = {'true' if identity_federation_enabled else 'false'}",
        "",
        "[ai_identity_governance_connectors.controls.conditional_access]",
        "enable = "
        f"{'true' if identity_federation_enabled and security_suite.get('ai_identity_conditional_access', False) else 'false'}",
        "",
        "[ai_identity_governance_connectors.controls.purview_writeback]",
        "enable = "
        f"{'true' if identity_federation_enabled and security_suite.get('ai_identity_purview_writeback', False) else 'false'}",
        "",
        "[ai_identity_governance_connectors.controls.production]",
        f"enable = {'true' if identity_production_enabled else 'false'}",
        "mapping_revision = 1",
        "mapping_retention_seconds = 86400",
        "max_authority_records = 10000",
        "max_observation_age_seconds = 300",
        "max_future_skew_seconds = 30",
        "outcome_max_attempts = 5",
        "outcome_retry_base_seconds = 30",
        "outcome_retry_max_seconds = 3600",
        "outcome_lease_seconds = 60",
        "outcome_signal_validity_seconds = 86400",
        "purview_max_attempts = 3",
        "purview_operation_lease_seconds = 60",
        "",
        "[model_improvement]",
        f"enable = {'true' if security_suite.get('model_improvement', False) else 'false'}",
        "allow_hosted = "
        f"{'true' if security_suite.get('allow_hosted_training', False) else 'false'}",
        "allow_cross_tenant = false",
        "require_signed_receipt = true",
        "minimum_train_families = 20",
        "minimum_holdout_families = 20",
        "",
        "[threat_hunt]",
        f"enable = {'true' if security_suite.get('threat_hunt', False) else 'false'}",
        "",
        "[env_hunt]",
        f"enable = {'true' if security_suite.get('env_hunt', False) else 'false'}",
        "response_execution = "
        f"{'true' if security_suite.get('response_execution', False) else 'false'}",
        f"poll_seconds = {max(30, min(3600, int(security_suite.get('poll_seconds', 300))))}",
    ]
    enrichment_sources = security_suite.get("enrichment_sources") or []
    enrichment_sources = list(
        dict.fromkeys(
            str(value).strip().lower() for value in enrichment_sources if str(value).strip()
        )
    )
    for name in enrichment_sources:
        lines += [
            "",
            f"[env_hunt.enrichment_sources.{name}]",
            "enable = true",
        ]
    try:
        from maverick.env_hunt import CONNECTOR_NAMES
    except ImportError:  # installer can render before maverick-core is installed
        CONNECTOR_NAMES = (
            "cloudtrail",
            "guardduty",
            "syslog",
            "edr",
            "splunk",
            "elastic",
            "sentinel",
            "kubernetes_audit",
            "okta",
            "entra",
        )
    configured = security_suite.get("connectors") or {}
    configured = configured if isinstance(configured, dict) else {}
    for name in CONNECTOR_NAMES:
        value = configured.get(name, False)
        enabled = value.get("enable", False) if isinstance(value, dict) else value
        push_enabled = value.get("push_enable", False) if isinstance(value, dict) else False
        pivot_enabled = value.get("pivot_enable", False) if isinstance(value, dict) else False
        lines += [
            "",
            f"[env_hunt.connectors.{name}]",
            f"enable = {'true' if enabled is True else 'false'}",
            f"push_enable = {'true' if push_enabled is True else 'false'}",
            f"pivot_enable = {'true' if pivot_enabled is True else 'false'}",
        ]
    tech_risk_compliance = security_suite.get("tech_risk_compliance")
    if isinstance(tech_risk_compliance, dict):
        _append_nested_product_config(lines, "tech_risk_compliance", tech_risk_compliance)
    third_party_management = security_suite.get("third_party_management")
    if isinstance(third_party_management, dict):
        _append_nested_product_config(lines, "third_party_management", third_party_management)
    return lines


def _cfg_value(value: dict[str, Any] | None) -> list[str]:
    if not value:
        return []
    # The client's cost/value assumptions behind the dashboard Savings page
    # (read by config.get_value; per-department overrides are added later from
    # the page itself).
    lines = ["", "[value]"]
    for k, v in value.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_finance(finance: dict[str, Any] | None) -> list[str]:
    if not finance:
        return []
    # Finance suite governance (finance-agent-suite): pause money movement for
    # a human, enforce compliance regimes (strictest-wins), and screen
    # sanctions. _cfg_governance owns the parent table and its scalar keys;
    # this helper emits only the finance-owned child and peer tables.
    lines: list[str] = []
    rha = finance.get("require_human_above") or 0
    if rha and rha > 0:
        lines.append("")
        lines.append("[governance.require_human_above]")
        lines.append(f'"*" = {rha}')
    da = finance.get("deny_above") or 0
    if da and da > 0:
        lines.append("")
        lines.append("[governance.deny_above]")
        lines.append(f'"*" = {da}')
    regimes = finance.get("regimes") or []
    if regimes:
        lines.append("")
        lines.append("[finance]")
        _emit_kv(lines, "regimes", regimes)
    sdn = (finance.get("sdn_path") or "").strip()
    if sdn:
        lines.append("")
        lines.append("[screening]")
        _emit_kv(lines, "sdn_path", sdn)
    if "operations_enable" in finance:
        lines.append("")
        lines.append("[finance_operations]")
        _emit_kv(lines, "enable", finance.get("operations_enable") is True)
        if finance.get("operations_enable") is True:
            for key in (
                "federal_register_enable",
                "texas_register_enable",
                "eur_lex_enable",
                "edpb_enable",
                "regulatory_domains",
                "regulatory_poll_seconds",
                "control_test_interval_seconds",
                "anomaly_enable",
                "sanctions_max_age_hours",
                "control_owner",
            ):
                if key in finance:
                    _emit_kv(lines, key, finance[key])
            # State register sources are explicit arrays of tables added after
            # onboarding; an empty list is a clear, safe starting posture.
            lines.append("state_feeds = []")
    return lines


def _cfg_capabilities(
    capability_config: dict[str, Any],
    embedded_flash: bool,
) -> list[str]:
    lines: list[str] = []
    if capability_config:
        lines.append("")
        lines.append("[capabilities]")
        for k, v in capability_config.items():
            lines.append(f"{k} = {str(v).lower()}")
    if embedded_flash:
        lines.append("")
        lines.append("[embedded]")
        lines.append("allow_flash = true")
    return lines


def _cfg_agent_factory(jd_hiring: bool) -> list[str]:
    # JD hiring is default-on in the kernel (maverick.jd_hiring); only write
    # the table when the operator opted OUT, matching the
    # write-only-on-divergence convention.
    if jd_hiring:
        return []
    return ["", "[agent_factory]", "jd_hiring = false"]


def _cfg_suites(suites: dict[str, bool] | None) -> list[str]:
    if not suites:
        return []
    # Per-suite enable/disable; the kernel's enabled_domains() reads this.
    lines = ["", "[suites]"]
    for k, v in suites.items():
        lines.append(f"{k} = {str(v).lower()}")
    return lines


def _cfg_license(license_cfg: dict[str, Any] | None) -> list[str]:
    if not license_cfg:
        return []
    # Entitlement enforcement; maverick.entitlements reads this [license] table.
    lines = ["", "[license]"]
    for k, v in license_cfg.items():
        _emit_kv(lines, k, v)
    return lines


def _cfg_advanced(  # noqa: C901 - flat sequence of independent feature toggles
    advanced: dict[str, Any] | None,
    providers: list[str],
) -> list[str]:
    if not advanced:
        return []
    lines: list[str] = []
    # These governed-learning sections are default-on in the kernel. Preserve a
    # deliberate "no" from the advanced wizard instead of silently omitting the
    # table (which would now mean on). True choices are emitted with their richer
    # comments/settings in the existing blocks below.
    _learning_sections = {
        "reflexion": "reflexion",
        "self_harness": "self_harness",
        "dreaming": "dreaming",
        "skill_synthesis": "skill_synthesis",
        "experience_guidance": "experience",
        "credit_assignment": "credit",
        "rehearsal": "rehearsal",
        "data_engine": "data_engine",
        "operations_scientist": "operations_scientist",
        "consequence": "consequence",
        "cortex": "cortex",
    }
    for choice, section in _learning_sections.items():
        if choice in advanced and advanced[choice] is False:
            lines.extend(["", f"[{section}]", "enable = false"])
    # Advanced reasoning toggles -> the kernel's config sections. Each is
    # off unless the wizard wrote it, matching the modules' own defaults.
    if (
        advanced.get("cost_aware")
        or advanced.get("verify_ensemble")
        or advanced.get("energy_aware")
        or advanced.get("autonomy_gate")
    ):
        lines.append("")
        lines.append("[routing]")
        # Constrain routing features enabled by the wizard to the providers
        # the user selected in this run. Some router/verifier fallbacks also
        # know about API keys from the shell environment; the allowlist keeps
        # advanced opt-ins from sending prompts to those unselected providers.
        _emit_kv(lines, "allowed_providers", providers)
        if advanced.get("cost_aware"):
            lines.append("cost_aware = true")
        if advanced.get("verify_ensemble"):
            lines.append("verify_ensemble = true")
        if advanced.get("energy_aware"):
            lines.append("energy_aware = true")
    if advanced.get("risk_proportional_verify"):
        lines.append("")
        lines.append("[verification]")
        lines.append("risk_proportional = true")
    if advanced.get("autonomy_gate") or advanced.get("headless_assume"):
        lines.append("")
        lines.append("[autonomy]")
        if advanced.get("autonomy_gate"):
            lines.append("enable = true")
        # Independent axis: assume-and-proceed instead of blocking on ask_user.
        if advanced.get("headless_assume"):
            lines.append("headless_assume = true")
    if advanced.get("governed_actions"):
        lines.append("")
        lines.append("[actions]")
        lines.append("enable = true")
    # data_grounding defaults ON, so emit the knob only to DISABLE it. Both keys
    # share one [workforce] section.
    _wf_disable_grounding = advanced.get("workforce_data_grounding") is False
    if advanced.get("workforce_levels") or _wf_disable_grounding:
        lines.append("")
        lines.append("[workforce]")
        if advanced.get("workforce_levels"):
            lines.append("levels = true")
        if _wf_disable_grounding:
            lines.append("data_grounding = false")
    if advanced.get("calibration_enforce") or advanced.get("reward_laundering"):
        lines.append("")
        lines.append("[calibration]")
        lines.append("enforce = true")
        if advanced.get("reward_laundering"):
            lines.append("# Reward-laundering interlock: also freeze learning when the")
            lines.append("# verifier's edge on adversarial probes falls below this fraction")
            lines.append("# of its edge on natural traffic (the judge is being gamed). Needs")
            lines.append("# adversarial probes recorded (calibration.record_probe) to bite.")
            lines.append("min_resistance = 0.5")
    if advanced.get("donate_trajectories"):
        lines.append("")
        lines.append("[telemetry]")
        lines.append("donate_trajectories = true")
        # Metadata-only by default. For DPO, add donate_text + the
        # donate_min_entropy/donate_min_confidence knobs by hand;
        # see docs/self-learning-runbook.md.
    if advanced.get("adaptive_compute"):
        lines.append("")
        lines.append("[adaptive_compute]")
        lines.append("enable = true")
    if advanced.get("best_of_n"):
        lines.append("")
        lines.append("[search]")
        lines.append("enable = true")
    if advanced.get("structured_verifier_off") or advanced.get("audit_rewards"):
        lines.append("")
        lines.append("[reasoning_reward]")
        if advanced.get("structured_verifier_off"):
            lines.append("# The structured rubric verifier is ON by default (per-dimension")
            lines.append("# scoring + safety veto); disable it to fall back to the scalar")
            lines.append("# verifier verdict (maverick.reasoning_reward).")
            lines.append("enable = false")
        if advanced.get("audit_rewards"):
            lines.append("# Sign each structured reward into the tamper-evident audit")
            lines.append("# chain (provable learning) -- the evidence the system learns")
            lines.append("# from, not just a mutable verdict. Enabled by default.")
            lines.append("audit_rewards = true")
    if advanced.get("jit_rl_off"):
        lines.append("")
        lines.append("[jit_rl]")
        lines.append("# JitRL test-time adaptation is ON by default (a no-op until it has")
        lines.append("# learned something); disable it entirely here (maverick.jit_rl).")
        lines.append("enable = false")
    if advanced.get("skill_synthesis"):
        lines.append("")
        lines.append("[skill_synthesis]")
        lines.append("enable = true")
    if advanced.get("experience_guidance"):
        lines.append("")
        lines.append("[experience]")
        lines.append("enable = true")
    if advanced.get("credit_assignment"):
        lines.append("")
        lines.append("[credit]")
        lines.append("enable = true")
    # The [self_improvement] block carries several default-on sub-toggles; emit
    # explicit values when the wizard records a choice, and write
    # factory_learning=false when the user opts out.
    declined_factory = "factory_learning" in advanced and not advanced.get("factory_learning")
    has_learning_policy = any(
        key in advanced for key in ("causal_promotion", "factory_learning", "evaluator_evolution")
    )
    if has_learning_policy or advanced.get("signed_approval"):
        lines.append("")
        lines.append("[self_improvement]")
        lines.append("enable = true")
        lines.append("capture = true")
        lines.append("prm_guidance = true")
        if "causal_promotion" in advanced:
            lines.append("# Promote learned changes on their confounder-adjusted causal")
            lines.append("# effect (maverick.promotion_effect), not a correlation. Applies")
            lines.append("# when self-improvement is enabled.")
            lines.append(
                f"causal_promotion = {str(bool(advanced.get('causal_promotion'))).lower()}"
            )
        if declined_factory:
            lines.append("# Keep the agent factory's generator static (do not mine")
            lines.append("# pack-generation gaps into proposer corrections).")
            lines.append("factory_learning = false")
        if "evaluator_evolution" in advanced:
            lines.append("# Promote a better judge instead of only freezing when the")
            lines.append("# evaluator drifts: a challenger replaces the incumbent only when")
            lines.append("# its agreement with a checksum-locked ground-truth anchor beats")
            lines.append("# it (maverick.evaluator_evolution, 'evaluator' rung).")
            lines.append(
                f"evaluator_evolution = {str(bool(advanced.get('evaluator_evolution'))).lower()}"
            )
        if advanced.get("signed_approval"):
            lines.append("# Require a cryptographic (Ed25519) human approval for the")
            lines.append("# code/weights rungs instead of a self-settable boolean, bound to")
            lines.append("# the exact change (maverick.approval_signing). Fails closed until")
            lines.append("# you provide approver PUBLIC keys (hex) or a dir of *.pub.")
            lines.append("require_signed_approval = true")
            lines.append('# approver_keys = ["<ed25519-pubkey-hex>"]')
            lines.append('# approver_keys_dir = "/etc/maverick/approvers"')
    if advanced.get("self_modify"):
        lines.append("")
        lines.append("[self_modify]")
        lines.append("# Research-only DGM cycles (maverick.self_modify): propose a diff,")
        lines.append("# enforce the reference monitor, evaluate baseline/candidate in distinct")
        lines.append("# no-egress non-root containers, and archive development telemetry.")
        lines.append("# This runner NEVER applies or promotes code. Production adoption remains")
        lines.append("# disabled until a one-shot external evaluator, evidence/base/tenant-bound")
        lines.append("# approval manifest, and durable PREPARE/CAS/COMMIT integration exist.")
        lines.append("# Inert until both gates are on, editable_paths is narrow, and at least")
        lines.append("# two discriminating eval_tests are configured. The tests are visible to")
        lines.append("# the candidate and are a development challenge corpus, not sealed proof.")
        lines.append("# Also select a container sandbox with require_container=true,")
        lines.append("# allow_network=false, allow_root=false, and finite process/memory limits.")
        lines.append("enable = true")
        lines.append('# editable_paths = ["packages/maverick-core/maverick/domains/*.toml"]')
        lines.append('# eval_tests = ["path/test_feature.py::case_a",')
        lines.append('#               "path/test_feature.py::case_b"]')
        lines.append('# eval_command = "python3 -m pytest -q"')
    if advanced.get("adapter_rung"):
        lines.append("")
        lines.append("[adapter_rung]")
        lines.append("# Governed in-tenant weights adaptation (maverick.adapter_rung):")
        lines.append("# LoRA adapters trained on YOUR corrections/traces, promoted through")
        lines.append("# the weights rung (held-out evidence + non-escalation + Ed25519")
        lines.append("# approval + one-step rollback). Frontier-model output is refused as")
        lines.append("# training data unless allow_model_output = true (distillation guard).")
        lines.append("# Inert until base_model is set to YOUR local serving spec; prefer an")
        lines.append("# Apache-2.0/MIT base so no vendor can restrict the tenant's model.")
        lines.append("enable = true")
        lines.append('# base_model = "ollama:<model-id>"   # e.g. an Apache-2.0 Qwen3 size')
        lines.append('# trainer = "dpo-lora"               # needs the [training] extra')
        lines.append("# allow_synthetic = false")
        lines.append("# allow_model_output = false")
    if advanced.get("rehearsal"):
        lines.append("")
        lines.append("[rehearsal]")
        lines.append("# Simulate a risky plan against the learned world-model before it")
        lines.append("# runs; proceed when confidently safe, block a poor outcome, escalate")
        lines.append("# the unknown (maverick.rehearsal). Fail-open while disabled.")
        lines.append("enable = true")
    if advanced.get("speculative"):
        lines.append("")
        lines.append("[speculative]")
        lines.append("# Draft a confidently-predictable turn with a cheap model, keeping the")
        lines.append("# frontier model for novel/uncertain turns (maverick.speculative_exec).")
        lines.append("# Set draft_model to a cheap spec to activate; a no-op until you do.")
        lines.append("enable = true")
        lines.append('# draft_model = "anthropic:claude-haiku-4-5-20251001"')
    if advanced.get("data_engine"):
        lines.append("")
        lines.append("[data_engine]")
        lines.append("# Triage production failures by causal impact on real outcomes, then")
        lines.append("# mine + validate + promote fixes (maverick.data_engine). The Tesla")
        lines.append("# data-engine flywheel for the workforce; reads the trajectory store.")
        lines.append("enable = true")
    if advanced.get("operations_scientist"):
        lines.append("")
        lines.append("[operations_scientist]")
        lines.append("# Discover a better process and prove it: pair a harmful action with")
        lines.append("# the beneficial habit that should replace it, validate the swap in the")
        lines.append("# world-model, then experiment for real (maverick.operations_scientist).")
        lines.append("enable = true")
    if advanced.get("consequence"):
        lines.append("")
        lines.append("[consequence]")
        lines.append("# Ground learning in REAL downstream outcomes: a recorded consequence")
        lines.append("# (invoice paid, ticket reopened) overrides the model's self-graded")
        lines.append(
            "# proxy reward, so the data engine learns from reality (maverick.consequence)."
        )
        lines.append("enable = true")
    if advanced.get("earned_autonomy"):
        lines.append("")
        lines.append("[earned_autonomy]")
        lines.append("# Consequence-proven trust (maverick.earned_autonomy): pin a consequence")
        lines.append("# card per rehearsed high-stakes action, score it against reality, and")
        lines.append("# graduate a proven action type to policy-auto-approval -- demoted on")
        lines.append("# one miss. Enabling records evidence only; arming graduation is the")
        lines.append("# separate auto_graduate switch below.")
        lines.append("enable = true")
        lines.append("# auto_graduate = true      # arm the dial (authority-widening)")
        lines.append('# max_auto_risk = "medium"  # raise to "high" only deliberately')
        lines.append("# min_streak = 10           # consecutive accurate predictions required")
    if advanced.get("flows"):
        lines.append("")
        lines.append("[flows]")
        lines.append("# Deterministic control-flow skeleton (branch/foreach/parallel/approval/")
        lines.append("# delay) over agentic + tool steps; migrated workflows keep their shape")
        lines.append("# while any node can still be a full agentic goal (maverick.flow).")
        lines.append("enable = true")
        lines.append("# Per-node retry cap; bounds repeated provider/tool calls.")
        lines.append("max_node_retries = 5")
        if advanced.get("flows_auto_evolve"):
            lines.append("# Autonomously revert a node rewrite whose grounded outcomes")
            lines.append("# regressed (the loop undoes its own bad changes).")
            lines.append("auto_evolve = true")
        if advanced.get("flows_public_url"):
            lines.append("# Externally-reachable dashboard base URL for signed channel")
            lines.append("# Approve/Reject links (actionable approvals).")
            _emit_kv(lines, "public_url", advanced["flows_public_url"])
    if advanced.get("connections"):
        lines.append("")
        lines.append("[connections]")
        lines.append("# Named SaaS connections (base URL + sealed token) so a connector")
        lines.append("# can be wired from the dashboard without <NAME>_TOKEN env vars; an")
        lines.append("# existing env var still takes precedence (maverick.connections).")
        lines.append("enable = true")
    if advanced.get("emergent_protocol"):
        lines.append("")
        lines.append("[emergent_protocol]")
        lines.append("# Learn short codes for the swarm's repeated coordination boilerplate;")
        lines.append("# every code decodes exactly back to English, so nothing is hidden from")
        lines.append("# the Shield/audit (maverick.emergent_protocol). No-op until learned.")
        lines.append("enable = true")
    if advanced.get("emergent_codec"):
        lines.append("")
        lines.append("[emergent_codec]")
        lines.append("# Measure the token-aware codec (maverick.emergent_tokens) on the live")
        lines.append("# coordination stream: byte-stuffed cheap codes that save real tokens.")
        lines.append("# Telemetry only -- the rendered text agents/Shield see is unchanged.")
        lines.append("enable = true")
    if advanced.get("enforce_quotas"):
        lines.append("")
        lines.append("[quotas]")
        lines.append("enforce = true")
        # Starter daily caps per principal; edit or set to 0 to disable a
        # dimension. The kernel also reads MAVERICK_QUOTA_* env overrides.
        lines.append("max_dollars_per_day = 25.0")
        lines.append("max_tokens_per_day = 5000000")
    if advanced.get("tenant_by_user"):
        lines.append("")
        lines.append("[tenancy]")
        lines.append("by_user = true")
    _client_id = str(advanced.get("client_id") or "").strip()
    if _client_id:
        lines.append("")
        lines.append("[client]")
        lines.append(f'id = "{_client_id}"')
        # Enforced: refuse to start unbound so this client's data can never land
        # in the shared root. Also set MAVERICK_CLIENT_ID in the service unit.
        lines.append("enforce = true")
    if advanced.get("enterprise"):
        lines.append("")
        lines.append("[enterprise]")
        lines.append("mode = true")
    if advanced.get("residency_region"):
        lines.append("")
        lines.append("# Data-residency region hint (maverick.residency); strict")
        lines.append("# mode is a separate [residency] strict knob.")
        lines.append("[residency]")
        _emit_kv(lines, "region", advanced["residency_region"])
    if advanced.get("compliance_disclosure_text") or advanced.get("compliance_profiles"):
        lines.append("")
        lines.append("# AI-disclosure line surfaced to users (maverick.compliance), and the")
        lines.append("# regulatory obligations compiled into the action gate")
        lines.append("# (maverick.regulatory_compiler; strictest-wins).")
        lines.append("[compliance]")
        if advanced.get("compliance_disclosure_text"):
            _emit_kv(lines, "disclosure_text", advanced["compliance_disclosure_text"])
        if advanced.get("compliance_profiles"):
            lines.append("profiles = [" + ", ".join(
                _toml_str(p) for p in advanced["compliance_profiles"]) + "]")
    if advanced.get("agent_trust"):
        lines.append("")
        lines.append("[agent_trust]")
        lines.append("enforce = true")
        # require_signed: refuse a federation peer that authenticates with only a
        # shared token (no pinned-key signature). Peers WITH a pinned key are
        # always signature-verified regardless of this flag.
        lines.append("require_signed = false")
        # Default-deny: external agents must be listed here, by pinned Ed25519
        # public key (lowercase id, e.g. "vega"), with the direction and ceiling
        # they're trusted within. Swap pubkeys out of band
        # (data_dir('audit','keys')/<key_id>.pub). expires_at/not_before (epoch
        # seconds) and revoked support key rotation/revocation.
        lines.append("# agents = [")
        lines.append(
            '#   { id = "vega", pubkey = "<64-hex Ed25519>", '
            'direction = "both", allow_tools = ["read_file", '
            '"http_fetch"], max_risk = "medium", max_dollars = 2.0, '
            'max_wall_seconds = 600, data_scopes = ["support"] },'
        )
        lines.append("# ]")
    if advanced.get("control_plane"):
        lines.append("")
        lines.append("# Agent Control Plane: arms principal verbs on the dashboard and CLI.")
        lines.append("# CLI HALT is local; dashboard cluster HALT needs a shared authority.")
        lines.append("# Roster/status/ledger reads work even when mutation verbs are off.")
        lines.append("[control_plane]")
        lines.append("enable = true")
    if advanced.get("anonymous_logs"):
        lines.append("")
        lines.append("[privacy]")
        lines.append("anonymous = true")
    if advanced.get("encrypt_at_rest"):
        lines.append("")
        lines.append("[encryption]")
        lines.append("at_rest = true")
        if advanced.get("encrypt_per_tenant"):
            lines.append("per_tenant = true")
    if advanced.get("pg_rls"):
        lines.append("")
        lines.append("[world_model]")
        # Database-enforced tenant isolation (Postgres backend only; ignored on
        # SQLite). The policy is strict, fail-closed equality, so prep BEFORE the
        # first start or pre-tenancy (NULL-tenant) rows become invisible:
        #   maverick tenant rls-preflight         # ownership + legacy-row check
        #   maverick tenant backfill --tenant ID  # assign pre-tenancy NULL rows
        lines.append(
            "# Run `maverick tenant rls-preflight` + `maverick tenant "
            "backfill` before first start (see docs/multi-tenancy.md)."
        )
        lines.append("rls = true")
    if advanced.get("audit_sign") or advanced.get("unified_audit"):
        lines.append("")
        lines.append("[audit]")
        lines.append("sign = true")
    if advanced.get("unified_audit"):
        lines.append("")
        lines.append("[audit.unified]")
        lines.append("# Fixed completeness floor: AI Governance, Consent & Preferences,")
        lines.append("# Data Use Governance, Privacy Automation, Tech Risk & Compliance,")
        lines.append("# and Third-Party Management. Sources cannot be removed in config.")
        lines.append("enable = true")
        lines.append("page_size = 100")
        lines.append("lease_seconds = 120")
        lines.append("max_lag_seconds = 300")
        lines.append("max_attempts = 5")
        lines.append("retry_backoff_seconds = 30")
        lines.append("poll_seconds = 60")
        lines.append("")
        lines.append("[audit.unified.custody]")
        lines.append("# Managed tenant-exact checkpoint/custody run after each projection sweep.")
        lines.append("# It never seals files automatically; run `maverick audit seal` explicitly.")
        lines.append("enable = true")
        lines.append("authority_revision = 1")
        lines.append("checkpoint_enable = true")
        lines.append(
            f"worm_enable = {'true' if advanced.get('audit_worm') else 'false'}"
        )
        lines.append("siem_enable = false")
        lines.append('# siem_destination_template = "https://siem.example/tenants/{tenant_sha256}/raw"')
        lines.append("max_siem_events_per_run = 500")
        lines.append("retry_backoff_seconds = 30")
        lines.append("max_retry_backoff_seconds = 3600")
    if advanced.get("audit_worm"):
        lines.append("")
        # WORM export of closed audit day-files. Defaults to a local read-only
        # mirror (best-effort, tamper-evident); switch provider to "s3" + an
        # Object-Lock bucket for regulator-grade immutability. Ship with
        # `maverick audit worm push` (see docs/security-hardening.md).
        lines.append("[audit.worm]")
        lines.append('provider = "local"   # or "s3" for S3 Object-Lock')
        lines.append("retention_days = 2555   # lock duration (~7y)")
        lines.append('# bucket = "my-audit-worm"   # s3: Object-Lock + versioning enabled')
        lines.append('# prefix = "maverick/audit/"')
        lines.append('# mode = "COMPLIANCE"        # COMPLIANCE | GOVERNANCE')
        lines.append('# region = "us-east-1"')
    # Dashboard editing locks. Both default on, so we only emit the disables --
    # and as ONE [features] table (two tables would be a duplicate-key TOML
    # error). The kernel reads these via config.get_features.
    _feature_locks = []
    if advanced.get("allow_pack_editing") is False:
        _feature_locks.append("pack_editing = false")
    if advanced.get("allow_role_editing") is False:
        _feature_locks.append("role_editing = false")
    if _feature_locks:
        lines.append("")
        lines.append("[features]")
        lines.extend(_feature_locks)
    if advanced.get("tree_of_thought"):
        lines.append("")
        lines.append("[planning]")
        lines.append('mode = "tree_of_thought"')
    _context_lines = []
    if advanced.get("compact_history"):
        _context_lines.append("compact = true")
    strat = advanced.get("compaction_strategy")
    if strat and strat != "default":
        _context_lines.append(f'compaction_strategy = "{strat}"')
    if advanced.get("model_scaled_context") is False:
        # Default-on; only a decline needs a line.
        _context_lines.append("model_scaled = false")
    if _context_lines:
        lines.append("")
        lines.append("[context]")
        lines.extend(_context_lines)
    _voice_lines = []
    if advanced.get("voice_commands") is False:
        # Default-on; only a decline needs a line.
        _voice_lines.append("dashboard_commands = false")
    if advanced.get("voice_local_stt") is False:
        # Built-in local STT model auto-fetch is default-on (egress-locked
        # deployments already default off); only a decline needs a line.
        _voice_lines.append("auto_fetch_model = false")
    if _voice_lines:
        # ONE [voice] table -- two would be a duplicate-key TOML error.
        lines.append("")
        lines.append("[voice]")
        lines.extend(_voice_lines)
    _attach_lines = []
    if advanced.get("attach_any_mime"):
        _attach_lines.append("allow_any_mime = true")
    if advanced.get("attachment_understanding") is False:
        # Default-on; only a decline needs lines.
        _attach_lines.append("transcribe_media = false")
        _attach_lines.append("extract_text = false")
    if _attach_lines:
        lines.append("")
        lines.append("[attachments]")
        lines.extend(_attach_lines)
    if advanced.get("reflexion"):
        lines.append("")
        lines.append("[reflexion]")
        lines.append("enable = true")
    if advanced.get("self_harness"):
        lines.append("")
        lines.append("[self_harness]")
        lines.append("enable = true")
        lines.append("risk_limited = true")
        # Ship production-safe validation floors by default (an operator can
        # relax them): never promote on only the mined examples, require an
        # unseen-sample floor, and an effect-size floor so noisy lifts don't
        # promote. No effect until a promotion pass actually runs.
        lines.append("require_held_out = true")
        lines.append("min_held_out = 5")
        lines.append("min_delta = 0.02")
        # Optional advanced paths, written only when the operator opted in above;
        # each is at its historical default otherwise, so the block stays minimal.
        if advanced.get("self_harness_eval_corpus"):
            # Free-text path: escape via _toml_str so a path with a backslash or
            # quote can't corrupt the config the wizard writes.
            _emit_kv(lines, "eval_corpus", advanced["self_harness_eval_corpus"])
            eval_budget = advanced.get("self_harness_eval_budget")
            if eval_budget and eval_budget > 0:
                lines.append(f"eval_budget_dollars = {float(eval_budget)}")
        if advanced.get("self_harness_bucket_domain"):
            lines.append('mine_bucket_by = ["domain"]')
        if advanced.get("self_harness_semantic_mining"):
            lines.append("semantic_mining = true")
        if advanced.get("self_harness_efficacy_review"):
            lines.append("efficacy_review = true")
        if advanced.get("self_harness_canary"):
            lines.append("promote_as_canary = true")
        if advanced.get("self_harness_auto_run"):
            lines.append("auto_run = true")
        elif "self_harness_auto_run" in advanced:
            lines.append("auto_run = false")
        if advanced.get("self_harness_metamorphic"):
            lines.append("metamorphic = true")
        if advanced.get("self_harness_relapse"):
            lines.append("relapse_failure_share = 0.5")
        if advanced.get("self_harness_calibrate_judge"):
            lines.append("calibrate_judge = true")
        if advanced.get("self_harness_transfer_auto"):
            lines.append("transfer_auto = true")
        if advanced.get("self_harness_corpus_harvest"):
            _emit_kv(lines, "corpus_harvest", advanced["self_harness_corpus_harvest"])
        if advanced.get("self_harness_store"):
            _emit_kv(lines, "store", advanced["self_harness_store"])
        cands = advanced.get("self_harness_candidates")
        if cands and cands > 1:
            lines.append(f"candidates_per_signature = {int(cands)}")
        retire_days = advanced.get("self_harness_retire_days")
        if retire_days and retire_days > 0:
            lines.append(f"retire_after_days = {int(retire_days)}")
    if advanced.get("fleet_memory"):
        lines.append("")
        lines.append("[fleet_memory]")
        lines.append("enable = true")
    if advanced.get("cortex"):
        lines.append("")
        lines.append("[cortex]")
        lines.append("# Cortex, the company brain (maverick.cortex): one governed recall/deposit")
        lines.append("# surface over knowledge, skills, lessons, the Operating Record and fleet")
        lines.append("# memory -- tenant-exact, one audit row per read and per write. Each")
        lines.append("# source's own toggle still applies. `maverick cortex status` shows it.")
        lines.append("enable = true")
    if advanced.get("external_agents"):
        lines.append("")
        lines.append("[external_agents]")
        lines.append("enable = true")
        if advanced.get("external_require_signed"):
            lines.append("require_signed = true")
        if advanced.get("external_connectors"):
            _emit_kv(lines, "connectors", advanced["external_connectors"])
        if advanced.get("external_mint_approval"):
            lines.append("mint_approval = true")
    sidecar = advanced.get("mcp_sidecar")
    if isinstance(sidecar, dict) and sidecar.get("enable") is True:
        # The wizard only emits a closed reviewed manifest.  It never writes an
        # upstream bearer value, broad CORS origin, or arbitrary forwarded
        # header; those are hand-managed deployment controls if needed.
        lines.append("")
        lines.append("[mcp_sidecar]")
        lines.append("enable = true")
        for key in (
            "upstream_url",
            "upstream_token_env",
            "require_shield",
            "max_calls_per_window",
            "budget_window_seconds",
            "listen_host",
            "listen_port",
        ):
            if key in sidecar:
                _emit_kv(lines, key, sidecar[key])
        tool_rows = sidecar.get("tools")
        if isinstance(tool_rows, dict):
            for name, spec in sorted(tool_rows.items()):
                if (
                    not isinstance(name, str)
                    or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name)
                    or "__" in name
                    or not isinstance(spec, dict)
                ):
                    continue
                lines.append("")
                lines.append(f"[mcp_sidecar.tools.{name}]")
                _emit_kv(lines, "risk", spec.get("risk", "high"))
                _emit_kv(
                    lines,
                    "description",
                    spec.get("description", "Reviewed governed MCP tool"),
                )
                # Schema authoring is deliberately bounded in the wizard. A
                # stricter reviewed schema can be edited into config later.
                lines.append('input_schema = { type = "object", properties = {} }')
                tags = spec.get("obligation_tags")
                if isinstance(tags, list) and all(isinstance(tag, str) for tag in tags):
                    _emit_kv(lines, "obligation_tags", tags)
    if advanced.get("repl"):
        lines.append("")
        lines.append("[repl]")
        lines.append("enable = true")
    if advanced.get("harness_refine"):
        lines.append("")
        lines.append("[harness_refine]")
        lines.append("enable = true")
        # require_approval defaults ON (a proposal must never self-apply), so
        # only an explicit decline is written.
        if advanced.get("harness_refine_require_approval") is False:
            lines.append("require_approval = false")
    # Forking defaults ON (lineage only, no execution); only a decline writes.
    if advanced.get("session_tree") is False:
        lines.append("")
        lines.append("[session_tree]")
        lines.append("enable = false")
    if advanced.get("memory_guard"):
        lines.append("")
        lines.append("[memory_guard]")
        lines.append("enable = true")
    if advanced.get("temporal_memory"):
        lines.append("")
        lines.append("[memory]")
        lines.append("temporal = true")
    if advanced.get("fairness_monitor"):
        lines.append("")
        lines.append("[fairness_monitor]")
        lines.append("enable = true")
    if advanced.get("drift_monitoring"):
        lines.append("")
        lines.append("[drift_monitoring]")
        lines.append("enable = true")
    if advanced.get("ai_incident_reporting"):
        lines.append("")
        lines.append("[ai_incident_reporting]")
        lines.append("enable = true")
    if advanced.get("eu_conformity"):
        lines.append("")
        lines.append("[eu_conformity]")
        lines.append("enable = true")
    if advanced.get("explainability"):
        lines.append("")
        lines.append("[explainability]")
        lines.append("enable = true")
    if advanced.get("content_provenance"):
        lines.append("")
        lines.append("[content_provenance]")
        lines.append("enable = true")
    if advanced.get("llm_gateway"):
        lines.append("")
        lines.append("[llm_gateway]")
        lines.append("enable = true")
        lines.append('upstream_url = "https://api.openai.com"')
        lines.append("screen_prompts = true")
        lines.append("redact_prompts = true")
        lines.append("redact_completions = true")
    if advanced.get("vendor_management"):
        lines.append("")
        lines.append("[vendor_management]")
        lines.append("enable = true")
    if advanced.get("consent_registry"):
        lines.append("")
        lines.append("[consent_registry]")
        lines.append("enable = true")
        lines.append(f'subject_salt = "{_prior_consent_salt()}"')
    if advanced.get("data_map"):
        lines.append("")
        lines.append("[data_map]")
        lines.append("enable = true")
    if advanced.get("breach_notification"):
        lines.append("")
        lines.append("[breach_notification]")
        lines.append("enable = true")
    if advanced.get("dsar_fulfillment"):
        lines.append("")
        lines.append("[dsar_fulfillment]")
        lines.append("enable = true")
    if advanced.get("grc_sync"):
        lines.append("")
        lines.append("[grc_sync]")
        lines.append("enable = true")
    if advanced.get("agent_topology"):
        lines.append("")
        lines.append("[agent_topology]")
        lines.append("enable = true")
    if advanced.get("governance_inventory"):
        lines.append("")
        lines.append("[governance_inventory]")
        lines.append("enable = true")
        lines.append("retention_seconds = 31536000")
        lines.append("max_source_records = 10000")
        lines.append("max_entities = 20000")
        lines.append("max_relationships = 40000")
        lines.append("max_export_bytes = 10485760")
        lines.append("max_snapshots = 10000")
    # Discipline defaults ON; only an explicit decline is written.
    if advanced.get("specialist_discipline") is False:
        lines.append("")
        lines.append("[domains]")
        lines.append("discipline = false")
    if advanced.get("dreaming"):
        lines.append("")
        lines.append("[dreaming]")
        lines.append("enable = true")
        if advanced.get("dreaming_llm_consolidation"):
            lines.append("llm_consolidation = true")
        keys = advanced.get("insight_pubkeys") or []
        if keys:
            # Free-text user input: route through _emit_kv so each key is
            # escaped via _toml_str (a key with a quote/backslash would
            # otherwise corrupt the config the wizard writes).
            _emit_kv(lines, "trusted_insight_pubkeys", keys)
    if advanced.get("tax_update_url") or advanced.get("tax_pubkeys"):
        lines.append("")
        lines.append("[tax]")
        lines.append("auto_update = true")
        if advanced.get("tax_update_url"):
            # User-entered free text: escape via _toml_str so a URL with a
            # backslash or quote can't corrupt the config the wizard writes.
            _emit_kv(lines, "update_url", advanced["tax_update_url"])
        tax_keys = advanced.get("tax_pubkeys") or []
        if tax_keys:
            # Same escaping concern as the insight pubkeys above.
            _emit_kv(lines, "trusted_constants_pubkeys", tax_keys)
    if advanced.get("effort"):
        lines.append("")
        lines.append("[effort]")
        lines.append("enabled = true")
    if advanced.get("cache_prewarm"):
        lines.append("")
        lines.append("[cache]")
        lines.append("prewarm = true")
    if advanced.get("hedge_requests"):
        lines.append("")
        lines.append("[latency]")
        lines.append("hedge_ms = 1500")
    tool_lines: list[str] = []
    if advanced.get("deferred_tools"):
        tool_lines.append("deferred_loading = true")
    if advanced.get("output_cache"):
        tool_lines.append("output_cache = true")
    if advanced.get("hardware_sensors"):
        tool_lines.append("hardware_sensors = true")
    if tool_lines:
        lines.append("")
        lines.append("[tools]")
        lines.extend(tool_lines)
    if advanced.get("shield_updates"):
        lines.append("")
        lines.append("[shield]")
        lines.append("federated_updates = true")
        lines.append('# update_url    = "https://..."  # REQUIRED')
        lines.append('# update_pubkey = "<ed25519 hex>"  # REQUIRED')
    if advanced.get("ebpf_monitor"):
        lines.append("")
        lines.append("[ebpf_monitor]")
        lines.append("enable = true")
    if advanced.get("local_runtime"):
        lines.append("")
        lines.append("[local_runtime]")
        lines.append("enabled = true")
        lines.append('# engine = "vllm"  # vllm | tgi | llamacpp')
        lines.append('# model  = "..."   # REQUIRED before `maverick local-runtime plan`')
    if advanced.get("local_first"):
        lines.append("")
        lines.append("[system]")
        lines.append("local_first = true")
        local_model = _local_first_model(providers)
        if local_model:
            lines.append("")
            lines.append("[local_first]")
            _emit_kv(lines, "model", local_model)
    oidc = advanced.get("oidc") or {}
    if isinstance(oidc, dict) and oidc.get("enabled"):
        # SSO ID-token verification for `maverick serve`. Its own table
        # (written once), so no duplicate-[auth.oidc] bug. The kernel reads
        # it via maverick.oidc.oidc_enabled() / load_oidc_config().
        lines.append("")
        lines.append("[auth.oidc]")
        lines.append("enabled = true")
        _emit_kv(lines, "issuer", oidc.get("issuer", ""))
        _emit_kv(lines, "audience", oidc.get("audience", ""))
        _emit_kv(lines, "jwks_uri", oidc.get("jwks_uri", ""))
        # Built-in browser-login fields, written ONLY when the operator
        # opted into that flow (so a bearer-only OIDC config is unchanged).
        # The kernel's login_enabled() additionally gates the routes.
        for key in (
            "client_id",
            "client_secret",
            "redirect_uri",
            "session_secret",
        ):
            val = oidc.get(key)
            if val:
                _emit_kv(lines, key, val)
    dept = advanced.get("department_access") or {}
    if isinstance(dept, dict) and (
        dept.get("default_suites") or dept.get("group_roles") or dept.get("group_suites")
    ):
        # Department (job-function) scoping for signed-in users. default_suites
        # is deny-by-default for users with no explicit grant; the group tables
        # turn IdP (SCIM) team membership into a role / department grant.
        lines.append("")
        lines.append("[dashboard]")
        if dept.get("default_suites"):
            lines.append("# Departments a signed-in user may use until an admin grants more.")
            _emit_kv(lines, "default_suites", list(dept["default_suites"]))
        # Group names contain spaces, so the TOML key must be a quoted string
        # ("Finance Team" = ...), not the bare key _emit_kv would render.
        if dept.get("group_roles"):
            lines.append("")
            lines.append("[dashboard.group_roles]")
            for group, role in dept["group_roles"].items():
                lines.append(f"{_toml_str(group)} = {_toml_str(role)}")
        if dept.get("group_suites"):
            lines.append("")
            lines.append("[dashboard.group_suites]")
            for group, suites in dept["group_suites"].items():
                rendered = ", ".join(_toml_str(s) for s in suites)
                lines.append(f"{_toml_str(group)} = [{rendered}]")
    if advanced.get("saml"):
        lines.append("")
        # SAML 2.0 SP browser SSO (alongside OIDC). Fill in the SP/IdP details
        # then hand /saml/metadata to the IdP. Needs the [saml] extra (pysaml2)
        # and the [auth.oidc] session_secret above. See docs/security-hardening.md.
        lines.append("[auth.saml]")
        lines.append('sp_entity_id = "https://YOUR-HOST/saml/metadata"')
        lines.append('acs_url = "https://YOUR-HOST/saml/acs"')
        lines.append('idp_metadata_url = "https://IDP/app/metadata"   # or idp_metadata_file')
        lines.append("# want_assertions_signed = true")
        lines.append('# sp_cert_file = ""   # to sign AuthnRequests / decrypt')
        lines.append('# sp_key_file = ""')
    return lines


def _cfg_mcp_servers(mcp_servers: dict[str, dict[str, Any]] | None) -> list[str]:
    if not mcp_servers:
        return []
    lines: list[str] = []
    for name, cfg in mcp_servers.items():
        lines.append("")
        # The server name is free text: a bare identifier goes in as-is, but a
        # name with dots/spaces/quotes must be a quoted+escaped TOML key or it
        # corrupts the table header (e.g. `foo"bar` or `a.b`).
        key = name if name.replace("_", "").replace("-", "").isalnum() else _toml_str(name)
        lines.append(f"[mcp_servers.{key}]")
        for k, v in cfg.items():
            _emit_kv(lines, k, v)
    return lines


def _cfg_registries(header: str, indexes: list[str] | None) -> list[str]:
    if not indexes:
        return []
    lines = ["", f"[{header}]"]
    _emit_kv(lines, "indexes", indexes)
    return lines


def _cfg_plugins(
    plugins: list[str] | None,
    plugin_grant: list[str] | None,
    plugin_enforce: bool,
    ts_plugins: list[list[str]] | None,
) -> list[str]:
    if not (plugins or ts_plugins):
        return []
    lines = ["", "[plugins]"]
    if plugins:
        _emit_kv(lines, "enabled", plugins)
    if plugin_grant:
        _emit_kv(lines, "grant", plugin_grant)
    if plugin_enforce:
        _emit_kv(lines, "enforce_permissions", plugin_enforce)
    if ts_plugins:
        _emit_kv(lines, "ts", ts_plugins)
    return lines


def _cfg_security(
    tool_acl: dict[str, Any] | None, autofix: bool, dual_approval: bool = False
) -> list[str]:
    if not (tool_acl or autofix or dual_approval):
        return []
    lines = ["", "[security]"]
    if autofix:
        lines.append("auto_fix = true")
    if dual_approval:
        # N-of-M dual control (two-person rule): high/critical-risk actions need
        # 2 distinct approvers and the requester can't self-approve. See
        # docs/security-hardening.md. Use a [security.approvals_required] table
        # to vary N per risk band.
        lines.append("approvals_required = 2")
        lines.append("allow_self_approval = false")
    for k, v in (tool_acl or {}).items():
        if k == "channels":
            continue
        _emit_kv(lines, k, v)
    for ch_id, ch_cfg in ((tool_acl or {}).get("channels") or {}).items():
        lines.append("")
        lines.append(f"[security.channels.{ch_id}]")
        for k, v in ch_cfg.items():
            _emit_kv(lines, k, v)
    return lines


def _cfg_rate_limits(rate_limits: dict[str, str] | None) -> list[str]:
    if not rate_limits:
        return []
    lines = ["", "[rate_limits]"]
    for name, spec in rate_limits.items():
        # Quote names that aren't bare identifiers (e.g. "mcp_*").
        key = name if name.replace("_", "").isalnum() else f'"{name}"'
        # spec is free-text ("N/seconds"); escape via _toml_str so a stray
        # quote/backslash can't corrupt the config the wizard writes.
        lines.append(f"{key} = {_toml_str(spec)}")
    return lines


def _cfg_table(header: str, mapping: dict[str, Any] | None) -> list[str]:
    if not mapping:
        return []
    lines = ["", f"[{header}]"]
    for k, v in mapping.items():
        _emit_kv(lines, k, v)
    return lines


def _backup_wizard_file(path: Path) -> None:
    """Best-effort private backup of a file the wizard is about to replace."""

    try:
        if not path.exists():
            return
        backup = Path(f"{path}.bak")
        temporary = Path(f"{backup}.tmp")
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with path.open("rb") as source, os.fdopen(fd, "wb") as destination:
                fd = -1
                shutil.copyfileobj(source, destination)
            try:
                stat = path.stat()
                os.utime(temporary, (stat.st_atime, stat.st_mtime))
            except OSError:
                pass
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, backup)
            try:
                os.chmod(backup, 0o600)
            except OSError:
                pass
        finally:
            if fd != -1:
                os.close(fd)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    except OSError:
        pass


def _wizard_file_unchanged(path: Path, data: bytes) -> bool:
    try:
        if not path.is_file():
            return False
        # Text-mode writes use CRLF on Windows. Compare logical content so the
        # same wizard answers are idempotent on every supported OS.
        existing = path.read_bytes().replace(b"\r\n", b"\n")
        expected = data.replace(b"\r\n", b"\n")
        return existing == expected
    except OSError:
        return False


def _tighten_wizard_file(path: Path) -> None:
    # A true no-op re-run still repairs custody if an older install left a
    # matching file with broad permissions.
    try:
        from maverick.file_lock import ensure_private_file

        ensure_private_file(path, 0o600)
    except ImportError:  # pragma: no cover -- installer normally depends on core
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _write_wizard_file(path: Path, body: str) -> None:
    """Write one private wizard file, preserving a logically equivalent retry."""

    if _wizard_file_unchanged(path, body.encode("utf-8")):
        _tighten_wizard_file(path)
        console.print(f"[green]ok[/green] unchanged {path} (chmod 600)")
        return
    _backup_wizard_file(path)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as destination:
            destination.write(body)
    finally:
        # If the file already existed at a wider mode, tighten it.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    console.print(f"[green]ok[/green] wrote {path} (chmod 600)")


def write_config(
    providers: list[str],
    role_models: dict[str, str],
    channels: dict[str, dict[str, Any]],
    safety: dict[str, Any],
    budget: dict[str, Any],
    sandbox: dict[str, Any],
    keys: dict[str, str],
    capabilities: dict[str, bool] | None = None,
    *,
    advanced: dict[str, Any] | None = None,
    mcp_servers: dict[str, dict[str, Any]] | None = None,
    mcp_registries: list[str] | None = None,
    template_registries: list[str] | None = None,
    plugins: list[str] | None = None,
    plugin_grant: list[str] | None = None,
    plugin_enforce: bool = False,
    ts_plugins: list[list[str]] | None = None,
    tool_acl: dict[str, Any] | None = None,
    rate_limits: dict[str, str] | None = None,
    retention: dict[str, int] | None = None,
    analytics: dict[str, Any] | None = None,
    persona: dict[str, str] | None = None,
    notifications: dict[str, Any] | None = None,
    webhooks: dict[str, Any] | None = None,
    deliverables: dict[str, Any] | None = None,
    personas: dict[str, Any] | None = None,
    a2a: dict[str, Any] | None = None,
    web_search_enabled: bool = False,
    skills: dict[str, Any] | None = None,
    self_learning: dict[str, Any] | None = None,
    ekko: dict[str, Any] | None = None,
    automation_import: dict[str, Any] | None = None,
    event_triggers: dict[str, Any] | None = None,
    flows: dict[str, Any] | None = None,
    knowledge: dict[str, Any] | None = None,
    oauth: dict[str, Any] | None = None,
    governed_connectors: dict[str, Any] | None = None,
    durable: dict[str, Any] | None = None,
    workspace: dict[str, Any] | None = None,
    finance: dict[str, Any] | None = None,
    value: dict[str, Any] | None = None,
    assessments: dict[str, Any] | None = None,
    security_suite: dict[str, Any] | None = None,
    deployment: str | None = None,
    suites: dict[str, bool] | None = None,
    license_cfg: dict[str, Any] | None = None,
    governance_profile: str | None = None,
) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    keys = dict(keys)
    if security_suite and security_suite.get("red_team_assurance"):
        commitment_name = "MAVERICK_RED_TEAM_COMMITMENT_KEY"
        prior = os.environ.get(commitment_name, "")
        if not prior and ENV_FILE.is_file():
            try:
                for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                    name, separator, value = line.partition("=")
                    if separator and name == commitment_name:
                        prior = value
                        break
            except OSError:
                prior = ""
        keys.setdefault(
            commitment_name,
            prior or secrets.token_urlsafe(48),
        )

    # Re-running the wizard truncates config.toml / .env. The loader explicitly
    # supports hand-editing, so back up any existing file first (0o600) instead
    # of silently destroying a user's manual edits.
    if keys:
        # Atomic + perm-from-creation: previous version was
        # ``write_text(...)`` followed by ``chmod(0o600)``, which left
        # the file world-readable (0o644) for one syscall. Open with
        # ``O_CREAT | O_WRONLY | O_TRUNC`` and mode 0o600 so the file
        # never exists at any other permission.
        body = "\n".join(f"{k}={v}" for k, v in keys.items()) + "\n"
        _write_wizard_file(ENV_FILE, body)

    # [flows] can be requested by two independent wizard steps: the dedicated
    # flow-engine step (the `flows` arg) and the advanced-reasoning "Flow
    # engine?" toggle (`advanced["flows"]`). Emitting the table from both paths
    # writes [flows] twice, which is invalid TOML -- tomllib rejects the WHOLE
    # file and load_config() silently falls back to {} (every wizard choice
    # lost; the smoke test then reports "sandbox backend missing"). Fold the
    # advanced flow extras into the single dedicated [flows] table so it is
    # emitted exactly once. Copy `advanced` so we never mutate the caller's dict.
    advanced = dict(advanced or {})
    if advanced.pop("flows", False):
        flows = dict(flows or {})
        flows.setdefault("enable", True)
        if advanced.pop("flows_auto_evolve", False):
            flows.setdefault("auto_evolve", True)
        _flows_url = advanced.pop("flows_public_url", None)
        if _flows_url:
            flows.setdefault("public_url", _flows_url)

    # The security-suite questionnaire owns these shared tables. The advanced
    # questionnaire can also enable them, so OR its answers into a copied suite
    # config and remove the duplicate emit path before composing TOML.
    security_suite = None if security_suite is None else dict(security_suite)
    for shared_key in ("ai_regulatory_feed", "ai_literacy"):
        if advanced.pop(shared_key, False):
            security_suite = dict(security_suite or {})
            security_suite[shared_key] = True

    # The assessment questionnaire owns the single [entity_graph] table. The
    # shared governance inventory requires that projection, so reconcile a
    # prior assessment-time decline here instead of emitting a second table
    # from _cfg_advanced (duplicate TOML tables invalidate the whole config).
    assessments = dict(assessments or {})
    if advanced.get("governance_inventory"):
        assessments["entity_graph"] = True

    # Push-based red-team campaigns are delivered only through the enrolled,
    # self-authenticating external-agent boundary. Enabling the campaign plane
    # therefore prepares that dependency once in the advanced config block.
    if security_suite and security_suite.get("red_team_assurance"):
        advanced["external_agents"] = True
    if security_suite and security_suite.get("administration_document_access"):
        # The document token ledger is externally consequential and refuses to
        # run without the signed audit chain.  Preserve the user's other
        # advanced choices while deriving this mandatory dependency.
        advanced["audit_sign"] = True

    lines = [
        "# Lightwork config. Regenerate with:  maverick init",
        "",
    ]
    lines += _cfg_deployment(deployment)
    lines += _cfg_governance(governance_profile, finance)
    lines += _cfg_providers(providers)
    lines += _cfg_role_models(role_models)
    lines += _cfg_channels(channels)
    lines += _cfg_core(budget, safety, sandbox)
    lines += _cfg_skills(skills)
    lines += _cfg_self_learning(self_learning)
    lines += _cfg_ekko(ekko)
    lines += _cfg_automation_import(automation_import)
    lines += _cfg_event_triggers(event_triggers)
    lines += _cfg_flows(flows)
    lines += _cfg_knowledge(knowledge)
    lines += _cfg_oauth(oauth)
    lines += _cfg_governed_connectors(governed_connectors)
    lines += _cfg_durable(durable)
    lines += _cfg_workspace(workspace)
    lines += _cfg_finance(finance)
    lines += _cfg_value(value)
    lines += _cfg_assessments(assessments)
    lines += _cfg_security_suite(security_suite)

    capability_config = dict(capabilities or {})
    if web_search_enabled and not capability_config.get("web_search"):
        # web_search is wired through enable_web_search at kernel
        # boot; reflect the wizard's pick under [capabilities].
        capability_config["web_search"] = True
    if advanced and advanced.get("enforce_capabilities"):
        capability_config["enforce"] = True
    if advanced and advanced.get("per_call_token_exchange"):
        # Per-call token exchange only makes sense atop capability enforcement
        # (a token minted from no grant has nothing to scope), so turning it on
        # implies enforcement.
        capability_config["enforce"] = True
        capability_config["per_call_tokens"] = True

    # The embedded-device flash gate lives under [embedded], not
    # [capabilities] -- pull it out before emitting the capabilities block.
    embedded_flash = bool(capability_config.pop("embedded_flash", False))
    # JD hiring lives under [agent_factory] (kernel: maverick.jd_hiring).
    jd_hiring = bool(capability_config.pop("jd_hiring", True))

    lines += _cfg_capabilities(capability_config, embedded_flash)
    lines += _cfg_agent_factory(jd_hiring)
    lines += _cfg_suites(suites)
    lines += _cfg_license(license_cfg)
    lines += _cfg_advanced(advanced, providers)
    lines += _cfg_mcp_servers(mcp_servers)
    lines += _cfg_registries("mcp_registries", mcp_registries)
    lines += _cfg_registries("template_registries", template_registries)
    lines += _cfg_plugins(plugins, plugin_grant, plugin_enforce, ts_plugins)
    lines += _cfg_security(
        tool_acl,
        bool((advanced or {}).get("security_autofix")),
        dual_approval=bool((advanced or {}).get("dual_approval")),
    )
    lines += _cfg_rate_limits(rate_limits)
    lines += _cfg_table("retention", retention)
    lines += _cfg_table("analytics", analytics)
    lines += _cfg_table("persona", persona)
    lines += _cfg_table("notifications", notifications)
    lines += _cfg_table("webhooks", webhooks)
    lines += _cfg_table("deliverables", deliverables)
    lines += _cfg_table("personas", personas)
    lines += _cfg_table("a2a", a2a)

    # SECURITY: config.toml is NOT secret-free. Unlike API keys (which live in
    # ~/.maverick/.env and are referenced via ${VAR}), the OIDC browser-login
    # client_secret and session_secret (HMAC session-cookie signing key) are
    # written here as literal values. The 0600 mode below is therefore load-
    # bearing, not just tidiness -- never relax it, and treat this file as
    # secret-bearing in backups/log redaction. chmod 600 so multi-user hosts
    # don't leak it to other accounts.
    config_body = "\n".join(lines) + "\n"
    _write_wizard_file(CONFIG_FILE, config_body)


def smoke_test() -> bool:
    console.print()
    console.print("[dim]Running smoke test...[/dim]")
    try:
        from maverick.config import load_config

        cfg = load_config()
        assert cfg.get("sandbox", {}).get("backend"), "sandbox backend missing"
        console.print("[green]✓[/green] Config readable")
    except Exception as e:
        console.print(f"[red]✗[/red] Config read failed: {e}")
        return False

    try:
        import maverick_shield  # noqa: F401

        console.print("[green]✓[/green] Lightwork Shield available")
    except ImportError:
        console.print("[yellow]⚠[/yellow] maverick-shield not installed (safety will be disabled)")

    try:
        import anthropic  # noqa: F401

        console.print("[green]✓[/green] Anthropic SDK available")
    except ImportError:
        console.print(
            "[yellow]⚠[/yellow] anthropic not installed; install with: pip install anthropic"
        )

    return True


PARTIAL_STATE_PATH = CONFIG_DIR / "wizard-partial.json"


def _save_partial(state: dict[str, Any]) -> None:
    """Persist wizard progress so --resume can pick up later."""
    try:
        import json as _json

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PARTIAL_STATE_PATH.write_text(_json.dumps(state, default=str))
        os.chmod(PARTIAL_STATE_PATH, 0o600)
    except OSError:
        pass


def _load_partial() -> dict[str, Any] | None:
    """Return persisted partial state, or None if absent."""
    if not PARTIAL_STATE_PATH.exists():
        return None
    try:
        import json as _json

        return _json.loads(PARTIAL_STATE_PATH.read_text())
    except (OSError, ValueError):
        return None


def _clear_partial() -> None:
    try:
        if PARTIAL_STATE_PATH.exists():
            PARTIAL_STATE_PATH.unlink()
    except OSError:
        pass


def run_fast() -> int:
    """``maverick init --fast``: zero-question setup with sensible defaults.

    Skips every prompt. Writes a minimal config that runs on Anthropic
    Claude (BYOK via ANTHROPIC_API_KEY env), the Docker sandbox when its
    daemon is up (else local), balanced safety, $5/run cap. Users can
    `maverick init` later to customize.
    """
    welcome()
    if not preflight():
        console.print("[red]Preflight failed.[/red] Fix the issues above and re-run.")
        return 1
    console.print(
        "[bold]Fast setup:[/bold] using safe defaults. "
        "Run `maverick init` (no --fast) anytime to customize.\n"
    )
    providers = ["anthropic"]
    role_models: dict[str, str] = {}  # use ROLE_MODELS defaults
    channels: dict[str, Any] = {}
    safety = {
        "profile": "balanced",
        "block_threshold": "high",
        "scan_input": True,
        "scan_tool_calls": True,
        "scan_output": True,
        "compartments": False,
    }
    budget = {
        "max_dollars": 5.0,
        "max_wall_seconds": 3600.0,
        "max_tool_calls": 500,
    }
    # Prefer the isolated Docker sandbox, but fall back to local when the
    # daemon isn't up -- otherwise fast-setup writes a docker config that the
    # very next `maverick start` can't run (the user never chose docker, yet
    # hits "Docker not available"). Mirrors write_consumer_config.
    backend = "docker" if _docker_available() else "local"
    sandbox = {
        "backend": backend,
        "workdir": str(Path.home() / "maverick-workspace"),
        "timeout": 60,
    }
    denied_tools = ["computer", "browser"]
    if backend == "local":
        denied_tools.extend(["shell", "write_file", "apply_patch", "str_replace_editor"])
        console.print(
            "[yellow]![/yellow] Docker daemon not detected — using the "
            "[bold]local[/bold] sandbox with host-mutating tools disabled. "
            "Run [bold]maverick init[/bold] to switch to docker once it's up."
        )
    capabilities = {"computer_use": False, "browser": False, "ros": False}
    # Pick up the API key from the env if it's already there;
    # otherwise the wizard's later run can populate ~/.maverick/.env.
    keys: dict[str, str] = {}
    if os.environ.get("ANTHROPIC_API_KEY"):
        keys["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]
    write_config(
        providers,
        role_models,
        channels,
        safety,
        budget,
        sandbox,
        keys,
        capabilities,
        tool_acl={"denied_tools": denied_tools},
    )
    # smoke_test() returns a bool and it was discarded, so `maverick init
    # --fast` printed "Fast setup finished" and exited 0 over a broken install
    # -- the one command whose entire promise is that you can trust the result
    # without answering questions. An installer that cannot fail is an
    # installer whose success means nothing.
    if not smoke_test():
        console.print()
        console.print(
            Panel.fit(
                "[bold red]Fast setup failed its smoke test.[/bold red]\n\n"
                "The config was written, but the checks above did not pass, so "
                "this install is not ready to run.\n"
                "Fix the failures listed above, then re-run "
                "[bold]maverick init --fast[/bold] (or `maverick doctor`).",
                border_style="red",
            )
        )
        return 1
    console.print()
    console.print(
        Panel.fit(
            "[bold green]Fast setup finished.[/bold green]\n\n"
            'Try: [bold]maverick start "hello"[/bold]\n'
            "(If ANTHROPIC_API_KEY wasn't set, edit ~/.maverick/.env first.)\n"
            "[dim]The maverick command is also available as lightwork.[/dim]",
            border_style="green",
        )
    )
    return 0


CONSUMER_DEMO_GOAL = "Write me a haiku about Tuesday."
CONSUMER_DEMO_MODEL = "anthropic:claude-haiku-4-5"


def pick_mode() -> str:
    """First-screen picker: consumer vs advanced.

    Council round-2 design: every launch starts here so a non-technical
    user lands in a four-question flow with safe defaults, and a power
    user can opt straight into the full wizard.
    """
    console.print()
    console.print(
        Panel.fit(
            "[bold]How do you want to set this up?[/bold]\n\n"
            "  consumer  Four questions, safe defaults. About a minute.\n"
            "  express   A few questions, then turn ON all safe features.\n"
            "  advanced  Pick every model, channel, safety level, budget.",
            border_style="cyan",
        )
    )
    pick = _q_select(
        "Pick a mode:",
        [
            "consumer - just get me running",
            "express  - turn on all features, few questions",
            "advanced - let me configure everything",
        ],
        default="consumer - just get me running",
    )
    return pick.split()[0]


def _consumer_budget() -> dict[str, float]:
    """Single-question budget chip picker for consumer mode."""
    pick = _q_select(
        "Stop after spending how much per task?",
        ["$1", "$5", "$20", "custom"],
        default="$5",
    )
    if pick == "custom":
        dollars = _safe_float(_q_text("  Custom cap ($)", default="5.0"), default=5.0)
    else:
        dollars = float(pick.lstrip("$"))
    return {
        "max_dollars": dollars,
        "max_wall_seconds": 600.0,
        "max_tool_calls": 100,
    }


def _consumer_api_key() -> dict[str, str]:
    """Single-screen Anthropic key collection for consumer mode.

    No DevTools paste, no jargon. Three escape hatches:
      1. Paste the key (the default).
      2. Skip for now (write config without keys; user can re-run later).
      3. Open the console in a browser to make a key.
    """
    console.print()
    console.print(
        "Lightwork needs an account with Claude (Anthropic). "
        "Get a key at: [cyan]https://console.anthropic.com/settings/keys[/cyan]\n"
        "[dim]It looks like 'sk-ant-...' and is about 100 characters long.[/dim]",
    )
    val = _q_secret("  Paste your Anthropic API key (leave blank to skip):")
    if not val.strip():
        console.print(
            "[yellow]Skipped.[/yellow] You can add one later by running "
            "[bold]maverick init[/bold] again."
        )
        return {}
    # Validate with the 7-day cache.
    cached = _cached_validation("ANTHROPIC_API_KEY", val)
    if cached is not None:
        ok, msg = cached
    else:
        ok, msg = _validate_anthropic_key(val)
        _remember_validation("ANTHROPIC_API_KEY", val, ok, msg)
    if ok:
        console.print(f"  {_validation_marker(ok, msg)} {msg}")
        return {"ANTHROPIC_API_KEY": val}
    # On failure, surface the branded error and let the user decide.
    show_bad_key_error("ANTHROPIC_API_KEY", msg)
    if _q_confirm("Save the key anyway and continue?", default=False):
        return {"ANTHROPIC_API_KEY": val}
    return {}


def write_consumer_config(
    *,
    user_name: str,
    keys: dict[str, str],
    workdir: str,
    budget: dict[str, float],
    profile: str | None = None,
) -> None:
    """Write a consumer-mode config with the safety-seat safe defaults.

    Single source of truth shared by the CLI consumer flow
    (``run_consumer``) and the desktop installer sidecar
    (``maverick_installer.bridge``) so the two front ends can't drift.
    ``profile`` is the governance level from :data:`GOVERNANCE_PROFILES`
    (unknown values fall back to the default) -- it layers the learning
    lifecycle + governance posture on top of the safety-seat base, which
    is identical at every level. Creates the workspace dir. Raises on
    write failure (caller renders the branded error).
    """
    preset = GOVERNANCE_PROFILES.get(profile or "")
    if preset is None:
        profile, preset = (
            DEFAULT_GOVERNANCE_PROFILE,
            GOVERNANCE_PROFILES[DEFAULT_GOVERNANCE_PROFILE],
        )
    Path(workdir).expanduser().mkdir(parents=True, exist_ok=True)
    backend = "docker" if _docker_available() else "local"
    # Computer + browser always require explicit opt-in (consumer is
    # never asked). When there's no Docker sandbox to contain it, also
    # deny the host-mutating tools — fail closed on the host. With
    # Docker present, shell/write_file/apply_patch stay enabled because
    # the container is the blast radius, not the user's machine.
    denied_tools = ["computer", "browser"]
    if backend == "local":
        denied_tools.extend(["shell", "write_file", "apply_patch", "str_replace_editor"])
    write_config(
        ["anthropic"],  # providers
        {},  # role_models -> kernel defaults
        {},  # channels -> none in consumer mode
        {
            "profile": "strict",  # strictest shield
            "block_threshold": "medium",  # block medium+ threats
            "scan_input": True,
            "scan_tool_calls": True,
            "scan_output": True,
        },
        budget,
        {
            "backend": backend,
            "workdir": str(Path(workdir).expanduser()),
            "timeout": 60,
        },
        keys,
        {"computer_use": False, "browser": False, "ros": False},  # capabilities
        advanced=dict(preset["advanced"]),
        self_learning=dict(preset["self_learning"]),
        tool_acl={"denied_tools": denied_tools},
        rate_limits={
            "web_search": "5/60",
            "http_fetch": "10/60",
            "shell": "5/60",
            "mcp_*": "20/60",
        },
        retention=dict(preset["retention"]),
        persona={"name": "Lightwork", "style": "balanced", "user_name": user_name},
        web_search_enabled=True,
        governance_profile=profile,
    )


def run_consumer() -> int:
    """Four-question consumer flow. Writes a minimal config with
    consumer-grade safe defaults, then prints a one-line demo command."""
    console.print()
    console.print(
        Panel.fit(
            "[bold]Lightwork setup[/bold]\n\n"
            "Five quick questions. About a minute. You can change anything later\n"
            "by running [bold]maverick init[/bold] again.",
            border_style="cyan",
        )
    )

    if not preflight():
        console.print("[red]Setup can't continue.[/red] Fix the issues above and try again.")
        return 1

    user_name = (
        _q_text(
            "What should we call you?",
            default=os.environ.get("USER") or os.environ.get("USERNAME") or "",
        ).strip()
        or "you"
    )

    profile = pick_governance_profile()

    keys = _consumer_api_key()

    workdir = _q_text(
        "Where can Lightwork work?",
        default=str(Path.home() / "Documents" / "Maverick"),
    ).strip() or str(Path.home() / "Documents" / "Maverick")

    budget = _consumer_budget()

    try:
        write_consumer_config(
            user_name=user_name,
            keys=keys,
            workdir=workdir,
            budget=budget,
            profile=profile,
        )
    except Exception as e:
        show_install_failure(e)
        return 1

    # First-goal nudge. Don't run the goal here (the kernel doesn't
    # stream into a wizard window today, and shelling out from inside
    # the installer is ugly); print the one-liner instead. The Haiku
    # model keeps the demo under $0.01 and finishes in a couple of
    # seconds even on cold connections.
    console.print()
    if keys:
        console.print(
            Panel.fit(
                f"[bold green]Setup complete, {user_name}.[/bold green]\n\n"
                "Try your first goal:\n"
                f'  [bold]maverick start "{CONSUMER_DEMO_GOAL}" --model {CONSUMER_DEMO_MODEL}[/bold]\n\n'
                "Then:\n"
                "  [bold]maverick dashboard[/bold]   web UI at http://127.0.0.1:8765\n\n"
                "[dim]The maverick command is also available as lightwork.[/dim]",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel.fit(
                f"[bold yellow]Setup saved without an API key, {user_name}.[/bold yellow]\n\n"
                "Add one later by exporting ANTHROPIC_API_KEY or by running\n"
                "[bold]maverick init[/bold] again.",
                border_style="yellow",
            )
        )
    _clear_partial()
    return 0


# Express mode turns on the safe, single-user PRODUCT + self-improvement
# features with sane defaults -- everything that makes Lightwork "fully lit up"
# WITHOUT the host-dangerous or infra-shaped toggles that must stay explicit
# opt-in (computer/browser control, code execution, autonomous self-
# modification, at-rest encryption + multi-tenancy, channel tokens, license
# enforcement). Kept as data so a test can assert exactly what express enables
# without driving the prompts. Every key here is one `_cfg_advanced` recognises.
_EXPRESS_ADVANCED: dict[str, Any] = {
    # cheaper + faster + stronger reasoning
    "cost_aware": True,
    "compact_history": True,
    "adaptive_compute": True,
    "risk_proportional_verify": True,
    "verify_ensemble": True,
    "autonomy_gate": True,
    "effort": True,
    "cache_prewarm": True,
    "output_cache": True,
    # the closed self-improvement lifecycle
    "reflexion": True,
    "dreaming": True,
    "experience_guidance": True,
    "skill_synthesis": True,
    "credit_assignment": True,
    "self_harness": True,
    "data_engine": True,
    "operations_scientist": True,
    "consequence": True,
    "rehearsal": True,
    "evaluator_evolution": True,
    "cortex": True,
    # governed memory + provable-learning audit (non-blocking)
    "memory_guard": True,
    "temporal_memory": True,
    "fairness_monitor": True,
    "governed_actions": True,
    "calibration_enforce": True,
    "audit_sign": True,
    "audit_rewards": True,
    # product surface: named SaaS connections (flows go via the dedicated arg)
    "connections": True,
}

# Self-learning ON, but WITHOUT the generate-and-run-new-code autonomy
# (create_tools) and WITHOUT MCP-server acquisition (allow_mcp) -- those two are
# higher-trust and stay an explicit opt-in even in express.
_EXPRESS_SELF_LEARNING: dict[str, Any] = {
    "enable": True,
    "preflight": True,
    "create_tools": False,
    "provision_packs": True,
    "allow_mcp_acquisition": False,
    "allow_provider_egress": False,
    "distill_local": True,
}


# --- Governance profiles: business-shaped onboarding levels --------------------
#
# One early question -- "what kind of business is this?" -- maps to a preset
# bundle of ALREADY-EXISTING knobs. Every level gets the self-learning /
# self-improvement lifecycle (that's the product); what escalates is the
# governance posture around it. A gymnastics studio wants the learning without
# the signing ceremony; a manufacturer wants budgets + an audit trail; a bank
# wants everything signed, immutable, and quota-enforced. Kept as data (same
# convention as _EXPRESS_ADVANCED) so tests can assert exactly what each level
# enables without driving prompts. Budget caps are NOT part of a profile: they
# are collected separately and are never optional at any level.

# The closed learning lifecycle every level turns on. Safe-by-construction
# subset: in-process learning + consolidation only -- no generate-and-run-code
# autonomy, no MCP acquisition (those stay explicit opt-ins at every level).
_LEARNING_LIFECYCLE: dict[str, Any] = {
    "reflexion": True,
    "dreaming": True,
    "experience_guidance": True,
    "skill_synthesis": True,
    "credit_assignment": True,
    "self_harness": True,
    "data_engine": True,
    "evaluator_evolution": True,
    "consequence": True,
    "rehearsal": True,
    "cortex": True,
}

_PROFILE_SELF_LEARNING: dict[str, Any] = dict(_EXPRESS_SELF_LEARNING)

# Governance additions by level (cumulative: standard ⊃ essentials,
# regulated ⊃ standard). Every key is one _cfg_advanced recognises.
_STANDARD_GOVERNANCE: dict[str, Any] = {
    "memory_guard": True,  # governed memory writes
    "governed_actions": True,  # side-effectful actions logged + gated
    "calibration_enforce": True,  # confidence calibration gate
    "fairness_monitor": True,
    "audit_sign": True,  # Ed25519-signed audit chain
    "enforce_quotas": True,  # per-principal daily spend/token caps
}
_REGULATED_GOVERNANCE: dict[str, Any] = {
    **_STANDARD_GOVERNANCE,
    "audit_rewards": True,  # provable-learning reward audit
    "audit_worm": True,  # WORM export of closed audit day-files
    "signed_approval": True,  # human Ed25519 sign-off for code/weights rungs
}

GOVERNANCE_PROFILES: dict[str, dict[str, Any]] = {
    "essentials": {
        "advanced": dict(_LEARNING_LIFECYCLE),
        "self_learning": dict(_PROFILE_SELF_LEARNING),
        "retention": {"audit_days": 30, "episodes_days": 90, "events_days": 30},
    },
    "standard": {
        "advanced": {**_LEARNING_LIFECYCLE, **_STANDARD_GOVERNANCE},
        "self_learning": dict(_PROFILE_SELF_LEARNING),
        "retention": {"audit_days": 90, "episodes_days": 180, "events_days": 90},
    },
    "regulated": {
        "advanced": {**_LEARNING_LIFECYCLE, **_REGULATED_GOVERNANCE},
        "self_learning": dict(_PROFILE_SELF_LEARNING),
        "retention": {"audit_days": 365, "episodes_days": 365, "events_days": 365},
    },
}

DEFAULT_GOVERNANCE_PROFILE = "essentials"

# Menu strings start with the profile name so the picker can split()[0] them,
# same convention as pick_mode(). Public: the desktop sidecar (bridge.py)
# imports these so the two front ends show the SAME menu and cannot drift.
GOVERNANCE_CHOICES: list[str] = [
    "essentials - small business: learn + improve, stay out of my way (a gym, a studio, a shop)",
    "standard   - growing company: + budgets enforced, signed audit trail "
    "(a manufacturer, an agency)",
    "regulated  - bank / clinic / government: + human sign-off, immutable audit, long retention",
]


def _prior_governance_profile() -> str | None:
    """The [governance] profile recorded by a previous `maverick init`, if any.

    Same pattern as pick_deployment's prior-choice defaulting: a re-run of the
    wizard must default to what the operator ALREADY chose. Without this, an
    operator on a regulated install re-running init to change a name would
    silently downgrade to essentials -- dropping audit signing, quotas, and
    signed approvals -- by accepting the default. Reads CONFIG_FILE (the file
    this wizard writes) rather than the kernel loader so it honors the same
    path every other wizard read/write uses.
    """
    try:
        cfg = tomllib.loads(CONFIG_FILE.read_text())
        prior = (cfg.get("governance") or {}).get("profile", "")
        return prior if prior in GOVERNANCE_PROFILES else None
    except Exception:  # pragma: no cover -- absent/invalid config: no prior
        return None


def pick_governance_profile() -> str:
    """One onboarding question: how much governance does this business need?

    Defaults to the previously configured level on a re-run, else
    :data:`DEFAULT_GOVERNANCE_PROFILE`; falls back to the default on anything
    it doesn't recognise, so an <Enter> or a stubbed answer never lands
    outside the three supported levels.
    """
    console.print()
    console.print(
        "[dim]Every level gets self-learning and self-improvement. The level "
        "sets how much governance wraps it -- you can change it any time by "
        "re-running maverick init.[/dim]"
    )
    prior = _prior_governance_profile()
    default = next(
        (c for c in GOVERNANCE_CHOICES if c.split()[0] == prior),
        GOVERNANCE_CHOICES[0],
    )
    pick = _q_select(
        "What kind of business is this for?",
        list(GOVERNANCE_CHOICES),
        default=default,
    )
    name = pick.split()[0]
    return name if name in GOVERNANCE_PROFILES else (prior or DEFAULT_GOVERNANCE_PROFILE)


def run_express() -> int:
    """Express setup: a few user-specific questions, then turn on every safe
    single-user feature at once. The shortcut for "I want everything on"
    without sitting through the full advanced wizard. Host-dangerous and
    infra-shaped toggles stay off (and are listed for the user)."""
    console.print()
    console.print(
        Panel.fit(
            "[bold]Lightwork express setup[/bold]\n\n"
            "A few questions, then every safe single-user feature is turned on:\n"
            "the flow engine, named connections, the self-improvement lifecycle\n"
            "(reflexion, dreaming, experience, self-harness, data engine),\n"
            "governed memory, web search, durable execution, and all 53 suites.\n\n"
            "[dim]Left off (enable later via `maverick init` advanced): computer/\n"
            "browser control, code execution, autonomous self-modification, at-rest\n"
            "encryption + multi-tenancy, messaging channels, and license enforcement\n"
            "(leaving enforcement off is what keeps every paid feature unlocked).[/dim]",
            border_style="cyan",
        )
    )

    if not preflight():
        console.print("[red]Setup can't continue.[/red] Fix the issues above and try again.")
        return 1

    user_name = (
        _q_text(
            "What should we call you?",
            default=os.environ.get("USER") or os.environ.get("USERNAME") or "",
        ).strip()
        or "you"
    )
    profile = pick_governance_profile()
    keys = _consumer_api_key()
    workdir = _q_text(
        "Where can Lightwork work?",
        default=str(Path.home() / "Documents" / "Maverick"),
    ).strip() or str(Path.home() / "Documents" / "Maverick")
    budget = _consumer_budget()

    Path(workdir).expanduser().mkdir(parents=True, exist_ok=True)
    backend = "docker" if _docker_available() else "local"
    # Express turns on every safe single-user feature; the governance level
    # layers its posture (signed audit, quotas, WORM, signed approvals) on
    # top. Union of two all-True dicts, so profile keys can only ADD.
    preset = GOVERNANCE_PROFILES.get(profile, GOVERNANCE_PROFILES[DEFAULT_GOVERNANCE_PROFILE])
    # Computer + browser are never enabled by express. If Docker is
    # unavailable, fail closed on host-mutating tools too: the local backend
    # runs against the user's host filesystem, not a container blast radius.
    denied_tools = ["computer", "browser"]
    if backend == "local":
        denied_tools.extend(["shell", "write_file", "apply_patch", "str_replace_editor"])
    try:
        write_config(
            providers=["anthropic"],
            role_models={},
            channels={},
            safety={
                "profile": "balanced",
                "scan_input": True,
                "scan_tool_calls": True,
                "scan_output": True,
            },
            budget=budget,
            sandbox={"backend": backend, "workdir": str(Path(workdir).expanduser()), "timeout": 60},
            keys=keys,
            # Host-mutating capabilities require explicit opt-in even in express.
            capabilities={
                "computer_use": False,
                "browser": False,
                "ros": False,
                "code_exec": False,
            },
            advanced={**_EXPRESS_ADVANCED, **preset["advanced"]},
            persona={"name": "Lightwork", "style": "balanced", "user_name": user_name},
            web_search_enabled=True,
            self_learning=dict(_EXPRESS_SELF_LEARNING),
            automation_import={"enable": True},
            event_triggers={"enable": True},
            flows={"enable": True},
            oauth={"vault": True},
            durable={"enabled": True},
            deployment="desktop",
            # Express historically wrote NO [retention] table, and absent
            # means keep-forever (retention is opt-in pruning). Preserve that
            # at the default level; only an explicitly-picked governance
            # upgrade may introduce pruning windows.
            retention=(
                dict(preset["retention"]) if profile != DEFAULT_GOVERNANCE_PROFILE else None
            ),
            governance_profile=profile,
            tool_acl={"denied_tools": denied_tools},
        )
    except Exception as e:
        show_install_failure(e)
        return 1

    console.print()
    if keys:
        console.print(
            Panel.fit(
                f"[bold green]Everything's on, {user_name}.[/bold green]\n\n"
                "Open the dashboard:\n"
                "  [bold]maverick dashboard[/bold]   web UI at http://127.0.0.1:8765\n\n"
                "[dim]Change anything in ~/.maverick/config.toml, or re-run "
                "maverick init.[/dim]",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel.fit(
                f"[bold yellow]Everything's on, but no API key yet, {user_name}.[/bold yellow]\n\n"
                "Add one by exporting ANTHROPIC_API_KEY or re-running "
                "[bold]maverick init[/bold].",
                border_style="yellow",
            )
        )
    _clear_partial()
    return 0


# (command, one-line description) surfaced after a regulated-posture setup, so a
# non-technical operator discovers the verification + GDPR/EU AI Act
# documentation commands they'd otherwise never find. Defined as data so a test
# can assert the set without rendering the Rich panel.
_COMPLIANCE_COMMANDS: list[tuple[str, str]] = [
    ("maverick enterprise verify", "check configured application-layer boundary controls"),
    ("maverick compliance", "GDPR + EU AI Act control coverage"),
    ("maverick ropa", "GDPR Art. 30 record-of-processing scaffold"),
    ("maverick dpia", "GDPR Art. 35 impact-assessment scaffold"),
    ("maverick ai-act", "EU AI Act risk classification"),
    ("maverick assess", "run a PIA / AIRA / vendor-risk assessment"),
    ("maverick hunt", "hunt the audit trail for agent attacks"),
    ("maverick remediate", "assess security posture + fix low-risk gaps"),
    ("maverick regulatory verify", "verify active obligations compile into non-vacuous gates enforced live"),
    ("maverick attest receipt mint", "hand an auditor a bearer-verifiable receipt for one decision"),
]


def _regulated_deployment(advanced: dict[str, Any]) -> bool:
    """True if the operator turned on a sensitive-data control, so the wizard
    should point them at the compliance + documentation commands."""
    advanced = advanced or {}
    return bool(
        advanced.get("enterprise")
        or advanced.get("compliance_profiles")
        or advanced.get("encrypt_at_rest")
        or advanced.get("audit_sign")
        or advanced.get("audit_worm")
        or advanced.get("dual_approval")
        or advanced.get("saml")
        or advanced.get("security_autofix")
    )


def show_compliance_commands(advanced: dict[str, Any]) -> None:
    """Print the compliance/documentation command panel after a regulated setup.

    No-op unless the deployment enabled a compliance profile or another
    regulated-data control such as enterprise mode, at-rest encryption, or
    audit signing -- otherwise it's just noise for a personal install.
    """
    if not _regulated_deployment(advanced):
        return
    rows = "\n".join(
        f"  [bold]{cmd}[/bold]{' ' * max(1, 28 - len(cmd))}# {desc}"
        for cmd, desc in _COMPLIANCE_COMMANDS
    )
    console.print()
    console.print(
        Panel.fit(
            "[bold]You enabled a regulated-data posture.[/bold] Verify configured "
            "controls and prepare documentation:\n\n"
            f"{rows}\n\n"
            "[dim]See docs/regulated-deployment.md. Control coverage, not legal advice.[/dim]",
            border_style="cyan",
            title="Compliance & documentation",
        )
    )


def _run_simple_picks(state: dict[str, Any], _announce) -> dict[str, Any]:
    """Run the contiguous block of single-answer ``state.get(x) or pick_x()``
    steps (safety through advanced), persisting each. Returns the answers."""
    _announce()
    safety = state.get("safety") or pick_safety()
    state["safety"] = safety
    _save_partial(state)

    _announce()
    signed_skills = state.get("signed_skills") or pick_signed_skills()
    state["signed_skills"] = signed_skills
    _save_partial(state)

    _announce()
    budget = state.get("budget") or pick_budget()
    state["budget"] = budget
    _save_partial(state)

    _announce()
    sandbox = state.get("sandbox") or pick_sandbox()
    state["sandbox"] = sandbox
    _save_partial(state)

    _announce()
    capabilities = state.get("capabilities") or pick_capabilities()
    state["capabilities"] = capabilities
    _save_partial(state)

    _announce()
    self_learning = state.get("self_learning") or pick_self_learning()
    state["self_learning"] = self_learning
    _save_partial(state)

    _announce()
    # Use an explicit None sentinel: {"enable": False} is a completed answer.
    ekko = state.get("ekko")
    if ekko is None:
        ekko = pick_ekko()
        state["ekko"] = ekko
        _save_partial(state)

    _announce()
    automation_import = state.get("automation_import") or pick_automation_import()
    state["automation_import"] = automation_import
    _save_partial(state)

    _announce()
    event_triggers = state.get("event_triggers") or pick_event_triggers()
    state["event_triggers"] = event_triggers
    _save_partial(state)

    _announce()
    flows = state.get("flows") or pick_flows()
    state["flows"] = flows
    _save_partial(state)

    _announce()
    knowledge = state.get("knowledge") or pick_knowledge()
    state["knowledge"] = knowledge
    _save_partial(state)

    _announce()
    oauth = state.get("oauth") or pick_oauth_vault()
    state["oauth"] = oauth
    _save_partial(state)

    _announce()
    governed_connectors = state.get("governed_connectors") or pick_governed_connectors()
    state["governed_connectors"] = governed_connectors
    _save_partial(state)

    _announce()
    durable = state.get("durable") or pick_durable()
    state["durable"] = durable
    _save_partial(state)

    _announce()
    workspace = state.get("workspace") or pick_workspace_journal()
    state["workspace"] = workspace
    _save_partial(state)

    _announce()
    finance = state.get("finance") or pick_finance()
    state["finance"] = finance
    _save_partial(state)

    # `is None` sentinel: declining the savings step legitimately returns {}.
    _announce()
    value = state.get("value")
    if value is None:
        value = pick_value()
        state["value"] = value
        _save_partial(state)

    # `is None` sentinel: keeping both defaults legitimately returns {}.
    _announce()
    assessments = state.get("assessments")
    if assessments is None:
        assessments = pick_assessments()
        state["assessments"] = assessments
        _save_partial(state)

    _announce()
    security_suite = state.get("security_suite")
    if security_suite is None:
        security_suite = pick_security_suite()
        state["security_suite"] = security_suite
        _save_partial(state)

    _announce()
    advanced = state.get("advanced") or pick_advanced()
    state["advanced"] = advanced
    _save_partial(state)

    return {
        "safety": safety,
        "signed_skills": signed_skills,
        "budget": budget,
        "sandbox": sandbox,
        "capabilities": capabilities,
        "self_learning": self_learning,
        "ekko": ekko,
        "automation_import": automation_import,
        "event_triggers": event_triggers,
        "flows": flows,
        "knowledge": knowledge,
        "oauth": oauth,
        "governed_connectors": governed_connectors,
        "durable": durable,
        "workspace": workspace,
        "finance": finance,
        "value": value,
        "assessments": assessments,
        "security_suite": security_suite,
        "advanced": advanced,
    }


def _run_plugin_picks(state: dict[str, Any], _announce, channels: dict[str, Any]) -> dict[str, Any]:
    """Run the plugin/ACL/policy block (mcp_servers through analytics).

    Uses the ``is None`` sentinel for steps whose legitimate answer is falsy.
    Returns the answers needed downstream by ``write_config``.
    """
    # NOTE: these steps use the `is None` sentinel (not `or`) because a
    # legitimately-declined answer is falsy ({}/[]); the `or` pattern treated
    # "I chose nothing" as "unanswered" and re-prompted it on --resume.
    _announce()
    mcp_servers = state.get("mcp_servers")
    if mcp_servers is None:
        mcp_servers = pick_mcp_servers()
        state["mcp_servers"] = mcp_servers
        _save_partial(state)

    _announce()
    plugins = state.get("plugins")
    if plugins is None:
        plugins = pick_plugins()
        state["plugins"] = plugins
        _save_partial(state)

    ts_plugins = state.get("ts_plugins")
    if ts_plugins is None:
        ts_plugins = pick_ts_plugins()
        state["ts_plugins"] = ts_plugins
        _save_partial(state)

    # Only ask about plugin permissions when at least one plugin is enabled --
    # most setups have none, so the step is skipped entirely.
    plugin_grant = state.get("plugin_grant")
    plugin_enforce = state.get("plugin_enforce", False)
    if plugins and plugin_grant is None:
        plugin_grant, plugin_enforce = pick_plugin_permissions()
        state["plugin_grant"] = plugin_grant
        state["plugin_enforce"] = plugin_enforce
        _save_partial(state)

    _announce()
    tool_acl = state.get("tool_acl")
    if tool_acl is None:
        tool_acl = pick_tool_acl(channels)
        state["tool_acl"] = tool_acl
        _save_partial(state)

    _announce()
    rate_limits = state.get("rate_limits")
    if rate_limits is None:
        rate_limits = pick_rate_limits(channels)
        state["rate_limits"] = rate_limits
        _save_partial(state)

    _announce()
    retention = state.get("retention")
    if retention is None:
        retention = pick_retention()
        state["retention"] = retention
        _save_partial(state)

    _announce()
    analytics = state.get("analytics")
    if analytics is None:
        analytics = pick_analytics()
        state["analytics"] = analytics
        _save_partial(state)

    return {
        "mcp_servers": mcp_servers,
        "plugins": plugins,
        "ts_plugins": ts_plugins,
        "plugin_grant": plugin_grant,
        "plugin_enforce": plugin_enforce,
        "tool_acl": tool_acl,
        "rate_limits": rate_limits,
        "retention": retention,
        "analytics": analytics,
    }


def run(fast: bool = False, resume: bool = False) -> int:
    if fast:
        return run_fast()
    # A non-interactive stdin (CI, Docker build, `... | maverick init`) can't
    # answer prompts -- questionary just prints "Input is not a terminal" and
    # the first prompt aborts with a terse "Aborted!". Detect it up front and
    # point at the paths that DO work without a TTY.
    if not sys.stdin.isatty():
        console.print(
            "[yellow]maverick init needs an interactive terminal.[/yellow]\n"
            "  - run it in a terminal, or\n"
            "  - use  [bold]maverick init --fast[/bold]  for recommended defaults, or\n"
            "  - edit  ~/.maverick/config.toml  by hand (see docs/configuration.md)."
        )
        return 1
    welcome()
    # Council round-2: mode picker on every launch. Consumer is default.
    # Skip the picker on --resume since it implies an in-progress
    # advanced flow.
    if not resume:
        mode = pick_mode()
        if mode == "consumer":
            return run_consumer()
        if mode == "express":
            return run_express()
    if not preflight():
        console.print(
            "[red]Preflight failed.[/red] Fix the issues above and re-run `maverick init`."
        )
        return 1

    # --resume: load any persisted partial state and only ask
    # questions the user hasn't answered yet.
    state: dict[str, Any] = {}
    if resume:
        loaded = _load_partial()
        if loaded:
            state = loaded
            console.print(
                f"[dim]Resuming from {PARTIAL_STATE_PATH}: "
                f"{len(state)} answers already on file.[/dim]\n"
            )
        else:
            console.print(
                f"[yellow]⚠[/yellow] No partial state at {PARTIAL_STATE_PATH}; starting fresh.\n"
            )

    # Progress bar: announce "Step N/M <label>" before each pick_*, with a
    # breadcrumb of steps already behind us. Purely cosmetic.
    _done: list[str] = []
    _step = [0]

    def _announce() -> None:
        _step[0] += 1
        console.print(_step_indicator(_step[0], done=_done), style="bold cyan")
        _done.append(STEPS[_step[0] - 1][1])

    _announce()
    deployment = state.get("deployment") or pick_deployment()
    state["deployment"] = deployment
    _save_partial(state)

    _announce()
    providers = state.get("providers") or pick_providers()
    while not providers:
        # Aborting on empty selection forced the user to restart the
        # whole wizard (UX seat finding). Re-ask instead.
        console.print("[yellow]Pick at least one provider; Lightwork needs an LLM.[/yellow]")
        providers = pick_providers()
    state["providers"] = providers
    _save_partial(state)

    _announce()
    role_models = state.get("role_models")
    if role_models is None:
        role_models = pick_models_per_role(providers)
        state["role_models"] = role_models
        _save_partial(state)

    _announce()
    channels_state = state.get("channels")
    if channels_state is None:
        channels, channel_envs = pick_channels(deployment)
        # JSON-safe: store envs as a sorted list.
        state["channels"] = channels
        state["channel_envs"] = sorted(channel_envs)
        _save_partial(state)
    else:
        channels = channels_state
        channel_envs = set(state.get("channel_envs") or [])

    _simple = _run_simple_picks(state, _announce)
    safety = _simple["safety"]
    signed_skills = _simple["signed_skills"]
    budget = _simple["budget"]
    sandbox = _simple["sandbox"]
    capabilities = _simple["capabilities"]
    self_learning = _simple["self_learning"]
    ekko = _simple["ekko"]
    automation_import = _simple["automation_import"]
    event_triggers = _simple["event_triggers"]
    flows = _simple["flows"]
    knowledge = _simple["knowledge"]
    oauth = _simple["oauth"]
    governed_connectors = _simple["governed_connectors"]
    durable = _simple["durable"]
    workspace = _simple["workspace"]
    finance = _simple["finance"]
    value = _simple["value"]
    assessments = _simple["assessments"]
    security_suite = _simple["security_suite"]
    advanced = _simple["advanced"]

    _announce()
    web_search_enabled, web_search_envs = state.get("_web_search_pair") or pick_web_search()
    state["_web_search_pair"] = [web_search_enabled, web_search_envs]
    _save_partial(state)

    _plugins_block = _run_plugin_picks(state, _announce, channels)
    mcp_servers = _plugins_block["mcp_servers"]
    plugins = _plugins_block["plugins"]
    ts_plugins = _plugins_block["ts_plugins"]
    plugin_grant = _plugins_block["plugin_grant"]
    plugin_enforce = _plugins_block["plugin_enforce"]
    tool_acl = _plugins_block["tool_acl"]
    rate_limits = _plugins_block["rate_limits"]
    retention = _plugins_block["retention"]
    analytics = _plugins_block["analytics"]

    _announce()
    persona = state.get("persona")
    if persona is None:
        persona = pick_persona()
        state["persona"] = persona
        _save_partial(state)

    _announce()
    notifications, notify_envs = state.get("_notifications_pair") or pick_notifications()
    state["_notifications_pair"] = [notifications, notify_envs]
    _save_partial(state)

    _announce()
    webhooks, webhook_envs = state.get("_webhooks_pair") or pick_webhooks()
    state["_webhooks_pair"] = [webhooks, webhook_envs]
    _save_partial(state)

    deliverables, deliverable_envs = state.get("_deliverables_pair") or pick_deliverable_handoff()
    state["_deliverables_pair"] = [deliverables, deliverable_envs]
    _save_partial(state)

    personas = state.get("_personas") or pick_persona_roles()
    state["_personas"] = personas
    _save_partial(state)

    _announce()
    a2a_cfg, a2a_envs = state.get("_a2a_pair") or pick_a2a()
    state["_a2a_pair"] = [a2a_cfg, a2a_envs]
    _save_partial(state)

    # Keys/sessions are never persisted to disk in the partial state
    # (they're secrets; the only safe place is ~/.maverick/.env).
    extra_envs = (
        set(web_search_envs)
        | set(notify_envs)
        | set(webhook_envs)
        | set(a2a_envs)
        | set(deliverable_envs)
    )
    keys = collect_api_keys(providers, channel_envs | extra_envs)
    # Enterprise connectors are always registered; collect any credentials the
    # user wants to wire up now (merged into ~/.maverick/.env, never persisted
    # to partial state). Editable later in the .env file.
    keys.update(pick_connectors())

    suites = pick_suites()

    license_cfg = state.get("license")
    if license_cfg is None:
        license_cfg = pick_license()
        state["license"] = license_cfg
        _save_partial(state)

    console.print()
    if not _q_confirm("Write config and finish?", default=True):
        # Be honest about where the state lives and what restore does.
        console.print(
            f"Stopped. Partial answers saved to {PARTIAL_STATE_PATH}.\n"
            "Resume with: maverick init --resume"
        )
        return 0

    write_config(
        providers,
        role_models,
        channels,
        safety,
        budget,
        sandbox,
        keys,
        capabilities,
        advanced=advanced,
        mcp_servers=mcp_servers,
        plugins=plugins,
        plugin_grant=plugin_grant,
        ts_plugins=ts_plugins,
        plugin_enforce=plugin_enforce,
        tool_acl=tool_acl,
        rate_limits=rate_limits,
        retention=retention,
        analytics=analytics,
        persona=persona,
        notifications=notifications,
        webhooks=webhooks,
        deliverables=deliverables,
        personas=personas,
        a2a=a2a_cfg,
        web_search_enabled=web_search_enabled,
        skills=signed_skills
        if (
            signed_skills.get("trusted_pubkeys")
            or signed_skills.get("require_signed")
            or signed_skills.get("require_signed_catalog")
        )
        else None,
        self_learning=self_learning if self_learning.get("enable") else None,
        # Persist the explicit default-off decision. Unlike default-on governed
        # learning, Ekko never inherits authority from another feature.
        ekko=ekko,
        automation_import=automation_import if automation_import.get("enable") else None,
        event_triggers=event_triggers if event_triggers.get("enable") else None,
        flows=flows if flows.get("enable") else None,
        knowledge=knowledge if knowledge.get("enable") else None,
        oauth=oauth if oauth.get("vault") else None,
        governed_connectors=governed_connectors if governed_connectors.get("enable") else None,
        durable=durable if durable.get("enabled") else None,
        workspace=workspace if workspace.get("journal") else None,
        finance=finance if finance.get("enable") else None,
        value=value or None,
        assessments=assessments or None,
        security_suite=security_suite,
        suites=suites,
        license_cfg=license_cfg or None,
        # The advanced flow applies no preset -- the operator hand-picked
        # every knob -- so record "custom" rather than omitting the label
        # (dashboards keyed on [governance].profile would otherwise read
        # advanced-configured tenants, the most governance-sensitive ones,
        # as tier-less) or borrowing a level name the knobs may not match.
        governance_profile="custom",
    )
    _clear_partial()
    ok = smoke_test()
    if ok:
        console.print()
        next_step = "maverick serve" if channels else 'maverick start "hello"'
        console.print(
            Panel.fit(
                "[bold green]Setup complete.[/bold green]\n\n"
                "Try:\n"
                f"  [bold]{next_step}[/bold]\n"
                "  [bold]maverick status[/bold]\n"
                "  [bold]maverick dashboard[/bold]    # web UI at http://127.0.0.1:8765\n\n"
                "[dim]The maverick command is also available as lightwork.[/dim]",
                border_style="green",
            )
        )
        show_compliance_commands(advanced)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
