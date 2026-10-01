# PostgreSQL secure-mode rerun: recovered startup blocker

The launcher blocker is **resolved by direct foreground startup under the default
sandbox identity**, without authentication or ACL changes. The complete 363-case
secure attempt02 is now running; qualification remains pending its completion
receipt. No passing total is claimed yet. The earlier failures below are retained
as historical evidence, not a current inability to start the private fixture.

Its 29 complete test files at `aa37a5a58d81940af4ab87434f6c33e6af96c75e` still
match the recorded Git blobs and working-file hashes. The selection covers the
270 original identities missing a completed secure-mode qualification.

Attempt `postgres-gap-secure-final06-01` collected 363 cases, then its log stopped
at 33%. Its process/session was no longer available, and no completion or JUnit
receipt was produced. The original five artifacts remain byte-identical. No
product cause or complete passing total is inferred from that interruption.

Recovery evidence records two child native-action requests whose automatic
approval reviews timed out before execution. The coordinator also reported two
native-action review timeouts. **These were review timeouts, not safety
rejections, and the local tests remain user-authorized.**

The bounded default-sandbox recovery confirmed that the recorded process was
absent, the task-owned loopback port was bindable, and `pg_ctl status` reported no
server. Starting PostgreSQL then failed with `could not create restricted token:
error code 87` and `could not start server: error code 87`. The server did not
start; subsequent checks again found the recorded process absent and the port
bindable. Attempt `postgres-gap-secure-final06-02` did not begin testing through
that `pg_ctl` launch path. A later direct `postgres.exe -D <task-owned-data>`
launch passed readiness on the same loopback port and started the complete run.

The data directory and stale PID file were retained. No ACLs, credentials,
product/test code, assertions, or deadlines changed. The 270 identities remain
unqualified by this planned secure rerun until its result is available. The separate
12 pgvector cases retain their compiler/extension prerequisites.

The [sanitized evidence record](postgresql-secure-gap-blocked-aa37a5a.json)
contains source/test identities, preserved artifact hashes, and exact recovery
outcomes. Account identity and raw logs are retained only in task-local
`audit/postgres-gap-secure-final06-02-*` artifacts. No PostgreSQL process was
started, stopped, or retried during this documentation pass.
