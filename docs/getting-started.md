# Getting started

## Install

Use Python 3.10–3.12 and a reviewed full commit SHA. The first-party distribution
names are not yet published as a complete public PyPI cohort; install their local
sources together through the manifest installer. It supplies all eight packages,
including the dashboard, wizard, channels, MCP server, knowledge and learning
packages. `release-runtime` supplies the OpenAI-compatible provider client;
`evidence-analysis` supplies the PDF and spreadsheet parsers.

On macOS/Linux:

```bash
git clone https://github.com/Daybreak-AI-Labs/Lightwork
cd Lightwork
git checkout --detach <reviewed-full-40-character-commit-sha>
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade 'pip==26.2.1'
python scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
lightwork init
lightwork dashboard
```

On Windows PowerShell (replace the commit placeholder first):

```powershell
git clone https://github.com/Daybreak-AI-Labs/Lightwork
Set-Location Lightwork
git checkout --detach <reviewed-full-40-character-commit-sha>
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip==26.2.1
.\.venv\Scripts\python.exe scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
.\.venv\Scripts\lightwork.exe init
.\.venv\Scripts\lightwork.exe dashboard
```

The explicit executable paths avoid a PowerShell activation-policy requirement.
Configure the intended provider and sandbox in the wizard. A source installation
does not provision third-party accounts, API credits, a container engine or a
Kubernetes cluster. Follow the selected integration's requirements and verify a
real authorized operation before relying on it for customer work.

To verify the installed cohort, use the same environment's Python:

```bash
python scripts/install_release_cohort.py --verify-only
python -m pip check
lightwork version
lightwork doctor
```

On Windows, substitute `.\.venv\Scripts\python.exe` and
`.\.venv\Scripts\lightwork.exe` for those executable names.

For the authenticated desktop source bootstrap, obtain
`deploy/desktop/install.sh` or `deploy/desktop/install.ps1` from that reviewed
commit and set `MAVERICK_REF` to its lowercase full SHA. This remains a source
bootstrap with repository-access and platform prerequisites; it is not a
self-contained signed product installer. The scripts reject mutable refs and
have no first-party public-index fallback. Every `MAVERICK_*` variable can also
be spelled `LIGHTWORK_*`.

## First run

In the desktop app, open **Start → Set up named sign-in** before inviting a
team. On a fresh local installation, the owner chooses an email label and a
local passphrase. This closes anonymous administrator access and opens the
existing **Users** invitation controls. The email is a local identity label;
Lightwork does not verify that mailbox. Keep invitation links private.

The owner can sign in again on this computer at `/auth/local-login`. Invitees
use their single-use invitation and its expiring session; an administrator can
issue a new invitation when needed. In **Workspaces**, create and select the
workspace, then grant each colleague workspace permissions and department
access in **Users**. Project reviewers receive only explicitly shared captured
results; reviewing one does not authorize execution of the underlying task.

First-owner setup accepts only direct loopback requests on the running
computer, before any role assignments or managed sign-in configuration. The
installer asks whether to allow it (`[dashboard] local_owner_setup`, default
`true`; `MAVERICK_LOCAL_OWNER_SETUP=0` disables enrollment). Explicit deployment
policies disabling invites or configuring managed sign-in remain authoritative.
The private `dashboard-owner.json` claim is recovery authority: a damaged or
incomplete claim keeps anonymous access closed, and restore cannot silently
replace it with a different owner. Login never restores a demoted or removed
owner's privileges.

The source-install command-line workflow remains available:

```bash
maverick init
maverick preflight
maverick doctor
```

The wizard takes ~2 minutes. It writes `~/.maverick/config.toml` and `~/.maverick/.env`.

New setup leaves ordinary task limits unset unless you choose one. Existing
numeric settings are preserved; **Settings → Task limits** can disable saved
task defaults reversibly. Usage remains metered, and organization allowances
remain independent. See [task limits and spend accounting](task-limits.md).

Then:

```bash
maverick start "Build a CLI that emails me a digest of today's top Hacker News stories — research the API, write it, and verify it runs"
```

## Watch the swarm decompose

Run `maverick monitor` in a second terminal. The orchestrator plans the goal, then spawns specialist sub-agents that work in parallel — here a researcher pins down the API, a coder writes the tool, and a verifier runs it:

```
Goal #1 active  2m elapsed
Build a CLI that emails me a digest of today's top Hacker News stories

Plan tree
  ├─        done  #2 Research the Hacker News Firebase API
  ├─      active  #3 Write the digest CLI (fetch + format + send)
  ├─      active  #4 Verify it runs and emails a sample digest
  ├─     pending  #5 Write a short usage README

Latest episode #7 (running)  $0.0431  in=18,204 out=2,910 tools=11

Recent activity
  4s ago [researcher] decision: top stories live at /v0/topstories.json, then /v0/item/<id>.json
  3s ago [coder] tool_call: write_file hn_digest.py (118 lines)
  1s ago [verifier] tool_call: run "python hn_digest.py --dry-run" -> printed 10 stories

Cumulative spend on this DB: $0.21
```

When done:

```bash
maverick status      # what's currently active or blocked
maverick skills      # what the swarm distilled from this run
maverick facts       # what it learned about you
```

## Pausing / resuming

If the swarm needs something only you can answer, it pauses and queues a question:

```bash
maverick status
# shows: open questions: #3 (goal 1): Which dates are you traveling?

maverick answer 3 "May 15-29"
maverick resume
```

Goal records survive restarts. Restoring an ordinary agent's saved execution
state requires `[durable] enabled = true` or `MAVERICK_DURABLE=1`; without it,
retained goal context is not an execution checkpoint. An uncertain external
action or provider charge needs reconciliation before retry. Durable workflow
frames preserve supported continuation boundaries, not arbitrary external
effects. See [workflow progress and retry](task-limits.md#workflow-progress-and-retry).

## Undoing a run's file edits

With the workspace journal enabled (`[workspace] journal = true`, or say yes
at the wizard's *Workspace journal* step), every file the agents overwrite or
create through the file tools is captured before the mutation:

```bash
maverick journal list --goal 12     # what was touched
maverick journal diff --goal 12     # pre-run state vs the disk now
maverick journal revert --goal 12   # restore every file, newest-first
```

Reverting writes a signed `workspace_revert` audit row and archives the
journal. Shell-driven mutations (`rm`, `mv`, redirects) are not journaled —
the audit trail records those commands instead.

## Building your own specialist from a watched task

You don't have to describe a job in words — you can show it. Capture an ordered
record of someone doing the work (the actions they took and any narration of why)
as JSONL or simple prefixed text, then hand the file to Lightwork:

```
ACTION[gmail]: send the morning digest -> ops@acme.com
NOTE: only the top 5 stories, with one-line summaries
SEE: digest looks right, ops confirmed receipt
```

```bash
maverick learn-demo demo.txt
```

This parses the demonstration, induces a draft specialist, shows you the derived
workflow, and waits for your approval before saving. Secrets are redacted at the
door, and the draft inherits the same capability clamp and persona scan a
described pack gets — nothing activates without your yes. Useful flags:

```bash
maverick learn-demo demo.txt --name "Morning Digest" --no-llm --yes
```

`--no-llm` mirrors the observed steps deterministically (tools = what the person
used); drop it to let the model propose from the transcript.

The same agent-factory flow runs when you build a pack conversationally:

```bash
maverick onboard
```

On approval, `onboard` can provision catalog skills and synthesize declared tools
when `[self_learning]` / `provision_packs` permits it. This does not provision
third-party credentials or establish that an external adapter is available.
Deployment checks the assembled tool registry, including custom adapters, and
refuses a workflow whose required tools are missing. Complete the reported
adapter, enablement or approved-export prerequisites before running the pack.
Provisioning never widens its capability envelope.

## Voice input (built-in, offline)

The dashboard's mic button and the `transcribe_audio` tool work with no
provider key: the local Whisper engine ships with the dashboard package, and
opening the app does not download model files by default. In **Settings > Models >
Local voice setup**, a global administrator can review the configured model's
source and size (about 148 MB for the default model), then check or download it.
Progress survives a page reload; cancellation removes the unfinished download,
and failures can be retried in the same screen. Existing files are not labelled
verified until their exact size and checksum pass. Model verification does not
prove the speech engine is loaded or that a microphone works.

The installer asks explicitly whether to allow startup downloads. A saved
`[voice] auto_fetch_model = true` or `MAVERICK_VOICE_AUTO_FETCH=1` still enables
them; network policy is always enforced, including redirects. Managed custom
model paths and a selected cloud speech backend retain their configuration;
this setup screen does not silently replace them. The optional CLI is also available:

```bash
maverick voice setup    # fetch the checksum-verified model (~148 MB)
maverick voice status   # see which speech-to-text backends are usable
maverick voice transcribe clip.wav   # smoke-test the pipeline
```

Prefer a system engine instead? A `whisper.cpp` binary on PATH (`brew
install whisper-cpp`) or faster-whisper both plug into the same chain. Set
`[voice] stt_backend = "local"` to keep audio on-machine even when
OpenAI/Groq keys are configured.

## Changing models or providers

Re-run the wizard any time:

```bash
maverick init
```

Or edit `~/.maverick/config.toml` directly. The `[models]` section maps each agent role to a `provider:model-id` string. See [`configuration.md`](./configuration.md) for the schema.

## Where data lives

| File | What |
|---|---|
| `~/.maverick/config.toml` | Your config (deployment, models, safety, budget) |
| `~/.maverick/.env` | API keys (chmod 600) |
| `~/.maverick/world.db` | Persistent world model: goals, facts, episodes |
| `~/.maverick/skills/` | Auto-distilled SKILL.md files from successful runs |
| `~/maverick-workspace/` | Default sandbox working directory |
| `~/.maverick/learned-skills/` | Skills distilled by the learning loops |
| `~/.maverick/dreams/` | Consolidated insights, rehearsal queue, learning snapshots |

All local. Nothing is uploaded except your prompts to the cloud LLM you chose.

Once you have a few runs behind you, the learning surface is four commands:
`maverick dream` (consolidate experience), `maverick hindsight` (did learning
help or regress?), `maverick proof` (deliverables, cost avoided, ROI), and
`maverick domains-lint` (audit the 2,031-agent specialist catalog), plus
`maverick domains-audit` (governance posture: what each agent can reach, denies,
and refuses) and `maverick domains-eval --check` (behavioral golden cases).
