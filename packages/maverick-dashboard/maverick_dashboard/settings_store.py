"""Dashboard Settings overlay for provider keys + capability/feature toggles.

Writes ``~/.maverick/dashboard-config.toml`` (config.dashboard_overrides_path),
which ``maverick.config.load_config`` deep-merges over config.toml — so a key
or toggle set in the UI takes effect on the next run without touching the
user's config.toml. (Models / budget / tool denials live in the separate
runtime-overrides.toml, read via their own hooks.)

Secrets: provider api_keys, webhook signing material, and channel credentials
are authenticated-encrypted with the deployment at-rest key before this file is
written (the file is also mode 0600). The kernel transparently opens those
values while loading the fixed dashboard overlay, so a key set here unblocks
goals immediately. The HTTP layer must NEVER echo a stored key back — callers
get a masked hint only (see ``state()``).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
from pathlib import Path

from maverick import config

# Serializes the overlay load-modify-save in-process; cross_process_lock in
# _locked() extends it across processes (multiple dashboard workers edit
# provider keys / toggles).
_SETTINGS_LOCK = threading.Lock()


class SecuritySuiteRevisionConflict(RuntimeError):
    """The admin edited an out-of-date deployment-wide security control."""


class SecuritySuiteConfigUnavailable(RuntimeError):
    """The security-suite overlay cannot be read without risking data loss."""


class ProviderRotationConflict(RuntimeError):
    """Provider authority changed or rejected a dashboard credential rotation."""


class ProviderConfigurationUnavailable(RuntimeError):
    """Provider authority could not be inspected or restored safely."""


class ResearchSettingsConflict(RuntimeError):
    """The administrator reviewed an older research execution profile."""


class ResearchSettingsUnavailable(RuntimeError):
    """A higher-precedence operator source owns the research profile."""


def _locked(*, strict: bool = False):
    from contextlib import ExitStack

    from maverick.authority_effects import authority_effect_fence
    from maverick.file_lock import cross_process_lock
    stack = ExitStack()
    try:
        stack.enter_context(authority_effect_fence())
        stack.enter_context(_SETTINGS_LOCK)
        stack.enter_context(
            cross_process_lock(config.dashboard_overrides_path(), strict=strict)
        )
    except BaseException:
        # ``ExitStack`` is not itself entered until the caller's ``with``. If
        # the second acquisition fails here, release the already-held
        # in-process lock instead of wedging every later settings mutation.
        stack.close()
        raise
    return stack


def _security_suite_revision(data: dict) -> int:
    metadata = data.get("security_suite_control")
    value = metadata.get("revision") if isinstance(metadata, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def security_suite_revision() -> int:
    """Return the monotonic CAS revision for the four suite control bits."""
    with _locked():
        return _security_suite_revision(_load_security_suite_overlay())

# Providers offered in the UI: name, label, env var(s) that also satisfy it,
# whether a base_url (self-hosted endpoint) is relevant.
PROVIDERS: list[dict] = [
    {"name": "anthropic", "label": "Anthropic (Claude)", "env": ["ANTHROPIC_API_KEY"], "base_url": False},
    {"name": "openai", "label": "OpenAI", "env": ["OPENAI_API_KEY"], "base_url": False},
    {"name": "gemini", "label": "Google Gemini", "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"], "base_url": False},
    {"name": "openrouter", "label": "OpenRouter", "env": ["OPENROUTER_API_KEY"], "base_url": False},
    {"name": "moonshot", "label": "Moonshot", "env": ["MOONSHOT_API_KEY"], "base_url": False},
    {"name": "deepseek", "label": "DeepSeek", "env": ["DEEPSEEK_API_KEY"], "base_url": False},
    {"name": "xai", "label": "xAI (Grok)", "env": ["XAI_API_KEY", "GROK_API_KEY"], "base_url": False},
    {"name": "ollama", "label": "Ollama (self-hosted)", "env": [], "base_url": True},
    {"name": "vllm", "label": "vLLM / OpenAI-compatible", "env": ["VLLM_BASE_URL", "OPENAI_COMPATIBLE_BASE_URL"], "base_url": True},
]
_PROVIDER_NAMES = {p["name"] for p in PROVIDERS}

# Mirror maverick.config.get_capabilities/get_features keys + defaults.
CAPABILITY_DEFAULTS = {
    "computer_use": False, "browser": False, "web_search": False,
    "mobile_tools": False, "ros": False, "code_exec": False,
}
FEATURE_DEFAULTS = {
    "workflow_readiness": True,
    "project_reviews": False,
    "outcome_measurement": False,
    "guided_onboarding": True,
    "action_center": True,
    "draft_recovery": True,
    "support_center": True,
    "record_search": True,
    "workspace_recovery": False,
    "signing_identity_transition": False,
    "skills": True,
    "world_model": True,
    "streaming": True,
    "pack_editing": True,
    "role_editing": True,
    "scheduling": True,
    "triggers": True,
}

# What each toggle actually turns on -- shown next to the checkbox so an
# admin knows what they are selecting before they select it.
CAPABILITY_INFO = {
    "computer_use": "Agents drive a real desktop inside the sandbox — click, "
                    "type, read the screen. The highest-impact capability; "
                    "enable only for workflows that need to use another app.",
    "browser": "Agents open web pages, follow links, and read page content. "
               "Your rules for external connections still apply.",
    "web_search": "Live web search for research tasks; results flow into the "
                  "agent's context with their sources.",
    "mobile_tools": "Agents use connected Android or iOS devices to tap, "
                    "swipe, and read screens for app testing and tasks.",
    "ros": "Agents exchange instructions and readings with connected robots. "
           "Requires an installed Robot Operating System connection.",
    "code_exec": "Agents run the code they write, inside the governed "
                 "work environment. Allowed actions are checked and recorded "
                 "before shell commands run.",
}
FEATURE_INFO = {
    "support_center": "Allow installation administrators to prepare, review and download redacted diagnostics in Help. Reports are never sent automatically.",
    "workflow_readiness": "Show available workflows, their supported boundaries, and the live or platform validation still required. Enablement never establishes customer acceptance.",
    "project_reviews": "Share selected, fixed task results with assigned workspace reviewers. Review does not execute or sign off the live task.",
    "outcome_measurement": "Plan a paired workflow pilot, record observed baseline and review effort, and compare current accepted outcomes. Unmeasured costs and unreviewed work remain explicit.",
    "guided_onboarding": "Offer a guided path to a reviewed deliverable, privacy assessment, or accepted vendor case. Task creation alone never establishes success.",
    "action_center": "Collect approvals, deliverable sign-offs, assessment reviews, and uncertain external actions in one personal list, with private read and snooze preferences. Overdue items remain visible.",
    "draft_recovery": "Keep encrypted recovery copies of unfinished editor work, private to each account and workspace. Credentials and attachments are excluded; restoring never runs or publishes work.",
    "record_search": "Find accessible vendors, evidence, assessments, decisions, tasks, and reports by name or reference. Searches use current metadata and disclose unavailable or partial sources.",
    "workspace_recovery": "Let global administrators export signed local workspace snapshots, verify and test them, and restore only in isolated local recovery mode. External stores and shared administration are excluded.",
    "signing_identity_transition": "Permit global administrators to explicitly review a dual-signed, exact-release publisher or update-key transition in isolated recovery mode. This changes installation trust for every local workspace. It never accepts an unrelated signer or bypasses Windows signature checks.",
    "skills": "Agents can use saved procedures from the Skills library "
              "while working on a task.",
    "world_model": "The shared operating record: goals, episodes, approvals, "
                   "and memory live in one governed store. Turning this off "
                   "stops information from carrying across tasks.",
    "streaming": "Show responses as they are written in Chat and task "
                 "activity. When off, responses appear when complete.",
    "pack_editing": "Edit and override domain packs from the dashboard. Turn "
                    "off to make pack editors read-only.",
    "role_editing": "Edit core agent role prompts, models, and effort from the "
                    "dashboard. Turn off to lock the role roster.",
    "scheduling": "Create recurring workflow schedules in Automations. "
                  "Existing schedules are managed from that page.",
    "triggers": "Start saved workflows when a connected service sends an "
                "authenticated event. Requires a configured signing secret.",
}

# The self-learning loop's subsystems -- ONE source of truth shared by the
# one-click toggle (set_learning writes each (section, config_key) to the
# overlay, which load_config deep-merges so every subsystem's enabled() sees it)
# and the /learning status view (which pairs each key with its live enabled()).
# Keeping both off one list stops the button and the view from drifting when a
# subsystem is added. Governed learning defaults ON; DGM is intentionally not
# in this registry and has its own high-risk production control.
LEARNING_SUBSYSTEMS: list[dict] = [
    {"key": "self_learning", "section": "self_learning", "config_key": "enable",
     "label": "Governed capability learning",
     "how": "[self_learning] enable — or MAVERICK_SELF_LEARNING=0 to opt out"},
    {"key": "distill_local", "section": "self_learning", "config_key": "distill_local",
     "label": "Local skill distillation",
     "how": "[self_learning] distill_local — or MAVERICK_DISTILL_LOCAL=0"},
    {"key": "failure_telemetry", "section": "telemetry", "config_key": "failure_modes",
     "label": "Local failure-mode telemetry",
     "how": "[telemetry] failure_modes or MAVERICK_FAILURE_TELEMETRY=0"},
    {"key": "budget_tuning", "section": "budget", "config_key": "self_tuning",
     "label": "Evidence-based budget tuning",
     "how": "[budget] self_tuning or MAVERICK_BUDGET_SELF_TUNING=0"},
    {"key": "consequence", "section": "consequence", "config_key": "enable",
     "label": "Grounded outcomes (reality feedback)",
     "how": "[consequence] enable — or MAVERICK_CONSEQUENCE=0"},
    {"key": "reflexion", "section": "reflexion", "config_key": "enable",
     "label": "Failure memory (learn from mistakes)",
     "how": "[reflexion] enable — or MAVERICK_REFLEXION=0"},
    {"key": "dreaming", "section": "dreaming", "config_key": "enable",
     "label": "Consolidation (distil skills + insights)",
     "how": "[dreaming] enable — or MAVERICK_DREAMING=0"},
    {"key": "experience", "section": "experience", "config_key": "enable",
     "label": "Experience-guided planning",
     "how": "[experience] enable — or MAVERICK_EXPERIENCE_GUIDANCE=0"},
    {"key": "skill_synthesis", "section": "skill_synthesis", "config_key": "enable",
     "label": "Task-specific skill synthesis",
     "how": "[skill_synthesis] enable — or MAVERICK_SKILL_SYNTHESIS=0"},
    {"key": "self_improvement", "section": "self_improvement", "config_key": "enable",
     "label": "Governed improvement promotion",
     "how": "[self_improvement] enable — or MAVERICK_SELF_IMPROVEMENT=0"},
    {"key": "prm_guidance", "section": "self_improvement", "config_key": "prm_guidance",
     "label": "Process-reward guidance",
     "how": "[self_improvement] prm_guidance or MAVERICK_PRM_GUIDANCE=0"},
    {"key": "capture", "section": "self_improvement", "config_key": "capture",
     "label": "Trajectory capture (fuel for the flywheel)",
     "how": "[self_improvement] capture — or MAVERICK_TRAJECTORY_CAPTURE=0"},
    {"key": "causal_promotion", "section": "self_improvement", "config_key": "causal_promotion",
     "label": "Causal promotion evidence",
     "how": "[self_improvement] causal_promotion — or MAVERICK_CAUSAL_PROMOTION=0"},
    {"key": "factory_learning", "section": "self_improvement", "config_key": "factory_learning",
     "label": "Self-improving agent factory",
     "how": "[self_improvement] factory_learning — or MAVERICK_FACTORY_LEARNING=0"},
    {"key": "evaluator_evolution", "section": "self_improvement", "config_key": "evaluator_evolution",
     "label": "Evaluator co-evolution",
     "how": "[self_improvement] evaluator_evolution — or MAVERICK_EVALUATOR_EVOLUTION=0"},
    {"key": "self_harness", "section": "self_harness", "config_key": "enable",
     "label": "Risk-limited self-harness",
     "how": "[self_harness] enable — or MAVERICK_SELF_HARNESS=0"},
    {"key": "data_engine", "section": "data_engine", "config_key": "enable",
     "label": "Improvement flywheel",
     "how": "[data_engine] enable — or MAVERICK_DATA_ENGINE=0"},
    {"key": "operations_scientist", "section": "operations_scientist", "config_key": "enable",
     "label": "Operations scientist",
     "how": "[operations_scientist] enable — or MAVERICK_OPERATIONS_SCIENTIST=0"},
    {"key": "rehearsal", "section": "rehearsal", "config_key": "enable",
     "label": "Pre-execution rehearsal",
     "how": "[rehearsal] enable — or MAVERICK_REHEARSAL=0"},
    {"key": "credit", "section": "credit", "config_key": "enable",
     "label": "Counterfactual agent credit",
     "how": "[credit] enable — or MAVERICK_CREDIT=0"},
    {"key": "reasoning_reward", "section": "reasoning_reward", "config_key": "enable",
     "label": "Structured reasoning rewards",
     "how": "[reasoning_reward] enable or MAVERICK_REASONING_REWARD=0"},
    {"key": "reward_audit", "section": "reasoning_reward", "config_key": "audit_rewards",
     "label": "Tamper-evident reward audit",
     "how": "[reasoning_reward] audit_rewards or MAVERICK_REASONING_REWARD_AUDIT=0"},
    {"key": "jit_rl", "section": "jit_rl", "config_key": "enable",
     "label": "JitRL test-time adaptation",
     "how": "[jit_rl] enable or MAVERICK_JIT_RL=0"},
]
LEARNING_TOGGLES = [(s["section"], s["config_key"]) for s in LEARNING_SUBSYSTEMS]
_LEARNING_SECTIONS = list(dict.fromkeys(s["section"] for s in LEARNING_SUBSYSTEMS))

# Channels offered in the UI: name + per-field spec. Keys MUST match what
# server.py's _wire_<name> reads from [channels.<name>] (verified against the
# wiring), so a value saved here actually configures the channel via the
# load_config() deep-merge. ``secret`` fields are masked + never echoed back;
# ``type: int`` fields are stored as numbers.
CHANNELS: list[dict] = [
    {"name": "telegram", "label": "Telegram", "fields": [
        {"key": "bot_token", "label": "Bot token", "secret": True},
    ]},
    {"name": "discord", "label": "Discord", "fields": [
        {"key": "bot_token", "label": "Bot token", "secret": True},
    ]},
    {"name": "slack", "label": "Slack", "fields": [
        {"key": "app_token", "label": "App-level token", "secret": True},
        {"key": "bot_token", "label": "Bot token", "secret": True},
    ]},
    {"name": "whatsapp_cloud", "label": "WhatsApp (Cloud API)", "fields": [
        {"key": "access_token", "label": "Access token", "secret": True},
        {"key": "phone_number_id", "label": "Phone number ID", "secret": False},
        {"key": "verify_token", "label": "Verify token", "secret": True},
        {"key": "app_secret", "label": "App secret", "secret": True},
        {"key": "port", "label": "Webhook port", "secret": False, "type": "int"},
    ]},
    {"name": "sms", "label": "SMS (Twilio)", "fields": [
        {"key": "account_sid", "label": "Account SID", "secret": False},
        {"key": "auth_token", "label": "Auth token", "secret": True},
        {"key": "from_number", "label": "From number", "secret": False},
        {"key": "port", "label": "Webhook port", "secret": False, "type": "int"},
    ]},
    {"name": "email", "label": "Email (IMAP/SMTP)", "fields": [
        {"key": "imap_host", "label": "IMAP host", "secret": False},
        {"key": "imap_user", "label": "IMAP user", "secret": False},
        {"key": "imap_password", "label": "IMAP password", "secret": True},
        {"key": "smtp_host", "label": "SMTP host", "secret": False},
        {"key": "smtp_user", "label": "SMTP user", "secret": False},
        {"key": "smtp_password", "label": "SMTP password", "secret": True},
        {"key": "smtp_port", "label": "SMTP port", "secret": False, "type": "int"},
    ]},
]
_CHANNELS_BY_NAME = {c["name"]: c for c in CHANNELS}


def _tomllib():
    try:
        import tomllib  # 3.11+
    except ModuleNotFoundError:  # 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    return tomllib


def _unseal_overlay(data: dict) -> dict:
    """Open authenticated dashboard ciphertext without mutating parsed TOML."""
    # Reuse the config loader's bounded tree contract.  It performs a cheap
    # marker preflight, authenticates every token, and forces the deployment
    # key so an ambient tenant cannot change which key opens global settings.
    return config._unseal_config_tree(data)  # type: ignore[return-value]


def _seal_global_secret(value: object) -> str:
    """Return one deployment-key ciphertext, preserving existing ciphertext."""
    from maverick.crypto_at_rest import (
        deployment_key_scope,
        is_sealed_str,
        seal_to_str,
    )

    text = str(value)
    if is_sealed_str(text):
        return text
    with deployment_key_scope():
        return seal_to_str(text)


def load_overlay() -> dict:
    p = config.dashboard_overrides_path()
    if not p.exists():
        return {}
    try:
        with open(p, "rb") as f:
            return _unseal_overlay(_tomllib().load(f))
    except (OSError, ValueError):
        return {}


def _load_overlay_for_update() -> dict:
    """Strict overlay read for every load-modify-write operation.

    The general Settings UI historically treats a malformed optional overlay as
    empty. A mutation cannot: writing a newly constructed table over unreadable
    state would delete unrelated provider, channel, feature, and security-suite
    settings. Missing remains the one legitimate empty state.
    """
    path = config.dashboard_overrides_path()
    try:
        with open(path, "rb") as handle:
            value = _tomllib().load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SecuritySuiteConfigUnavailable(
            "dashboard settings are unreadable; settings were not changed"
        ) from exc
    if not isinstance(value, dict):  # defensive: TOML roots are mappings
        raise SecuritySuiteConfigUnavailable(
            "dashboard settings are unreadable; settings were not changed"
        )
    return value


def _load_security_suite_overlay() -> dict:
    """Compatibility name for the strict security-suite CAS read."""
    return _load_overlay_for_update()


def _toml_str(value: str) -> str:
    return json.dumps(str(value))  # JSON strings are valid TOML basic strings


def _toml_value(value) -> str:
    """Render the bounded TOML value types load_overlay can return.

    Security connector selections and vendor extension settings may be arrays
    or inline tables. Coercing them through ``str()`` silently changed their
    type on every unrelated feature-toggle save.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        entries = (
            f"{_toml_str(str(key))} = {_toml_value(item)}"
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
        return "{ " + ", ".join(entries) + " }"
    raise TypeError(f"unsupported dashboard setting value: {type(value).__name__}")


def _dump_value(lines: list[str], val: dict) -> None:
    """Emit [value] -- the client's cost/value assumptions for the Savings
    page (hourly rate, hours per task, optional per-department overrides).
    Read back by maverick.config.get_value via the same deep-merge as
    everything else."""
    if not val:
        return
    lines.append("[value]")
    if "enable" in val:
        lines.append(f"enable = {'true' if val['enable'] else 'false'}")
    for k in ("hourly_rate", "hours_per_task"):
        if k in val:
            lines.append(f"{k} = {float(val[k])}")
    if val.get("currency"):
        lines.append(f"currency = {_toml_str(val['currency'])}")
    lines.append("")
    for name in sorted(val.get("departments") or {}):
        ov = val["departments"][name] or {}
        if not ov:
            continue
        lines.append(f"[value.departments.{_toml_str(name)}]")
        for k in ("hourly_rate", "hours_per_task"):
            if k in ov:
                lines.append(f"{k} = {float(ov[k])}")
        lines.append("")


def _dump_selector_sections(lines: list[str], data: dict) -> None:
    """Emit the single-key selector tables the dashboard owns: [dashboard]
    active_tenant (set_active_tenant) and [sandbox] backend
    (set_sandbox_backend). _dump is an allowlist serializer, so a key missing
    here would be silently dropped by the next save."""
    dash = data.get("dashboard") or {}
    if dash.get("active_tenant"):
        lines.append("[dashboard]")
        lines.append(f"active_tenant = {_toml_str(dash['active_tenant'])}")
        lines.append("")
    sandbox = data.get("sandbox") or {}
    if sandbox.get("backend"):
        lines.append("[sandbox]")
        lines.append(f"backend = {_toml_str(sandbox['backend'])}")
        lines.append("")


def _dump_plugins(lines: list[str], plugins: dict) -> None:
    """Emit [plugins] -- the dashboard-install allowlist + opt-in switch
    (set_installable_plugins / set_plugin_install_allowed)."""
    if not plugins:
        return
    lines.append("[plugins]")
    if "allow_install" in plugins:
        lines.append(
            f"allow_install = {'true' if plugins['allow_install'] else 'false'}"
        )
    if plugins.get("installable"):
        lines.append(f"installable = {_toml_value(list(plugins['installable']))}")
    lines.append("")


def _dump(data: dict) -> str:
    lines = [
        "# Dashboard-managed settings overlay (provider keys + capability/feature",
        "# toggles). Edit via the dashboard Settings page, not by hand. Your",
        "# config.toml is never touched by the dashboard.",
        "",
    ]
    # "flows" carries the autonomous self-improvement knobs (auto_evolve/auto_apply,
    # both booleans) -- persisted here so set_flow_autonomy survives a restart.
    # _MODULE_TOP_SECTIONS: the governance-module tables set_module_enables
    # writes (nested subsection dicts serialize as inline tables, which
    # deep-merge identically to [a.b] headers). dict.fromkeys dedupes: a
    # section emitted twice is a duplicate TOML table, which invalidates the
    # WHOLE overlay file.
    for section in dict.fromkeys((
                    "capabilities", "features", "flows", "self_modify", "ekko",
                    "security_ops", "threat_hunt", "env_hunt",
                    "security_suite_control", "oauth", "workspace",
                    "research_execution", "models", "catalogs",
                    *COMPLIANCE_SETTING_SECTIONS,
                    *_LEARNING_SECTIONS, *_MODULE_TOP_SECTIONS,
                    *SWITCHABLE_SECTIONS)):
        tbl = data.get(section) or {}
        if tbl:
            lines.append(f"[{section}]")
            for k in sorted(tbl):
                lines.append(f"{k} = {_toml_value(tbl[k])}")
            lines.append("")
    _dump_selector_sections(lines, data)
    _dump_plugins(lines, data.get("plugins") or {})
    _dump_value(lines, data.get("value") or {})
    # [model_cost_tiers] -- manager-set cost bands per model. Keys are model
    # ids that can contain '/' or '.', so they are always quoted.
    tiers = data.get("model_cost_tiers") or {}
    if tiers:
        lines.append("[model_cost_tiers]")
        for model in sorted(tiers):
            lines.append(f"{_toml_str(model)} = {_toml_str(str(tiers[model]))}")
        lines.append("")
    # [webhooks] secret -- the signing key the Settings page manages. Sealed
    # before persistence and never logged or re-displayed.
    whk = data.get("webhooks") or {}
    if whk.get("secret"):
        lines.append("[webhooks]")
        lines.append(f"secret = {_toml_str(_seal_global_secret(whk['secret']))}")
        lines.append("")
    for name in sorted(data.get("providers") or {}):
        pcfg = data["providers"][name] or {}
        fields = [(f, pcfg.get(f)) for f in ("api_key", "base_url") if pcfg.get(f)]
        if not fields:
            continue
        lines.append(f"[providers.{name}]")
        for field, val in fields:
            if field == "api_key":
                val = _seal_global_secret(val)
            lines.append(f"{field} = {_toml_str(val)}")
        lines.append("")
    for name in sorted(data.get("channels") or {}):
        ccfg = data["channels"][name] or {}
        if not ccfg:
            continue
        lines.append(f"[channels.{name}]")
        lines.append(f"enabled = {'true' if ccfg.get('enabled') else 'false'}")
        secret_fields = {
            field["key"]
            for field in (_CHANNELS_BY_NAME.get(name) or {}).get("fields", [])
            if field.get("secret")
        }
        for k in sorted(ccfg):
            if k == "enabled":
                continue
            v = ccfg[k]
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, int):
                lines.append(f"{k} = {v}")
            else:
                if k in secret_fields:
                    v = _seal_global_secret(v)
                lines.append(f"{k} = {_toml_str(v)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write(data: dict) -> None:
    # Unique temp + os.replace (0600): the fixed ".toml.tmp" collided between two
    # concurrent workers. RMW serialization is in the mutators via _locked().
    from maverick.file_lock import atomic_write_text
    atomic_write_text(config.dashboard_overrides_path(), _dump(data))


def set_provider(name: str, api_key: str | None = None, base_url: str | None = None) -> None:
    """Set a provider's api_key/base_url. Empty/None values are left unchanged
    (so re-saving a base_url never wipes a key you can't see). Use
    ``clear_provider`` to remove."""
    if name not in _PROVIDER_NAMES:
        raise ValueError("unknown provider")
    with _locked():
        data = _load_overlay_for_update()
        pcfg = data.setdefault("providers", {}).setdefault(name, {})
        if api_key and api_key.strip():
            pcfg["api_key"] = api_key.strip()
        if base_url and base_url.strip():
            pcfg["base_url"] = base_url.strip()
        if not pcfg:
            data["providers"].pop(name, None)
        _write(data)


def _provider_authority_fingerprint(name: str) -> str:
    """Digest only provider sources that outrank the dashboard overlay.

    The digest is deliberately one-way because those source tables can contain
    credentials.  It binds a rotation to the operator/tenant generations that
    were authorized without ever returning or logging their values.
    """
    import hashlib

    # Resolve installer-persisted environment before reading interpolated
    # operator material, matching provider_configuration_state().
    config._load_wizard_env()
    sources: list[tuple[str, Path]] = []
    operator = os.environ.get(config.CONFIG_OVERLAY_ENV, "").strip()
    if operator:
        sources.append(("operator", Path(operator).expanduser()))
    tenant = config.tenant_config_path()
    if tenant is not None:
        sources.append(("tenant", tenant))

    authority: list[dict] = []
    for source, path in sources:
        loaded = config._load_config_file(path)
        providers = loaded.get("providers") if isinstance(loaded, dict) else None
        if providers is not None and not isinstance(providers, dict):
            raise ProviderConfigurationUnavailable(
                f"{source} provider configuration is invalid"
            )
        table = providers.get(name) if isinstance(providers, dict) else None
        if (
            isinstance(providers, dict)
            and name in providers
            and not isinstance(table, dict)
        ):
            raise ProviderConfigurationUnavailable(
                f"{source} provider configuration is invalid"
            )
        authority.append(
            {
                "source": source,
                "path": str(path.absolute()),
                "provider": table,
            }
        )
    encoded = json.dumps(
        authority,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_provider_rotation(
    *,
    authority_expected: str,
    authority_after: str,
    authority_after_confirm: str,
    observed_state: dict,
    observed: object,
    key_value: str,
    base_value: str,
) -> None:
    """Reject a post-commit generation that is not the authorized one."""
    import hashlib
    import hmac

    if observed_state["source_errors"]:
        raise ProviderConfigurationUnavailable(
            "provider configuration became unreadable during rotation"
        )
    if not isinstance(observed, dict):
        raise ProviderConfigurationUnavailable(
            "effective provider configuration is invalid"
        )
    if not (
        authority_expected == authority_after == authority_after_confirm
    ):
        raise ProviderRotationConflict(
            "provider configuration changed while the rotation was committing"
        )
    if key_value:
        expected_digest = hashlib.sha256(key_value.encode("utf-8")).digest()
        observed_digest = hashlib.sha256(
            str(observed.get("api_key") or "").strip().encode("utf-8")
        ).digest()
        if not hmac.compare_digest(expected_digest, observed_digest):
            raise ProviderRotationConflict(
                "provider key is not the effective credential; review the active source"
            )
    if base_value and str(observed.get("base_url") or "").strip() != base_value:
        raise ProviderRotationConflict(
            "provider base URL is not the effective endpoint; review the active source"
        )


def _restore_provider_overlay(
    *,
    path: Path,
    existed: bool,
    previous_bytes: bytes | None,
    previous_mode: int | None,
) -> None:
    """Restore the exact pre-rotation dashboard generation under its lock."""
    if existed:
        if previous_bytes is None or previous_mode is None:
            raise ProviderConfigurationUnavailable(
                "provider rollback state is unavailable"
            )
        from maverick.file_lock import atomic_write_bytes

        atomic_write_bytes(path, previous_bytes, mode=previous_mode)
    else:
        path.unlink(missing_ok=True)
    config.reset_config_cache()


def rotate_provider(
    name: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict:
    """Atomically publish a provider rotation only when it becomes effective.

    The entire dashboard read/modify/verify/rollback transaction holds the
    settings lock.  A one-way fingerprint detects an operator or tenant source
    changing while the transaction is in flight.  Any rejected post-write
    verification restores the exact preceding overlay generation before this
    function returns.
    """
    import copy
    if name not in _PROVIDER_NAMES:
        raise ValueError("unknown provider")
    key_value = str(api_key or "").strip()
    base_value = str(base_url or "").strip()
    if not key_value and not base_value:
        raise ValueError("enter a provider value to save")

    try:
        # Provider credentials are security-sensitive shared state. Refuse the
        # rotation when the filesystem cannot provide a real cross-process
        # lock; a process-local fallback could let two workers clobber a
        # successfully verified credential generation.
        with _locked(strict=True):
            overlay_path: Path | None = None
            overlay_existed = False
            before_overlay: dict | None = None
            before_bytes: bytes | None = None
            before_mode: int | None = None
            mutation_attempted = False
            try:
                overlay_path = config.dashboard_overrides_path()
                overlay_existed = overlay_path.exists()
                if overlay_existed:
                    import stat

                    before_bytes = overlay_path.read_bytes()
                    before_mode = stat.S_IMODE(overlay_path.stat().st_mode)
                before_overlay = _load_overlay_for_update()
                # Keep the sealed generation for byte-equivalent rollback, but
                # authenticate every ciphertext before changing anything. A
                # corrupt provider or unrelated secret must not be overwritten
                # into an apparently healthy generation by a rotation.
                _unseal_overlay(copy.deepcopy(before_overlay))

                config.reset_config_cache()
                authority_before = _provider_authority_fingerprint(name)
                before = config.provider_configuration_state(name)
                authority_confirm = _provider_authority_fingerprint(name)
                if before["source_errors"]:
                    raise ProviderConfigurationUnavailable(
                        "provider configuration is unreadable; repair the active "
                        "config source before rotating credentials"
                    )
                if authority_before != authority_confirm:
                    raise ProviderRotationConflict(
                        "provider configuration changed while the rotation was being authorized"
                    )

                blockers = []
                if key_value and not before["dashboard_can_set_api_key"]:
                    blockers.append(
                        f"API key is controlled by {before['api_key_source']}"
                    )
                if base_value and not before["dashboard_can_set_base_url"]:
                    blockers.append(
                        f"base URL is controlled by {before['base_url_source']}"
                    )
                if blockers:
                    raise ProviderRotationConflict(
                        "; ".join(blockers) + "; rotate that source instead"
                    )

                data = copy.deepcopy(before_overlay)
                provider_table = data.setdefault("providers", {}).setdefault(name, {})
                if key_value:
                    provider_table["api_key"] = key_value
                if base_value:
                    provider_table["base_url"] = base_value

                # Recheck immediately before commit. The post-commit check below is
                # still required because higher-authority files do not share this
                # dashboard lock.
                if authority_confirm != _provider_authority_fingerprint(name):
                    raise ProviderRotationConflict(
                        "provider configuration changed before the rotation could commit"
                    )
                # atomic_write_text may replace the file before a later flush
                # or directory-sync error is reported. Treat any attempted
                # write as published so every failure takes the rollback path.
                mutation_attempted = True
                _write(data)
                config.reset_config_cache()

                authority_after = _provider_authority_fingerprint(name)
                observed_state = config.provider_configuration_state(name)
                observed = config.get_provider_config(name) or {}
                authority_after_confirm = _provider_authority_fingerprint(name)
                _verify_provider_rotation(
                    authority_expected=authority_confirm,
                    authority_after=authority_after,
                    authority_after_confirm=authority_after_confirm,
                    observed_state=observed_state,
                    observed=observed,
                    key_value=key_value,
                    base_value=base_value,
                )
                return observed_state
            except Exception as exc:
                if mutation_attempted:
                    try:
                        if overlay_path is None or before_overlay is None:
                            raise ProviderConfigurationUnavailable(
                                "provider rollback state is unavailable"
                            )
                        _restore_provider_overlay(
                            path=overlay_path,
                            existed=overlay_existed,
                            previous_bytes=before_bytes,
                            previous_mode=before_mode,
                        )
                    except Exception as rollback_exc:
                        raise ProviderConfigurationUnavailable(
                            "provider rotation failed and the previous dashboard "
                            "configuration could not be restored"
                        ) from rollback_exc
                if isinstance(
                    exc,
                    (ProviderConfigurationUnavailable, ProviderRotationConflict),
                ):
                    raise
                raise ProviderConfigurationUnavailable(
                    "provider configuration is unavailable; the rotation was "
                    "not committed"
                ) from exc
    except (ProviderConfigurationUnavailable, ProviderRotationConflict):
        raise
    except Exception as exc:
        # Lock acquisition, path inspection, and any failure before the inner
        # transaction are still recoverable configuration outages to the API,
        # never raw 500s.
        raise ProviderConfigurationUnavailable(
            "provider configuration is unavailable; settings were not changed"
        ) from exc


def clear_provider(name: str) -> None:
    if name not in _PROVIDER_NAMES:
        raise ValueError("unknown provider")
    with _locked():
        data = _load_overlay_for_update()
        (data.get("providers") or {}).pop(name, None)
        _write(data)


def set_webhooks_secret(secret: str | None) -> None:
    """Set (or clear, when empty) the inbound/outbound webhook signing secret
    in the dashboard overlay -- the same [webhooks] secret knob operators set
    in config; the MAVERICK_WEBHOOK_SECRET env var still takes precedence."""
    with _locked():
        data = _load_overlay_for_update()
        cleaned = (secret or "").strip()
        if cleaned:
            data.setdefault("webhooks", {})["secret"] = cleaned
        else:
            (data.get("webhooks") or {}).pop("secret", None)
            if not data.get("webhooks"):
                data.pop("webhooks", None)
        _write(data)


def set_model_discovery(enabled: bool) -> None:
    """Turn automatic model lists ([models] discovery) on or off in the overlay.

    The MAVERICK_MODEL_DISCOVERY environment variable still wins; the Settings
    page only offers this switch when no such override is present."""
    with _locked():
        data = _load_overlay_for_update()
        models = data.setdefault("models", {})
        if not isinstance(models, dict):
            raise SecuritySuiteConfigUnavailable(
                "dashboard settings are unreadable; settings were not changed"
            )
        models["discovery"] = bool(enabled)
        _write(data)


def set_catalog_indexes(urls) -> None:
    """Replace the skill-catalog addresses ([catalogs] indexes) in the overlay.

    Each entry must be a plain https web address (no credentials in it); at
    most five. An empty list removes the key so the built-in catalog is used
    again. Installing from any catalog still needs the admin role and the
    server's skill-install opt-in; this only changes what the Store lists."""
    from urllib.parse import urlsplit

    cleaned: list[str] = []
    for raw in urls or []:
        url = str(raw or "").strip().rstrip("/")
        if not url:
            continue
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username
                or parts.password or len(url) > 300 or any(c.isspace() for c in url)):
            raise ValueError("catalog addresses must be plain https web addresses")
        if url not in cleaned:
            cleaned.append(url)
    if len(cleaned) > 5:
        raise ValueError("at most five catalog addresses")
    with _locked():
        data = _load_overlay_for_update()
        catalogs = data.setdefault("catalogs", {})
        if not isinstance(catalogs, dict):
            raise SecuritySuiteConfigUnavailable(
                "dashboard settings are unreadable; settings were not changed"
            )
        if cleaned:
            catalogs["indexes"] = cleaned
        else:
            catalogs.pop("indexes", None)
        _write(data)


def set_toggle(section: str, name: str, enabled: bool) -> None:
    """Override a [capabilities] or [features] flag in the overlay."""
    set_toggles(section, {name: enabled})


def set_toggles(section: str, updates: dict[str, bool]) -> None:
    """Commit a complete group of flag changes in one atomic overlay write."""
    defaults = {"capabilities": CAPABILITY_DEFAULTS, "features": FEATURE_DEFAULTS}.get(section)
    if defaults is None or not isinstance(updates, dict) or any(name not in defaults for name in updates):
        raise ValueError("unknown setting")
    clean = {name: bool(enabled) for name, enabled in updates.items()}
    with _locked():
        data = _load_overlay_for_update()
        flags = data.setdefault(section, {})
        if not isinstance(flags, dict):
            raise SecuritySuiteConfigUnavailable(
                "dashboard settings are unreadable; settings were not changed"
            )
        flags.update(clean)
        _write(data)


def _research_settings_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def research_execution_state() -> dict:
    """Return a non-secret, exact-revision view of the global research profile."""
    config.reset_config_cache()
    config.load_global_config()
    if config.config_source_errors(include_tenant=False):
        return {"settings_sha256": "", "configuration": {},
                "writable": False, "source": "unavailable"}
    base = config._load_config_file(config.config_path())
    overlay = load_overlay()
    # This security-sensitive table is a complete installation-owned profile.
    # Nested deep merge would make deleting a base-config evaluator impossible;
    # an explicit dashboard table therefore replaces the base table wholesale.
    raw = (overlay["research_execution"] if "research_execution" in overlay
           else base.get("research_execution", {}))
    source = "dashboard" if "research_execution" in overlay else "base"
    writable = True
    operator_overlay = os.environ.get(config.CONFIG_OVERLAY_ENV)
    if operator_overlay:
        operator = config._load_config_file(Path(operator_overlay).expanduser())
        if "research_execution" in operator:
            raw = operator["research_execution"]
            source = "operator_overlay"
            writable = False
    if not isinstance(raw, dict):
        raw = {"invalid": True}
    return {"settings_sha256": _research_settings_sha256(raw), "configuration": raw,
            "writable": writable, "source": source}


def set_research_execution(value: dict, *, expected_settings_sha256: str) -> dict:
    """Persist one validated global research profile in the dashboard overlay."""
    from dataclasses import asdict

    from maverick.research_jobs import ResearchProfile

    if (type(expected_settings_sha256) is not str or len(expected_settings_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_settings_sha256)):
        raise ValueError("invalid research settings revision")
    if not isinstance(value, dict):
        raise ValueError("invalid research execution settings")
    if value.get("enable") is True:
        profile = ResearchProfile.from_config(value)
        clean = {"enable": True, "image_id": profile.image_id,
                 "queue_seconds": profile.queue_seconds,
                 "evaluators": {item.id: {"version": item.version,
                                             "stdout_sha256": item.stdout_sha256}
                                for item in profile.evaluators},
                 "limits": asdict(profile.limits)}
    elif value == {"enable": False}:
        clean = {"enable": False}
    else:
        raise ValueError("disabled research settings may contain only enable=false")
    with _locked(strict=True):
        config.reset_config_cache()
        data = _load_overlay_for_update()
        operator_overlay = os.environ.get(config.CONFIG_OVERLAY_ENV)
        if operator_overlay and "research_execution" in config._load_config_file(
                Path(operator_overlay).expanduser()):
            raise ResearchSettingsUnavailable(
                "Research execution is owned by the operator config overlay")
        current = (data["research_execution"] if "research_execution" in data
                   else config._load_config_file(config.config_path()).get(
                       "research_execution", {}))
        if not hmac.compare_digest(expected_settings_sha256, _research_settings_sha256(current)):
            raise ResearchSettingsConflict("Research execution settings changed; reload before saving")
        data["research_execution"] = clean
        _write(data)
        config.reset_config_cache()
    return research_execution_state()


# Platform systems whose [section] enable flag an admin may switch from
# inside the app -- the single authority the /features/switches API trusts.
SWITCHABLE_SECTIONS: tuple[str, ...] = (
    "dreaming", "self_improvement", "self_harness", "fleet_memory",
    "rehearsal", "flows", "threat_hunt", "env_hunt", "entity_graph",
)


def set_section_enable(section: str, enabled: bool) -> None:
    """Overlay one switchable platform system's ``enable`` flag."""
    if section not in SWITCHABLE_SECTIONS:
        raise ValueError("unknown setting")
    with _locked():
        data = _load_overlay_for_update()
        data.setdefault(section, {})["enable"] = bool(enabled)
        _write(data)


# Compliance controls the /compliance page can strengthen from inside the app.
# Only these exact (section, key) pairs are writable, and only to the value
# types listed; nothing here can turn a protection off except retention
# periods, which only accept whole days within the bounds below.
COMPLIANCE_SETTINGS: dict[tuple[str, str], type] = {
    ("audit", "sign"): bool,
    ("privacy", "anonymous"): bool,
    ("encryption", "at_rest"): bool,
    ("enterprise", "mode"): bool,
    ("compliance", "disclosure_text"): str,
    ("compliance", "profiles"): list,
    ("retention", "audit_days"): int,
    ("retention", "episodes_days"): int,
    ("retention", "events_days"): int,
    ("retention", "messages_days"): int,
    ("retention", "usage_days"): int,
}
COMPLIANCE_SETTING_SECTIONS: tuple[str, ...] = tuple(
    dict.fromkeys(section for section, _key in COMPLIANCE_SETTINGS)
)
_RETENTION_DAY_BOUNDS = (1, 3650)
_PROFILE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def set_compliance_settings(updates: dict[tuple[str, str], object]) -> None:
    """Write a bounded group of compliance settings in one overlay save."""
    if not isinstance(updates, dict) or not updates:
        raise ValueError("unknown setting")
    clean: dict[tuple[str, str], object] = {}
    for pair, value in updates.items():
        kind = COMPLIANCE_SETTINGS.get(pair)
        if kind is None:
            raise ValueError("unknown setting")
        if kind is bool:
            if value is not True:
                # Protections can only be switched on from the dashboard.
                raise ValueError("unknown setting")
        elif kind is list:
            # Regulation keys only; callers must pass a superset of what is
            # already active (checked where the active set is known).
            if (
                not isinstance(value, list)
                or not value
                or len(value) > 16
                or any(
                    not isinstance(item, str) or not _PROFILE_KEY.fullmatch(item)
                    for item in value
                )
            ):
                raise ValueError("regulation list is invalid")
            value = list(dict.fromkeys(value))
        elif kind is int:
            low, high = _RETENTION_DAY_BOUNDS
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError("retention days are out of range")
        elif not isinstance(value, str) or not value.strip() or len(value) > 1000:
            raise ValueError("text setting is empty or too long")
        clean[pair] = value
    with _locked():
        data = _load_overlay_for_update()
        for (section, key), value in clean.items():
            table = data.setdefault(section, {})
            if not isinstance(table, dict):
                raise SecuritySuiteConfigUnavailable(
                    "dashboard settings are unreadable; settings were not changed"
                )
            table[key] = value
        _write(data)
    config.reset_config_cache()


# Governance-module enable flags an admin may switch from inside the app --
# each entry is a dotted path whose final table carries the ``enable``
# boolean the module's fail-closed gate reads (e.g.
# "consent_preferences.publishing" -> [consent_preferences.publishing]
# enable). The single authority the /features/switches API trusts for
# module switching; anything not listed stays config-file-only.
MODULE_ENABLE_PATHS: frozenset[str] = frozenset({
    "connections",
    "ai_inventory",
    "ai_entity_tasks",
    "governed_document_vault",
    "tech_risk_compliance.grc_studio",
    "third_party_management.contracts",
    "vendor_onboarding",
    "ai_vendor_risk",
    "model_risk_assurance",
    "evidence_graph",
    "ai_evidence_analysis",
    "assessment_evidence_autopopulation",
    "consent_preferences",
    "consent_preferences.analytics",
    "consent_preferences.application_scanner",
    "consent_preferences.bulk_jobs",
    "consent_preferences.delivery",
    "consent_preferences.experiments",
    "consent_preferences.global_opt_out",
    "consent_preferences.native_sdk",
    "consent_preferences.profile_data",
    "consent_preferences.profile_data.custom_index",
    "consent_preferences.public_api",
    "consent_preferences.publishing",
    "consent_preferences.publishing.web_settings",
    "consent_preferences.scanner",
    "consent_preferences.scanner.notice_proposals",
    "consent_preferences.trust_center",
    "consent_registry",
    "data_map",
    "data_use_governance",
    "data_use_governance.compiler",
    "data_use_governance.databricks",
    "data_use_governance.enforcement",
    "data_use_governance.snowflake",
    "dsar_fulfillment",
    "external_agents",
    "finance_operations",
    "governance_agents",
    "privacy_automation",
    "privacy_automation.data_discovery",
    "privacy_automation.notice_governance",
    "privacy_automation.regulatory_intelligence",
    "privacy_automation.rights_consumer_delivery",
    "privacy_automation.rights_orchestration",
    "tech_risk_compliance",
    "tech_risk_compliance.continuous_evidence",
    "tech_risk_compliance.policy_publication_links",
    "tech_risk_compliance.scope_actions",
    "tech_risk_compliance.scoping",
    "third_party_management",
    "third_party_management.executive_reports",
    "third_party_management.intelligence",
    "third_party_management.lifecycle",
    "third_party_management.lifecycle.portal",
    "vendor_management",
})

# Top-level tables the module paths live under; _dump is an allowlist, so a
# section missing here would be silently dropped on the next save.
_MODULE_TOP_SECTIONS: tuple[str, ...] = tuple(sorted(
    {path.split(".", 1)[0] for path in MODULE_ENABLE_PATHS}
))


def set_module_enables(paths, enabled: bool) -> None:
    """Overlay ``enable`` for one or more governance-module dotted paths.

    All writes land in ONE locked read-modify-write, so "turn on this module
    and the parents its gate requires" is atomic -- a crash can't leave a
    child enabled under a disabled parent that the one click promised to
    open. Every path must be allowlisted in :data:`MODULE_ENABLE_PATHS`.
    """
    wanted = [str(p or "").strip() for p in paths]
    if not wanted:
        raise ValueError("no module sections given")
    for path in wanted:
        if path not in MODULE_ENABLE_PATHS:
            raise ValueError(f"unknown module section {path!r}")
    with _locked():
        data = _load_overlay_for_update()
        for path in wanted:
            node = data
            for part in path.split("."):
                child = node.get(part)
                if not isinstance(child, dict):
                    child = {}
                    node[part] = child
                node = child
            node["enable"] = bool(enabled)
        _write(data)


SANDBOX_BACKENDS: tuple[str, ...] = ("local", "docker", "podman")


def set_sandbox_backend(backend: str) -> None:
    """Overlay the sandbox backend ([sandbox] backend).

    Only the backend selector is dashboard-writable; the rest of [sandbox]
    (image, limits, network) stays config-file policy. The caller (the
    sandbox API) is responsible for probing daemon reachability first so a
    switch never silently strands every goal on an unreachable runtime.
    """
    cleaned = str(backend or "").strip().lower()
    if cleaned not in SANDBOX_BACKENDS:
        raise ValueError(f"unknown sandbox backend {cleaned!r}")
    with _locked():
        data = _load_overlay_for_update()
        data.setdefault("sandbox", {})["backend"] = cleaned
        _write(data)


def set_installable_plugins(names) -> None:
    """Replace the dashboard-install plugin allowlist ([plugins] installable).

    Every entry must be a plain distribution name (maverick.plugins._SAFE_PKG:
    no URLs, version specifiers, or shell metacharacters) -- the same check
    install_plugin() re-applies at install time, enforced here too so a bad
    name is rejected at approval, not discovered at install.
    """
    from maverick.plugins import _SAFE_PKG

    cleaned: list[str] = []
    for raw in names:
        name = str(raw or "").strip()
        if not name:
            continue
        if not _SAFE_PKG.match(name):
            raise ValueError(f"unsafe package name: {name!r}")
        if name not in cleaned:
            cleaned.append(name)
    with _locked():
        data = _load_overlay_for_update()
        if cleaned:
            data.setdefault("plugins", {})["installable"] = cleaned
        else:
            (data.get("plugins") or {}).pop("installable", None)
            if not data.get("plugins"):
                data.pop("plugins", None)
        _write(data)


def set_plugin_install_allowed(enabled: bool) -> None:
    """Overlay the in-app plugin-install opt-in ([plugins] allow_install).

    The MAVERICK_ALLOW_PLUGIN_INSTALL env var still outranks it in BOTH
    directions (an explicit falsy value force-disables installs on a hardened
    deployment); this knob is the everyone-else path that needs no shell.
    """
    with _locked():
        data = _load_overlay_for_update()
        data.setdefault("plugins", {})["allow_install"] = bool(enabled)
        _write(data)


def active_tenant() -> str | None:
    """The dashboard-selected active tenant id, or ``None`` when unset.

    Read through the merged config (the overlay deep-merges over config.toml),
    so an operator can also pin ``[dashboard] active_tenant`` in config.toml
    and the UI selection overrides it the same way every other setting does.
    """
    loaded = config.load_config() or {}
    value = (loaded.get("dashboard") or {}).get("active_tenant")
    cleaned = str(value or "").strip()
    return cleaned or None


def set_active_tenant(tenant_id: str | None) -> None:
    """Persist (or clear) the dashboard's active tenant selection.

    The id must name an existing, active tenant in the registry -- selecting a
    tenant that does not exist would silently scope every workbench to an
    empty data root, which reads as data loss.
    """
    cleaned = str(tenant_id or "").strip()
    if cleaned:
        from maverick.tenant.registry import get_tenant

        record = get_tenant(cleaned)
        if record is None:
            raise ValueError(f"unknown tenant {cleaned!r}")
        if not record.active:
            raise ValueError(f"tenant {cleaned!r} is suspended")
    with _locked():
        data = _load_overlay_for_update()
        if cleaned:
            data.setdefault("dashboard", {})["active_tenant"] = cleaned
        else:
            (data.get("dashboard") or {}).pop("active_tenant", None)
            if not data.get("dashboard"):
                data.pop("dashboard", None)
        _write(data)


def set_model_cost_tier(model: str, band: str | None) -> None:
    """Persist a manager's cost-band override for a model in
    ``[model_cost_tiers]``. ``band=None`` clears the override (back to the
    price-derived band). Validated against the known bands."""
    from maverick.model_cost_tier import BANDS
    model = (model or "").strip()
    if not model:
        raise ValueError("model is required")
    if band is not None and band not in BANDS:
        raise ValueError(f"band must be one of {BANDS} or null")
    with _locked():
        data = _load_overlay_for_update()
        tiers = data.setdefault("model_cost_tiers", {})
        if band is None:
            tiers.pop(model, None)
            if not tiers:
                data.pop("model_cost_tiers", None)
        else:
            tiers[model] = band
        _write(data)


def set_security_suite(
    *,
    security_ops: bool,
    threat_hunt: bool,
    env_hunt: bool,
    response_execution: bool,
    actor: str,
    expected_revision: int,
) -> int:
    """Atomically publish deployment-wide security-suite enablement.

    Response execution can never be armed without the environment hunter. The
    signed authorization is durable before the overlay is published, so a
    crash or concurrent reader can never observe an unaudited authority bit.
    """
    if response_execution and not env_hunt:
        raise ValueError("response execution requires env_hunt")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise ValueError("expected_revision must be a positive integer")
    requested = {
        "security_ops": bool(security_ops),
        "threat_hunt": bool(threat_hunt),
        "env_hunt": bool(env_hunt),
        "response_execution": bool(response_execution),
    }
    with _locked():
        before = _load_security_suite_overlay()
        current_revision = _security_suite_revision(before)
        if current_revision != expected_revision:
            raise SecuritySuiteRevisionConflict(
                f"expected security-suite revision {expected_revision}, "
                f"found {current_revision}"
            )
        effective_before = config.load_global_config() or {}
        if config.config_source_errors(include_tenant=False):
            raise SecuritySuiteConfigUnavailable(
                "an active global config source is unreadable; "
                "security-suite configuration was not changed"
            )
        data = json.loads(json.dumps(before))
        security_section = dict(data.get("security_ops") or {})
        platform_section = dict(data.get("threat_hunt") or {})
        environment_section = dict(data.get("env_hunt") or {})
        security_section["enable"] = requested["security_ops"]
        platform_section["enable"] = requested["threat_hunt"]
        environment_section["enable"] = requested["env_hunt"]
        environment_section["response_execution"] = requested["response_execution"]
        # This endpoint owns only the four booleans above. Preserve connector
        # selections, polling intervals, limits, and vendor extension settings.
        data["security_ops"] = security_section
        data["threat_hunt"] = platform_section
        data["env_hunt"] = environment_section
        next_revision = current_revision + 1
        metadata = dict(data.get("security_suite_control") or {})
        metadata["revision"] = next_revision
        data["security_suite_control"] = metadata
        from maverick.config import reset_config_cache

        effective_security = effective_before.get("security_ops") or {}
        effective_platform = effective_before.get("threat_hunt") or {}
        effective_environment = effective_before.get("env_hunt") or {}
        previous = {
            "security_ops": effective_security.get("enable", True) is True,
            "threat_hunt": effective_platform.get("enable", False) is True,
            "env_hunt": effective_environment.get("enable", False) is True,
            "response_execution": (
                effective_environment.get("response_execution", False) is True
            ),
        }
        import time

        from maverick.audit import AuditEvent, EventKind, global_audit_log
        from maverick.privacy_ops import _actor_label

        accepted = global_audit_log().record(AuditEvent(
            ts=time.time(),
            kind=EventKind.SECURITY_SUITE_CONTROL_CHANGED,
            agent="dashboard",
            payload={
                "actor": _actor_label(actor),
                "previous": previous,
                "requested": requested,
                "revision": next_revision,
                "phase": "authorized",
            },
        ))
        if not accepted:
            raise RuntimeError("security-suite control audit was not accepted")
        _write(data)
        reset_config_cache()
        return next_revision


def set_learning(enabled: bool, *, actor: str = "local") -> None:
    """Turn the whole self-learning loop on or off in one action -- writes each
    of ``LEARNING_TOGGLES`` to the overlay, which ``load_config()`` deep-merges,
    so every subsystem's ``enabled()`` sees it. (An env var like
    ``MAVERICK_CONSEQUENCE`` still overrides the overlay if a deployment set
    one.)"""
    with _locked():
        before = _load_overlay_for_update()
        data = json.loads(json.dumps(before))
        for section, key in LEARNING_TOGGLES:
            data.setdefault(section, {})[key] = bool(enabled)
        # The product-wide control selects the conservative unattended harness
        # profile explicitly. Core config keeps legacy partial [self_harness]
        # tables backward-compatible, so relying on an implicit default here
        # could otherwise reactivate a weaker old policy.
        harness = data.setdefault("self_harness", {})
        harness["risk_limited"] = bool(enabled)
        harness["auto_run"] = bool(enabled)
        # Persist the authorization record before publishing a control bit.
        # Runtime readers do not take the dashboard settings lock, so writing
        # first would create a window where an unaudited state was observable.
        from maverick.audit import EventKind, audit_event
        from maverick.config import reset_config_cache

        audited = audit_event(
            EventKind.LEARNING_CONTROL_CHANGED,
            _global=True,
            control="governed_learning",
            enabled=bool(enabled),
            actor=actor,
            acknowledged=False,
            phase="authorized",
        )
        if not audited:
            raise RuntimeError("global learning control audit could not be persisted")
        _write(data)
        reset_config_cache()


def set_dgm(
    enabled: bool,
    *,
    actor: str = "local",
    acknowledged: bool = False,
) -> dict:
    """Set only the canonical, default-off DGM request bit.

    Editable surfaces, challenge tests, evaluator policy and adoption authority
    are deliberately not dashboard-editable. The production runner rechecks all
    of them and never adopts code.
    """
    with _locked():
        from maverick import self_modify
        from maverick.config import reset_config_cache

        before_status = self_modify.production_status()
        blocker_codes = {item["code"] for item in before_status["blockers"]}
        if before_status["control_managed"]:
            raise PermissionError("a higher-precedence deployment policy owns DGM")
        if "config_source_error" in blocker_codes:
            raise PermissionError("an active global config source is invalid")
        if enabled and not acknowledged:
            raise ValueError("research-only acknowledgement is required")
        if enabled and any(code.startswith("invalid_") for code in blocker_codes):
            raise PermissionError("DGM boolean configuration is invalid")
        before = _load_overlay_for_update()
        data = json.loads(json.dumps(before))
        data.setdefault("self_modify", {})["enable"] = bool(enabled)
        # The global audit entry is the durable authorization record; the
        # subsequent atomic overlay replacement is the commit. Record the
        # authorization before publishing so an audit failure can never leave
        # even a transiently observable enabled state.
        from maverick.audit import EventKind, audit_event

        audited = audit_event(
            EventKind.LEARNING_CONTROL_CHANGED,
            _global=True,
            control="self_modify",
            enabled=bool(enabled),
            actor=actor,
            acknowledged=bool(acknowledged),
            previous_requested=bool(before_status["requested"]),
            previous_effective=bool(before_status["effective"]),
            previous_state=before_status["state"],
            phase="authorized",
        )
        if not audited:
            raise RuntimeError("global DGM control audit could not be persisted")
        _write(data)
        reset_config_cache()
        after = self_modify.production_status()
        if bool(after["requested"]) != bool(enabled):
            _write(before)
            reset_config_cache()
            raise PermissionError("a higher-precedence deployment policy owns DGM")
        return after


def _higher_precedence_ekko_enable_owner() -> str | None:
    """Return the active operator/tenant source that owns ``ekko.enable``.

    The dashboard overlay intentionally overrides the base config, but the
    optional operator and tenant sources are merged after it.  A non-table
    ``ekko`` value is also ownership for this purpose: it replaces the whole
    dashboard table and is invalid as an Ekko policy, so an expansive request
    must fail closed.
    """
    operator = os.environ.get(config.CONFIG_OVERLAY_ENV)
    sources = []
    if operator:
        sources.append((config.CONFIG_OVERLAY_ENV, Path(operator).expanduser()))
    tenant = config.tenant_config_path()
    if tenant is not None:
        sources.append(("tenant config", tenant))
    for owner, path in sources:
        source = config.load_config(path)
        if "ekko" not in source:
            continue
        section = source.get("ekko")
        if not isinstance(section, dict) or "enable" in section:
            return owner
    return None


def set_ekko(enabled: bool, *, actor: str = "local") -> dict:
    """Set the deployment-global, default-off Ekko request bit.

    The dashboard owns only ``[ekko] enable``.  Capture scope, retention,
    provider egress, device enrollment, and endpoint permissions remain in the
    client's higher-authority configuration and enrollment record.

    As with DGM, the durable global audit authorization is written before the
    atomic overlay commit.  Environment ownership is always rejected.  Before
    an expansive ON commit, active operator/tenant sources are inspected and
    an existing ``ekko.enable`` owner is rejected without publishing a latent
    dashboard ON bit.  A post-commit effective-state check remains as a race
    defense and rolls back a change superseded while this call was in flight.
    """
    from maverick.ekko_control import control_barrier

    # Serialize the complete authorization change with collector commits. A
    # disable call therefore cannot return while an append authorized under
    # the previous deployment policy is still in flight.
    with control_barrier(), _locked():
        from maverick.config import config_source_errors, get_ekko, reset_config_cache

        before_status = get_ekko()
        if "MAVERICK_EKKO" in os.environ:
            raise PermissionError("MAVERICK_EKKO owns the Ekko control")
        if config_source_errors():
            raise PermissionError("an active config source is invalid")
        if enabled:
            owner = _higher_precedence_ekko_enable_owner()
            if owner is not None:
                raise PermissionError(
                    f"higher-precedence {owner} owns the Ekko control"
                )
        before = _load_overlay_for_update()
        data = json.loads(json.dumps(before))
        data.setdefault("ekko", {})["enable"] = bool(enabled)
        from maverick.audit import EventKind, audit_event

        audited = audit_event(
            EventKind.EKKO_CONTROL_CHANGED,
            _global=True,
            control="ekko",
            enabled=bool(enabled),
            actor=actor,
            previous_enabled=bool(before_status.get("enable", False)),
            phase="authorized",
        )
        if not audited:
            raise RuntimeError("global Ekko control audit could not be persisted")

        _write(data)
        reset_config_cache()
        try:
            after = get_ekko()
        except Exception:
            _write(before)
            reset_config_cache()
            raise
        if bool(after.get("enable", False)) != bool(enabled):
            _write(before)
            reset_config_cache()
            raise PermissionError("a higher-precedence deployment policy owns Ekko")
        return after


def set_flow_autonomy(*, auto_evolve: bool | None = None,
                      auto_apply: bool | None = None) -> None:
    """Override the flow self-improvement loop's [flows] knobs in the overlay.

    Kept OFF the blanket ``set_learning`` list on purpose: autonomously rewriting
    a live workflow is a heavier consequence than the passive learning that button
    controls, so it gets its own explicit opt-in. ``auto_apply`` only takes effect
    while ``auto_evolve`` is on (the forward arrow needs the revert safety net);
    only the fields passed are changed. An env var (``MAVERICK_FLOWS_AUTO``) still
    overrides the overlay if a deployment set one."""
    with _locked():
        data = _load_overlay_for_update()
        flows = data.setdefault("flows", {})
        if auto_evolve is not None:
            flows["auto_evolve"] = bool(auto_evolve)
        if auto_apply is not None:
            flows["auto_apply"] = bool(auto_apply)
        if not bool(flows.get("auto_evolve", False)):
            flows["auto_apply"] = False
        _write(data)


def set_value_assumptions(*, hourly_rate: float | None = None,
                          hours_per_task: float | None = None,
                          currency: str | None = None,
                          enable: bool | None = None,
                          departments: dict | None = None) -> None:
    """Persist the client's cost/value assumptions ([value]) in the overlay.

    Only the fields passed are changed. ``departments`` maps department name ->
    {hourly_rate, hours_per_task}; an entry whose dict is empty/None REMOVES
    that department's override (reverts it to the global assumption). Rates and
    hours must be non-negative -- the Savings page is a claim a client will
    repeat to their CFO, so junk never persists."""
    def _pos(v: object, label: str) -> float:
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be a number") from exc
        if f < 0:
            raise ValueError(f"{label} must be >= 0")
        return f

    with _locked():
        data = _load_overlay_for_update()
        val = data.setdefault("value", {})
        if hourly_rate is not None:
            val["hourly_rate"] = _pos(hourly_rate, "hourly_rate")
        if hours_per_task is not None:
            val["hours_per_task"] = _pos(hours_per_task, "hours_per_task")
        if currency is not None and currency.strip():
            val["currency"] = currency.strip().upper()[:8]
        if enable is not None:
            val["enable"] = bool(enable)
        for name, ov in (departments or {}).items():
            dep = val.setdefault("departments", {})
            if not ov:
                dep.pop(str(name), None)
                continue
            row = {}
            if ov.get("hourly_rate") is not None:
                row["hourly_rate"] = _pos(ov["hourly_rate"],
                                          f"{name} hourly_rate")
            if ov.get("hours_per_task") is not None:
                row["hours_per_task"] = _pos(ov["hours_per_task"],
                                             f"{name} hours_per_task")
            if row:
                dep[str(name)] = {**dep.get(str(name), {}), **row}
        if not val.get("departments"):
            val.pop("departments", None)
        if not val:
            data.pop("value", None)
        _write(data)


def set_channel(name: str, enabled: bool, values: dict | None = None) -> None:
    """Enable/disable a channel and set its credentials in the overlay. A blank
    field value is left unchanged (so toggling enabled never wipes a secret you
    can't see). Writes [channels.<name>] to dashboard-config.toml, which
    load_config() deep-merges -- so `maverick serve` picks it up with no
    config.toml edit. Use ``clear_channel`` to remove."""
    spec = _CHANNELS_BY_NAME.get(name)
    if spec is None:
        raise ValueError("unknown channel")
    values = values or {}
    with _locked():
        data = _load_overlay_for_update()
        ccfg = data.setdefault("channels", {}).setdefault(name, {})
        ccfg["enabled"] = bool(enabled)
        for field in spec["fields"]:
            key = field["key"]
            raw = values.get(key)
            val = raw.strip() if isinstance(raw, str) else raw
            if val in (None, ""):
                continue  # blank -> keep the stored value (don't wipe a hidden secret)
            if field.get("type") == "int":
                try:
                    ccfg[key] = int(val)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{field['label']} must be a number") from exc
            else:
                ccfg[key] = str(val)
        _write(data)


def clear_channel(name: str) -> None:
    """Remove a channel's overlay table entirely (reverts to config.toml/env)."""
    if name not in _CHANNELS_BY_NAME:
        raise ValueError("unknown channel")
    with _locked():
        data = _load_overlay_for_update()
        (data.get("channels") or {}).pop(name, None)
        _write(data)


def _raw_config_providers() -> dict:
    """Providers from config.toml ONLY (no overlay), to attribute the source."""
    try:
        return config._load_config_file(config.config_path()).get("providers", {}) or {}
    except Exception:
        return {}


def _raw_config_channels() -> dict:
    """Channels from config.toml ONLY (no overlay), to attribute the source."""
    try:
        return config._load_config_file(config.config_path()).get("channels", {}) or {}
    except Exception:
        return {}


def _mask(secret: str) -> str:
    s = str(secret)
    return ("•" * 4 + s[-4:]) if len(s) > 4 else "••••"


def state() -> dict:
    """Redacted snapshot for the settings page. NEVER returns a raw key."""
    overlay = load_overlay()
    ov_providers = overlay.get("providers") or {}
    raw_providers = _raw_config_providers()
    providers = []
    for p in PROVIDERS:
        name = p["name"]
        ov = ov_providers.get(name) or {}
        raw = raw_providers.get(name) or {}
        effective = config.provider_configuration_state(name)
        key = ov.get("api_key") or raw.get("api_key")
        providers.append({
            "name": name, "label": p["label"], "base_url_field": p["base_url"],
            "configured": effective["configured"], "via": effective["via"],
            "key_hint": _mask(key) if (ov.get("api_key") or raw.get("api_key")) else None,
            "base_url": effective["base_url"],
            "env_hint": ", ".join(p["env"]) if p["env"] else "",
            "dashboard_set": effective["dashboard_set"],
            "api_key_source": effective["api_key_source"],
            "base_url_source": effective["base_url_source"],
            "dashboard_can_set_api_key": effective["dashboard_can_set_api_key"],
            "dashboard_can_set_base_url": effective["dashboard_can_set_base_url"],
            "source_errors": effective["source_errors"],
        })
    caps_eff = config.get_capabilities()
    feats_eff = config.get_features()
    ov_caps = overlay.get("capabilities") or {}
    ov_feats = overlay.get("features") or {}
    capabilities = [
        {"name": k, "enabled": bool(caps_eff.get(k, v)),
         "overridden": k in ov_caps, "info": CAPABILITY_INFO.get(k, "")}
        for k, v in CAPABILITY_DEFAULTS.items()
    ]
    features = [
        {"name": k, "enabled": bool(feats_eff.get(k, v)),
         "overridden": k in ov_feats, "info": FEATURE_INFO.get(k, "")}
        for k, v in FEATURE_DEFAULTS.items()
    ]
    return {"providers": providers, "capabilities": capabilities, "features": features}


def channels_state() -> list[dict]:
    """Redacted snapshot for the channels page. NEVER returns a raw secret:
    secret fields are blanked (with a masked hint), non-secret fields prefill so
    the form shows the current host/port/etc."""
    overlay = load_overlay()
    ov_ch = overlay.get("channels") or {}
    raw_ch = _raw_config_channels()
    out = []
    for spec in CHANNELS:
        name = spec["name"]
        ov = ov_ch.get(name) or {}
        raw = raw_ch.get(name) or {}
        fields = []
        for f in spec["fields"]:
            ov_v = ov.get(f["key"])
            raw_v = raw.get(f["key"])
            v = ov_v if ov_v not in (None, "") else raw_v
            configured = v not in (None, "")
            fields.append({
                "key": f["key"], "label": f["label"],
                "secret": bool(f.get("secret")), "type": f.get("type", "text"),
                "value": "" if f.get("secret") else (str(v) if configured else ""),
                "hint": _mask(v) if (configured and f.get("secret")) else "",
                "configured": configured,
            })
        out.append({
            "name": name, "label": spec["label"],
            "enabled": bool(ov.get("enabled", raw.get("enabled", False))),
            "fields": fields, "dashboard_set": bool(ov),
            "via": "dashboard" if ov else ("config.toml" if raw else None),
        })
    return out


__all__ = [
    "PROVIDERS", "CAPABILITY_DEFAULTS", "CAPABILITY_INFO",
    "FEATURE_DEFAULTS", "FEATURE_INFO", "CHANNELS",
    "LEARNING_SUBSYSTEMS", "load_overlay", "set_provider", "rotate_provider",
    "clear_provider", "ProviderRotationConflict",
    "ProviderConfigurationUnavailable",
    "SecuritySuiteConfigUnavailable", "SecuritySuiteRevisionConflict",
    "ResearchSettingsConflict", "ResearchSettingsUnavailable",
    "research_execution_state", "set_research_execution",
    "security_suite_revision",
    "set_dgm", "set_ekko", "set_security_suite",
    "set_toggle", "set_toggles", "set_learning", "set_flow_autonomy", "state", "set_channel", "clear_channel",
    "channels_state",
]
