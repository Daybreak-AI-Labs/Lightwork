# LW-043 budget journey localization

The budget page (`budget_guidance.html`) and shared launch-budget controller use the explicit journey translation helpers. `i18n_journey_budget.py` adds 119 literal English/French/German/Japanese/Chinese catalog rows covering controls, accounting states, notices, original-period review consequences, and the closed product messages from the native budget APIs. Arabic has not been machine-completed; missing translations retain `lang="en"` fallback.

Provider names, schedule titles, account names, receipt references, and unknown stored notices stay verbatim, with `lang="und"`, automatic text direction, and escaped/bidirectional-isolated interpolation. Formatting follows the selected regional locale for dates, currency amounts, counts, and percentages. API codes, mutation names, periods, revisions, submitted numeric values, and ownership checks are unchanged.

Local validation: nine DOM tests pass across French, German, Japanese, Chinese, Arabic fallback, launch-only rendering, unknown-source handling, structured errors, and exact mutation payloads. Seventeen Python tests pass covering the budget template, catalog placeholders/native message coverage, native budget guidance, and budget Copilot adapters. Ruff, Python parsing, Node syntax, and whitespace validation pass. Reports are `audit/budget-i18n-js.log` and `audit/budget-i18n-python.xml` in the executor workspace.

These are draft translations. Native-language and financial consequence-copy review remains required; this checkpoint does not claim complete or human-reviewed localization for the whole product.
