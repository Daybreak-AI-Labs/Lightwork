# Public translation catalog delivery

Full ordinary qualification exposed a real first-load regression: the Settings
index response was 454,660 bytes against its existing 300,000-byte limit. Every
page embedded all product translations, including text unrelated to that page.
The response-size test and its limit remain unchanged.

The complete public catalog now loads synchronously from a same-origin,
content-addressed JavaScript asset before the existing translation runtime.
Only interface language, regional formatting and catalog review status remain
inline. The asset includes no account, workspace, request or customer data.
Every original catalog entry, fallback language, placeholder and membership
lookup is preserved. Missing or mismatched assets fall back to marked English;
server-rendered content and deterministic controls remain available.

The asset rejects unknown languages and stale digests, supports conditional
requests, and uses immutable caching with a weak ETag for compression variants.
The inventory now follows the actual imported ROWS modules, so the separately
added flow-credential guidance is counted: 1,660 sources, no missing literal
bindings on the ten inventoried journey templates. This does not qualify all
page text or human-reviewed translations.

Local evidence from the working patch:

- Initial five-module ordinary cohort: 80 passed, one failed because the
  inventory's filename glob omitted the existing flow-credential catalog.
  The inventory now follows the actual catalog imports.
- Corrected five-module secure-default cohort: all 81 passed, including the
  unchanged Settings size limit and new first-load limits for all six languages,
  complete catalog content, current digest, conditional requests, and no cookies
  on public assets.
- Six JavaScript localization/controller files: all 40 cases passed, including
  matching, missing and mismatched asset startup, safe interpolation, language
  tagging, regional formatting and existing dynamic controls.
- Changed-file Ruff and whitespace checks passed.
- Final ordinary targeted cohort: 23 passed, including all 13 new asset/size
  cases, eight journey helper cases and both unchanged Settings/compartments
  page-size regressions (`audit/catalog-assets-final-ordinary.log`).

The earlier dashboard-only "secure-default" label above records the requested
test switch but does not independently establish production secure posture:
the dashboard-only harness also needs `MAVERICK_SECURE_DEFAULT=1`. Both flags
are now explicit in subsequent cohorts and final consolidated qualification.

Logs are retained locally as audit/catalog-assets-01.log,
audit/catalog-assets-02.log and audit/catalog-assets-js.log. Final integrated
ordinary, browser and static/build qualification remain separately tracked.
