"""Registry of the product settings an administrator can change in the dashboard.

Every entry names a config table + key that the kernel really reads (the
``reader`` module is checked by ``tests/test_settings_catalog.py``), with the
plain words, control, bounds, default and safety posture the "All settings"
page (``/settings/all``) renders. Values are written to the dashboard settings
overlay through :func:`maverick_dashboard.settings_store.set_catalog_values`;
the kernel's ``load_config`` deep-merges that overlay over config.toml, so a
change applies without editing a file or re-running the installer.

Precedence shown on the page (highest first): deployment settings (an
environment variable named in ``env`` or the operator config overlay) > this
page (dashboard overlay) > the installer's config.toml > the built-in default.

Deliberately NOT in the catalog:

* Developer / operations plumbing with no business meaning: ``[grpc]``,
  ``[grpc_dispatch]``, ``[lsp]``, ``[langchain]``, ``[tui]``, ``[perf]``,
  ``[benchmark]``, ``[logging]``, ``[observability]``, ``[queue]``,
  ``[coding]``, ``[model_proxy]``, ``[obsidian]``, ``[github]``,
  ``[calendar]``, ``[embedded]``, ``[ebpf_monitor]``, ``[local_runtime]``
  (serving engines), ``[emergent_protocol]``/``[emergent_codec]`` (research
  telemetry), ``[self_modify]``/``[adapter_rung]`` (research-only code and
  weights rungs with their own high-risk page).
* Deployment-only by design (sign-in, isolation and key custody must not be
  changeable from inside the app they protect): ``[auth]`` (single sign-on),
  ``[tenancy]``, ``[client]``, ``[world_model] rls``, ``[kms]``,
  ``[federation]``, ``[license]``, ``[providers] per_tenant_keys``, the sandbox
  ``workdir``/``host``/``namespace``, ``[audit.worm]`` / ``[audit.unified]``
  custody, the Stripe and Pushover credentials (read only from the server's
  environment), ``[deployment]`` (installer provenance).
* Settings that already have a dedicated control elsewhere: provider keys,
  models, the per-task spending cap, capabilities, features, channels,
  plugins, connections, the learning switches, research execution, savings
  assumptions, workspace selection.
* Structured per-module policies (security suite modules, consent and privacy
  workbench options, model-risk thresholds, rate-limit patterns beyond the
  common tools) that need their module's own page to author safely.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

GROUPS: tuple[str, ...] = (
    "Safety",
    "Security",
    "AI models",
    "Spending & limits",
    "Memory & learning",
    "Privacy",
    "Data retention",
    "Integrations",
    "Performance",
)

GROUP_INTROS: dict[str, str] = {
    "Safety": "How carefully agents are checked before and while they act.",
    "Security": "Protections for records, approvals and outside agents.",
    "AI models": "How agents plan, check their answers and pick models.",
    "Spending & limits": "Limits on money, time and effort for each task and each day.",
    "Memory & learning": "What agents remember and how they learn from past work.",
    "Privacy": "What is recorded, kept or shared about the people who use Lightwork.",
    "Data retention": "How long records are kept before they are removed automatically.",
    "Integrations": "Notifications and connections to other systems.",
    "Performance": "Speed and reliability settings.",
}

CONTROLS = frozenset({
    "toggle", "select", "number", "text", "list", "secret", "multiselect", "rate",
})


@dataclass(frozen=True)
class Setting:
    section: str  # dotted table path, e.g. "safety" or "billing.stripe"
    key: str
    label: str
    help: str
    control: str
    group: str
    default: object
    reader: str  # kernel module that reads this key (checked by tests)
    choices: tuple[tuple[str, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""
    integer: bool = False
    restart: bool = False
    # Safety posture. ``weaker`` says which direction lowers a protection:
    # toggle -> the boolean that weakens; select -> ``order`` is strongest
    # first; number -> "lower" or "higher". ``warning`` is the plain
    # consequence the page shows and the confirmation repeats.
    weaker: object = None
    order: tuple[str, ...] = ()
    warning: str = ""
    env: tuple[str, ...] = ()
    max_length: int = 200
    max_items: int = 20
    pattern: str = ""  # optional full-match rule for text/list items
    aliases: str = ""  # extra search words
    # "key" = a fixed key the reader names; "tool" = the key is a registered
    # tool name in a per-tool table (``[rate_limits]``).
    key_kind: str = "key"

    @property
    def id(self) -> str:
        return f"{self.section}.{self.key}"

    @property
    def top(self) -> str:
        return self.section.split(".", 1)[0]

    @property
    def path(self) -> tuple[str, ...]:
        return (*self.section.split("."), self.key)

    @property
    def secret(self) -> bool:
        return self.control == "secret"

    @property
    def risky(self) -> bool:
        return bool(self.warning)


_RISK_ORDER = (("low", "Low risk only"), ("medium", "Up to medium risk"),
               ("high", "Up to high risk"), ("none", "No limit"))
_HTTPS = r"https://[^\s\"'<>]{1,300}"

S = Setting
SETTINGS: tuple[Setting, ...] = (
    # ------------------------------------------------------------------ Safety
    S("safety", "profile", "Safety level",
      "How strictly incoming requests, tool use and answers are screened for threats.",
      "select", "Safety", "balanced", "maverick.config",
      choices=(("strict", "Strict: block anything medium risk or above"),
               ("balanced", "Balanced: block high risk and above (recommended)"),
               ("permissive", "Permissive: block only critical threats"),
               ("off", "Off: no screening")),
      weaker=True, order=("strict", "balanced", "permissive", "off"),
      warning="Agents will be screened less strictly, so harmful requests or actions are more likely to get through.",
      aliases="shield threat scanning"),
    S("safety", "block_threshold", "Block threats from",
      "The lowest threat level that stops a request or action.",
      "select", "Safety", "high", "maverick.config",
      choices=(("medium", "Medium and above"), ("high", "High and above"),
               ("critical", "Critical only")),
      weaker=True, order=("medium", "high", "critical"),
      warning="Fewer threats will be blocked."),
    S("safety", "scan_input", "Check incoming requests",
      "Screen every request before an agent starts work on it.",
      "toggle", "Safety", True, "maverick.config", weaker=False,
      warning="Requests will reach agents without being checked for harmful instructions."),
    S("safety", "scan_tool_calls", "Check actions before they run",
      "Screen each tool an agent wants to use before it runs.",
      "toggle", "Safety", True, "maverick.config", weaker=False,
      warning="Agent actions will run without a safety check first."),
    S("safety", "scan_output", "Check answers before they are shown",
      "Screen agent answers for leaked secrets or harmful content.",
      "toggle", "Safety", True, "maverick.config", weaker=False,
      warning="Answers will be shown without being checked for leaked secrets or harmful content."),
    S("safety", "compartments", "Share blocked threats across a team of agents",
      "When one agent's check blocks a threat, the other agents on the same task are protected from it too.",
      "toggle", "Safety", False, "maverick.config"),
    S("safety", "compartment_unseal", "Who may lift a safety lock",
      "Who can release an agent group that was locked after a blocked threat.",
      "select", "Safety", "human", "maverick.config",
      choices=(("human", "A person only (recommended)"),
               ("both", "A person or the lead agent"),
               ("orchestrator", "The lead agent")),
      weaker=True, order=("human", "both", "orchestrator"),
      warning="An agent, not only a person, will be able to lift a safety lock."),
    S("skills", "require_signed", "Only allow signed skills",
      "Refuse saved procedures that are not signed by a trusted publisher.",
      "toggle", "Safety", False, "maverick.config", weaker=False,
      warning="Unsigned skills from unknown publishers will be accepted again."),
    S("skills", "require_signed_catalog", "Require a signature for catalog installs",
      "Installing a skill from a catalog needs a verified signature from a trusted publisher.",
      "toggle", "Safety", False, "maverick.config", weaker=False,
      env=("MAVERICK_REQUIRE_SIGNED_CATALOG",),
      warning="Skills from catalogs will install without a verified signature."),
    S("skills", "builtin", "Use the built-in skills library",
      "Let agents recall Lightwork's own library of proven procedures.",
      "toggle", "Safety", True, "maverick.config"),
    S("ifc", "mode", "Sensitive data leaving Lightwork",
      "What happens when labelled sensitive data is about to reach an outside service.",
      "select", "Safety", "audit", "maverick.config",
      choices=(("block", "Block it"), ("approve", "Ask a person to approve"),
               ("audit", "Allow it and record it"), ("off", "Do nothing")),
      weaker=True, order=("block", "approve", "audit", "off"), env=("MAVERICK_IFC_MODE",),
      warning="Sensitive data will be able to reach outside services with less oversight.",
      aliases="information flow"),
    S("ifc", "scope", "When data counts as seen",
      "Whether an agent that has read sensitive data is treated as carrying it, or only when the data appears word for word.",
      "select", "Safety", "context", "maverick.config",
      choices=(("context", "Once an agent has read it (recommended)"),
               ("explicit", "Only when it appears word for word")),
      weaker=True, order=("context", "explicit"),
      warning="Sensitive data that an agent rephrases will no longer be caught."),
    S("codebase", "default_protect", "Protection for new code repositories",
      "What happens when work on a newly added repository tries to send its code elsewhere.",
      "select", "Safety", "block", "maverick.config",
      choices=(("block", "Block it"), ("approve", "Ask a person to approve"),
               ("audit", "Allow it and record it")),
      weaker=True, order=("block", "approve", "audit"),
      warning="Code from new repositories will be able to leave with less oversight."),
    S("autonomy", "enable", "Extra checks on uncertain work",
      "When agents disagree or are unsure, double-check the answer and tighten what risky tools may do.",
      "toggle", "Safety", False, "maverick.config"),
    S("autonomy", "headless_assume", "Carry on when nobody can answer",
      "When no person is available to answer a question, let the agent make a reasonable assumption instead of waiting.",
      "toggle", "Safety", False, "maverick.config", weaker=True,
      warning="Agents will act on their own assumptions instead of waiting for a person."),
    S("earned_autonomy", "enable", "Track which actions agents predict well",
      "Record how accurately agents predict the results of their actions, as evidence for trusting them more.",
      "toggle", "Safety", False, "maverick.config"),
    S("earned_autonomy", "auto_graduate", "Let proven actions skip approval",
      "Actions whose results agents have predicted correctly many times in a row are approved automatically.",
      "toggle", "Safety", False, "maverick.config", weaker=True,
      warning="Some actions will run without a person approving them."),
    S("earned_autonomy", "max_auto_risk", "Highest risk that can skip approval",
      "The riskiest kind of action that may be approved automatically once proven.",
      "select", "Safety", "medium", "maverick.config",
      choices=(("low", "Low risk"), ("medium", "Medium risk"), ("high", "High risk")),
      weaker=True, order=("low", "medium", "high"),
      warning="Riskier actions will be able to run without a person approving them."),
    S("earned_autonomy", "require_reversible", "Only actions that can be undone",
      "Only actions that can be reversed may ever skip approval.",
      "toggle", "Safety", True, "maverick.config", weaker=False,
      warning="Actions that cannot be undone will be able to skip approval."),
    S("workforce", "levels", "Per-agent independence levels",
      "Let each hired agent act on its own for the risk levels you choose, instead of only drafting for a person to commit.",
      "toggle", "Safety", False, "maverick.config", weaker=True,
      warning="Agents you have set to act on their own will commit work without a person."),
    S("workforce", "auto_graduate", "Promote agents automatically",
      "Move agents out of their supervised first weeks automatically once their record allows it.",
      "toggle", "Safety", False, "maverick.config", weaker=True,
      warning="Agents will leave supervision without a person deciding."),
    S("workforce", "data_grounding", "Check agent work against your records",
      "Compare agent claims with your connected systems before trusting them.",
      "toggle", "Safety", True, "maverick.config", weaker=False,
      warning="Agent claims will no longer be checked against your connected systems."),
    S("self_learning", "create_tools", "Let agents build new tools",
      "Agents may write and run new tools for themselves. Each new tool still needs approval.",
      "toggle", "Safety", False, "maverick.config", weaker=True,
      warning="Agents will be able to write new programs for themselves."),
    S("self_learning", "allow_mcp_acquisition", "Let agents add outside tool servers",
      "Agents may propose starting approved outside tool servers from the catalog.",
      "toggle", "Safety", False, "maverick.config", weaker=True,
      env=("MAVERICK_ALLOW_MCP_ACQUISITION",),
      warning="Agents will be able to start outside programs from the catalog."),
    S("self_learning", "allow_provider_egress", "Let learning send work to other AI services",
      "Extra learning steps may send task text to a different configured AI service.",
      "toggle", "Safety", False, "maverick.config", weaker=True,
      warning="Task text may be sent to additional AI services for learning."),
    S("self_learning", "max_acquisitions", "New skills or tools per task",
      "The most skills or tools an agent may add while working on one task.",
      "number", "Safety", 5, "maverick.config", minimum=1, maximum=50, integer=True,
      unit="per task"),
    S("harness_refine", "enable", "Let agents suggest changes to their instructions",
      "After a failure, agents may propose an improvement to their own working instructions.",
      "toggle", "Safety", False, "maverick.config", env=("MAVERICK_HARNESS_REFINE",)),
    S("harness_refine", "require_approval", "Instruction changes need approval",
      "A person must approve every suggested change to agent instructions before it applies.",
      "toggle", "Safety", True, "maverick.config", weaker=False,
      warning="Agents will be able to change their own instructions without a person approving."),
    S("repl", "enable", "Let agents run code step by step",
      "Agents may write and run code in the protected work area, with every step recorded.",
      "toggle", "Safety", False, "maverick.config", weaker=True, env=("MAVERICK_REPL",),
      warning="Agents will be able to run code they write."),
    S("repl", "max_seconds", "Time limit for each code step",
      "How long one step of agent code may run.",
      "number", "Safety", 30.0, "maverick.config", minimum=1, maximum=600, unit="seconds"),
    S("sandbox", "timeout", "Time limit for each command",
      "How long a single command in the protected work area may run.",
      "number", "Safety", 60, "maverick.config", minimum=5, maximum=3600, integer=True,
      unit="seconds"),
    S("a2a", "max_risk", "What other agents may ask for",
      "The riskiest tools a task sent by another agent may use.",
      "select", "Safety", "medium", "maverick.a2a_tasks", choices=_RISK_ORDER,
      weaker=True, order=("low", "medium", "high", "none"), env=("MAVERICK_A2A_MAX_RISK",),
      warning="Tasks sent by other agents will be able to use riskier tools."),

    # ---------------------------------------------------------------- Security
    S("audit", "sign", "Sign the activity record",
      "Seal each activity record entry so any later change can be detected.",
      "toggle", "Security", True, "maverick.audit.writer", weaker=False, restart=True,
      env=("MAVERICK_AUDIT_SIGN",),
      warning="New activity records will not be sealed, so changes to them could go unnoticed."),
    S("encryption", "at_rest", "Encrypt stored data",
      "Keep sensitive records and settings encrypted on disk.",
      "toggle", "Security", True, "maverick.crypto_at_rest", weaker=False,
      env=("MAVERICK_ENCRYPT_AT_REST",),
      warning="New records will be stored without encryption."),
    S("enterprise", "mode", "Enterprise protections",
      "Turn on the stricter protections expected in regulated organisations, such as required encryption and governed actions.",
      "toggle", "Security", False, "maverick.enterprise", weaker=False, env=("MAVERICK_ENTERPRISE",),
      warning="The stricter enterprise protections will stop applying."),
    S("security", "secure_defaults", "Secure defaults",
      "Keep protections such as record signing and encryption on unless they are turned off one by one.",
      "toggle", "Security", True, "maverick.security_defaults", weaker=False,
      env=("MAVERICK_SECURE_DEFAULT",),
      warning="Record signing, encryption and other protections that rely on this will turn off."),
    S("security", "approvals_required", "People needed to approve risky actions",
      "How many different people must approve a high-risk action. One means a single approver.",
      "number", "Security", 1, "maverick.safety.dual_control", minimum=1, maximum=5, integer=True,
      unit="people", weaker="lower", env=("MAVERICK_APPROVALS_REQUIRED",),
      warning="Fewer people will need to approve risky actions."),
    S("security", "allow_self_approval", "Let people approve their own requests",
      "Allow the person who asked for an action to approve it too.",
      "toggle", "Security", False, "maverick.safety.dual_control", weaker=True,
      env=("MAVERICK_ALLOW_SELF_APPROVAL",),
      warning="People will be able to approve their own requests."),
    S("security", "auto_fix", "Fix unsafe settings automatically",
      "When the security check finds a risky setting, correct it automatically.",
      "toggle", "Security", False, "maverick.remediation", env=("MAVERICK_SECURITY_AUTOFIX",)),
    S("agent_trust", "enforce", "Only trust registered outside agents",
      "Refuse outside agents that are not on your trusted list.",
      "toggle", "Security", False, "maverick.agent_trust", weaker=False,
      warning="Outside agents that are not on your trusted list will be able to connect."),
    S("agent_trust", "require_signed", "Require outside agents to sign",
      "Refuse outside agents that only present a shared password instead of a signature.",
      "toggle", "Security", False, "maverick.federation", weaker=False,
      warning="Outside agents will be able to connect with a shared password alone."),
    S("external_agents", "require_signed", "Outside agents must use their strongest credential",
      "An outside agent registered with a signature key must always use it.",
      "toggle", "Security", False, "maverick.config", weaker=False,
      warning="Outside agents will be able to sign in with a weaker credential."),
    S("external_agents", "mint_approval", "Approve new outside-agent credentials",
      "A person must approve before a new sign-in credential is issued to an outside agent.",
      "toggle", "Security", False, "maverick.config", weaker=False,
      warning="Credentials for outside agents will be issued without a person approving."),
    S("governed_connectors", "enable", "Govern writes to your business systems",
      "Changes to connected business systems are previewed, approved and recorded with a way to undo them.",
      "toggle", "Security", False, "maverick.config", weaker=False,
      env=("MAVERICK_GOVERNED_CONNECTORS",),
      warning="Changes to connected business systems will no longer go through preview and approval."),
    S("governed_connectors", "restore_points", "Save a restore point before changes",
      "Read the current record before changing it so the change can be undone.",
      "toggle", "Security", True, "maverick.config", weaker=False,
      env=("MAVERICK_GOVERNED_RESTORE_POINTS",),
      warning="Changes to business systems will not be undoable, so each one will need a person."),
    S("residency", "region", "Where data must be stored",
      "The region your data is declared to live in, for example EU or US. Leave empty for no declared region.",
      "text", "Security", "", "maverick.residency", max_length=16, pattern=r"[A-Za-z0-9-]{0,16}",
      env=("MAVERICK_DATA_REGION",)),
    S("residency", "strict", "Enforce the storage region",
      "Refuse to store data outside the declared region.",
      "toggle", "Security", False, "maverick.residency", weaker=False, restart=True,
      env=("MAVERICK_RESIDENCY_STRICT",),
      warning="Data may be stored outside the declared region."),
    S("a2a", "enabled", "Let other agents send tasks here",
      "Other agents you give a password to may hand tasks to Lightwork. The password is set on the server.",
      "toggle", "Security", False, "maverick.a2a", weaker=True, env=("MAVERICK_A2A_ENABLED",),
      warning="Other agents on the network will be able to send tasks that spend your budget."),

    # --------------------------------------------------------------- AI models
    S("routing", "cost_aware", "Pick the cheapest capable model",
      "Use the least expensive model that can do each job well.",
      "toggle", "AI models", False, "maverick.cost.router"),
    S("routing", "verify_ensemble", "Double-check with a second AI service",
      "Confirm important answers with models from more than one provider.",
      "toggle", "AI models", False, "maverick.verifier"),
    S("routing", "energy_aware", "Prefer lower-energy models",
      "Favour models that use less energy when they can do the job.",
      "toggle", "AI models", False, "maverick.energy_aware_router"),
    S("routing", "allowed_providers", "AI services agents may use for these choices",
      "Limit automatic model choices to these AI services. Leave empty to allow any service you have connected.",
      "multiselect", "AI models", [], "maverick.cost.router",
      choices=(("anthropic", "Anthropic (Claude)"), ("openai", "OpenAI"),
               ("gemini", "Google Gemini"), ("openrouter", "OpenRouter"),
               ("moonshot", "Moonshot"), ("deepseek", "DeepSeek"), ("xai", "xAI (Grok)"),
               ("ollama", "Ollama (self-hosted)"), ("vllm", "Self-hosted compatible service"),
               ("bedrock", "Amazon Bedrock"), ("azure", "Microsoft Azure"))),
    S("verification", "risk_proportional", "Check risky work more carefully",
      "Spend more checking effort on work with bigger consequences.",
      "toggle", "AI models", False, "maverick.agent"),
    S("verification", "verify_chat_answers", "Check chat answers",
      "Verify answers in Chat before they are shown.",
      "toggle", "AI models", False, "maverick.agent"),
    S("chat", "stream_answers", "Show chat answers as they are written",
      "Show a live preview of each answer while it is being written.",
      "toggle", "AI models", True, "maverick.agent"),
    S("planning", "mode", "How agents plan",
      "Plan once, compare several plans before starting, or let Lightwork choose from past results.",
      "select", "AI models", "", "maverick.tree_of_thought",
      choices=(("", "Plan once (default)"), ("tree_of_thought", "Compare several plans first"),
               ("auto", "Choose automatically from past results")),
      env=("MAVERICK_TREE_OF_THOUGHT",)),
    S("search", "enable", "Draft several answers and keep the best",
      "Write a few candidate answers and keep the one the checker rates highest.",
      "toggle", "AI models", False, "maverick.config"),
    S("search", "n", "Number of candidate answers",
      "How many candidate answers to write when the option above is on.",
      "number", "AI models", 3, "maverick.config", minimum=1, maximum=10, integer=True,
      unit="answers"),
    S("adaptive_compute", "enable", "Spend effort where it is needed",
      "Use fewer parallel attempts when agents are confident and more when they are unsure.",
      "toggle", "AI models", False, "maverick.config"),
    S("effort", "enabled", "Adjust thinking effort per task",
      "Let Lightwork choose how much reasoning effort each task needs.",
      "toggle", "AI models", False, "maverick.effort", env=("MAVERICK_EFFORT_ENABLED",)),
    S("context", "compaction_strategy", "How long conversations are shortened",
      "The method used to shorten long conversations so they fit.",
      "select", "AI models", "", "maverick.compaction.strategies",
      choices=(("", "Keep recent turns and trim long tool results (default)"),
               ("learned", "Written summary that improves over time"),
               ("multimodal", "Replace large images and audio with short notes"),
               ("streaming", "Running summary for very long chats"),
               ("graph", "Summary of people, things and how they relate")),
      env=("MAVERICK_COMPACTION_STRATEGY",)),
    S("context", "compact", "Keep the most useful older turns",
      "Keep the most relevant older parts of long conversations instead of only the latest few.",
      "toggle", "AI models", False, "maverick.context_compactor"),
    S("context", "model_scaled", "Size memory to each model",
      "Fit conversation history to how much each model can read at once.",
      "toggle", "AI models", True, "maverick.context_scaling"),
    S("speculative", "enable", "Use a quick model for predictable steps",
      "Draft predictable steps with a cheaper model and keep the main model for new situations.",
      "toggle", "AI models", False, "maverick.config"),
    S("persona", "name", "Assistant name",
      "The name your agents use for themselves.",
      "text", "AI models", "", "maverick.persona", max_length=60,
      pattern=r"[^\n\r<>{}]{0,60}"),
    S("persona", "style", "Writing style",
      "How much detail agents put in their replies.",
      "select", "AI models", "", "maverick.persona",
      choices=(("", "Balanced (default)"), ("concise", "Short and direct"),
               ("verbose", "Explains its reasoning"))),
    S("domains", "discipline", "Department working rules",
      "Give each department's specialists their department's working rules.",
      "toggle", "AI models", True, "maverick.config"),

    # ------------------------------------------------------- Spending & limits
    S("budget", "max_wall_seconds", "Time limit per task",
      "The longest a single task may run.",
      "number", "Spending & limits", None, "maverick.budget", minimum=60, maximum=604800,
      unit="seconds"),
    S("budget", "max_tool_calls", "Actions per task",
      "The most tool uses allowed in a single task.",
      "number", "Spending & limits", None, "maverick.budget", minimum=1, maximum=100000,
      integer=True, unit="actions"),
    S("budget", "max_output_tokens", "Words written per task",
      "The most text, in tokens (about three quarters of a word each), agents may write in one task.",
      "number", "Spending & limits", None, "maverick.budget", minimum=1000,
      maximum=100000000, integer=True, unit="tokens"),
    S("budget", "strict_pricing", "Require known prices",
      "Stop a task when a model's price is unknown instead of guessing its cost.",
      "toggle", "Spending & limits", True, "maverick.budget", weaker=False,
      env=("MAVERICK_BILLING_STRICT",),
      warning="Spending on models with unknown prices will be estimated, so costs may be under-counted."),
    S("quotas", "enforce", "Daily limits per person",
      "Enforce the daily limits below for each person.",
      "toggle", "Spending & limits", False, "maverick.quotas", env=("MAVERICK_QUOTA_ENFORCE",)),
    S("quotas", "max_dollars_per_day", "Daily spending limit per person",
      "The most one person's tasks may spend in a day. Zero means no limit.",
      "number", "Spending & limits", 0.0, "maverick.quotas", minimum=0, maximum=1000000,
      unit="dollars per day", env=("MAVERICK_QUOTA_MAX_DOLLARS_PER_DAY",)),
    S("quotas", "max_tokens_per_day", "Daily text limit per person",
      "The most text, in tokens, one person's tasks may use in a day. Zero means no limit.",
      "number", "Spending & limits", 0, "maverick.quotas", minimum=0, maximum=10000000000,
      integer=True, unit="tokens per day", env=("MAVERICK_QUOTA_MAX_TOKENS_PER_DAY",)),
    S("swarm", "max_total_spawns", "Helper agents per task",
      "The most helper agents one task may create.",
      "number", "Spending & limits", 64, "maverick.config", minimum=1, maximum=1000,
      integer=True, unit="agents", env=("MAVERICK_MAX_TOTAL_SPAWNS",)),
    S("swarm", "max_concurrent_model_calls", "Helpers thinking at once",
      "How many helper agents may wait on an AI service at the same time. Zero means no limit.",
      "number", "Spending & limits", 0, "maverick.config", minimum=0, maximum=256,
      integer=True, unit="at once"),
    S("credit", "max_children", "Largest team to score",
      "Skip scoring each agent's contribution in teams larger than this.",
      "number", "Spending & limits", 6, "maverick.config", minimum=2, maximum=64,
      integer=True, unit="agents"),
    S("commerce", "enable", "Shopping and store tools",
      "Give shopping and store agents their tools: search, compare, build carts and suggest store changes for approval.",
      "toggle", "Spending & limits", False, "maverick.config"),
    S("commerce", "max_session_spend", "Cart limit per session",
      "The most a single shopping session's cart may add up to.",
      "number", "Spending & limits", 500.0, "maverick.config", minimum=1, maximum=1000000,
      unit="store currency"),
    S("commerce", "max_line_quantity", "Quantity limit per item",
      "The most of one item a cart may hold.",
      "number", "Spending & limits", 10, "maverick.config", minimum=1, maximum=10000,
      integer=True, unit="items"),
    S("commerce", "max_cart_lines", "Different items per cart",
      "The most different items one cart may hold.",
      "number", "Spending & limits", 25, "maverick.config", minimum=1, maximum=1000,
      integer=True, unit="items"),
    S("billing.stripe", "enable", "Collect payments with Stripe",
      "Turn usage invoices into real Stripe charges. The Stripe keys are set on the server.",
      "toggle", "Spending & limits", False, "maverick.billing_stripe", env=("MAVERICK_STRIPE_ENABLE",)),
    S("billing.stripe", "bill_metered_usage", "Charge for model usage",
      "Also charge customers for their model usage, not only the platform fee.",
      "toggle", "Spending & limits", False, "maverick.config"),
    S("billing.stripe", "currency", "Billing currency",
      "The three-letter currency code for charges, for example usd or eur.",
      "text", "Spending & limits", "usd", "maverick.config", max_length=3,
      pattern=r"[A-Za-z]{3}"),

    # ------------------------------------------------------- Memory & learning
    S("memory", "temporal", "Keep the history of every fact",
      "Remember what was believed on each date instead of only the latest value.",
      "toggle", "Memory & learning", False, "maverick.config", env=("MAVERICK_TEMPORAL_MEMORY",)),
    S("memory_guard", "enable", "Screen what agents remember",
      "Check everything written to memory for planted instructions and label where it came from.",
      "toggle", "Memory & learning", False, "maverick.config", weaker=False,
      env=("MAVERICK_MEMORY_GUARD",),
      warning="Memory will no longer be screened for planted instructions."),
    S("memory_guard", "min_recall_trust", "Memory agents may rely on",
      "The least trusted kind of memory agents may use while working.",
      "select", "Memory & learning", 1, "maverick.config",
      choices=(("3", "Only what people entered"), ("2", "Also what Lightwork learned"),
               ("1", "Also agents' own notes (default)"), ("0", "Everything, including outside content")),
      weaker=True, order=("3", "2", "1", "0"),
      warning="Agents will rely on less trusted memory, including content from outside sources."),
    S("domains", "memory", "Share department lessons",
      "Give specialists the lessons their department has learned.",
      "toggle", "Memory & learning", True, "maverick.config"),
    S("cortex", "enable", "Company memory",
      "One governed place to look up knowledge, skills, lessons and past decisions, with every look-up recorded.",
      "toggle", "Memory & learning", True, "maverick.cortex"),
    S("knowledge", "enable", "Document search for agents",
      "Let agents search your uploaded documents by meaning and cite them.",
      "toggle", "Memory & learning", False, "maverick.config"),
    S("session_tree", "enable", "Keep the family tree of runs",
      "Record which runs were copied from which, so alternatives can be compared.",
      "toggle", "Memory & learning", True, "maverick.config"),
    S("calibration", "enforce", "Pause learning when the checker drifts",
      "Stop learning from results if the answer checker is no longer reliable.",
      "toggle", "Memory & learning", False, "maverick.config", weaker=False,
      warning="Learning will continue even when the answer checker is unreliable."),
    S("calibration", "min_samples", "Checked results before judging the checker",
      "How many checked results are needed before the checker's reliability is judged.",
      "number", "Memory & learning", 20, "maverick.config", minimum=1, maximum=100000,
      integer=True, unit="results"),
    S("assessments", "learn", "Learn from past assessments",
      "Suggest answers and precedents from your organisation's own past assessments. A person still reviews them.",
      "toggle", "Memory & learning", True, "maverick.config", env=("MAVERICK_ASSESS_LEARN",)),
    S("assessments", "doc_discovery", "Find related documents for assessments",
      "Search your connected document sources for contracts and agreements related to an assessment.",
      "toggle", "Memory & learning", True, "maverick.config", env=("MAVERICK_ASSESS_DISCOVERY",)),
    S("fairness_monitor", "enable", "Watch decisions for unfair outcomes",
      "Continuously compare decision outcomes between groups and warn when one falls behind.",
      "toggle", "Memory & learning", False, "maverick.config", env=("MAVERICK_FAIRNESS_MONITOR",)),
    S("fairness_monitor", "threshold", "Fairness warning level",
      "Warn when a group's approval rate falls below this share of the best group's rate.",
      "number", "Memory & learning", 0.8, "maverick.config", minimum=0.5, maximum=1.0,
      step=0.05, unit="share"),
    S("dreaming", "llm_consolidation", "Summarise lessons with an AI model",
      "Use a model to write up the lessons found in past work, instead of only simple pattern matching.",
      "toggle", "Memory & learning", False, "maverick.config"),

    # ----------------------------------------------------------------- Privacy
    S("privacy", "anonymous", "Anonymous records",
      "Leave names and personal details out of logs and activity records.",
      "toggle", "Privacy", False, "maverick.privacy", weaker=False, env=("MAVERICK_ANON",),
      warning="Names and personal details will be written to logs and records again."),
    S("telemetry", "donate_trajectories", "Share anonymous work patterns",
      "Contribute outline information about completed work, never its text, to improve models.",
      "toggle", "Privacy", False, "maverick.donation", weaker=True,
      warning="Outline information about your completed work will be shared."),
    S("analytics", "mcp_client_language", "Count which programming languages connect",
      "Keep a local count of the programming languages used by connected tools. Nothing leaves this computer.",
      "toggle", "Privacy", False, "maverick.mcp_analytics", env=("MAVERICK_MCP_ANALYTICS",)),
    S("immune", "twin_record", "Record tool results for replay",
      "Keep sealed copies of every task's tool results so the task can be replayed to test changes.",
      "toggle", "Privacy", False, "maverick.config", weaker=True,
      warning="Copies of tool results, which may include personal data, will be kept."),
    S("immune", "canaries", "Test reviewers with practice approvals",
      "Allow administrators to plant known-flawed practice approvals to measure whether reviewers catch them.",
      "toggle", "Privacy", False, "maverick.config"),
    S("attachments", "allow_any_mime", "Accept any kind of attachment",
      "Accept attachments of any declared type. Programs and archives are always refused.",
      "toggle", "Privacy", False, "maverick.attachments", weaker=True,
      warning="Unusual file types will be accepted as attachments."),
    S("attachments", "transcribe_media", "Transcribe audio and video",
      "Turn speech in attached audio and video into text for the task.",
      "toggle", "Privacy", True, "maverick.attachments", env=("MAVERICK_ATTACH_TRANSCRIBE",)),
    S("attachments", "extract_text", "Read text from documents",
      "Pull the text out of attached office documents for the task.",
      "toggle", "Privacy", True, "maverick.attachments", env=("MAVERICK_ATTACH_EXTRACT",)),
    S("voice", "auto_fetch_model", "Download the offline speech model",
      "Download a verified speech-to-text model so voice input works without an outside service.",
      "toggle", "Privacy", False, "maverick.voice_models", restart=True),
    S("compliance", "disclosure_text", "AI disclosure message",
      "The sentence shown to people to tell them they are dealing with an AI system.",
      "text", "Privacy", "", "maverick.compliance", max_length=1000, env=("MAVERICK_AI_DISCLOSURE",),
      pattern=r"[^\x00-\x08\x0b\x0c\x0e-\x1f]{1,1000}"),

    # ---------------------------------------------------------- Data retention
    S("retention", "audit_days", "Keep the activity record for",
      "Activity record days older than this are removed. Leave unset to keep them forever.",
      "number", "Data retention", None, "maverick.audit.retention", minimum=1, maximum=3650,
      integer=True, unit="days", weaker="lower",
      warning="Older activity records will be permanently deleted at the next clean-up."),
    S("retention", "episodes_days", "Keep finished work records for",
      "Records of finished work older than this are removed. Leave unset to keep them forever.",
      "number", "Data retention", None, "maverick.audit.retention", minimum=1, maximum=3650,
      integer=True, unit="days", weaker="lower",
      warning="Older work records will be permanently deleted at the next clean-up."),
    S("retention", "events_days", "Keep task progress updates for",
      "Task progress updates older than this are removed. Leave unset to keep them forever.",
      "number", "Data retention", None, "maverick.audit.retention", minimum=1, maximum=3650,
      integer=True, unit="days", weaker="lower",
      warning="Older progress updates will be permanently deleted at the next clean-up."),
    S("retention", "messages_days", "Keep messages for",
      "Messages older than this are removed. Leave unset to keep them forever.",
      "number", "Data retention", None, "maverick.audit.retention", minimum=1, maximum=3650,
      integer=True, unit="days", weaker="lower",
      warning="Older messages will be permanently deleted at the next clean-up."),
    S("retention", "usage_days", "Keep usage tallies for",
      "Daily usage tallies older than this are removed. Leave unset to keep them forever.",
      "number", "Data retention", None, "maverick.audit.retention", minimum=1, maximum=3650,
      integer=True, unit="days", weaker="lower",
      warning="Older usage tallies will be permanently deleted at the next clean-up."),
    S("durable", "enabled", "Resume tasks after a crash",
      "Save progress as tasks run so they can continue from the last step after an interruption.",
      "toggle", "Data retention", False, "maverick.config"),
    S("durable", "keep_last", "Saved progress points per agent",
      "How many saved progress points to keep for each agent.",
      "number", "Data retention", 5, "maverick.config", minimum=1, maximum=100,
      integer=True, unit="points"),
    S("workspace", "journal", "Keep a copy before files change",
      "Save a copy of each file before an agent changes it, so a task's changes can be undone.",
      "toggle", "Data retention", False, "maverick.workspace_journal"),

    # ------------------------------------------------------------ Integrations
    S("notifications", "backend", "Send finished-task alerts with",
      "Where to send a message when a long task finishes.",
      "select", "Integrations", "ntfy", "maverick.notifications",
      choices=(("none", "Do not send alerts"), ("ntfy", "ntfy phone alerts"),
               ("slack", "Slack"), ("discord", "Discord"),
               ("pushover", "Pushover (keys set on the server)"))),
    S("notifications", "ntfy_topic", "ntfy topic",
      "Your private ntfy topic name. Treat it like a password.",
      "secret", "Integrations", "", "maverick.notifications"),
    S("notifications", "ntfy_server", "ntfy server",
      "The ntfy server web address. Leave empty to use the public ntfy service.",
      "text", "Integrations", "", "maverick.notifications", max_length=300,
      pattern=r"(" + _HTTPS + r")?"),
    S("notifications", "slack_webhook", "Slack alert link",
      "The Slack incoming message link for alerts.",
      "secret", "Integrations", "", "maverick.notifications", pattern=_HTTPS),
    S("notifications", "discord_webhook", "Discord alert link",
      "The Discord message link for alerts.",
      "secret", "Integrations", "", "maverick.notifications", pattern=_HTTPS),
    S("notifications", "batch_window_seconds", "Group routine alerts",
      "Collect routine alerts and send them together this often. Urgent alerts are always sent at once. Zero sends each alert straight away.",
      "number", "Integrations", 0, "maverick.notification_batcher", minimum=0, maximum=86400,
      integer=True, unit="seconds"),
    S("webhooks", "outbound", "Send task events to these addresses",
      "Web addresses that receive a signed message when tasks start and finish.",
      "list", "Integrations", [], "maverick.webhooks", max_items=10, pattern=_HTTPS),
    S("automation_import", "enable", "Import automations from other tools",
      "Bring in existing automations from tools such as n8n, Make, Workato, Power Automate or UiPath.",
      "toggle", "Integrations", False, "maverick.config", env=("MAVERICK_AUTOMATION_IMPORT",)),
    S("automation_import", "create_schedules", "Recreate imported schedules",
      "When an imported automation ran on a schedule, create the matching schedule here.",
      "toggle", "Integrations", False, "maverick.config"),
    S("event_triggers", "enable", "Start workflows from events",
      "Watch connected services and start the chosen workflow when something new arrives.",
      "toggle", "Integrations", False, "maverick.automation_events",
      env=("MAVERICK_EVENT_TRIGGERS",)),
    S("flows", "public_url", "Web address for approval links",
      "The address people use to reach this dashboard, used in Approve and Reject links sent to chat apps.",
      "text", "Integrations", "", "maverick_dashboard.automation_queue", max_length=300,
      pattern=r"(" + _HTTPS + r")?"),
    S("tax", "auto_update", "Update tax figures automatically",
      "Download signed updates to tax figures when an update address and trusted keys are set.",
      "toggle", "Integrations", True, "maverick.config"),
    S("tax", "check_hours", "Check for tax updates every",
      "How often to look for new tax figures.",
      "number", "Integrations", 20.0, "maverick.config", minimum=1, maximum=720, unit="hours"),
    S("shield", "federated_updates", "Receive shared threat updates",
      "Download signed updates to the threat checks when an update address and key are set.",
      "toggle", "Integrations", False, "maverick.shield_updates"),
    S("ai_cloud_agent_governance", "enable", "Oversee cloud AI agents",
      "Keep an inventory of the AI agents your cloud accounts run and govern them. Choose at least one cloud below.",
      "toggle", "Integrations", False, "maverick.ai_cloud_agent_governance"),
    S("ai_cloud_agent_governance", "enabled_providers", "Clouds to oversee",
      "The cloud services whose AI agents are brought under oversight.",
      "multiselect", "Integrations", [], "maverick.ai_cloud_agent_governance",
      choices=(("aws_bedrock", "Amazon Bedrock"), ("azure_ai_foundry", "Microsoft Azure AI Foundry"),
               ("google_vertex_ai", "Google Vertex AI"))),

    # ------------------------------------------------------------- Performance
    S("query_limits", "timeout_seconds", "Time limit for data look-ups",
      "How long a single look-up in a connected data source may take.",
      "number", "Performance", 30, "maverick.query_execution", minimum=1, maximum=300,
      unit="seconds"),
    S("connector_limits", "max_attempts", "Retries for connected services",
      "How many times a call to a connected service is tried before giving up.",
      "number", "Performance", 3, "maverick.connector_attempts", minimum=1, maximum=5,
      integer=True, unit="attempts"),
    S("flows", "max_node_retries", "Retries for each workflow step",
      "How many times a failed workflow step is retried.",
      "number", "Performance", 5, "maverick.config", minimum=1, maximum=20, integer=True,
      unit="retries", env=("MAVERICK_FLOW_MAX_NODE_RETRIES",)),
    S("cache", "prewarm", "Warm up AI connections",
      "Open connections to AI services early so the first reply is faster.",
      "toggle", "Performance", False, "maverick.llm"),
    S("latency", "hedge_ms", "Send a backup request after",
      "If an AI service has not answered after this long, send the same request again and use the first reply. Leave unset to never do this.",
      "number", "Performance", None, "maverick.llm", minimum=100, maximum=60000,
      integer=True, unit="milliseconds"),
    S("tools", "deferred_loading", "Load tools only when needed",
      "Describe rarely used tools to agents only when they need them, to save time and cost.",
      "toggle", "Performance", False, "maverick.tools"),
    S("tools", "output_cache", "Reuse recent tool results",
      "Reuse the result of an identical recent tool call instead of repeating it.",
      "toggle", "Performance", False, "maverick.cache.tool"),
    S("tools", "hardware_sensors", "Read this computer's sensors",
      "Let agents read temperature, battery and similar sensors on this computer.",
      "toggle", "Performance", False, "maverick.tools"),
    S("dashboard", "compress", "Compress dashboard pages",
      "Send smaller pages to browsers. Turn off only if another server in front already does this.",
      "toggle", "Performance", True, "maverick_dashboard.app", restart=True,
      env=("MAVERICK_DASHBOARD_COMPRESS",)),
    S("rate_limits", "web_search", "Web searches allowed",
      "The most web searches allowed in a period of time. Clear it to use the installer's choice.",
      "rate", "Performance", None, "maverick.safety.rate_limiter", key_kind="tool"),
    S("rate_limits", "http_fetch", "Web page reads allowed",
      "The most web page reads allowed in a period of time. Clear it to use the installer's choice.",
      "rate", "Performance", None, "maverick.safety.rate_limiter", key_kind="tool"),
    S("rate_limits", "shell", "Commands allowed",
      "The most commands allowed in a period of time. Clear it to use the installer's choice.",
      "rate", "Performance", None, "maverick.safety.rate_limiter", key_kind="tool"),
)

BY_ID: dict[str, Setting] = {s.id: s for s in SETTINGS}

# Top-level tables the catalog may write; settings_store's allowlisted
# serializer emits exactly these (plus its own existing tables).
CATALOG_TOP_SECTIONS: tuple[str, ...] = tuple(sorted({s.top for s in SETTINGS}))

# Settings that must agree with another setting before the kernel accepts
# them: enabling cloud-agent oversight without choosing a cloud makes the
# module's configuration invalid, so the pair is validated together.
_REQUIRES_NONEMPTY: dict[str, str] = {
    "ai_cloud_agent_governance.enable": "ai_cloud_agent_governance.enabled_providers",
    # Strict residency refuses to start without a declared region.
    "residency.strict": "residency.region",
}


class SettingError(ValueError):
    """A value the catalog refuses, with a plain sentence for the page."""


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def coerce(setting: Setting, value: object) -> object:  # noqa: C901 - one branch per control
    """Validate and normalize one submitted value for ``setting``.

    ``None`` is only accepted for numbers without a default (meaning "no
    limit"), which callers treat as removing the dashboard value.
    """
    control = setting.control
    label = setting.label
    if control == "toggle":
        if not isinstance(value, bool):
            raise SettingError(f"{label}: choose on or off.")
        return value
    if control == "select":
        allowed = {v for v, _ in setting.choices}
        text = str(value) if not isinstance(value, bool) and value is not None else None
        if text is None or text not in allowed:
            raise SettingError(f"{label}: choose one of the listed options.")
        if _is_number(setting.default):
            return int(text)
        return text
    if control == "multiselect":
        if not isinstance(value, list) or len(value) > len(setting.choices):
            raise SettingError(f"{label}: choose from the listed options.")
        allowed = [v for v, _ in setting.choices]
        picked = []
        for item in value:
            if not isinstance(item, str) or item not in allowed:
                raise SettingError(f"{label}: choose from the listed options.")
            if item not in picked:
                picked.append(item)
        # Nothing ticked means "stop setting this here": an empty list would
        # otherwise read as "allow none" to the kernel.
        return sorted(picked, key=allowed.index) or None
    if control == "number":
        if value is None or value == "":
            if setting.default is None:
                return None
            raise SettingError(f"{label}: enter a number.")
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError as exc:
                raise SettingError(f"{label}: enter a number.") from exc
        if not _is_number(value) or not math.isfinite(float(value)):
            raise SettingError(f"{label}: enter a number.")
        number = float(value)
        if setting.integer:
            if number != int(number):
                raise SettingError(f"{label}: enter a whole number.")
            number = int(number)
        elif isinstance(setting.default, int) and not isinstance(setting.default, bool) \
                and number == int(number):
            number = int(number)
        if setting.minimum is not None and number < setting.minimum:
            raise SettingError(f"{label}: enter at least {_fmt(setting.minimum)}.")
        if setting.maximum is not None and number > setting.maximum:
            raise SettingError(f"{label}: enter at most {_fmt(setting.maximum)}.")
        return number
    if control in {"text", "secret"}:
        if not isinstance(value, str):
            raise SettingError(f"{label}: enter text.")
        text = value.strip()
        if len(text) > setting.max_length:
            raise SettingError(f"{label}: use at most {setting.max_length} characters.")
        if control == "secret" and not text:
            raise SettingError(f"{label}: enter a value, or use Reset to remove it.")
        if setting.pattern and not re.fullmatch(setting.pattern, text):
            raise SettingError(f"{label}: this value is not in the expected form.")
        # An emptied text box means "stop setting this here".
        return text or None
    if control == "rate":
        if value is None or value == "":
            return None
        if not isinstance(value, dict):
            raise SettingError(f"{label}: enter how many and how often.")
        count, seconds = value.get("count"), value.get("seconds")
        if not (_is_number(count) and _is_number(seconds)) or count != int(count) \
                or seconds != int(seconds) or not 1 <= count <= 100000 \
                or not 1 <= seconds <= 86400:
            raise SettingError(f"{label}: enter a whole number of times and a period.")
        return f"{int(count)}/{int(seconds)}"
    if control == "list":
        if not isinstance(value, list) or len(value) > setting.max_items:
            raise SettingError(f"{label}: use at most {setting.max_items} entries.")
        items: list[str] = []
        for raw in value:
            if not isinstance(raw, str):
                raise SettingError(f"{label}: each entry must be text.")
            item = raw.strip()
            if not item:
                continue
            if len(item) > setting.max_length or (
                setting.pattern and not re.fullmatch(setting.pattern, item)
            ):
                raise SettingError(f"{label}: each entry must be a secure web address.")
            if item not in items:
                items.append(item)
        return items
    raise SettingError(f"{label}: this setting cannot be changed here.")  # pragma: no cover


RATE_PERIODS: tuple[tuple[int, str], ...] = (
    (60, "minute"), (3600, "hour"), (86400, "day"),
)


def parse_rate(value: object) -> tuple[int, int] | None:
    """``"10/60"`` -> ``(10, 60)``; anything else -> None."""
    match = re.fullmatch(r"\s*(\d{1,6})\s*/\s*(\d{1,6})\s*", str(value or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _period_text(seconds: int) -> str:
    for size, name in RATE_PERIODS:
        if seconds == size:
            return name
    return f"{seconds} seconds"


def _fmt(number: float) -> str:
    return str(int(number)) if float(number) == int(number) else str(number)


def weakens(setting: Setting, old: object, new: object) -> bool:
    """True when moving from the effective ``old`` to ``new`` lowers a protection."""
    if not setting.warning or old == new:
        return False
    if setting.control == "toggle":
        return new is setting.weaker and old is not setting.weaker
    if setting.control == "select" and setting.order:
        order = list(setting.order)
        try:
            return order.index(str(new)) > order.index(str(old))
        except ValueError:
            return str(new) != str(setting.order[0])
    if setting.control == "number":
        if setting.weaker == "lower":
            if new is None:
                return False
            return old is None or float(new) < float(old)
        if setting.weaker == "higher":
            if new is None:
                return old is not None
            return old is not None and float(new) > float(old)
    return False


def cross_checks(effective: dict[str, object]) -> None:
    """Refuse combinations the kernel would reject as a broken configuration."""
    for setting_id, needed in _REQUIRES_NONEMPTY.items():
        if effective.get(setting_id) is True and not effective.get(needed):
            first, second = BY_ID[setting_id], BY_ID[needed]
            raise SettingError(
                f"{first.label}: choose at least one option in “{second.label}” first."
            )


def lookup(tree: object, path: tuple[str, ...]) -> tuple[bool, object]:
    """``(present, value)`` for a nested key path in a config mapping."""
    node = tree
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def display_value(setting: Setting, value: object) -> str:
    """Plain words for a value (never a secret's content)."""
    if setting.secret:
        return "Set" if value else "Not set"
    if value is None or value == "" or value == []:
        if setting.control == "number":
            return "No limit"
        if setting.control == "select":
            return dict(setting.choices).get("", "Not set")
        return "Not set"
    if setting.control == "toggle":
        return "On" if value is True else "Off"
    if setting.control in {"select", "multiselect"}:
        labels = dict(setting.choices)
        if setting.control == "multiselect" and isinstance(value, list):
            return ", ".join(labels.get(str(v), str(v)) for v in value) or "None"
        return labels.get(str(value), "Other value")
    if setting.control == "list" and isinstance(value, list):
        return f"{len(value)} address" + ("" if len(value) == 1 else "es")
    if setting.control == "rate":
        parsed = parse_rate(value)
        if parsed is None:
            return "Other value"
        count, seconds = parsed
        return f"{count} every {_period_text(seconds)}"
    if setting.control == "number" and _is_number(value):
        text = _fmt(float(value))
        return f"{text} {setting.unit}".strip()
    return str(value)


__all__ = [
    "BY_ID", "CATALOG_TOP_SECTIONS", "GROUPS", "GROUP_INTROS", "SETTINGS",
    "RATE_PERIODS", "Setting", "SettingError", "coerce", "cross_checks",
    "display_value", "lookup", "parse_rate", "weakens",
]


def _assert_consistent() -> None:
    for s in SETTINGS:
        assert s.control in CONTROLS, s.id
        assert s.group in GROUPS, s.id
        if s.control in {"select", "multiselect"}:
            assert s.choices, s.id
        if s.warning:
            assert s.weaker is not None, s.id


_assert_consistent()
