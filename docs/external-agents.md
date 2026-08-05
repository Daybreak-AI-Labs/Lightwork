# External agents (bring your own agent)

Agents built on **other** platforms — Salesforce Agentforce, AWS Bedrock
Agents, Microsoft Copilot Studio, OpenAI, LangChain/LangGraph, or a home-grown
runtime — can enroll into Lightwork and come under governance: **they run on
their runtime, governed on ours**. One enrollment writes the Agent Trust entry
(inbound direction, tool/risk/budget ceilings, expiry), the fleet-memory
roster, and platform provenance; per-surface bearer credentials are minted
from the dashboard; and every completed run lands on the Operating Record as
a first-class goal owned by principal `agent:<id>`.

The governance seam is a pre-action **screening endpoint**: the foreign agent
asks *before* acting, and Lightwork applies trust admission, the cumulative
budget cutoff, Shield input scanning (fail-closed on scanner error), and the
`[actions] require_approval_at` approval floor — high-risk actions park a real
approval row that a human decides in the dashboard queue while the agent
polls. Everything is audited.

All endpoints mount on the dashboard server (`maverick dashboard`, default
`http://127.0.0.1:8765`).

## Enabling

Off by default; requires the `external_agents` entitlement (Gold tier). Turn
it on in the installer wizard (the **External-agent gateway** question in the
advanced flow) or directly:

```toml
# ~/.maverick/config.toml
[external_agents]
enable = true
```

