"""All-settings catalog rows for the runtime group.

Kept apart from settings_catalog.py so each group can be extended without
editing the shared catalog. entries receives the catalog's own row and
column types (a plain import would be circular).

This group covers how agents run: AI services and models, planning and
thinking, conversation length, tools and their caches, the protected work
area, safety checks, plugins, document search and memory back ends, and the
diagnostics a running server reads. Every row names the kernel module that
reads it; a value the environment sets (``env``) wins over this page.
"""
from __future__ import annotations

_HTTPS = r"https://[^\s\"'<>?#]{1,300}"
# An address that is secure, or plain http only on this computer.
_ADDRESS = (r"(" + _HTTPS + r"|http://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]{1,5})?"
            r"(/[^\s\"'<>?#]{0,300})?)?")
# A file on the server: an absolute path or one under the home folder.
_SERVER_PATH = r"((/|~/|[A-Za-z]:\\)[^\x00-\x1f\"'<>|?*]{0,500})?"
_HOST = r"[A-Za-z0-9*?.\[\]:-]{1,253}"
_ROLES = (("orchestrator", "Lead agent"), ("researcher", "Researcher"), ("coder", "Coder"),
          ("writer", "Writer"), ("analyst", "Analyst"), ("revisor", "Reviser"),
          ("verifier", "Answer checker"), ("summarizer", "Summariser"),
          ("skill_distiller", "Skill writer"), ("vision", "Image reader"),
          ("translator", "Translator"), ("coding", "Coding helper"),
          ("reviewer", "Reviewer"))
_RISKS = (("low", "Low risk"), ("medium", "Medium risk"), ("high", "High risk"))
_SEVERITIES = (("medium", "Medium"), ("high", "High (default)"), ("critical", "Critical"))


def entries(S, C, Column) -> tuple:  # noqa: ARG001 -- Column kept for the shared signature
    return (_models(S, C) + _providers(S) + _work_area(S, C) + _safety(S, C)
            + _tools(S, C) + _memory(S) + _diagnostics(S, C))


