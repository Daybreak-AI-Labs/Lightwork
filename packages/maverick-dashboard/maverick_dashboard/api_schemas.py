"""Pydantic request/response models for the dashboard REST API.

Extracted verbatim from api.py to keep that module focused on routing.
Importing here changes no behavior: these are pure data schemas.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class WorkflowStepIn(BaseModel):
    name: str = Field(..., max_length=80)
    instruction: str = ""
    tools: list[str] = Field(default_factory=list)
    gate: str | None = None


class AgentOverrideIn(BaseModel):
    """A tenant override patch for a domain pack (agent). Every field is
    optional: only the ones the client actually sets are persisted, so the
    pack inherits everything else from its built-in base. ``extends`` forks a
    base under a new name; omit it to customize the pack in place."""
    description: str | None = None
    persona: str | None = None
    allow_tools: list[str] | None = None
    deny_tools: list[str] | None = None
    max_risk: str | None = None
    knowledge_sources: list[str] | None = None
    models: dict[str, str] | None = None
    workflow: list[WorkflowStepIn] | None = None
    compartment: str | None = None
    extends: str | None = None


class JDMatchIn(BaseModel):
    """A job description to rank the specialist roster against. Bounded like a
    goal description: JD text flows into scoring and draft prompts."""
    jd_text: str = Field(..., min_length=1, max_length=16000)
    k: int = Field(5, ge=1, le=20)


class JDDraftIn(BaseModel):
    """Draft (not save) a new specialist pack from a job description. The
    result prefills the pack editor; persisting stays the editor's existing
    human-approved save."""
    role_title: str = Field(..., min_length=1, max_length=120)
    jd_text: str = Field(..., min_length=1, max_length=16000)
    industry: str = Field("", max_length=120)


class FleetAgentAddIn(BaseModel):
    """Add one specialist pack to a fleet roster (the JD-hire deploy step)."""
    pack: str = Field(..., min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=64)


class AssessmentRefreshIn(BaseModel):
    """Auto-draft (or re-draft) a governance assessment. ``template`` selects
    which lenses to run; ``None`` keeps the existing/default one."""
    template: str | None = Field(default=None, max_length=64)


class AssessmentReviewIn(BaseModel):
    """A reviewer's decision on one lens of an assessment."""
    lens: str = Field(..., pattern="^(privacy|security|ai_risk)$")
    decision: str = Field(..., pattern="^(accepted|needs_work)$")
    note: str | None = Field(default=None, max_length=4000)
    cadence_days: int | None = Field(default=None, ge=0, le=3650)


class AssessmentEvidenceIn(BaseModel):
    """An evidence note/link attached to a specific finding."""
    lens: str = Field(..., pattern="^(privacy|security|ai_risk)$")
    control: str = Field(..., max_length=200)
    evidence: str = Field(..., max_length=2000)


class SignoffIn(BaseModel):
    """A human's sign-off on a finished, gated deliverable -- the certify/reject
    decision the pack's output gate calls for, bound to the version the client
    actually rendered, with an optional review note."""
    decision: str = Field(..., pattern="^(approved|rejected)$")
    expected_updated_at: float = Field(..., gt=0)
    note: str | None = Field(default=None, max_length=2000)


class RoleOverrideIn(BaseModel):
    """A per-tenant override for a core agent role: a system-prompt addendum
    appended to the role's base template, plus optional model/effort overrides
    that win over the global [models]/[effort] config. Empty fields clear; an
    all-empty patch clears the role's override."""
    system_addendum: str | None = None
    model: str | None = None
    effort: str | None = None


class GoalIn(BaseModel):
    title: str = Field(..., max_length=200)
    # Bound the description like the title: it flows into SQLite, the at-rest
    # seal, and every downstream prompt, so an unbounded value from an untrusted
    # REST caller is a cost / DB-bloat amplification vector. 16k chars is ample
    # for a goal brief; oversized payloads get a 422 rather than silent bloat.
    description: str = Field("", max_length=16000)
    max_dollars: float = Field(5.0, ge=0.0, le=100.0)
    max_wall_seconds: float = Field(3600.0, ge=1.0, le=86400.0)
    max_depth: int = Field(3, ge=1, le=5)
    template: str | None = None
    params: dict[str, str] | None = None
    # Run the goal AS a specialist pack: the orchestrator inherits a domain
    # stamped on the goal row (persona + capability envelope + compartment).
    domain: str | None = Field(None, max_length=120)


