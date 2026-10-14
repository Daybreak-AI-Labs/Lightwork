# CRA Product Security Officer standalone

This reduced source SKU runs without Lightwork. It provides an unsigned local
product and release inventory, bounded minimal CycloneDX 1.5/1.6 and SPDX 2.3
JSON normalization, explicit human applicability and reportability gates,
Article 14 clock projections, local VEX/report drafts, and approval plus receipt
records bound to the exact report-packet SHA-256 digest. Each local receipt also
binds the exact approval timestamp and is rejected if its claimed external
submission predates that approval.

It is an operational aid, **not legal advice or an automated legal decision
maker**. A qualified human determines whether the CRA applies and whether a
case is reportable. The app makes no network regulator filing, does not sign an
audit or evidence graph, has no evidence connectors, and never persists raw
SBOM documents. It rejects secret-shaped field names, but ordinary allowed text
values are stored verbatim and are not secret-scanned; never enter credentials
or other secrets. A local receipt records a user's attestation about an
external submission; it does not claim the app submitted anything.

## Clock semantics

For both a potential actively exploited vulnerability and a potential severe
incident, the app projects early warning at awareness plus 24 hours and full
notification at awareness plus 72 hours. An actively exploited vulnerability's
final-report clock is 14 calendar days after a corrective or mitigating measure
becomes available. A severe incident's final-report clock is one calendar month
after the full notification is actually submitted. Only a receipt for an
exactly digest-bound, locally approved full-notification packet can supply its
required `submitted_at` anchor; the manual anchor endpoint rejects severe
incidents. Until that receipt is atomically recorded with the case update, the
final deadline remains visibly `awaiting_anchor`.

No deadline is projected or stored while reportability awaits a human decision,
and a case marked not reportable never receives a clock. After an explicit
`reportable` decision, projections remain labelled `calculation_only` and
`not_a_legal_decision`.

## Authority matrix

| Runtime | Clock declarations | Record and decision authority |
| --- | --- | --- |
| `CRA_PSO_STANDALONE=1` | Vendored rules; no `maverick` import | Unsigned private local JSON CAS store |
| Auto mode, core absent | Vendored rules | Unsigned private local JSON CAS store |
| Auto mode, core importable | `maverick.cra_ops.CRA_CLOCKS`, analysis only | Unsigned private local JSON CAS store; no platform writes |

Integrated Lightwork adds tenant-scoped governed records, signed audit and
evidence packs, evidence connectors, and production authority. Those are
deliberately absent here. Automatic legal decisions and unattended regulator
filings are not advertised as an upsell: they remain prohibited boundaries.

## Run

```bash
python -m pip install -r requirements.txt
bash run_standalone.sh                 # http://127.0.0.1:8895
```

Set `CRA_PSO_STORE` to select the local store path and `CRA_PSO_PORT` to change
the port. Mutations require the exact current non-negative `expected_revision`.
The server accepts JSON only on loopback, rejects cross-origin browser writes,
caps streaming request bodies at 640 KiB, bounds JSON structure, and rejects
raw or secret-shaped fields. The local store uses cross-process locking,
atomic replacement, private file permissions where the OS supports them, and
rejects any commit that would make its JSON file exceed the 8 MiB read limit.
Ordinary allowed values are not inspected for embedded secrets.

The main endpoints are:

- `POST /api/products` and `POST /api/products/{id}/applicability`
- `POST /api/releases` with `sbom`
- `POST /api/cases`, `POST /api/cases/{id}/reportability`, and
  `POST /api/cases/{id}/anchors`
- `POST /api/vex`
- `POST /api/reports`, `POST /api/reports/{id}/decision`, and
  `POST /api/reports/{id}/receipts` (requires the externally observed
  `submitted_at` timestamp)
- `GET /api/records`, `/about`, and `/healthz`

Tagged builds package the explicit `release-manifest.json` allowlist as a
licensed source archive with hashes and an aggregate CycloneDX release SBOM.