def _models(S, C) -> tuple:
    return (
        # ------------------------------------------------ effort and planning
        S("adaptive_compute", "low_uncertainty", "Use fewer attempts below this uncertainty",
          "When agents are this sure or surer, fewer parallel attempts are made, down to the "
          "fewest below. From 0 (never) to 1 (always).", "number", "AI models", 0.2,
          "maverick.config", subgroup="Effort and planning", minimum=0, maximum=1, step=0.05,
          unit="share"),
        S("adaptive_compute", "min_width", "Fewest attempts when agents are sure",
          "The fewest parallel attempts made when agents are confident.", "number",
          "AI models", 1, "maverick.config", subgroup="Effort and planning", minimum=1,
          maximum=16, integer=True, unit="attempts"),
        S("planning", "candidates", "Plans compared before starting",
          "How many plans are written and compared when agents compare several plans first. "
          "Each plan is one extra request to the AI service.", "number", "AI models", 3,
          "maverick.tree_of_thought", subgroup="Effort and planning", minimum=1, maximum=10,
          integer=True, unit="plans", env=("MAVERICK_TOT_CANDIDATES",)),
        S("thinking", "budget", "Thinking allowance for the lead agent",
          "How much private reasoning, in tokens, the lead agent and the reviser may use on "
          "each step. Thinking is billed like written text.", "number", "AI models", 8000,
          "maverick.agent", subgroup="Effort and planning", minimum=1024, maximum=128000,
          integer=True, unit="tokens"),
        S("thinking", "adaptive", "Adjust thinking to past results",
          "Use less thinking where recent work succeeded easily and more where it struggled.",
          "toggle", "AI models", False, "maverick.thinking_budget",
          subgroup="Effort and planning"),
        S("speculative", "draft_model", "Quick model for predictable steps",
          "The cheaper model that drafts predictable steps when that option is on.", "select",
          "AI models", "", "maverick.config", subgroup="Effort and planning",
          choices_from="models"),
        S("persona", "addendum", "Extra instruction for every agent",
          "A sentence added to every agent's instructions, for example: Always cite your "
          "sources.", "text", "AI models", "", "maverick.persona",
          subgroup="Effort and planning", max_length=500, pattern=r"[^\n\r<>{}]{0,500}"),
        S("agent_factory", "jd_hiring", "Hire agents from a job description",
          "Offer the page that turns a written job description into a new agent.", "toggle",
          "AI models", True, "maverick.jd_hiring", subgroup="Effort and planning"),

        # ------------------------------------------------ conversation length
        S("context", "max_output_tokens", "Longest reply per step",
          "The most text, in tokens, an agent may write in one step. Leave empty to size it "
          "to each model.", "number", "AI models", None, "maverick.context_scaling",
          subgroup="Conversation length", minimum=1024, maximum=128000, integer=True,
          unit="tokens", none_text="Sized to each model", env=("MAVERICK_AGENT_MAX_TOKENS",)),
        S("context", "window_override", "Treat every model as reading at most",
          "Use this reading limit, in tokens, for every model instead of each model's known "
          "limit. Leave empty to use the known limits.", "number", "AI models", None,
          "maverick.context_scaling", subgroup="Conversation length", minimum=8000,
          maximum=10000000, integer=True, unit="tokens", none_text="Known limit of each model",
          env=("MAVERICK_MODEL_CONTEXT_WINDOW",)),
        S("context", "model_windows", "Reading limits for particular models",
          "Tell Lightwork how much a model can read at once when it does not know, for "
          "example a model you run yourself.", "table", "AI models", {}, "maverick.preflight",
          subgroup="Conversation length", row_kind="grid", choices_from="models", scalar=True,
          columns=(C("window", "Reading limit", "number", minimum=1000, maximum=10000000,
                     integer=True, unit="tokens", required=True),),
          row_label="Model", item_label="Model", add_label="Add a limit",
          empty_text="No model has its own reading limit."),
        S("context", "preflight", "Check each request fits the model",
          "Before a request is sent, estimate whether it fits what the model can read.",
          "select", "AI models", "warn", "maverick.llm", subgroup="Conversation length",
          choices=(("strict", "Refuse requests that won't fit"),
                   ("warn", "Send them and record a warning (default)"),
                   ("off", "Don't check")),
          weaker=True, order=("strict", "warn", "off"), env=("MAVERICK_PREFLIGHT",),
          warning="Requests too long for the model will be sent unchecked, spending money "
                  "on calls that fail."),
        S("context", "history_tokens", "Shorten conversations to about",
          "When a conversation is shortened, aim for this much text, in tokens. Leave empty "
          "to size it to each model.", "number", "AI models", None,
          "maverick.context_compactor", subgroup="Conversation length", minimum=500,
          maximum=2000000, integer=True, unit="tokens", none_text="Sized to each model",
          env=("MAVERICK_HISTORY_TOKENS",)),
        S("context", "history_window", "Recent turns considered when shortening",
          "How many recent turns are looked at when a conversation is shortened. Leave empty "
          "to size it to each model.", "number", "AI models", None,
          "maverick.context_compactor", subgroup="Conversation length", minimum=2,
          maximum=10000, integer=True, unit="turns", none_text="Sized to each model",
          env=("MAVERICK_HISTORY_WINDOW",)),
        S("context", "router_threshold_tokens", "Read a document in parts above",
          "Documents longer than this, in tokens, are read in parts when that option is on. "
          "Leave empty to size it to each model.", "number", "AI models", None,
          "maverick.long_context_router", subgroup="Conversation length", minimum=1000,
          maximum=10000000, integer=True, unit="tokens", none_text="Sized to each model",
          env=("MAVERICK_ROUTER_THRESHOLD_TOKENS",)),
        S("context", "router_top_k", "Parts of a long document kept",
          "How many of the most relevant parts are given to agents.", "number", "AI models",
          12, "maverick.long_context_router", subgroup="Conversation length", minimum=1,
          maximum=200, integer=True, unit="parts", env=("MAVERICK_ROUTER_TOP_K",)),
        S("context", "async_compaction", "Reuse work when shortening conversations",
          "Keep counts from earlier shortening so long conversations shorten faster. Works "
          "together with keeping the most useful older turns.", "toggle", "AI models", False,
          "maverick.async_compaction", subgroup="Conversation length",
          env=("MAVERICK_ASYNC_COMPACTION",)),
        S("context", "compaction_stream_ttl_days", "Remove unused running summaries after",
          "Running summaries of very long chats that nobody has used for this long are "
          "removed.", "number", "AI models", 30, "maverick.compaction.streaming",
          subgroup="Conversation length", minimum=1, maximum=3650, integer=True, unit="days",
          weaker="higher", warning="Summaries of old conversations will be kept for longer."),
        S("context", "retrieval_router", "Read very long pasted documents in parts",
          "When a task includes a very long document, give agents only the parts that "
          "matter to each step.", "toggle", "AI models", False,
          "maverick.long_context_router", subgroup="Conversation length",
          env=("MAVERICK_RETRIEVAL_ROUTER",)),
        S("compaction", "hybrid", "Learn which shortening method works best",
          "Try the shortening methods on real conversations and prefer the one that works "
          "best. A method chosen above always wins.", "toggle", "AI models", False,
          "maverick.compaction.hybrid", subgroup="Conversation length",
          env=("MAVERICK_COMPACTION_HYBRID",)),

        # ------------------------------------------------------- model routing
        S("models", "catalog", "Extra models to offer",
          "Models that are not in the built-in list, so they can be chosen everywhere. Write "
          "each as the service name, a colon and the model name.", "list", "AI models", [],
          "maverick_dashboard.model_pickers", subgroup="Model choices", item_label="Model",
          item_hint="vllm:my-model", pattern=r"[a-z0-9_]{1,32}:[A-Za-z0-9._:/@-]{1,160}",
          item_error="a service name, a colon and a model name", max_items=50,
          max_length=200),
        S("models", "discovery_ttl_minutes", "Refresh model lists every",
          "How long a list of models fetched from an AI service is used before it is fetched "
          "again.", "number", "AI models", 60, "maverick.model_discovery",
          subgroup="Model choices", minimum=1, maximum=1440, integer=True, unit="minutes"),
        S("routing", "max_error_rate", "Skip an AI service that fails more than",
          "When picking the cheapest model, leave out services whose recent requests failed "
          "at least this often.", "number", "AI models", 0.5, "maverick.cost.router",
          subgroup="Model choices", minimum=5, maximum=100, scale=0.01,
          unit="percent", env=("MAVERICK_ROUTING_MAX_ERROR_RATE",)),
        S("routing", "battery_threshold_pct", "Prefer lower-energy models below",
          "On battery, switch to the lower-energy model once the charge falls below this.",
          "number", "AI models", 20, "maverick.energy_aware_router", subgroup="Model choices",
          minimum=1, maximum=100, integer=True, unit="percent"),
        S("routing", "roles", "Model rules for each kind of agent",
          "Limit which AI services each kind of agent may use when models are picked "
          "automatically, and how capable its model must be.", "table", "AI models", {},
          "maverick.cost.router", subgroup="Model choices", row_kind="grid", choices=_ROLES,
          columns=(
              C("providers", "Only these services", "picks", choices_from="providers",
                max_items=20),
              C("deny_providers", "Never these services", "picks", choices_from="providers",
                max_items=20),
              C("tier", "Model level", "select", default="",
                choices=(("", "Any level"), ("cheap", "Low cost"), ("base", "Standard"),
                         ("premium", "Most capable"))),
          ),
          row_label="Kind of agent", item_label="Kind of agent", add_label="Add rules",
          empty_text="Every kind of agent may use any connected service.",
          empty_hint="No AI services are set up yet. Add one under AI service keys on the "
                     "Settings page first."),
    )


