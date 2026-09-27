# Adaptive immunity

Policies, Shield, capabilities and approvals are *innate* controls: each one
governs a single agent or a single action. The adaptive layer
(`maverick.immune`) looks for what those controls cannot see and proves each
finding. Every analysis is deterministic and offline: a finding comes with the
witness that establishes it, never a model's opinion.

| Question | Module | CLI |
|---|---|---|
| What can agents do *together*? | `immune.network` | `maverick immune network` |
| Is the human in the loop catching flaws? | `immune.oversight` | `maverick immune canary / drill / oversight` |
| Can a machine-assisted discovery be owned? | `immune.title` | `maverick immune title check / seal / verify` |
| Do two rules contradict? Is a candidate rule better? | `immune.policy` | `maverick immune rules` |
| Does a model act differently when watched? | `immune.invariance` | library |
| What should an agent's output be bonded for? | `immune.reliability` | library |
| Which facts rest on an unverified agent claim? | `immune.epistemic` | library |
| Is a learning change inside its declared envelope? | `immune.envelope` | library |
| Are two pricing agents moving in lock-step? | `immune.market` | library |

## Composition risk

The Rule of Two: no agent system should combine *sensitive data*, *state
change* and *external reach* without a control. Data moves between agents
through the shared blackboard, peer messaging and delegation, and those
coordination tools cannot be removed by a pack allowlist. So the envelope that
matters is the one along a data-flow path, not the one on each agent.

`maverick immune network` evaluates every pack at its **runtime** capability
(`domain_capability`), under explicit settings (`--levels`, `--grounding`,
`--swarm`), or any agent estate you describe as JSON (`--graph`):

```json
{"agents": {"crm_reader": ["crm_read"], "planner": ["shell"], "mailer": ["email"]},
 "flows": [["crm_reader", "planner"], ["planner", "mailer"]]}
```

Each finding names the path and the concrete tools (`sensitive`, `mutate`,
`external`) and the risk of the least-guarded way out (`sink_risk`); `high`
sinks are the ones secure defaults escalate to a human. Tool classes are
explicit tables in `immune/network.py`; `mcp_*` tools are unknown remote
code and count as all three. `maverick domains-audit` output now also reports
each pack's `runtime_expansion` and `runtime_reachable_dangerous`.

## Proof of Oversight

With `[immune] canaries = true`, an operator seeds realistic, known-flawed
approvals into the real queue:

```bash
maverick immune canary --class payments --action "Pay INV-4411 to updated bank account" \
  --detail "Vendor bank details changed 2h ago" --flaw "bank change + urgency"
maverick immune oversight            # catch rate and earned authority per person/class
maverick immune drill carol month_end_close --passed   # no-agent capability drill
```

A denied canary is a catch; an approved one is a miss. The canary's identity
lives only in a private server-side ledger. The approval itself carries no
marker. Nothing waits on a canary approval, so no decision executes anything.
Canaries are excluded from agent autonomy graduation.

*Earned authority* is the Wilson lower bound of a person's catch rate for a
decision class (default: at least 10 decided canaries and 80% assured). It is
evidence for routing decisions, not a performance-management tool. Deploy it
with the consent and works-council process your jurisdiction requires.

`oversight.find_cruxes` returns the claims whose falsity alone flips a
decision, so a reviewer checks those instead of the whole document.
`oversight.oversight_record` signs who decided what, on which cruxes, with
what measured reliability.

## Discovery Title

A dossier (JSON) records a machine-assisted discovery as it happens:

- `inputs` and `inputs_committed_at`: digests committed before the run.
- `priority`: commit-reveal digests of ideas with timestamps (`title.commit`).
- `gates`: human decisions and the claim elements each conceived.
  `framed`, `selected`, `modified` and `conceived` count as conception;
  `reviewed` or `accepted` alone does not.
- `claims`, `verification` and `coverage`.

`maverick immune title check` lists what would not survive scrutiny: an
unconceived claim element, an unsigned problem statement, a failed or missing
check, partial or unknown search coverage, or inputs committed after the first
idea. `seal` signs the dossier with the audit identity. `verify` checks it
offline, including any priority commitments the holder chooses to open.

The dossier is evidence for counsel, not a legal opinion on inventorship.

## Production twin

With `[immune] twin_record = true`, every tool result a goal receives is
recorded at the agent's single execution point. The recording is private, per
tenant, and sealed (bound to the goal) when at-rest encryption is on.
`maverick immune twin replay GOAL` runs a new goal with the same title,
description, owner, principal, domain and project, while each tool returns the
result production received for the same tool and arguments, in the same order.

- Nothing live is touched. A call production never made returns an ordinary
  tool failure, is not executed, and is counted as unmatched.
- Coordination tools still run, so the swarm re-forms and its children replay.
  `ask_user` is replayed, so a twin never messages a person.
- Nothing in the run marks it as a test. That makes twins the unwatched arm
  for `maverick immune awareness`, which compares a behaviour's rate in runs
  a model could identify as tests with its rate in runs it could not.
- Use a twin to try a new model, prompt or policy on real work. The report
  gives served, unserved and unmatched counts; `twin list` keeps counts only.

A twin goal is an ordinary goal row, so the learning loops see it like any
other run. Recording stops at 64 MB per goal.

## Configuration

```toml
[immune]
canaries = false   # allow `maverick immune canary` to seed the approvals queue
twin_record = false  # record tool results so goals can be replayed as twins
```

The analyses are read-only and need no switch. Canary seeding writes to shared
state and twin recording retains tool output, so those two are gated.
