# Lightwork JetBrains plugin (live-run scaffold)

A minimal IntelliJ-platform plugin: a **Lightwork Runs** tool window that
streams a run's events live from the self-hosted dashboard SSE endpoint
(`GET /api/v1/goals/{id}/events/stream`), mirroring the VS Code extension's
"Watch run live" command.

**Build requirements (not possible in this repo's CI):** JetBrains
IntelliJ Platform Gradle plugin (`org.jetbrains.intellij.platform`), JDK 17+.
Create a Gradle project with these sources and Gson 2.11.0 on its plugin
runtime classpath; use `runIde` for the later native qualification phase.
The repository still does not contain a complete IntelliJ SDK build project.
These source changes and the fixtures below have not been compiled or run
against the installed IDE SDK during the requested testing deferral.

Open **Settings → Lightwork Dashboard**, enter a connection name and dashboard
address, and save the token in **Replace access token**. Secrets use JetBrains
PasswordSafe on a background thread; only the name/address are ordinary settings.
Blank token keeps the exact address's saved token. The remove-token checkbox
removes it after protected-store confirmation. Changing address never copies a
previous host's token. HTTPS is required for a remote bearer; HTTP is accepted
only for loopback. No restart is required to configure, rotate or repair access.

Existing `MAVERICK_DASHBOARD_URL` / `MAVERICK_DASHBOARD_TOKEN` remain a legacy
fallback until the first successful settings save. Save the current address to
migrate its token to PasswordSafe, then remove the environment secret from your
launcher. Failed protected storage does not clear that fallback or claim success.

In **Lightwork Runs**, use **Test connection**, then search a task title/content
with **Find tasks**. The labeled picker shows up to 100 authorized matches; refine
the query for older tasks. The separately labeled numeric ID is optional. Invalid
selection gives a remedy and an accessible field description. Status/error changes
update the accessible output description. Changing a connection clears the prior
view, task selection and replay cursors and closes owned reads. Late results from
that connection cannot repopulate the new identity's view. A 401/403 clears private
watch output and directs the user back to Settings.

Search/test requests have a 15-second overall deadline and 1-MiB response bound;
Stop closes their owned request as well as any watch. No redirect or automatic
write/retry is involved. The existing SSE reader keeps its replay and idle limits.
The [native qualification checklist](tests/connection-qualification.md) records
remaining installed-IDE and assistive-technology checks. PasswordSafe follows the
[official IntelliJ SDK guidance](https://plugins.jetbrains.com/docs/intellij/persisting-sensitive-data.html).

Security model: talks only to YOUR dashboard host; sends the
`Authorization: Bearer <token>` header when a dashboard token is configured.

Run watches use server replay cursors, bounded frames/output, connection and
idle deadlines, and owned cancellation. Read the [client lifecycle
contract](../../docs/client-request-lifecycle.md). `tests/RunEventStreamTest.kt`
is a standalone Kotlin/JVM main for real loopback HTTP acceptance: compile it
together with `RunEventStream.kt` using Kotlin 2.1.20, JRE/JDK 17+, and Gson
2.11.0, then run `dev.maverick.RunEventStreamTestKt`. It does not qualify an
installed IntelliJ plugin or substitute for building against the actual SDK.