def _providers(S) -> tuple:
    return (
        S("providers.anthropic", "base_url", "Claude service address",
          "Send Claude requests through this address instead of Anthropic's own, for example "
          "a regional gateway. Leave empty for the standard address.", "text", "Integrations",
          "", "maverick.config", subgroup="AI service addresses", pattern=_ADDRESS,
          max_length=320, weaker="change",
          warning="Claude requests, and the task text in them, will go to a different "
                  "address."),
        S("providers.openai_compatible", "base_url", "Compatible AI service address",
          "The address of an AI service that speaks the OpenAI request format, such as your "
          "own model server or a company gateway.", "text", "Integrations", "",
          "maverick.config", subgroup="AI service addresses", pattern=_ADDRESS,
          max_length=320, weaker="change",
          warning="Requests, and the task text in them, will go to a different address."),
        S("providers.openai_compatible", "api_key", "Compatible AI service key",
          "The key for the service above. It is stored sealed and never shown again.",
          "secret", "Integrations", "", "maverick.config", subgroup="AI service addresses",
          max_length=500),
        S("providers.azure", "base_url", "Microsoft Azure address",
          "The address of your Azure OpenAI resource.", "text", "Integrations", "",
          "maverick.config", subgroup="Microsoft Azure", pattern=r"(" + _HTTPS + r")?",
          max_length=320, weaker="change",
          warning="Azure requests, and the task text in them, will go to a different "
                  "address."),
        S("providers.azure", "api_key", "Microsoft Azure key",
          "The key for your Azure OpenAI resource. It is stored sealed and never shown again.",
          "secret", "Integrations", "", "maverick.config", subgroup="Microsoft Azure",
          max_length=500),
        S("providers.azure", "auth_mode", "How Lightwork signs in to Azure",
          "Use the key above, or the server's Microsoft sign-in.", "select", "Integrations",
          "", "maverick.config", subgroup="Microsoft Azure",
          choices=(("", "Decide from what is set (default)"), ("api_key", "The key above"),
                   ("entra_id", "The server's Microsoft sign-in"))),
        S("providers.azure", "deployment", "Azure model deployment",
          "The name of the model deployment in your Azure resource.", "text", "Integrations",
          "", "maverick.llm", subgroup="Microsoft Azure", pattern=r"[A-Za-z0-9._-]{0,64}",
          max_length=64),
        S("providers.azure", "api_version", "Azure service version",
          "The Azure OpenAI version date to use. Leave empty for the standard one.", "text",
          "Integrations", "", "maverick.llm", subgroup="Microsoft Azure",
          pattern=r"(20[0-9]{2}-[0-9]{2}-[0-9]{2}(-preview)?)?", max_length=20),
        S("providers.bedrock", "region", "Amazon Bedrock region",
          "The AWS region your Bedrock models run in, such as us-east-1.", "text",
          "Integrations", "", "maverick.llm", subgroup="Amazon Bedrock",
          pattern=r"([a-z]{2}(-gov)?-[a-z]+-[0-9])?", max_length=20),
        S("providers.bedrock", "endpoint", "Amazon Bedrock service",
          "Which Bedrock service Lightwork calls.", "select", "Integrations", "mantle",
          "maverick.llm", subgroup="Amazon Bedrock",
          choices=(("mantle", "Current service (default)"), ("legacy", "Older runtime service"))),
        S("providers.bedrock", "api_key", "Amazon Bedrock access key",
          "The Bedrock access key, when Lightwork signs in with one. It is stored sealed and "
          "never shown again.", "secret", "Integrations", "", "maverick.llm",
          subgroup="Amazon Bedrock", max_length=500),
        S("providers", "per_tenant_keys", "Let each workspace use its own AI service key",
          "A workspace that has its own key for an AI service uses it instead of the shared "
          "one.", "toggle", "Integrations", False, "maverick.config",
          subgroup="AI service addresses", env=("MAVERICK_PER_TENANT_KEYS",), weaker=True,
          warning="Workspaces with their own key will send their work to the AI service "
                  "under that key instead of the shared one."),
        S("provider_failover.policy", "failover_on", "Move to a backup model after",
          "The kinds of failure that send a request on to the backup models. With none "
          "ticked, every failure except a wrong key or a bad request does.", "multiselect",
          "Integrations", [], "maverick.failover_policy", subgroup="Backup AI models",
          choices=(("rate_limit", "The service is busy"), ("timeout", "No reply in time"),
                   ("network", "The connection failed"), ("server", "A service fault"),
                   ("auth", "A wrong or expired key"),
                   ("bad_request", "The request was refused"), ("other", "Anything else")),
          weaker="grow",
          warning="Requests will move to your backup AI services in more situations, "
                  "which may send task text to another service."),
        S("provider_failover.policy", "cooldown_s", "Rest a failing model for",
          "After repeated failures a model is skipped for this long. Zero never rests it.",
          "number", "Integrations", 0, "maverick.failover_policy", subgroup="Backup AI models",
          minimum=0, maximum=3600, integer=True, unit="seconds"),
        S("provider_failover.policy", "cooldown_after", "Failures before resting a model",
          "How many failures in a row make a model rest.", "number", "Integrations", 2,
          "maverick.failover_policy", subgroup="Backup AI models", minimum=1, maximum=20,
          integer=True, unit="failures"),
        # ------------------------------------------------------------ AI gateway
        S("llm_gateway", "gateway_token", "Gateway password for callers",
          "The password agents built elsewhere send to use the gateway. It is stored sealed "
          "and never shown again.", "secret", "AI governance", "", "maverick.llm_gateway",
          subgroup="AI gateway", max_length=500, weaker="change",
          warning="Callers using the old password will be refused, and anyone with the new "
                  "one can send requests that spend your budget."),
        S("llm_gateway", "upstream_token", "Key for the AI service behind the gateway",
          "The key the gateway uses with the AI service it forwards to. It is stored sealed "
          "and never shown again.", "secret", "AI governance", "", "maverick.llm_gateway",
          subgroup="AI gateway", max_length=500),
        S("llm_gateway", "anthropic_upstream_token", "Key for the Claude service behind the gateway",
          "The key used for requests written for Claude. It is stored sealed and never shown "
          "again.", "secret", "AI governance", "", "maverick.llm_gateway",
          subgroup="AI gateway", max_length=500),
        S("llm_gateway", "anthropic_version", "Claude request version",
          "The version date sent with requests written for Claude. Leave empty for the "
          "standard one.", "text", "AI governance", "", "maverick.llm_gateway",
          subgroup="AI gateway", pattern=r"(20[0-9]{2}-[0-9]{2}-[0-9]{2})?", max_length=10),
    )