class GoalOut(BaseModel):
    id: int
    status: str
    title: str
    description: str | None = None
    result: str | None = None


class ScheduleIn(BaseModel):
    """Arm a recurring run on a 5-field cron expression. Provide either a saved
    ``template`` (rendered with ``params`` each fire) or a raw ``text`` prompt.
    Executed by ``maverick worker`` as a ``start_goal`` job."""
    cron: str = Field(..., max_length=120)
    template: str | None = None
    params: dict[str, str] | None = None
    text: str | None = None
    title: str | None = Field(None, max_length=200)


class ScheduleOut(BaseModel):
    id: int
    cron: str
    kind: str
    title: str
    next_run: float
    # Stable across cron re-arms (the job id changes each occurrence); used to
    # group a schedule's run history. Empty for schedules armed before v15.
    schedule_id: str = ""


class ImportRunIn(BaseModel):
    """Import automations from an external platform into Lightwork templates.

    ``source`` is one of the registered importers (n8n/make/.../zapier). Provide
    ``definitions`` (exported definition JSON objects) for an offline/connect
    import, or omit it to live-fetch from the platform's API (server env creds).
    ``dry_run`` previews without writing; ``activate_schedules`` auto-creates
    schedules for recovered cron triggers (``None`` = fall back to the
    ``[automation_import] create_schedules`` config default);
    ``create_webhook_triggers`` wires an inbound webhook trigger for each
    webhook-triggered automation.
    """
    source: str = Field(..., max_length=40)
    definitions: list[dict] | None = Field(default=None, max_length=25)
    dry_run: bool = False
    activate_schedules: bool | None = None
    create_webhook_triggers: bool = False
    # Also lower each automation to a real flow GRAPH (branch/loop structure
    # preserved where the source exposes it) and save it, returning the per-step
    # migration fidelity report. Requires the flow engine.
    as_flows: bool = False


class ImportResultOut(BaseModel):
    source: str
    name: str
    template: str
    created: bool
    trigger: str
    webhook_trigger: str | None = None
    schedule: dict | None = None
    tools: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Set when as_flows: the saved flow's id + the per-step fidelity log
    # ({step, node, kind, fidelity: preserved|approximated, note}).
    flow: str | None = None
    fidelity: list[dict] = Field(default_factory=list)


class ImportRunOut(BaseModel):
    imported: list[ImportResultOut]
    dry_run: bool
    webhook_url: str
    secret_configured: bool
    # How many supplied definitions could not be translated (malformed / an
    # unsupported shape) and were dropped. Surfaced so a partial import doesn't
    # look like a clean success.
    skipped: int = 0


class TriggerIn(BaseModel):
    """Bind a saved template OR flow to an inbound webhook. Set exactly one of
    ``template`` / ``flow`` (a flow receives the webhook ``data`` as its run
    data). ``params`` are the operator's default values (template targets only);
    an HMAC-signed POST to /webhook/run may override declared params at fire
    time. ``name`` defaults to the target name (slugified)."""
    template: str = Field("", max_length=80)
    flow: str = Field("", max_length=120)
    params: dict[str, str] | None = None
    name: str | None = Field(None, max_length=48)


class TriggerOut(BaseModel):
    name: str
    template: str
    flow: str = ""
    params: dict[str, str] = Field(default_factory=dict)
    webhook_url: str
    secret_configured: bool


class EventTriggerIn(BaseModel):
    """Bind a saved template OR flow to a polled event source ("when X appears,
    run this"). ``source`` is a registered EventSource; ``config`` is its settings
    (e.g. the ``url`` for http_json). ``params`` are default values; each polled
    event's fields fill declared params at fire time. Set exactly one of
    ``template`` / ``flow`` (a flow receives the event fields as its run data).
    ``name`` defaults to the target name (slugified)."""
    template: str = Field("", max_length=80)
    flow: str = Field("", max_length=120)
    source: str = Field(..., max_length=40)
    config: dict = Field(default_factory=dict)
    params: dict[str, str] | None = None
    name: str | None = Field(None, max_length=48)
    # Per-trigger poll cadence in seconds (0 = the scheduler default). Floored at
    # 60s by the scheduler; a day cap keeps a fat-fingered value sane.
    interval_seconds: int = Field(0, ge=0, le=86_400)


