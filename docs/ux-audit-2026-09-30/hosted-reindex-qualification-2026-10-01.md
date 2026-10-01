# LW-122 hosted rebuild qualification

This follow-up applies to isolated `slice-hosted-reindex`, based on
`c24813cf91abd6f352c78e9cd5c3e970650f580c`. It closes the missing bounded paid
admission for the existing default Voyage path and supported direct OpenAI text
embedding models. It does not change the selected deployment model, endpoint,
credentials or OpenAI configuration.

The native library and typed Copilot review the same collection, model, captured
rate, expiry, effective configuration revision and cost/time limits. They display
positive small ceilings to six decimal places. The new administrator cost limit
is exposed in the settings catalog and knowledge installer journey. Unconfirmed
jobs offer status and explicit archival with retained receipts, without a retry
control that could resend an uncertain paid batch.

Each batch is admitted through the cumulative Budget plus native provider and
organization authority. Strict native usage drives the observed charge; the
cumulative usage ledger acknowledges it before a paid shadow checkpoint is
accepted. Unknown outcomes retain their holds and cannot be resent by the same
job. Shadow bindings, fresh source/configuration checks, safe cancellation and
atomic publication markers preserve the current index and distinguish a
completed publication from a pre-publication interruption.

The immutable price contracts are supported by the official
[Voyage price table](https://docs.voyageai.com/docs/pricing),
[Voyage input-limit table](https://docs.voyageai.com/docs/embeddings),
[OpenAI embedding reference](https://developers.openai.com/api/reference/resources/embeddings/methods/create)
and the linked OpenAI model pages recorded in `embedding_prices.py`. Prices were
captured on 2026-10-01 and expire after 2026-11-01. Voyage reservations use the
literal per-item limits; ambiguous aggregate “K” units are not used to reduce
the reservation. The rate-based ceiling excludes tax, negotiated terms and
storage-service costs and is explicitly not an absolute vendor-invoice promise.

The current [Cohere pricing page](https://cohere.com/pricing) does not provide a
verified standard rate for the configured `embed-v4.0` model. Newer Embed 5 or
reseller prices are not substitutes. Cohere v4 and custom/unpriced routes remain
blocked before dispatch pending current first-party evidence or a separately
verified agreement. No silent fallback or deployment change resolves that
external evidence boundary.

Final targeted evidence is recorded in task-owned `audit/` logs. The native
cohort covers paid reservation before HTTP, exact returned usage and model,
invalid/missing/over-bound usage, per-task and organization limits, partial
accounting acknowledgement, interruption before/after sealed batches and atomic
publication, cancellation, changed requester, configuration/credential/cost
authority/source changes, evidence expiry, and native/Copilot review parity.
Tests freeze the price-capture date deliberately. HTTP responses are synthetic;
no live provider, production, deployment or credential operation was performed.

- **137 passed, ordinary**, 38.27 seconds:
  `audit/reindex-final-ordinary-v3.log`.
- **The same 137 passed, secure**, 39.42 seconds, with both
  `MAVERICK_SECURE_DEFAULT=1` and `MAVERICK_TEST_SECURE_DEFAULT=1`:
  `audit/reindex-final-secure-v3.log`.
- **1,579 settings-catalog checks passed** in the preceding secure cohort
  (`audit/reindex-paid-secure.log`, 1,711 total including 132 then-current
  overlapping native cases). Do not add overlapping cohort counts as unique
  tests. The catalog entry was unchanged after that check.
- Changed-file Ruff, Python 3.10 syntax, JavaScript syntax and Git whitespace
  checks passed. The repository Vulture check passed.

The final 137-case cohort includes the complete files
`test_hosted_knowledge_reindex.py`, `test_knowledge_reindex_jobs.py`,
`test_embedding_prices.py`, `test_budget_pricing.py`, `test_pricing_provider.py`,
`test_knowledge_library_api.py`, `test_product_operations_knowledge_analysis.py`,
`test_product_operations_reindex_cost.py`, `test_wizard_knowledge_publication.py`,
`test_wizard_knowledge_revision.py`, `test_provider_cost_accounting.py` and
`test_cohere_embed.py`. All eight package roots came from this isolated clone
through process-local `PYTHONPATH`; the shared environment was not modified.

The focused installed-Chrome journey passed at worker commit
`43f567a6fc9d18e96d024499f2ae217baf40ced3` (1.8-second case, 12.455-second
runner). Synthetic responses exercise the shipped client: a positive $0.000125
ceiling appears before confirmation; dispatch preserves the exact reviewed
context; observed and unresolved costs retain six decimal places; unconfirmed
jobs offer no resend control; confirmed Archive retains cost receipts. The
report is `audit/browser-hosted-reindex-43f567a-results.json`. This browser case
does not invoke a provider; the native accounting behavior is covered by the
137-case cohort above. Final complete-browser qualification remains separate.

Installed-model performance and distributed PostgreSQL publication are separate
qualification boundaries. New cost/recovery copy remains under the existing
English fallback; this patch does not claim added translation coverage.