def _work_area(S, C) -> tuple:
    group = "Protected work area"
    return (
        S("sandbox", "workdir", "Folder for agent files",
          "The folder on the server where agents keep the files they work on.", "text", group,
          "~/maverick-workspace", "maverick.sandbox", subgroup="Work area", pattern=_SERVER_PATH, max_length=500,
          weaker="change",
          warning="Agents will read and write files in a different folder on the server."),
        S("sandbox", "require_container", "Never run commands directly on the server",
          "Refuse to run agent commands outside a container, even when no container program "
          "is chosen.", "toggle", group, False, "maverick.sandbox", subgroup="Work area",
          env=("MAVERICK_REQUIRE_CONTAINER_BACKEND",), weaker=False,
          warning="Agent commands may run directly on the server when no container is "
                  "available."),
        S("sandbox", "reuse_container", "Keep one container for a whole task",
          "Start the work area once per task instead of once per command, which is faster.",
          "toggle", group, False, "maverick.sandbox", subgroup="Work area"),
        S("sandbox", "cross_run_pool", "Keep ready work areas between tasks",
          "Keep a clean work area ready so the next task starts faster.", "toggle", group,
          False, "maverick.sandbox", subgroup="Work area"),
        S("sandbox", "runtime", "Stronger isolation program",
          "The name of the isolation program used when the work area type is the stronger "
          "isolation option.", "text", group, "runsc", "maverick.sandbox",
          subgroup="Work area", pattern=r"[A-Za-z0-9._/-]{0,128}", max_length=128,
          weaker="change",
          warning="Commands will run under a different isolation program."),
        S("sandbox", "ssh_args", "Extra options for the remote computer",
          "Extra connection options used when commands run on another computer, one per "
          "entry.", "list", group, [], "maverick.sandbox", subgroup="Remote computer",
          item_label="Option", item_hint="-p2222", pattern=r"-[A-Za-z0-9][A-Za-z0-9=._:/,-]{0,120}",
          item_error="a connection option starting with a dash", max_items=20,
          weaker="change",
          warning="Commands will reach the remote computer with different connection "
                  "options."),
        S("sandbox", "context", "Cluster to use",
          "The named cluster connection to use, when your cluster settings file lists more "
          "than one. Leave empty for its default.", "text", group, "", "maverick.sandbox",
          subgroup="Cluster", pattern=r"[A-Za-z0-9@._:/-]{0,200}", max_length=200,
          weaker="change", warning="Agent commands will run in a different cluster."),
        S("sandbox", "kubeconfig", "Cluster settings file",
          "Where the cluster settings file is on the server. Leave empty for the standard "
          "one.", "text", group, "", "maverick.sandbox", subgroup="Cluster",
          pattern=_SERVER_PATH, max_length=500, weaker="change",
          warning="Agent commands will run with different cluster settings."),
        S("sandbox", "run_as_user", "User number inside the cluster",
          "The user number cluster commands run as. Zero is the all-powerful user.", "number",
          group, 1000, "maverick.sandbox", subgroup="Cluster", minimum=0, maximum=2147483647,
          integer=True, unit="", weaker="lower",
          warning="Cluster commands may run as a more powerful user."),
        S("sandbox", "extra_kubectl_args", "Extra cluster options",
          "Extra options added to every cluster command, one per entry.", "list", group, [],
          "maverick.sandbox", subgroup="Cluster", item_label="Option",
          item_hint="--request-timeout=30s",
          pattern=r"--[a-z][a-z0-9-]{0,60}(=[A-Za-z0-9._:/,@-]{1,120})?",
          item_error="an option starting with two dashes", max_items=20, weaker="change",
          warning="Cluster commands will run with different options."),
        S("sandbox", "project_dir", "Project folder for development containers",
          "The folder whose development container settings are used for that work area "
          "type.", "text", group, "", "maverick.sandbox", subgroup="Development container",
          pattern=_SERVER_PATH, max_length=500, weaker="change",
          warning="Commands will run in a container built from a different project."),
        S("sandbox", "memory_mb", "Memory for cloud work areas",
          "The most memory a cloud work area may use. Leave empty for the service's choice.",
          "number", group, None, "maverick.sandbox", subgroup="Cloud work areas",
          minimum=256, maximum=262144, integer=True, unit="MB",
          none_text="The service's choice"),
        S("sandbox", "provider", "Where micro-machines run",
          "Run micro-machine work areas on this server, or with the E2B hosted service.",
          "select", group, "local", "maverick.sandbox", subgroup="Cloud work areas",
          choices=(("local", "On this server (default)"), ("e2b", "E2B hosted service")),
          weaker="change", warning="Agent commands and their files will run on an outside "
                                   "service."),
        S("sandbox", "api_key", "E2B key",
          "The key for the E2B hosted service. It is stored sealed and never shown again.",
          "secret", group, "", "maverick.sandbox", subgroup="Cloud work areas",
          max_length=500),
        S("sandbox", "network", "Internet for micro-machines",
          "Whether micro-machine work areas may reach the internet.", "select", group,
          "egress-deny", "maverick.sandbox", subgroup="Cloud work areas",
          choices=(("egress-deny", "No internet (default)"),
                   ("egress-allow", "Allow the internet")),
          weaker=True, order=("egress-deny", "egress-allow"),
          warning="Agent commands will be able to send data to, and download programs from, "
                  "anywhere on the internet."),
        S("sandbox", "warm", "Keep a hosted micro-machine running",
          "Reuse one hosted micro-machine between commands instead of starting a new one "
          "each time.", "toggle", group, False, "maverick.sandbox",
          subgroup="Cloud work areas"),
        # ----------------------------------------------------- locked-down mode
        S("containment", "enabled", "Locked-down mode for every task",
          "Run every task with nothing leaving and nothing kept: the tools that send data "
          "out are removed, commands get no internet, work happens in a throwaway folder "
          "and each task has a time limit. Use it while handling untrusted material.",
          "toggle", group, False, "maverick.containment", subgroup="Locked-down mode",
          env=("MAVERICK_CONTAINMENT",), weaker=False,
          warning="Tasks will again be able to send data out and keep their files.",
          aliases="containment untrusted quarantine"),
        S("containment", "no_network", "No internet in locked-down mode",
          "Commands get no internet while locked-down mode is on.", "toggle", group, True,
          "maverick.containment", subgroup="Locked-down mode", weaker=False,
          warning="Commands in locked-down mode will be able to reach the internet."),
        S("containment", "ephemeral_workspace", "Throwaway folder in locked-down mode",
          "Tasks work in a private folder that is deleted afterwards, so nothing they write "
          "is kept.", "toggle", group, True, "maverick.containment",
          subgroup="Locked-down mode", weaker=False,
          warning="Files written by tasks in locked-down mode will be kept."),
        S("containment", "max_wall_seconds", "Time limit in locked-down mode",
          "The longest a task may run while locked-down mode is on.", "number", group, 1800,
          "maverick.containment", subgroup="Locked-down mode", minimum=1, maximum=1440,
          scale=60, unit="minutes", weaker="higher",
          warning="Tasks in locked-down mode will be able to run for longer."),
        S("containment", "deny_tools", "More tools to remove in locked-down mode",
          "Tools removed in addition to the built-in list of tools that send data out, "
          "which is always removed.", "list", group, [], "maverick.containment",
          subgroup="Locked-down mode", item_label="Tool", item_hint="github_issues",
          pattern=r"[A-Za-z0-9_.-]{1,128}", item_error="a tool name", max_items=100,
          weaker="shrink",
          warning="The tools you remove from this list will be usable in locked-down mode."),
        S("sandbox", "tool", "Websites each web tool may reach",
          "Limit the addresses the web tools may contact. Blocked addresses always win; "
          "when you list allowed addresses, only those can be reached. A star matches any "
          "part of a name.", "table", group, {}, "maverick.sandbox.network_policy",
          subgroup="Web tool addresses", row_kind="grid",
          choices=(("http_fetch", "Reading web pages"), ("browser", "Web browser"),
                   ("websocket", "Live connections")),
          columns=(
              C("allow_egress", "Only these addresses", "words", pattern=_HOST,
                max_items=100, item_error="an address such as api.example.com"),
              C("deny_egress", "Never these addresses", "words", pattern=_HOST,
                max_items=100, item_error="an address such as api.example.com"),
          ),
          row_label="Web tool", item_label="Web tool", add_label="Add rules",
          empty_text="The web tools may reach any address the rules below allow.",
          weaker="change",
          warning="The web tools will be able to reach a different set of addresses."),
    )