class EventTriggerOut(BaseModel):
    name: str
    template: str
    flow: str = ""
    source: str
    config: dict = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    cursor: str = ""
    interval_seconds: int = 0


class LearningToggleIn(BaseModel):
    """Turn the whole self-learning loop on/off from the dashboard (writes the
    learning knobs to the config overlay)."""
    enabled: bool


class DgmToggleIn(BaseModel):
    """Request the separate, default-off DGM research harness.

    Enabling requires an explicit acknowledgement because this permits code
    proposal/evaluation. It still never authorizes live adoption.
    """
    enabled: bool
    acknowledge_research_only: bool = False


class FlowAutonomyIn(BaseModel):
    """Toggle the autonomous flow self-improvement loop ([flows] auto_evolve /
    auto_apply). Its own opt-in, distinct from the blanket learning button. Only
    the fields provided are changed; ``auto_apply`` needs ``auto_evolve`` on."""
    auto_evolve: bool | None = None
    auto_apply: bool | None = None


class GoalEventOut(BaseModel):
    id: int
    agent: str
    kind: str
    content: str
    ts: float


class GoalEventsResponse(BaseModel):
    status: str
    result: str | None
    next_id: int
    events: list[GoalEventOut]


class FactIn(BaseModel):
    key: str
    value: str


class OutcomeIn(BaseModel):
    """A real downstream outcome for a past episode (the Consequence Engine)."""
    goal_id: int
    episode_id: int
    value: float
    kind: str = ""


class FeedbackIn(BaseModel):
    """A human thumbs-up/down on a goal's result -- the always-available verdict
    (unlike the gated sign-off). Grounds a real outcome into the learning loop."""
    rating: str = Field(..., pattern="^(up|down)$")
    note: str | None = Field(default=None, max_length=2000)


class DeliverableEditIn(BaseModel):
    """A human's revised version of a goal's deliverable. How much they changed it
    (vs. the agent's draft) grounds a graded outcome -- a verbatim keep is a strong
    positive, a heavy rewrite is graded-negative ground truth."""
    text: str = Field(..., max_length=200_000)


class OutcomeLinkIn(BaseModel):
    """Link an external business key (``invoice:INV-42``) to the episode that
    acted on it, so a later outcome reported against that key can be grounded."""
    goal_id: int
    episode_id: int
    key: str = Field(..., min_length=1, max_length=256)


class OutcomeByKeyIn(BaseModel):
    """A real downstream outcome reported by a system of record using only the
    business key it owns -- resolved to the episode that acted via the link."""
    key: str = Field(..., min_length=1, max_length=256)
    value: float
    kind: str = ""


class OAuthAuthorizeIn(BaseModel):
    """Start a turnkey 'connect this account' flow for a preset provider: build
    the consent URL. The returned PKCE verifier is kept by the (admin) operator
    and passed back to /exchange -- no server-side state."""
    redirect_uri: str = Field(..., min_length=1, max_length=2048)
    scopes: list[str] | None = None
    client_id: str | None = Field(default=None, max_length=512)


class OAuthExchangeIn(BaseModel):
    """Finish the connect flow: swap the authorization code for tokens and seal
    them in the per-tenant vault under the provider name."""
    code: str = Field(..., min_length=1, max_length=4096)
    redirect_uri: str = Field(..., min_length=1, max_length=2048)
    verifier: str = Field(default="", max_length=256)
    client_id: str | None = Field(default=None, max_length=512)


class TrustAgentIn(BaseModel):
    """Register (or replace) an external agent in the Agent Trust Plane's
    dashboard-managed registry overlay (agent_trust.json) -- so an admin can
    govern federation / A2A / MCP peers from the app without editing the
    operator's config file. A managed entry with the same id overrides the
    config-file one."""
    id: str = Field(..., min_length=1, max_length=64)
    pubkey: str = Field(default="", max_length=64)
    direction: str = Field(default="both", pattern="^(inbound|outbound|both)$")
    allow_tools: list[str] = Field(default_factory=list)
    deny_tools: list[str] = Field(default_factory=list)
    max_risk: str | None = Field(default=None, pattern="^(low|medium|high)$")
    max_dollars: float | None = Field(default=None, ge=0)
    max_wall_seconds: float | None = Field(default=None, ge=0)
    data_scopes: list[str] = Field(default_factory=list)


class TrustRevokeIn(BaseModel):
    """Revoke (or restore) a managed external agent without deleting it."""
    revoked: bool = True


