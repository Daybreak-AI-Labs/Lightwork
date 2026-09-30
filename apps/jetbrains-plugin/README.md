# Lightwork JetBrains plugin (live-run scaffold)

A minimal IntelliJ-platform plugin: a **Lightwork Runs** tool window that
streams a run's events live from the self-hosted dashboard SSE endpoint
(`GET /api/v1/goals/{id}/events/stream`), mirroring the VS Code extension's
"Watch run live" command.

**Build requirements (not possible in this repo's CI):** JetBrains
IntelliJ Platform Gradle plugin (`org.jetbrains.intellij.platform`), JDK 17+.
Create a Gradle project with these sources and Gson 2.11.0 on its plugin
runtime classpath; `runIde` to test. This scaffold reads `MAVERICK_DASHBOARD_URL`
and `MAVERICK_DASHBOARD_TOKEN` from the IDE process environment. It does not
currently provide a Settings page.

Security model: talks only to YOUR dashboard host; sends the
`Authorization: Bearer <token>` header when a dashboard token is configured.

Run watches use server replay cursors, bounded frames/output, connection and
idle deadlines, and owned cancellation. Read the [client lifecycle
contract](../../docs/client-request-lifecycle.md). `tests/RunEventStreamTest.kt`
is a standalone Kotlin/JVM main for real loopback HTTP acceptance: compile it
together with `RunEventStream.kt` using Kotlin 2.1.20, JRE/JDK 17+, and Gson
2.11.0, then run `dev.maverick.RunEventStreamTestKt`. It does not qualify an
installed IntelliJ plugin or substitute for building against the actual SDK.