def _safety(S, C) -> tuple:
    return (
        S("safety", "consent_mode", "Actions that can't be undone",
          "What happens when an agent wants to take an action that deletes, sends or "
          "changes something for good.", "select", "Safety", "", "maverick.safety.consent",
          subgroup="Destructive actions",
          choices=(("", "Ask for risky ones, allow the rest (default)"),
                   ("dashboard", "Wait on the Approvals page"),
                   ("ask", "Ask a person each time"),
                   ("auto-deny", "Refuse them all"),
                   ("auto-approve", "Allow them all")),
          weaker=True, order=("auto-deny", "dashboard", "ask", "", "auto-approve"),
          env=("MAVERICK_CONSENT_MODE",),
          warning="Actions that can't be undone will go ahead with less oversight."),
        S("safety", "constitution", "Your own phrases to block",
          "Requests and answers containing any of these words or phrases are treated as a "
          "threat of the level you choose. Separate phrases with commas.", "rules", "Safety",
          [], "maverick_shield.guard", subgroup="Destructive actions",
          columns=(
              C("phrases", "Words or phrases", "words", required=True,
                pattern=r"[^,<>{}\n\r]{1,80}", max_items=50, max_length=80,
                item_error="a word or phrase without commas"),
              C("severity", "Treat as", "select", default="high", choices=_SEVERITIES),
          ),
          item_label="Rule", add_label="Add phrases",
          empty_text="Only the built-in checks apply.", weaker="change",
          warning="Phrases you remove or lower will no longer be stopped."),
        S("safety", "require_shield", "Require the safety checks",
          "Refuse outside messages and agent requests when Lightwork's safety checks are not "
          "installed, instead of letting them through.", "toggle", "Safety", False,
          "maverick.shield_policy", subgroup="Destructive actions",
          env=("MAVERICK_REQUIRE_SHIELD",), weaker=False,
          warning="Outside messages will be let through unchecked if the safety checks "
                  "are missing."),
        S("embedded", "allow_flash", "Let agents reprogram connected devices",
          "Agents may write new software onto, or reset, circuit boards connected to this "
          "server.", "toggle", "Safety", False, "maverick.tools.embedded_device",
          subgroup="Destructive actions", weaker=True,
          warning="Agents will be able to overwrite the software on connected devices."),
        S("capabilities", "enforce", "Give each agent only what it was granted",
          "Each agent and its helpers may use only the tools and limits handed to them.",
          "toggle", "Safety", False, "maverick.capability", subgroup="Agent permissions",
          env=("MAVERICK_ENFORCE_CAPABILITIES",), weaker=False,
          warning="Helper agents will be able to use tools their lead agent was not given."),
        S("capabilities", "per_call_tokens", "Check a fresh pass for every tool use",
          "Each tool use needs a short-lived pass tied to that one call. Works with the "
          "option above.", "toggle", "Safety", False, "maverick.tool_token",
          subgroup="Agent permissions", env=("MAVERICK_TOOL_TOKENS",), weaker=False,
          warning="Tool uses will no longer need their own pass."),
        S("repl", "max_output_chars", "Output kept from each code step",
          "The most characters of output kept from one step of agent code.", "number",
          "Safety", 8000, "maverick.config", subgroup="Agent permissions", minimum=256,
          maximum=1000000, integer=True, unit="characters"),
        S("repl", "max_state_bytes", "Memory kept between code steps",
          "How much data agent code may carry from one step to the next.", "number", "Safety",
          262144, "maverick.config", subgroup="Agent permissions", minimum=1, maximum=1024,
          integer=True, scale=1024, unit="KB"),
        S("repl", "max_statements", "Code steps per session",
          "The most steps of code an agent may run in one session.", "number", "Safety", 200,
          "maverick.config", subgroup="Agent permissions", minimum=1, maximum=10000,
          integer=True, unit="steps", weaker="higher",
          warning="Agents will be able to run more code in one session."),
        S("ifc", "allow_sinks", "Tools that may carry sensitive data out",
          "These tools are not checked when labelled sensitive data is about to leave through "
          "them.", "multiselect", "Safety", [], "maverick.config",
          subgroup="Agent permissions",
          choices=(("web_search", "Web search"), ("web_archive", "Web archive"),
                   ("browser", "Web browser"), ("computer", "Computer control"),
                   ("email", "Email"), ("gmail", "Gmail"), ("ses", "Amazon email"),
                   ("sns", "Amazon notifications"), ("twilio", "Text messages"),
                   ("slack_bot", "Slack"), ("discord_bot", "Discord"), ("teams", "Teams"),
                   ("notify", "Notifications"), ("calendar", "Calendar"),
                   ("github_issues", "GitHub issues"), ("git_advanced", "Code pushes")),
          weaker="grow",
          warning="Sensitive data will be able to leave through these tools unchecked."),
        # ------------------------------------------------------------- security
        S("security", "max_risk", "Riskiest tools agents may use",
          "Tools above this risk level are removed for every agent.", "select", "Security", "",
          "maverick.safety.tool_acl", subgroup="Tool access",
          choices=(("", "Up to high risk with secure defaults (default)"),
                   ("low", "Low risk only"), ("medium", "Up to medium risk"),
                   ("high", "Up to high risk")),
          weaker=True, order=("low", "medium", "high", ""),
          warning="Agents will be able to use riskier tools."),
        S("security", "tool_risk", "Risk level of particular tools",
          "Change the risk level Lightwork gives a tool. The level decides which approvals "
          "and limits apply to it.", "table", "Security", {}, "maverick.safety.tool_risk",
          subgroup="Tool access", row_kind="grid", choices_from="tools", scalar=True,
          columns=(C("risk", "Risk level", "select", choices=_RISKS, required=True),),
          row_label="Tool", item_label="Tool", add_label="Set a risk level",
          empty_text="Every tool has its built-in risk level.", weaker="change",
          warning="Tools marked as less risky will need fewer approvals and pass more "
                  "limits."),
        S("security", "isolate_parsers", "Open uploaded files in a separate process",
          "Read uploaded documents in a separate, limited program, so a harmful file cannot "
          "reach the rest of Lightwork.", "toggle", "Security", False,
          "maverick.parser_isolation", subgroup="Tool access",
          env=("MAVERICK_ISOLATE_PARSERS",), weaker=False,
          warning="Uploaded files will be read inside Lightwork itself."),
        S("tools", "database_allow_url_arg", "Let agents choose which database to open",
          "Agents may give the database tool an address of their own, instead of only the "
          "one set on the server.", "toggle", "Security", False,
          "maverick.tools.database_tool", subgroup="Tool access",
          env=("MAVERICK_DATABASE_ALLOW_URL_ARG",), weaker=True,
          warning="Agents will be able to connect to any database they can reach."),
        S("egress", "deny", "Addresses no tool may reach",
          "Tools and connections refuse these addresses. A star matches any part of a name; "
          "a single star blocks everything not allowed below.", "list", "Security", [],
          "maverick.tenant.egress", subgroup="Outbound connections",
          item_label="Address", item_hint="169.254.169.254", pattern=_HOST,
          item_error="an address such as api.example.com", max_items=200,
          weaker="shrink", warning="Tools will be able to reach the addresses you remove."),
        S("egress", "allow", "Only these addresses",
          "When this list has entries, tools may reach only these addresses.", "list",
          "Security", [], "maverick.tenant.egress", subgroup="Outbound connections",
          item_label="Address", item_hint="api.example.com", pattern=_HOST,
          item_error="an address such as api.example.com", max_items=200, weaker="grow",
          warning="Tools will be able to reach more addresses, or any address once the list "
                  "is empty."),
        S("safety", "receipt_key", "Key for signed spending receipts",
          "A long secret phrase used to sign each task's spending receipt. It is stored "
          "sealed and never shown again; keep a copy to check receipts later.", "secret",
          "Security", "", "maverick.budget_receipts", subgroup="Signing keys",
          env=("MAVERICK_RECEIPT_KEY",), max_length=500),
        S("safety", "screenshot_key", "Key for sealed screenshots",
          "A long secret phrase used to seal the screenshots agents take as evidence. It is "
          "stored sealed and never shown again.", "secret", "Security", "",
          "maverick.screenshot_seal", subgroup="Signing keys",
          env=("MAVERICK_SCREENSHOT_KEY",), max_length=500),
        # -------------------------------------------------------------- plugins
        S("plugins", "require_signing", "Only load signed plugins",
          "Refuse plugins that are not signed by your plugin signing authority.", "toggle",
          "Integrations", False, "maverick.plugins", subgroup="Plugin safety", weaker=False,
          warning="Unsigned plugins will load and run."),
        S("plugins", "ca_root_pubkey", "Plugin signing authority",
          "Paste the contents of your plugin signing authority's public key file. Setting it "
          "turns signature checks on.", "text", "Integrations", "", "maverick.plugins",
          subgroup="Plugin safety", pattern=r"([0-9a-fA-F]{64})?", max_length=64,
          weaker="change",
          warning="Plugins signed by a different authority will be trusted."),
        S("plugins", "ca_revoked", "Withdrawn plugin signatures",
          "Signatures listed here are refused even though the authority issued them.",
          "list", "Integrations", [], "maverick.plugins", subgroup="Plugin safety",
          item_label="Signature number", item_hint="3f2a9c...",
          pattern=r"[0-9a-fA-F-]{8,64}", item_error="a signature number", max_items=500,
          weaker="shrink", warning="Plugins with the signatures you remove will load again."),
        S("plugins", "isolation", "Where plugins run",
          "Run plugins inside Lightwork, or apart from it so a faulty plugin can't affect "
          "the rest.", "select", "Integrations", "", "maverick.plugin_isolation",
          subgroup="Plugin safety",
          choices=(("", "Apart under enterprise protections, otherwise inside (default)"),
                   ("subprocess", "In a separate program"),
                   ("subinterpreter", "In a separate area of Lightwork"),
                   ("none", "Inside Lightwork")),
          weaker=True, order=("subprocess", "subinterpreter", "", "none"),
          env=("MAVERICK_PLUGIN_ISOLATION",),
          warning="Plugins will run with more access to the rest of Lightwork."),
        S("plugins", "lock_policy", "When a plugin's version changes",
          "What happens when an installed plugin no longer matches the recorded version.",
          "select", "Integrations", "", "maverick.plugin_lock", subgroup="Plugin safety",
          choices=(("", "Refuse under enterprise protections, otherwise nothing (default)"),
                   ("enforce", "Refuse to load it"), ("warn", "Load it and record a warning"),
                   ("off", "Do nothing")),
          weaker=True, order=("enforce", "", "warn", "off"),
          env=("MAVERICK_PLUGIN_LOCK_POLICY",),
          warning="Plugins that changed since they were checked will load."),
        S("plugins", "telemetry", "Count plugin use",
          "Keep a local count of how often each plugin tool is used. Nothing leaves this "
          "server.", "toggle", "Integrations", False, "maverick.plugin_telemetry",
          subgroup="Plugin safety", env=("MAVERICK_PLUGIN_TELEMETRY",)),
    )


