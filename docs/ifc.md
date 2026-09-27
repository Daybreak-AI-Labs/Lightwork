# Information-flow control

Per-tool policy answers "may this agent call `email`?". It cannot answer "has
this agent read payroll first?". In a swarm, the answer also spreads between
agents:
- every agent's tool output is excerpted to the shared blackboard, which is
  rendered into every other agent's context each turn;
- children inherit their parent's brief and hand their answers back;
- peers message each other.

`maverick.ifc` tracks what each agent has seen, and decides at the tool gate
whether that data may leave.

## Labels

- A tool that reads tenant data labels the calling agent `data:<tool>`. The
  classification is the one composition risk uses: `read_file`,
  `knowledge_search`, SQL and spreadsheet reads, enterprise connectors, `*_read`
  tools and `mcp_*`.
- A run can start labelled. A codebase run is labelled `code:<repo>` from its
  first token.

## Propagation

Labels only accumulate within a run.

| Channel | Rule |
|---|---|
| Blackboard | A reader absorbs the labels of every agent whose posts it is shown, both in its first brief and in each per-turn delta. |
| Spawn | A child inherits its parent's labels; the parent absorbs the child's when the child's answer returns. |
| Peer bus | The recipient of a delivered message (plain or signed handoff) absorbs the sender's labels. |

## Enforcement

A call to an external sink by a labelled agent is checked at the tool gate,
after org governance and before hooks. External sinks include `email`,
`notify`, `calendar`, `http_fetch`, `web_search`, `browser`, `webhook`, chat
connectors, public-data connectors and `mcp_*`. What happens depends on the
mode:

| Mode | Behaviour |
|---|---|
| `off` | No tracking. |
| `audit` (default) | Record an `ifc_flow` audit row and allow. |
| `approve` | A human approves through the consent path; auto-approve modes count as denial. A grant covers that sink for those labels for the rest of the run. |
| `block` | Refuse. The agent is told not to route the data through another external tool. |

`scope = "explicit"` acts only when the labelled data appears literally in the
outgoing arguments. The default `context` scope acts on anything the agent has
seen, because a model can paraphrase. Either way, each decision carries a
witness: sensitive outputs are fingerprinted, and any copied span of 47 or
more characters is reported as an explicit flow.

```toml
[ifc]
mode = "approve"             # off | audit | approve | block
scope = "context"            # context | explicit
allow_sinks = ["web_search"] # external tools exempt from the check
```

`MAVERICK_IFC_MODE` overrides `mode`. A codebase run enforces at least its
repository's protection mode whatever the global setting.

## Evidence

Each decision writes an `ifc_flow` audit row containing the tool, labels,
explicit labels, mode and outcome (`recorded`, `approved`, `denied` or
`blocked`), plus a counts-only local projection for the dashboard. Neither
records arguments or data.

## Limits

- Tracking is per run. Data an agent writes to durable memory and a later run
  recalls is not labelled by this module; `memory_guard` governs recall.
- Blackboard propagation is conservative: a reader absorbs a poster's labels
  even if the specific excerpt it was shown held none.
- The witness detects literal copies, not encodings or paraphrase. That is why
  context taint is the default gate.
