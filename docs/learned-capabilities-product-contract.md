# Learned capabilities: customer experience acceptance

This is the acceptance contract for the governed learning features described in
[Learned action recipes](./learned-action-recipes.md). It records the September
27, 2026 product requirement: an ordinary business user must be able to set up,
teach, use, review and manage a capability through the application. An engine,
CLI or authenticated API alone is not a completed customer feature.

The [platform customer experience contract](./customer-experience-contract.md)
also requires a consistent Liquid Glass interface, plain language, complete
post-install administration and Copilot coverage using shared product state and
authority. This learning-specific contract covers the learned operator, recipe, choice, workflow and memory
families. It does not certify the UI of every other Lightwork module.

## The complete customer journey

1. **Prepare.** See whether the workspace is ready, connect permitted systems,
   and configure required learning and evidence controls through forms. Explain
   missing prerequisites and identify who can resolve them. Existing feature
   gates, tenant scope and administrative permissions remain authoritative.
2. **Show the work.** Enroll supported work before capture begins. Explain what
   is retained, which fields are included and when retention ends. Capture
   permission is distinct from permission to carry out the business action.
3. **Review examples.** Select actual runs from an owned evidence library; see
   the inputs, actions, results and incomplete evidence as labelled business
   records. Accept or reject examples with a reason. Grant training use
   separately, with understandable dates and derived-copy disclosures.
4. **Teach.** Select eligible examples and the intended kind of work. Set
   supported bounds with ordinary forms. Explain missing information, competing
   interpretations and unsupported cases. A person must not author a schema,
   formula or selected accumulator rule to use a feature advertised as learned.
5. **Review the proposed procedure.** Show its scope, examples, important limits,
   current evidence eligibility and required action approvals. Create a draft;
   save and publish through the existing governed flow controls. Preserve exact
   artifact and graph binding rather than silently treating an edited graph as
   the original learned procedure.
6. **Use it.** Supply business inputs in labelled forms, including nested
   records and supported repeated fields. Show genuine teaching questions
   separately from permission to execute a selected action. Do not require
   JSON, hashes, internal IDs, a terminal or the developer console.
7. **Understand the result.** Show completed, waiting, stopped and uncertain work
   distinctly. Link the decision to the actual supporting facts and audit
   record. A successful tool response is not proof of the business outcome.
8. **Improve or withdraw it.** Add reviewed examples to the exact prior learned
   item, compare the replacement and publish explicitly. Allow withdrawal of
   training use and display the affected procedures and current status.

Technical details may be available under an optional inspection/export view.
Hiding transport details must not hide important uncertainty or permissions.

## Required exceptional journeys

| Condition | Required customer experience |
| --- | --- |
| Feature disabled or prerequisite missing | State what is unavailable and offer the appropriate settings or administrator handoff. Do not silently enable dependencies. |
| No consistent model or unresolved alternatives | Explain what disagrees and request a supported example or question; do not invent an answer or select a convenient model. |
| Unsupported domain or exhausted resource limit | Explain the concrete limit before presenting the item as usable; no truncated search presented as certainty. |
| Stale draft, example or review | Refresh the affected item and preserve the user's work where safe. Never reuse stale approval commitments. |
| Expired or withdrawn source | Explain affected current eligibility separately from the historical fact that a source was once accepted. |
| Missing, pending or refused audit receipt | Show the unresolved evidence state and an inspection/status path. An absent acknowledgement does not prove that an effect did not happen. |
| Effect happened or may have happened before failure | Preserve quarantine. Show known effects and permitted reconciliation steps; no generic retry that repeats the work. |
| Permission, tenant or ownership change | Recheck server-side authority; provide a safe error without disclosing another user's retained evidence. |

## Acceptance evidence

Each supported family needs an authenticated local end-to-end journey and
browser interaction/accessibility checks. Cover ordinary completion, cancellation,
stale revisions, source withdrawal, missing signing, ambiguity and uncertain
effects where applicable. UI fixtures that substitute API responses qualify
presentation and request construction only; they do not qualify authorization,
native execution or customer acceptance.

Preserve feature distinctions. The older two-option choice teaching path and
the internal conditional memory learner are not interchangeable. A pure helper
must not appear as completed authenticated teaching before that integration
exists. Likewise, a normal run input form does not complete capture, consent,
learning, refinement or dashboard configuration.

For each delivery, record separately: engine implementation, native integration,
nontechnical UI, local verification and release status. Keep the unresolved
parts visible rather than marking a family complete from a passing API suite.