def _tools(S, C) -> tuple:
    subgroup = "Connected services"
    return (
        S("connector_limits", "attempt_seconds", "Time limit for each try",
          "How long one try at a connected service may take.", "number", "Performance", 30.0,
          "maverick.connector_attempts", subgroup=subgroup, minimum=1, maximum=120,
          unit="seconds"),
        S("connector_limits", "total_seconds", "Time limit for a call with its retries",
          "How long a call to a connected service may take in total.", "number",
          "Performance", 60.0, "maverick.connector_attempts", subgroup=subgroup, minimum=1,
          maximum=300, unit="seconds"),
        S("connector_limits", "wire_bytes", "Largest reply from a service",
          "Bigger replies from a connected service are refused.", "number", "Performance",
          2097152, "maverick.connector_attempts", subgroup=subgroup, minimum=1, maximum=64,
          integer=True, scale=1048576, unit="MB"),
        S("connector_limits", "decoded_bytes", "Largest unpacked reply",
          "Bigger replies, once unpacked, are refused.", "number", "Performance", 4194304,
          "maverick.connector_attempts", subgroup=subgroup, minimum=1, maximum=64,
          integer=True, scale=1048576, unit="MB"),
        S("connector_limits", "json_depth", "Deepest nesting in a reply",
          "Replies nested deeper than this are refused.", "number", "Performance", 64,
          "maverick.connector_attempts", subgroup=subgroup, minimum=1, maximum=128,
          integer=True, unit="levels"),
        S("connector_limits", "json_items", "Most items in a reply",
          "Replies with more items than this are refused.", "number", "Performance", 100000,
          "maverick.connector_attempts", subgroup=subgroup, minimum=1, maximum=1000000,
          integer=True, unit="items"),
        S("tools", "output_cache_size", "Recent tool results kept",
          "How many recent tool results are kept for reuse.", "number", "Performance", 256,
          "maverick.cache.tool", subgroup="Reusing tool results", minimum=1, maximum=100000,
          integer=True, unit="results"),
        S("tools", "output_cache_ttl_s", "Reuse a tool result for up to",
          "How long a kept result may be reused. Leave empty to keep it until newer results "
          "push it out.", "number", "Performance", None, "maverick.cache.tool",
          subgroup="Reusing tool results", minimum=1, maximum=604800, integer=True,
          unit="seconds", none_text="Until newer results replace it"),
        S("tools", "output_cache_snapshot", "Keep reusable results after a restart",
          "Save kept tool results to disk so they can be reused after Lightwork restarts.",
          "toggle", "Performance", False, "maverick.cache.tool",
          subgroup="Reusing tool results", env=("MAVERICK_TOOL_CACHE_SNAPSHOT",)),
        S("tools", "latency_budget_ms", "Flag tool uses slower than",
          "Record a warning for any tool use that takes longer than this. The tool still "
          "finishes.", "number", "Performance", None, "maverick.latency_budget",
          subgroup="Reusing tool results", minimum=100, maximum=600000, integer=True,
          unit="milliseconds", none_text="Off", env=("MAVERICK_TOOL_LATENCY_BUDGET_MS",)),
        S("capabilities", "deferred_tools", "Hide rarely used connectors until needed",
          "Keep the long list of business-system connectors out of each request until an "
          "agent searches for one.", "toggle", "Performance", True, "maverick.agent",
          subgroup="Reusing tool results", env=("MAVERICK_DEFERRED_TOOLS",)),
        S("limits", "file_write_quota_mb", "Most a task may write to files",
          "The most data one task may write to the work area. Leave empty for no limit.",
          "number", "Spending & limits", None, "maverick.file_quota", minimum=1,
          maximum=1000000, integer=True, unit="MB", weaker="higher",
          warning="A single task will be able to write more data, or any amount."),
        S("attachments", "embed_documents", "Show attached documents to agents directly",
          "Give agents attached PDF documents as documents they can see, not only as "
          "extracted text.", "toggle", "Privacy", True, "maverick.attachments",
          subgroup="Attachments", env=("MAVERICK_ATTACH_EMBED_DOCS",)),
        S("attachments", "extra_mime_prefixes", "Extra kinds of attachment to accept",
          "Accept these file kinds too, written as their standard type name or its start. "
          "Programs and archives are always refused.", "list", "Privacy", [],
          "maverick.attachments", subgroup="Attachments", item_label="File kind",
          item_hint="application/vnd.visio", pattern=r"[a-z0-9]{1,40}/[a-z0-9.+-]{0,120}",
          item_error="a file kind such as application/vnd.visio", max_items=50,
          weaker="grow", warning="More kinds of files will be accepted as attachments."),
        S("attachments", "s3_bucket", "Copy attachments to storage bucket",
          "Also keep a copy of every attachment in this Amazon S3 bucket, using the server's "
          "AWS sign-in. Leave empty to keep them on this server only.", "text", "Privacy", "",
          "maverick.attachments", subgroup="Attachments",
          pattern=r"([a-z0-9][a-z0-9.-]{1,61}[a-z0-9])?", max_length=63, weaker="change",
          warning="Copies of every attachment will be stored in a different place."),
        S("attachments", "s3_prefix", "Folder inside the bucket",
          "The folder in the bucket where copies are kept.", "text", "Privacy", "",
          "maverick.attachments", subgroup="Attachments", pattern=r"[A-Za-z0-9!_.*'()/-]{0,200}",
          max_length=200),
        S("lsp", "servers", "Code language helpers",
          "The program, and its options, agents use to understand code in each language. "
          "Leave a language out to use the standard helper.", "table", "Integrations", {},
          "maverick.tools.lsp_bridge", subgroup="Code language helpers", row_kind="grid",
          choices=(("python", "Python"), ("go", "Go"), ("rust", "Rust"),
                   ("typescript", "TypeScript"), ("javascript", "JavaScript"), ("c", "C"),
                   ("cpp", "C++")),
          scalar=True,
          columns=(C("command", "Program and options", "words", required=True,
                     pattern=r"[A-Za-z0-9._/=:+-]{1,200}", max_items=20, max_length=200,
                     item_error="a program name or option without spaces"),),
          row_label="Language", item_label="Language", add_label="Choose a helper",
          empty_text="Every language uses its standard helper.", weaker="change",
          warning="A different program will run on the server when agents read code."),
    )


