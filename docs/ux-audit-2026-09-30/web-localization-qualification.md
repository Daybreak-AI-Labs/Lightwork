# Web localization qualification

The prioritized employee journey is wired through explicit translated product regions: activation, preferences, first task, live clarification and answer receipts, saved answers, review, file recovery, workflow editing and connected-account selection. Shared navigation, budget guidance and voice controls use the same contract. Source text, titles, filenames, account references, tool evidence and custom guidance remain verbatim and independently directed.

This record covers frozen integration `d38832161d1eea7e7c7bef823704b2457859e64c` plus the small qualification patch described below. It does not claim that every administrative page is translated or that the draft translations have received human approval.

## Implemented code and inventory

- 1,659 wired source messages have French, German, Japanese and Chinese draft translations. Catalog placeholder validation runs against the actual product loader.
- The generated [inventory](web-localization-coverage.json) measures 496 literal `ui`/`ut` bindings across ten selected templates. Every measured binding has a catalog entry.
- Four unbound literals are intentional: the Lightwork brand and the canonical identifier examples `competitor, timeframe`, `web_search, send_email` and `shell, delete_file`.
- The inventory explicitly excludes dynamic labels, includes, JavaScript/API messages and human visual/linguistic review. Controller tests cover representative dynamic, error, confirmation, source-preservation and exact-receipt paths; the static inventory is not a substitute for those tests.
- This qualification added the missing “Task files” title and a regression that detects a bound source lacking translations or an inventoried catalog missing from the product loader.
- Interface language remains separate from response language. Regional date/number/currency/plural formatting and actual region language attributes are preserved; saving the interface preference does not discard the current editor.

## Executed local checks

The shared Python 3.12 environment was used without dependency changes, with `PYTHONPATH` explicitly selecting all eight package roots in this isolated checkout and `TEMP`/`TMP` pointing to the task-local synthetic test directory. The printed dashboard module path confirmed that tests imported this checkout. No live provider or production requests were made.

| Check | Result |
| --- | --- |
| Catalog, portal, language/preferences, shared shell, budget, chat/stream/attachments, saved answers and project-review Python cohort | 75 passed |
| Catalog regression, employee activation and memory-preference follow-on | 22 passed; seven overlap the first cohort |
| Unique Python cases, computed from JUnit identities | 90 passed |
| Locale/shell/budget/preferences/answer/voice/review/recovery/composer/inbox/restart/workflow/account-picker DOM cohort | 135 cases: 134 passed initially; one outdated fixture corrected |
| Affected file-selection/workflow-handoff/account-picker rerun | 35 passed, including the previously failing case |
| Catalog placeholders, wired-source count, Python 3.10 AST, Ruff, JavaScript syntax and whitespace | Passed |

The DOM fixture lacked the hidden `connection_references` field added by LW068. It now uses the actual form structure and verifies that the exact reviewed metadata survives the translated template handoff. Product behavior and authority checks were not weakened.

Python cohorts ran with `python -m pytest -q` over:

```text
test_journey_i18n.py test_i18n_fonts.py test_i18n_portal.py
test_shell_i18n.py test_budget_i18n.py test_chat_goal_conversation.py
test_chat_streaming.py test_chat_attachments.py test_answer_projection.py
test_project_reviews_api.py

test_journey_i18n.py test_employee_activation.py test_preference_memory.py
```

The DOM cohort ran with `node --test` over:

```text
journey_i18n.test.js shell_i18n.test.js budget_i18n.test.js
preference_locale.test.js task_control_locales.test.js voice_language.test.js
voice_setup.test.js project_reviews.test.js task_file_recovery.test.js
audit_file_selection.test.js conversation_inbox.test.js task_resume.test.js
workflow_end_user_controls.test.js template_connections.test.js
```

Generate the static inventory with:

```text
python scripts/journey_i18n_coverage.py --output docs/ux-audit-2026-09-30/web-localization-coverage.json
```

## Later inventory check

At `7d38f76ccc7c8b1752eaeb44d65dccfd0646d4d5`, a read-only recomputation of
`scripts/journey_i18n_coverage.py` matched the saved coverage JSON exactly:
1,660 current wired source messages, 496 literal bindings across ten templates
and no bound source missing a catalog entry. The 1,659-message count above
belongs to the earlier qualification checkpoint. No additional runtime or
linguistic acceptance is inferred from this static inventory.

## Remaining qualification

English remains the only qualified complete-body locale. The four expanded catalogs remain visibly marked as drafts/partial languages until native-language reviewers validate wording, including approval, spending, privacy, restore and publication consequences. Unknown backend messages and administrative pages outside the selected journey retain honest English/source fallbacks.

Arabic remains the documented community seed: no machine completion was added. Community-authored coverage, RTL keyboard behavior, browser rendering and screen-reader pronunciation still require review. The synthetic DOM tests verify language and direction boundaries, not installed-browser or assistive-technology qualification. Native applications and installed-device acceptance have separate audit records.
