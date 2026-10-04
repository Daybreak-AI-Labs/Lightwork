# UX-070 and UX-073: specialist readiness and independence

The current branch already projects required-action readiness from the effective
tool inventory into the specialist roster, ready-first picker, selected
specialist/job card, department detail and cards. It prevents deployment of a
department when every specialist has a known missing required action. The
projection is deliberately narrower than live execution: credentials, current
permissions, external connections and the AI service are checked when work
starts. An unavailable inventory remains **unknown**, never ready.

This follow-up closes the in-product independence control. The specialist
editor now exposes Watch, Suggest, Ask and Act for usual, low, medium and high
risk actions, plus the onboarding supervision clamp. The job card displays the
current effective rung for each risk. A tenant's specialist override stores
the `[autonomy]` table alongside that specialist's other pack changes; the
runtime loads the same override. Existing autonomy settings survive edits in
the separate flow builder, and an older client that omits an existing setting
receives a conflict instead of silently clearing it. A server-configured
`[[workforce.agents]]` override remains authoritative and is shown as locked.

Workforce now links candidates to their editor and offers **Grant
independence** when the caller can edit specialists and levels are enabled.
The server checks the current specialist revision, a current clean approval
record, onboarding state and server override again before writing. It records
the administrator's decision intent in the tenant activity chain before any
authority increase; an unavailable activity record refuses the change. A
regular editor save that ends supervision uses the same evidence check. An
aggregate approval record is not evidence that a particular action is safe:
graduation and editor saves refuse to end supervision while any chosen risk
level would Act on its own. Workforce shows the reason and links to the policy
editor, where an administrator can set those levels to Ask or lower. Enabling
Act after supervision remains open until action- and risk-specific evidence is
available. Resetting a specialist override can restore broader built-in
permissions; the editor warns before doing so, and the API requires the current
revision and an activity record before any override removal.
Changes to the authority dial require the current specialist generation.
The Workforce distribution now uses effective policies, including client
configuration, rather than pack baselines.

Copilot provides page-specific, read-only guidance for both Specialists and
Workforce, with the required-action and live-readiness distinction and the
approval and role boundaries stated explicitly.

Local qualification: targeted Python core/dashboard tests, the Workforce
JavaScript tests, and changed-file Ruff checks. Browser usability and installed
tenant acceptance remain open; no live provider or external action was used.