def _memory(S) -> tuple:
    group = "Memory & learning"
    return (
        S("memory", "backend", "Recall related past work",
          "Find past tasks similar to a new one by meaning, using the store chosen here.",
          "select", group, "", "maverick.semantic_recall", subgroup="Recall of past work",
          choices=(("", "Off (default)"), ("chroma", "A store on this server"),
                   ("qdrant", "A Qdrant store"), ("weaviate", "A Weaviate store"),
                   ("pgvector", "Your company database")),
          env=("MAVERICK_VECTOR_STORE",), weaker="change",
          warning="Summaries of past tasks will be stored in a different place."),
        S("session_tree", "max_depth", "Longest chain of copied runs",
          "How many times a run may be copied from a copy.", "number", group, 10,
          "maverick.config", subgroup="Recall of past work", minimum=1, maximum=100,
          integer=True, unit="copies"),
        S("skills", "embed_threshold", "How closely a skill must match by meaning",
          "Skills are recalled only when they match the task at least this closely, from 0 "
          "(anything) to 1 (exact).", "number", group, 0.35, "maverick.config",
          subgroup="Recalling skills", minimum=0, maximum=1, step=0.05, unit="score"),
        S("skills", "lexical_min_relevance", "How many words a skill must share",
          "When matching by meaning is not available, a skill must share at least this "
          "much wording with the task.", "number", group, 4.0, "maverick.config",
          subgroup="Recalling skills", minimum=0, maximum=50, step=0.5, unit="score"),
        S("knowledge", "dsn", "Company database for document search",
          "The connection to your company database, when that is where searchable passages "
          "are kept. It is stored sealed and never shown again.", "secret", group, "",
          "maverick.config", subgroup="Document search", max_length=1000, restart=True),
        S("knowledge", "path", "File for the search index",
          "Where on the server the search index is kept when it is a file here. Leave empty "
          "to keep it only while Lightwork runs.", "text", group, "", "maverick.config",
          subgroup="Document search", pattern=_SERVER_PATH, max_length=500, restart=True),
        S("knowledge", "revision", "Indexing model version",
          "A fixed version of the indexing model, so indexing stays the same. Leave empty "
          "for the current version.", "text", group, "", "maverick.config",
          subgroup="Document search", pattern=r"[A-Za-z0-9._-]{0,64}", max_length=64),
    )


