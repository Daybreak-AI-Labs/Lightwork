# Workflow draft versions and publication

Workflow builder keeps immutable snapshots whenever a reusable template is saved. Open **Draft versions and publication** to compare snapshots, clone a workflow, check sample inputs, restore earlier content as a new draft, or publish a named version. A restoration never rewinds history or changes an existing publication.

Sample checks are deliberately limited to typed input validation and rendered instructions. They execute no model, tool, worker, connector, or external action. They do not certify task success or live service compatibility. Review the generated instructions and perform runtime qualification in the separately authorized testing phase.

An administrator publishes a saved version after a successful reviewed input sample. The publication review lists authorized referring schedules and their pinned versions. Existing schedules retain rendered content; publication governs newly created schedules only. To deliberately use another publication, retire the old schedule and create a reviewed replacement. There is no bulk migration or catch-up replay.

Existing templates are adopted as the prior publication on their first edit. New templates require publication before scheduling. The Settings control **Workflow draft and publication history**, also exposed in the installer, controls authoring. Definitions already admitted into schedules retain their pinned content independently of subsequent settings changes.

History and samples are private files in the tenant template directory and follow that directory's backup and retention policy. Only the author or an administrator may review history; only administrators may publish. Current ownership, account context, configuration, and generation checks are repeated for mutations. Fixture history retains the latest 50 checks; immutable content snapshots and publication events remain until the workflow data is removed through the installation retention process.

The visual controls are implemented. Conversational routing for these new version operations is not yet connected; this is a remaining customer-experience-contract coverage item, not a claim of complete Copilot coverage.
