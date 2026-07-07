---
name: add-capability
description: End-to-end wiring checklist for any new Lightwork capability — feature, tool, channel, provider, or config-gated behavior — so it ships reachable (wizard), governed (config/budget/shield), tested, and documented. Use whenever adding functionality a user could enable, not just when the word "feature" appears.
---

# Add a capability the Lightwork way

The most common rejected-work failure in this repo is not broken code — it's
a capability that EXISTS but is UNREACHABLE or UNGOVERNED. Real case: the
Consequence Engine shipped with a config knob and a kernel reader but no
wizard step, so no installer user could ever turn it on. Kernel rules 5–6
exist because of this: *no new top-level deps without a config knob; the
wizard is the UX source of truth.*

Work through every station below in order. Skipping one is how a green PR
still gets bounced.

## Station 0 — design answers (write these down before coding)

1. **What's the config section and key?** Every capability is toggled by
   `[section] enable = true`-style config, OFF by default.
2. **Does it call an LLM?** Then it needs a role, resolved via
   `maverick.llm.model_for_role(role)` — never a literal model id — and its
   token spend must flow through the runtime `Budget`.
3. **Does it shell out?** Only via `sandbox.exec()` on a sandbox obtained
   from `build_sandbox()`.
4. **Is it a tool the agent can invoke?** Then its execution goes through
   `ctx.shield.scan_tool_call()` and destructive options must use the
   `as_bool()` convention (only literal `True` authorizes).
5. **New third-party dependency?** STOP — that's a Tier-3 escalation
   (CLAUDE.md §7) unless the task explicitly grants it. If granted, it must
   be an optional extra with a config knob, never a hard kernel dep.

## Station 1 — kernel implementation

- New concern = new top-level module in `packages/maverick-core/maverick/`
  (one concern per module; grep first — 361 modules, yours may exist).
- Read config via `load_config()`; treat a missing section as disabled.
  Fail open on optional integrations (kernel rule 1 pattern —
  see `shield_policy.py` for the canonical seam).
- Relative imports inside the package; keep gate-visible modules
  side-effect-free at import (lazy-import heavy deps inside functions, the
  way `migration_governance.py` does).

## Station 2 — config knob registration

- Add the new `[section]` name to `KNOWN_SECTIONS` in
  `packages/maverick-core/maverick/migrate.py` — otherwise `config-lint`
  false-flags every config that uses it. `test_wizard_parity.py` pins this
  parity; it will fail and tell you.

## Station 3 — wizard step (apps/installer-cli/maverick_installer/wizard.py)

For a simple on/off feature (the common case — same shape as `self_modify`,
`energy_aware`, `output_cache`, `local_first`, `consequence`):
1. Add the key to `pick_advanced()` so the advanced flow offers it.
2. Teach `_cfg_advanced()` to emit the `[section]` when enabled.
3. Decide whether express mode (`_EXPRESS_ADVANCED`) should enable it —
   express = "all SAFE features fast"; anything that creates tools, allows
   MCP, or spends money stays out of express.

For a bigger step: a `pick_<name>()` function, wired in `run()`, added to
`STEPS` (progress bar), emitted by `write_config()`.

Parity tests that MUST exist (copy the consequence-engine trio in
`apps/installer-cli/tests/test_wizard_parity.py`):
- `test_new_pick_exists` parametrization covers your pick.
- `test_write_config_emits_<name>_when_enabled`
- `test_write_config_omits_<name>_by_default` — declined features must write
  NO section at all, not `enable = false`.

Special cases:
| Adding a… | Also wire |
|---|---|
| channel | wizard `CHANNELS` list + `server.py` `_WIRES`; if it receives inbound messages it MUST be in `_ALLOWLIST_CHANNELS` so the wizard collects `allowed_user_ids` — the base allowlist is fail-closed and an empty list refuses every sender |
| provider | wizard catalog `models.py`; OpenAI-compatible providers ride the `openai` client — don't add a new SDK dep |
| CLI command | mention it in `docs/getting-started.md` (PR-template checklist item) |
| dashboard page | a11y (`python -m maverick.a11y_audit --ci`), Origin-header test pattern, package-data globs for any new template/static asset, liquid-glass/enterprise copy voice |
| agent tool | register via the `tools/` registry pattern (thin wrapper over a rich module), shield scan, injected kwargs (`budget=`, sandbox) — grep `tools/budget_status.py` for the minimal shape |

## Station 4 — tests

- `packages/<pkg>/tests/test_<module>.py`, absolute imports.
- LLM paths use the `FakeLLM` fixture (`.scripted` responses, assert on
  `.calls`); never a real key.
- Any `Budget` in the test raises EVERY cap the code path will cross —
  `record_tokens` enforces all of them, not just the one you're testing.
- Async tests carry `@pytest.mark.asyncio` (strict mode).
- Fake credentials/tokens in fixtures: `# pragma: allowlist secret`.

## Station 5 — docs & changelog

- `docs/FEATURES.md`: present-tense entry, grounded in the module path —
  this file's credibility rule is "what Lightwork does today, on main".
- `CHANGELOG.md` under `## [Unreleased]` / `### Added`: bolded feature name,
  one-paragraph explanation, the config knob, and the kill-switch.
- New CLI verb → `docs/getting-started.md`.

## Station 6 — ship

1. Commit shape: `feat: <subject starting with a letter>`; body = why; end
   with a `Tests:` line naming what the parity/regression tests pin.
2. Run `/preflight` (all 16 lint gates + suite + audit parity).
3. Run the review ritual: `/code-review`, `/simplify`, and
   `/security-review` if the capability touches auth, tools, subprocess, or
   crypto. Land findings as a follow-up `fix:`/`refactor:` commit.

## Done means

A user on a fresh install can run `maverick init`, see your capability
offered, enable it, and the emitted config drives the kernel behavior — with
the suite, all gates, and the parity tests green, and FEATURES.md/CHANGELOG
telling the truth about it.