or `MAVERICK_EXTERNAL_AGENTS=1`. While off, the `/api/v1/external/*` routes
**404** (they don't exist for this deployment); enabled but unlicensed, they
**403** with a paid-add-on message.

## Enrolling an agent

On the **/external-agents** dashboard page (admin permission), enroll with:

| Field | Meaning |
| --- | --- |
| `agent_id` | Stable identifier; becomes principal `agent:<id>` on the record |
| `platform` | `agentforce` · `bedrock` · `copilot` · `openai` · `langchain` · `custom` — provenance, not a behavior switch |
| `description` / `owner` / `department` | Ownership metadata; `department` is the default domain for ingested runs |
| `allow_tools` / `deny_tools` | Tool ceiling checked at screen time. An allow entry may carry **your** risk rating as `name:risk` (e.g. `send_contract:high`) — it floors whatever the agent later declares, so a foreign tool cannot under-declare its way beneath a gate |
| `max_risk` | Risk ceiling (`low` / `medium` / `high`) |
| `max_dollars` | Spend ceiling for reported runs, metered per `budget_period` |
| `budget_period` | `monthly` (default — the meter resets each calendar month, UTC) or `total` (lifetime cap) |
| `max_wall_seconds` | Wall-clock ceiling: a reported `duration_seconds` above it is still ingested (spend is never uncounted) but flagged and counted on the roster |
| `data_scopes` | Memory domains it may touch |
| `expires_days` | Trust entry expiry; expired agents are denied until re-enrolled |

One enrollment writes the Agent Trust entry (direction `inbound` — the agent
calls us; we never dial an enrolled foreign runtime), registers the agent on
the fleet-memory roster (when that plane is on), and stamps the metadata
sidecar. Re-enrolling the same id replaces the ceilings but preserves the
spend meter and run counts. Deleting an enrollment removes trust + metadata;
the fleet roster entry stays (learning history is not rewritten).

The same operations exist as admin API: `GET/POST /api/v1/external-agents`,
`GET /api/v1/external-agents/{id}`, `DELETE /api/v1/external-agents/{id}`.

## Minting the rest credential

From the agent's dashboard detail (or `POST
/api/v1/external-agents/{id}/credentials` with `{"surface": "rest"}`), mint
the bearer the agent will authenticate with. The token value (shape
`lw-rest-…`) is shown **exactly once** — store it in the foreign platform's
secret store immediately; no read path ever returns it again.

- **Rotation** = mint again for the same surface. The new token replaces the
  old one immediately; the old token starts returning 401.
- **Revocation** — `POST /api/v1/external-agents/{id}/revoke` (or the
  dashboard button) denies the agent everywhere at once.
- Surfaces are distinct (`rest` / `a2a` / `grpc` / `mcp`) so a leak on one
  surface cannot authenticate another. The BYOA HTTP API uses `rest`.

Agent-facing calls send `Authorization: Bearer <rest token>`.

## Platform setup

`GET /api/v1/external/openapi.json` (same bearer auth) returns an importable
OpenAPI 3.0 description of the three agent-facing endpoints — it drops
directly into Agentforce External Services and Bedrock action groups.

### Salesforce Agentforce

1. **Credential** — Setup → Named Credentials: create an External Credential
   with a custom `Authorization: Bearer lw-rest-EXAMPLEEXAMPLE` header and a
   Named Credential pointing at your Lightwork base URL
   (`https://lightwork.example.com`).
2. **External Service** — Setup → External Services → New from API
   specification: paste the JSON from `GET /api/v1/external/openapi.json`,
   select the Named Credential. Salesforce generates invocable actions for
   the screen, run-report, and approval-poll operations.
3. **Actions** — in Agent Builder, add the generated screen and run-report
   actions to the agent's topic (action set).
4. **Instructions** — tell the topic to call **screen** (tool name, one-line
   detail, risk) before any consequential action and only proceed on
   `allowed: true`; when `requires_approval` comes back, poll the approval
   action until a human decides; call **runs** once with title/outcome/cost
   when the job completes.

### AWS Bedrock Agents

1. Save the schema: `curl -H "Authorization: Bearer lw-rest-EXAMPLEEXAMPLE"
   https://lightwork.example.com/api/v1/external/openapi.json >
   lightwork-external.json` and upload it to S3 (or paste inline).
2. Create an **action group** on the Bedrock agent from that OpenAPI schema.
   Executor: a small Lambda that forwards each operation to the matching
   Lightwork endpoint, reading the bearer from **Secrets Manager** (never
   hard-code it) — or use *return of control* and let your orchestrating app
   do the forwarding.
3. Agent instructions as above: screen before consequential actions, report
   the run at completion.
4. Rotation = mint a new rest token and update the Secrets Manager value.

### OpenAI / LangChain

Call the two endpoints from your tool layer:

```python
import requests

BASE = "https://lightwork.example.com"
HEADERS = {"Authorization": "Bearer lw-rest-EXAMPLEEXAMPLE"}

def screen(tool: str, detail: str = "", risk: str = "low") -> dict:
    r = requests.post(f"{BASE}/api/v1/external/screen",
                      json={"tool": tool, "detail": detail, "risk": risk},
                      headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

def report_run(title: str, outcome: str, **fields) -> dict:
    r = requests.post(f"{BASE}/api/v1/external/runs",
                      json={"title": title, "outcome": outcome, **fields},
                      headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()
```

Gate each consequential tool call (in LangChain, at the top of the tool
function or in a callback handler) and report once at the end:

```python
verdict = screen("send_email", detail="renewal note to ACME", risk="medium")
if verdict.get("requires_approval"):
    ...  # hold; poll GET /api/v1/external/approvals/{approval_id}
elif not verdict["allowed"]:
    ...  # skip the action; verdict["rule"] / verdict["reason"] say why

report_run("Renewal outreach", "success",
           steps=["pulled list", "drafted", "sent 14"],
           cost_dollars=0.42, input_tokens=18000, output_tokens=5200,
           tool_calls=9, department="sales")
```

### Anything else: curl

Screen a proposed action:

```bash
curl -sS -X POST https://lightwork.example.com/api/v1/external/screen \
  -H "Authorization: Bearer lw-rest-EXAMPLEEXAMPLE" \
  -H "Content-Type: application/json" \
  -d '{"tool": "send_email",
       "detail": "Quarterly summary to the finance list",
       "risk": "medium"}'
```

```json
{"allowed": true, "rule": "allow", "reason": "within ceilings"}
```

At or above the approval floor the action parks instead:

```json
{"allowed": false, "rule": "approval_required",
 "reason": "risk high is at/above the high approval floor; awaiting a human decision",
 "requires_approval": true, "approval_id": 42}
```

Poll while a human decides in the dashboard queue:

```bash
curl -sS https://lightwork.example.com/api/v1/external/approvals/42 \
  -H "Authorization: Bearer lw-rest-EXAMPLEEXAMPLE"
```

```json
{"approval_id": 42, "status": "pending", "risk": "high"}
```

`status` moves to `approved` or `denied`. Report a completed run:

```bash
curl -sS -X POST https://lightwork.example.com/api/v1/external/runs \
  -H "Authorization: Bearer lw-rest-EXAMPLEEXAMPLE" \
  -H "Content-Type: application/json" \
  -d '{"title": "Renewal outreach for Q3 at-risk accounts",
       "outcome": "success",
       "summary": "Drafted and sent 14 renewal emails; 2 escalated.",
       "steps": ["pulled at-risk list", "drafted emails",
                 "sent 14", "escalated 2"],
       "cost_dollars": 0.42, "input_tokens": 18000,
       "output_tokens": 5200, "tool_calls": 9,
       "department": "sales"}'
```

```json
{"ok": true, "goal_id": 118, "over_budget": false}
```

`outcome` is `"success"` or `"failure"`; only `title` and `outcome` are
required. Titles, summaries, and steps are capped in length; up to 50 steps
are kept.

## What shows up where

- **Operating Record** — each reported run is a goal owned by `agent:<id>`
  with a status event per step and a costed episode (dollars, tokens, tool
  calls). Overview, Spend, Workforce, and Savings boards and the audit binder
  count foreign agents exactly like native ones.
- **/external-agents page** — roster with platform, owner, department,
  lifecycle (active / expired / revoked), spend vs. ceiling, run count, and
  which surfaces hold credentials (never the token values); per-agent detail
  shows recent episodes.
- **Approvals queue** — parked screen rows appear with provenance
  `external_agents`, requested by `agent:<id>`; the human decision there is
  what the agent's poll sees.
- **Audit trail** — event kinds `external_agent_enrolled`,
  `external_credential_minted`, `external_run_ingested`, and
  `external_action_screened` (every screen decision, allow or deny); trust
  denials also record `agent_trust_denied` rows.

## Budget semantics

`max_dollars` is a ceiling on **reported spend, per budget period** — not
per-run. Under the default `monthly` period the meter resets each calendar
month (UTC); under `total` it is a lifetime cap. Every ingested run adds its
`cost_dollars` to the meter; the ingest response flags `over_budget: true`
once it crosses the ceiling, and from then on **screening denies with rule
`budget`** — the agent is refused further actions rather than silently
accumulating spend. Run ingest itself stays open (accounting never stops,
so spend is never uncounted). To lift the cutoff early, either re-enroll
with a higher `max_dollars` or use **Reset budget** on the console (`POST
/api/v1/external-agents/{id}/reset-budget`, audited) — lifetime totals are
always preserved.

## Live runs (start / heartbeat / finish)

`POST /runs` reports work after the fact. For work you want visible — and
stoppable — **while it happens**, open a live run:

1. `POST /api/v1/external/runs/start` `{title, summary?, department?}` →
   `{goal_id, heartbeat_seconds}`. The goal lands **active** on the
   Operating Record immediately.
2. `POST /api/v1/external/runs/{goal_id}/heartbeat` at least every
   `heartbeat_seconds` (default 60; the deployment's orphan-reclaim window).
   The reply is the kill switch: `{"continue": false, "reason": ...}` means
   **stop working now** — containment, revocation, expiry, and reclaimed or
   finished runs all return false. An agent that ignores it stops refreshing
   its liveness anyway: the run is reclaimed to `blocked` with a restart
   marker.
3. `POST /api/v1/external/runs/{goal_id}/finish` with the same body as
   `/runs` minus `title` — outcome, steps, cost, `duration_seconds`,
   `idempotency_key`. Cross-agent heartbeats and finishes are refused; a
   finished run cannot be finished again.

Heartbeats are limited to 240/minute per agent.

## The learning plane over REST

With `[fleet_memory] enable = true`, enrolled agents reach the governed
learning plane through the same gateway bearer (previously MCP-only):

- `POST /api/v1/external/memory/ingest` `{kind: success|failure|lesson,
  goal_text, reflection?, tools_used?, domain?}` deposits an experience
  record. Vendor provenance is stamped from the **enrollment's platform**,
  never the caller's claim; the record passes the same redaction, injection
  tripwires, and data-scope gates as the MCP path, and unenrolled or
  contained agents are refused. Deposits feed the dream cycle and are
  provable per vendor via `maverick proof --fleet`.
- `POST /api/v1/external/memory/recall` `{query, domain?}` returns
  department-scoped lessons for the asking agent (`{context, reason}`; an
  empty context with a reason is a refusal, not an error). Every read is
  audited with the reader's identity.
- Both are limited to 60 requests/minute per agent. The console shows total
  lessons shared; each scorecard shows that agent's contributions.

## Approval notifications

A parked approval no longer waits silently for someone to open the queue:

- The ops push transport (`[alerts]` / `[notifications]`, when configured)
  gets a high-priority "external agent waiting on approval #N" push.
- Set `[external_agents] approval_webhook = "https://…"` and the deployment
  fires a **signed** `external_approval.created` callback (HMAC-SHA256 via
  the shared `[webhooks] secret`, timestamped, SSRF-guarded) with a
  provenance-only payload: `approval_id`, `agent_id`, `tool`, `risk`,
  `department` — never the free-form detail text. Point it at your
  platform's inbound hook to unblock the agent the moment a human decides.
- The dashboard approval queue labels these rows "external agent · BYOA
  gateway".

## Retries, rate limits, and containment

- **Idempotent ingest** — send a stable `idempotency_key` per logical run
  and a retried `POST /runs` returns the original record (`duplicate:
  true`) instead of double-counting spend. Keys are per-agent; keep them
  under 128 characters.
- **Rate limits** — per agent, per minute: `screen` 120, `runs` 60,
  `approvals` 120 (the schema route is per-address, 30). Exceeding one
  returns `429` with `Retry-After: 60`; back off and retry.
- **Auto-containment** — repeated *misbehavior* denials (ceiling breaches,
  budget, blocked payloads; 5 within 24 hours) flip the agent to
  **contained**: every call is refused with rule `contained` until an
  administrator presses **Release** on the console (`POST
  /api/v1/external-agents/{id}/release`). Approval-floor parks and
  platform-side scanner errors never count toward containment. Containment,
  release, and budget resets are all audited
  (`external_agent_contained` / `external_agent_released` /
  `external_budget_reset`).

## Fail-closed notes

The external boundary fails closed throughout:

- An unenrolled, revoked, expired, or wrong-direction agent is denied.
- A Shield scanner **error** during screening denies (`rule:
  "screen_error"`) — it never waves an external action through unscreened.
  (The Shield itself stays optional, as everywhere: without it installed,
  screening degrades to the secret-redaction + injection-tripwire layer.)
- Ingested text (title, summary, steps) is secret-redacted and
  injection-screened; a tripwire hit **rejects** the run rather than
  laundering the payload into the record.
- A malformed enrollment sidecar is treated as empty-but-alarmed (logged);
  identity still gates at the trust plane.
- The registry binds at this gateway even when the global Agent Trust Plane
  is disengaged — it is an external boundary in its own right.

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `404` on `/api/v1/external/*` | Plane is off | Enable `[external_agents]` and restart the dashboard |
| `403` | License lacks the `external_agents` (Gold) entitlement | Install a Gold-tier license |
| `401` | Missing, wrong, rotated, or revoked rest token (the reply never says which) | Mint a new rest credential; minting replaces the old token immediately |
| rule `not_in_registry` | Agent has no trust entry | Enroll it on /external-agents |
| rule `revoked` | Trust entry revoked | Re-enroll (or clear the revocation) and mint a fresh token |
| rule `expired` | Past `expires_at` | Re-enroll with a new expiry |
| rule `direction` | Entry is not inbound | Enrollments are inbound-only; fix hand-edited trust entries |
| rule `capability` | Tool denied, or risk (after your `name:risk` floor) above `max_risk` | Adjust `allow_tools`/`deny_tools` or the risk ceiling |
| rule `budget` | Period spend exceeds `max_dollars` | Wait for the monthly reset, **Reset budget** on the console, or re-enroll with a higher ceiling (all audited) |
| rule `shield` | `detail` blocked by the input screen | Inspect the detail text; strip secrets/injection content |
| rule `screen_error` | Shield scanner errored — fail-closed deny (does not count toward containment) | Check the shield install and logs, then retry |
| rule `approval_required` | Not an error — risk at/above the `[actions] require_approval_at` floor | Poll `GET /api/v1/external/approvals/{approval_id}` until a human decides |
| rule `contained` | Repeated denials tripped auto-containment | An admin reviews and presses **Release** on /external-agents |
| `429` | Per-agent rate limit | Honor `Retry-After`; batch reports or slow the loop |
