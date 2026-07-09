# Lightwork — Proof & Results

> Reproducible evidence that Lightwork's core claims hold. Every scoreboard
> below is produced by **real enforcement / learning code on this machine** — no
> mocks, no LLM, byte-for-byte reproducible. Run them yourself in under a minute.
>
> **What this proves (and what it doesn't).** These artifacts prove the
> platform's *guarantees* and the self-learning loop's *properties* —
> governance holds, the loop is deterministic, bounded, reversible, and
> poison-resistant ("it can't quietly get worse" is **provable**). They are
> **not** a customer ROI figure or a competitive capability benchmark: the
> compounding learning curve needs real run history and competitive scores need
> a model provider key — both are reported honestly as *not yet populated*
> rather than fabricated (see §3). For Lightwork, that refusal-to-fabricate is
> the point.

---

## 1. Platform guarantees — `python proof/run_proof.py`

The control plane is the product. Seven guarantees, run against the **real
shipped roster** through the **same enforcement primitives** the production tool
chokepoint calls:

```
  [PASS]  Least privilege by construction   finance_ap runs at max_risk='medium' (parent=high): reads AP, no shell, no payments
  [PASS]  No money without a human          no money/posting tool is permitted by any of 70 finance packs
  [PASS]  Delegation-of-authority $ gate    $4k release auto, $6k release -> REQUIRE_HUMAN, $60k wire -> DENY (rule=deny_above)
  [PASS]  Fleet can read, not write         finance_ap reaches billdotcom_read (GET-only); the write connector is refused + capability-denied
  [PASS]  Segregation of duties clean       all 70 finance packs SoD-clean (no compartment unions incompatible duties)
  [PASS]  Verified peer handoffs            authentic handoff verifies (rule=ok); tampered copy rejected (rule=tampered)
  [PASS]  Tamper-evident audit ledger       2-row signed chain verifies clean; altering an amount is caught (bad_hash)
  ----------------------------------------------------------------------------
  7 guarantees PROVEN, 0 failed
```

## 2. Self-learning loop — `python proof/self_harness_proof.py`

The self-improvement loop drives the **real** `self_harness` → `self_improvement`
gate. The headline is **determinism**: identical inputs produce a
byte-identical learned store across independent runs — "improves consistently"
made checkable.

```
  [PASS]  determinism (consistent)    6/6 runs identical (sha256 a827f0a7b37f...)
  [PASS]  off by default              recall == '' and no store write when off
  [PASS]  governed gate enforced      open gate promotes; frozen verifier writes nothing
  [PASS]  no trace poisoning          secret/scoped/control-char excluded from the recalled prompt
  [PASS]  bounded addendum            <= 8 lines / 1500 chars under overflow
  [PASS]  concurrency safe            8 concurrent promotions, 0 lost, store valid
  [PASS]  reversible + auditable      rollback handle restores exactly; forget() clears guidance
  ----------------------------------------------------------------------------
  7 guarantees PROVEN, 0 failed
```

What each guarantee underwrites, in plain terms:
- **determinism / consistency** — the same situation always yields the same
  learned change; no silent drift.
- **governed gate enforced** — a learned change is promoted **only** when the
  verifier opens the gate; a frozen verifier promotes nothing. Self-improvement
  can't grade its own homework.
- **no trace poisoning** — secrets, attacker text, and control characters never
  reach a learned prompt.
- **bounded + reversible** — learned guidance stays within hard caps and is
  undone in one step (`forget()` / rollback handle), every change audited.

## 3. Signed evidence bundle — `python -m maverick.proof_pack`

One command emits an **Ed25519-signed, reproducible** bundle (`PROOF.md` +
`proof_manifest.json`). Verdict from a clean run:

```
**ALL HARD GUARANTEES HOLD.** Hard guarantees: governance, reliability, perf_sla.

| section            | status              | evidence |
|--------------------|---------------------|----------|
| governance (hard)  | PASS                | 7 guarantees proven |
| reliability (hard) | PASS                | chaos game-day exit 0; plugin 1500 calls 88.8% success; 16 writers x 25 rows: 400 written, 0 errors |
| perf_sla (hard)    | PASS                | dispatch p95 0.475ms (<=5); compaction 0.687ms (<=250); world write p95 0.534ms (<=25); world read p95 19.1ms (<=25) |
| shield_asr         | SKIPPED             | offline shield-ASR harness is repo-only |
| learning_curve     | INSUFFICIENT DATA   | no recorded run history yet — populates after real goals + `maverick dream` |
| benchmarks         | NOT RUN (needs key) | competitive scores require a provider key — reported NOT_RUN, never fabricated |
```

**Why the SKIPPED / INSUFFICIENT / NOT-RUN lines matter:** the bundle reports
exactly what it can and cannot prove on a given machine, and **refuses to invent
a number**. The hard guarantees (governance, reliability, performance) gate the
verdict and pass on real code; the competitive and learning-curve sections are
honestly empty until a real deployment populates them. This is the
"we don't say it — we show it, and we don't show what we can't" posture.

## 4. The golden path (the receipts) — `python -m maverick.golden_path`

A seeded finance-ops storyline through the real governance/capability/audit/
budget code (no model), emitting a narrated story + a signed audit chain:

| # | scene | verdict | receipt |
|---|---|---|---|
| 1 | AP specialist boots under the controller | **SEALED** | max_risk high→medium · cannot shell · cannot release payments |
| 2 | $60,000 wire | **DENY** | rule = deny_above |
| 3 | $6,000 release | **REQUIRE_HUMAN** | rule = require_human_above |
| 4 | $4,000 release | **ALLOW** | rule = default |
| 5 | runaway loop | **CAPPED** | hard budget ceiling held |
| 6 | auditor alters $60,000 → $60 | **CAUGHT** | signed hash-chain broken (bad_hash) |

---

## 5. Governed DGM code rung — `python proof/dgm_code_rung_proof.py`

The self-modification (Darwin-Gödel-Machine) capability, made checkable. A real
one-line code change is driven end-to-end through the **real governance chain**:
the editable-surface boundary, a held-out fitness eval on isolated copies with
real pytest, the capability non-escalation proof, an Ed25519 operator signature,
and the append-only promotion ledger. No LLM, no network.

```
  [PASS]  editable-surface boundary         allowlisted path accepted; control-plane edit refused structurally
  [PASS]  fitness discriminates (held-out)  held-out 0.462 -> 1.000 over 13 unseen tests, overfit=False
  [PASS]  reward-laundering refused         held-in gamed; held-out flat -> OVERFIT, refused
  [PASS]  capability non-escalation         before==after over 8 probed tools; no new authority
  [PASS]  unforgeable human approval        Ed25519 operator signature; swapped payload NOT authorised
  [PASS]  governed promotion + ledger       all 5 gates PASS; signed promotion recorded to append-only ledger
  ----------------------------------------------------------------------------
  6 guarantees PROVEN, 0 failed   (LLM proposer + container isolation stubbed, by design)
```

The load-bearing property is line 3: a candidate that **memorises the seen tests
without fixing the capability** gains on the held-in split but not the held-out
split, is flagged `OVERFIT`, and is refused. That is the anti-reward-laundering
guard — the reason a self-improving loop measured against a held-out yardstick
cannot quietly launder drift into a "promotion". Two seams are stubbed and
disclosed: the LLM *proposer* that authors the patch (the harness supplies the
exact diff a proposer would emit), and container isolation (the eval runs on the
host and says so; a require-container deployment fails closed instead).

---

## Reproduce & verify (≈1 minute, no provider key)

```bash
# from a source checkout
python proof/run_proof.py            # 7 platform guarantees
python proof/self_harness_proof.py   # 7 self-learning-loop guarantees
python proof/dgm_code_rung_proof.py  # 6 governed DGM code-rung guarantees
python -m maverick.golden_path -o ./gp        # the receipts (story + signed audit.ndjson)
python -m maverick.proof_pack -o ./proof      # signed evidence bundle

# verify the signed audit chain offline (third-party tamper-evidence)
maverick audit verify --file ./gp/audit.ndjson --pubkey <publisher-ed25519-hex>
# verify the proof bundle's signature
python -m maverick.proof_pack --verify ./proof/proof_manifest.json --pubkey <publisher-ed25519-hex>
```

## What we deliberately do **not** claim

- **No competitive capability benchmark.** Lightwork competes on governance and
  provable learning, not raw agent task-success; a leaderboard would be
  off-thesis and is intentionally not presented.
- **No customer ROI figure** until a real deployment produces one — the
  `learning_curve` section stays `INSUFFICIENT DATA` rather than estimate.
- **No fabricated security score.** `shield_asr` measures the *built-in*
  fallback detector; the load-bearing defense is **containment** (least
  privilege), proven under §1.

> Environment for the runs above: Python 3.11 on Linux; `cryptography` present
> (Ed25519 guarantees ran live, not CI-deferred). Results are reproducible on
> any source checkout.
