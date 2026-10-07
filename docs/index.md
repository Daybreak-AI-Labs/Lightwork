# Lightwork

> AI workers, company knowledge and repeatable workflows — under your control.

Lightwork brings AI workers, company knowledge, repeatable workflows and
governed learning together, with operating controls for your team —
deployed in your own environment. It supports
configured model providers including Claude, GPT, Gemini, DeepSeek, Ollama, and OpenRouter, and
ships a governed, auditable safety surface built for regulated teams.
Lightwork is proprietary, commercially licensed software (see
[`LICENSE`](https://daybreak-ai-labs.github.io/Lightwork/license/)).

## What you can do with it

- **Long-horizon software work**: recursive agent-spawns-agent
  orchestration with shared world model, budgets, and audit log.
- **Choose an AI connection**: connect a supported API provider or a
  self-hosted model from the product, then test the connection before work
  starts. Provider charges and task limits depend on the connection and
  settings you choose.
- **Computer use & web browser**: Anthropic-spec computer-use tool +
  Playwright-driven browser tool, with kill switches and an audit
  trail for every action.
- **Messaging channels**: connect supported services such as Telegram,
  Discord, Slack, Signal, and email in the Channels page, then send a test
  message to confirm delivery.
- **Primary-source data grounding**: analyst packs are auto-granted 37
  read-only public-data connectors (SEC EDGAR, FRED, Treasury, World Bank,
  FDIC, Census, BLS, EIA, openFDA, NPPES, ClinicalTrials, USAspending,
  SAM.gov, CourtListener, Federal Register, GLEIF, OpenCorporates,
  NWS/NOAA, EPA, Climatiq, …) so they cite authoritative sources instead of
  model recall. On by default; kill-switch via `[workforce] data_grounding
  = false`. Alongside 2,877 write-capable enterprise REST/GraphQL connectors
  and dedicated modules (Salesforce, HubSpot, Stripe, ServiceNow,
  Snowflake, …).
- **Proven governance**: a roster-wide invariant test suite checks six
  governance invariants (tool-reachability, autonomy dial, capability
  attenuation, compartment isolation, unstrippable hard refusals, budget
  caps) across all 2,031 packs with a non-vacuous fault-injection control
  (property-fuzzed up to 5,000 iterations),
  plus hostile-argument fuzzing of every connector and tool.
- **Build an agent from a demonstration**: record a reviewed example of the job,
  then synthesize the agent that does it — `maverick learn-demo <file>`
  ingests that transcript, induces a profile through the same
  intake clamp + review gate, and provisions the skills/tools it needs.
  The factory also improves itself: `maverick factory-learn` mines
  provisioning/approval gaps back into future pack generation
  (off by default, never widens an envelope).

## Quick start

Request the customer package for your operating system from
[Daybreak Labs](mailto:chris@daybreakailabs.com?subject=Lightwork%20customer%20package),
verify its checksum and exact-tag signature, then run its `dashboard` command.
Complete the owner and provider setup in the application. A repository clone
copies internal research, audit and patent documents; source installation is
for authorized engineering.

## Watch it work

Open the dashboard to review running work, its activity log and cost. Some
in-product controls still need implementation and customer acceptance.

## Licensing & access

Lightwork is **proprietary, commercially licensed** software (see
[`LICENSE`](https://daybreak-ai-labs.github.io/Lightwork/license/)). It is self-hostable — the runtime executes entirely
in your own environment — and use requires a license. Pricing is handled
per engagement; [contact us](mailto:chris@daybreakailabs.com?subject=Lightwork%20inquiry) for
evaluation or enterprise access.

A deliberately stripped-down **open-source "lite" edition** may be released
later as a community on-ramp; the full runtime and the governance/compliance
platform remain proprietary.

## Where to go next

- [Getting started](getting-started.md) — install + first goal
- [Architecture](enterprise/architecture.md) — the governed agent runtime (OS-style primitives)
- [Configuration](configuration.md) — providers, channels, budgets
- [Deployment](deployment.md) — desktop / docker / VPS / phone modes
- [Safety](safety.md) — shield, audit log, kill switches, consent
- [Threat model](security/threat-model.md) — trust boundaries, capabilities, tenancy, and security controls
- [Security & compliance overview](enterprise/security-overview.md) — application egress controls, deployment boundaries, identity, audit/evidence
- [Editions](enterprise/editions.md) — Community vs Enterprise
- [Plugins](plugins.md) — extending the tool / channel / skill surface
- [Governed model improvement](MODEL_IMPROVEMENT_PLATFORM.md) — the learning lifecycle, regression detection, and promotion controls
- [Starter goals](starter-goals.md) — ready-to-run examples for common workflows
- [How Lightwork compares](comparison.md) — shipped capabilities and platform positioning
- Roadmap details are available during enterprise evaluation.
- [Contributing](CONTRIBUTING.md) — how to send PRs