def _diagnostics(S, C) -> tuple:  # noqa: ARG001 -- same signature as the others
    return (
        S("observability", "sentry_dsn", "Error reporting address",
          "Send error reports to your Sentry project. Paste the project's address from "
          "Sentry. It is stored sealed.", "secret", "Performance", "",
          "maverick.observability", subgroup="Error reporting",
          env=("MAVERICK_SENTRY_DSN",), restart=True, max_length=500, weaker="change",
          warning="Error reports, which can include parts of tasks, will be sent to this "
                  "error-reporting service."),
        S("tui", "mouse", "Clickable plan in the terminal view",
          "Let people click the plan shown in the terminal view of a running task.",
          "toggle", "Performance", False, "maverick.tui_mouse", subgroup="Terminal view",
          env=("MAVERICK_TUI_MOUSE",)),
        S("screening", "sdn_path", "Sanctions list file",
          "Where on the server the sanctions list used to screen names is kept.", "text",
          "Finance", "", "maverick.tools.sanctions_screen", subgroup="Sanctions screening",
          pattern=_SERVER_PATH, max_length=500, weaker="change",
          warning="Names will be screened against a different list."),
        S("env_hunt", "poll_limit", "Events gathered from each source per run",
          "The most security events the environment hunter reads from one source each "
          "time it runs.", "number", "Security suite", 1000,
          "maverick_dashboard.security_api", subgroup="Hunting schedule", minimum=1,
          maximum=1000, integer=True, unit="events", weaker="lower",
          warning="Fewer events will be checked, so threats may be missed."),
        S("ekko", "poll_interval_seconds", "How often Ekko checks the active app",
          "How often Ekko notes which app is in use on an enrolled device.", "number",
          "Privacy", 5, "maverick.config", subgroup="Work pattern discovery (Ekko)",
          minimum=1, maximum=300, integer=True, unit="seconds", weaker="lower",
          warning="Ekko will note the active app more often."),
    )