class ConnectionIn(BaseModel):
    """Create/replace a named SaaS connection: the connector it credentials, its
    base URL, and an API token (sealed at rest, never returned). ``name`` lets
    one connector have several accounts (salesforce / salesforce-eu)."""
    name: str = Field(..., min_length=1, max_length=64)
    connector: str = Field("", max_length=64)
    base_url: str = Field("", max_length=2048)
    token: str = Field("", max_length=8192)


class FlowSaveIn(BaseModel):
    """A flow definition (a graph of typed nodes) to create or replace."""
    flow: dict


class FlowRunIn(BaseModel):
    """Start a flow run with optional initial data (the trigger payload).

    ``dry_run`` exercises the graph with MOCK executors -- no real goal, no real
    connector call, and no grounded outcome recorded -- so a flow can be tested
    with sample input before it's wired to a live trigger."""
    data: dict = Field(default_factory=dict)
    dry_run: bool = False
    # Optional caller-supplied dedup key: a repeat POST with the same key returns
    # the existing run instead of starting a second one (double-click / retry safe).
    idempotency_key: str = Field(default="", max_length=200)


class FlowResumeIn(BaseModel):
    """Resume a paused flow run: the human verdict on the approval node, plus any
    free-form inputs to merge into the run data (so an approval can also collect
    a corrected value or a note, not just yes/no). Beyond approved/rejected the
    verdict may be one of the node's declared ``choices`` (the runner validates
    against them; an unlisted verdict pauses again). A paused_event run ignores
    the decision -- the event's ``inputs`` are the point."""
    decision: str = Field(default="approved", max_length=40,
                          pattern=r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$")
    inputs: dict = Field(default_factory=dict)


class FlowDraftIn(BaseModel):
    """Draft a flow from a plain-English description (the designer's ✨ button)."""
    description: str = Field(..., min_length=1, max_length=4000)
    flow_id: str = Field(default="", max_length=120)


class FlowChatIn(BaseModel):
    """One copilot turn in the designer's chat panel: the user's message, the
    CURRENT canvas graph (not necessarily saved), the recent transcript, and
    optionally a run id to ground diagnose/repair questions in a real trace.
    The reply may carry a patched flow -- the canvas applies it; nothing is
    saved server-side until the user saves."""
    message: str = Field(..., min_length=1, max_length=4000)
    flow: dict = Field(default_factory=dict)
    history: list[dict] = Field(default_factory=list, max_length=24)
    run_id: str = Field(default="", max_length=64)


class FlowApplyIn(BaseModel):
    """Apply a self-rewrite proposal: swap one node between agent and action.

    Hardening to an action needs ``tool``; softening to an agent needs ``brief``
    (else the node's existing brief/label is used). Produces a new flow version."""
    node_id: str = Field(..., min_length=1, max_length=120)
    to_kind: str = Field(..., pattern="^(agent|action)$")
    tool: str = Field(default="", max_length=120)
    brief: str = Field(default="", max_length=4000)
    version: int | None = Field(default=None, ge=1)
    revision: str = Field(default="", max_length=128)


class FlowRollbackIn(BaseModel):
    """Roll a flow back to a prior version (default: the immediately previous)."""
    version: int | None = Field(default=None, ge=1)
    current_version: int | None = Field(default=None, ge=1)
    revision: str = Field(default="", max_length=128)


class AnswerIn(BaseModel):
    question_id: int
    answer: str


class SpeakIn(BaseModel):
    """Text for the dashboard read-aloud endpoint (POST /voice/speak)."""
    text: str
    voice: str | None = None


class SkillInstallIn(BaseModel):
    source: str = Field(..., description="https://... or gh:org/repo[:path]")


class SkillCreateIn(BaseModel):
    """Author a skill in the dashboard: a name, the trigger phrases that
    activate it, the tools it needs, and the instructions (markdown body)."""
    name: str = Field(..., max_length=80)
    instructions: str = Field(..., max_length=20000)
    triggers: list[str] = Field(default_factory=list)
    tools_needed: list[str] = Field(default_factory=list)


class SkillOut(BaseModel):
    name: str
    triggers: list[str]
    tools_needed: list[str]


class AttachmentOut(BaseModel):
    id: int
    filename: str
    mime: str
    size_bytes: int
    sha256: str


class CatalogInstallIn(BaseModel):
    name: str = Field(..., max_length=200)


class HaltIn(BaseModel):
    reason: str = Field("manual via dashboard", max_length=200)


class FleetRunIn(BaseModel):
    agent: str = Field(..., max_length=64)
    prompt: str = Field(..., max_length=8000)
    max_dollars: float | None = Field(None, ge=0.0, le=100.0)


class FleetAgentIn(BaseModel):
    name: str = Field(..., max_length=64)
    role: str = Field("", max_length=64)
    description: str = Field("", max_length=500)


class FleetCreateIn(BaseModel):
    name: str = Field(..., max_length=64)
    agents: list[FleetAgentIn] = Field(default_factory=list)


class RedactIn(BaseModel):
    text: str = Field(max_length=200_000)
    kinds: list[str] = Field(default_factory=list)  # empty = all kinds


class CachePurgeIn(BaseModel):
    scopes: list[str] = Field(default_factory=lambda: ["all"])


class RetitleIn(BaseModel):
    title: str = Field(..., max_length=200)


class ReparentIn(BaseModel):
    parent_id: int | None = None


class ChildIn(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = ""


class ComposeIn(BaseModel):
    title: str = Field(..., max_length=200)
    steps: list[str] = Field(default_factory=list)
    budget_dollars: float | None = Field(None, ge=0.0, le=100.0)
    channel: str | None = Field(None, max_length=64)
    priority: str | None = Field(None, max_length=16)


class WorkflowDraftIn(BaseModel):
    """Chat-path drafting: a natural-language brief. ``form`` selects the
    artifact — a reusable ``"template"`` (default) or an agent ``"playbook"``."""
    description: str = Field("", max_length=8000)
    form: str = Field("template", max_length=16)


class WorkflowRefineIn(BaseModel):
    """Refine an existing draft with a follow-up instruction. ``current`` is the
    draft as last shown/edited; ``form`` selects template vs playbook parsing."""
    form: str = Field("template", max_length=16)
    instruction: str = Field(..., max_length=2000)
    current: dict = Field(default_factory=dict)


class WorkflowSaveIn(BaseModel):
    """Persist an (AI-drafted, possibly edited) workflow as a user template.

    ``overwrite`` must be true to replace an existing template of the same name
    (the ``?edit=`` flow); a fresh save leaves it false so an accidental name
    collision returns 400 instead of silently clobbering another template."""
    name: str = Field(..., max_length=48)
    title: str = Field(..., max_length=200)
    body: str = Field(..., max_length=20000)
    params: list[str] = Field(default_factory=list)
    budget_dollars: float = Field(5.0, ge=0.0, le=100.0)
    budget_wall_seconds: float = Field(3600.0, ge=1.0, le=86400.0)
    overwrite: bool = False


class TenantCreateIn(BaseModel):
    """Provision a new tenant (admin only)."""
    id: str = Field(..., max_length=128)
    plan: str = Field("free", pattern="^(free|pro|enterprise)$")
    display_name: str = Field("", max_length=200)
    max_daily_dollars: float = Field(0.0, ge=0.0)


class TenantPlanIn(BaseModel):
    plan: str = Field(..., pattern="^(free|pro|enterprise)$")


class TenantQuotaIn(BaseModel):
    max_daily_dollars: float = Field(..., ge=0.0)


class TenantOut(BaseModel):
    """A provisioned tenant. ``config_path`` is where to drop this tenant's own
    config.toml (its provider keys / model choices / budget overlay)."""
    id: str
    status: str
    plan: str
    display_name: str
    max_daily_dollars: float
    created_at: float
    updated_at: float
    config_path: str


class TenantRoleIn(BaseModel):
    """Grant a principal a role within one tenant (per-tenant RBAC)."""
    role: str = Field(..., pattern="^(admin|operator|auditor|viewer)$")


class UserSuitesIn(BaseModel):
    """Scope a principal to a set of department suites (job function)."""
    suites: list[str] = Field(default_factory=list, max_length=64)


class DpaReviewIn(BaseModel):
    """Review a Data Processing Agreement against the Art. 28(3) checklist.
    ``text`` is the agreement body (pasted or extracted); bounded like other
    document inputs."""
    vendor: str = Field(..., min_length=1, max_length=200)
    document_name: str = Field("", max_length=300)
    text: str = Field(..., min_length=1, max_length=2_000_000)


class AiSystemIn(BaseModel):
    """Register an AI system in the governance registry; it is classified
    against the EU AI Act's tiers on the way in."""
    name: str = Field(..., min_length=1, max_length=200)
    purpose: str = Field(..., min_length=1, max_length=2000)
    provider: str = Field("", max_length=200)
    owner: str = Field("", max_length=200)
    assessment_id: str = Field("", max_length=64)


class RopaIn(BaseModel):
    """Create or update one Art. 30(1) record of processing activity. Only
    fields present are changed on update."""
    ropa_id: str = Field("", max_length=64)
    activity: str | None = Field(None, max_length=2000)
    purpose: str | None = Field(None, max_length=2000)
    controller: str | None = Field(None, max_length=2000)
    data_categories: str | None = Field(None, max_length=2000)
    data_subjects: str | None = Field(None, max_length=2000)
    recipients: str | None = Field(None, max_length=2000)
    transfers: str | None = Field(None, max_length=2000)
    retention: str | None = Field(None, max_length=2000)
    security_measures: str | None = Field(None, max_length=2000)
    assessment_id: str = Field("", max_length=64)


class DpaFromDocumentIn(BaseModel):
    """Review a DPA fetched straight from a connected source (the hit comes
    from the dpa-documents search); ``ref`` is the opaque hit reference the
    source returned."""
    vendor: str = Field(..., min_length=1, max_length=200)
    source: str = Field(..., min_length=1, max_length=32)
    doc_id: str = Field(..., min_length=1, max_length=300)
    ref: dict | None = None
    document_name: str = Field("", max_length=300)


class OneTrustImportIn(BaseModel):
    """OneTrust RoPA / Data Mapping CSV export to import into the Art. 30
    register; bounded like other document inputs."""
    csv_text: str = Field(..., min_length=1, max_length=2_000_000)


class DsarIn(BaseModel):
    """Open a data-subject request with its statutory due date."""
    subject_id: str = Field(..., min_length=1, max_length=200)
    kind: str = Field(..., pattern="^(access|portability|erasure)$")
    channel: str = Field("", max_length=64)


class FollowupsIn(BaseModel):
    """Reviewer follow-up questions to send back to an assessment's
    respondent (the pop-out's 'needs more detail' path)."""
    questions: list[str] = Field(..., min_length=1, max_length=20)


class AssessmentDecideIn(BaseModel):
    """The reviewer's decision on a saved assessment. An approval with a
    cadence schedules the next review (the record comes due); 0/None means
    no re-review date."""
    decision: str = Field(..., pattern="^(approved|rejected)$")
    cadence_days: int | None = Field(default=365, ge=0, le=3650)
    note: str = Field("", max_length=4000)


class DocDiscoverIn(BaseModel):
    """Search connected document sources (Microsoft Graph / Slack / Google
    Drive) for documents related to an assessment subject -- the SOW,
    contract, DPA a respondent would otherwise hunt for by hand."""
    subject: str = Field(..., min_length=1, max_length=300)
    sources: list[str] | None = Field(default=None, max_length=8)
    limit: int = Field(8, ge=1, le=25)


class SourceAttachIn(BaseModel):
    """Fetch one discovered document from its source and attach it to a goal
    as evidence (the one-click 'attach it for me'). ``ref`` round-trips the
    opaque fetch details discovery returned for the hit."""
    source: str = Field(..., min_length=1, max_length=40)
    doc_id: str = Field(..., min_length=1, max_length=500)
    name: str = Field("", max_length=300)
    ref: dict = Field(default_factory=dict)


class ValueDeptIn(BaseModel):
    """One department's cost/value override on the Savings page. Both fields
    optional so a client can override just the rate; an all-None row clears
    the override (reverts the department to the global assumption)."""
    hourly_rate: float | None = Field(None, ge=0.0, le=100_000.0)
    hours_per_task: float | None = Field(None, ge=0.0, le=10_000.0)


class ValueAssumptionsIn(BaseModel):
    """The client's own cost/value inputs behind the Savings report: what one
    hour of comparable human work costs them (fully loaded) and how many human
    hours one deliverable would take by hand. Bounds are sanity rails, not
    policy -- the number is theirs. Only fields present are changed."""
    hourly_rate: float | None = Field(None, ge=0.0, le=100_000.0)
    hours_per_task: float | None = Field(None, ge=0.0, le=10_000.0)
    currency: str | None = Field(None, max_length=8, pattern="^[A-Za-z]{3,8}$")
    departments: dict[str, ValueDeptIn | None] = Field(default_factory=dict)
