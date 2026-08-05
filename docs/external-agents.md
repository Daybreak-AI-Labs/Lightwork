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
| `allow_tools` / `deny_tools` | Tool ceiling checked at screen time |
| `max_risk` | Risk ceiling (`low` / `medium` / `high`) |
| `max_dollars` | **Cumulative** spend ceiling across all reported runs |
| `max_wall_seconds`, `data_scopes` | Wall-clock ceiling; memory domains it may touch |
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

`max_dollars` is a **cumulative** ceiling, not per-run. Every ingested run
adds its `cost_dollars` to the agent's reported-spend meter; the ingest
response flags `over_budget: true` once the meter crosses the ceiling. From
then on **screening denies with rule `budget`** — the agent is refused
further actions rather than silently accumulating spend. Run ingest itself
stays open (accounting never stops, so spend is never uncounted). To lift
the cutoff, re-enroll with a higher `max_dollars` — the meter is preserved,
only the ceiling changes.

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
| rule `capability` | Tool denied or risk above `max_risk` | Adjust `allow_tools`/`deny_tools` or the risk ceiling |
| rule `budget` | Cumulative reported spend exceeds `max_dollars` | Re-enroll with a higher ceiling (deliberate raise, audited) |
| rule `shield` | `detail` blocked by the input screen | Inspect the detail text; strip secrets/injection content |
| rule `screen_error` | Shield scanner errored — fail-closed deny | Check the shield install and logs, then retry |
| rule `approval_required` | Not an error — risk at/above the `[actions] require_approval_at` floor | Poll `GET /api/v1/external/approvals/{approval_id}` until a human decides |
