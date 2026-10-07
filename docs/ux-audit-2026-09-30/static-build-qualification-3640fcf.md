# Final06 static and build qualification

Source `3640fcf7906d3f7129df1b34bc1718492a164bcf` passes **all 26 checks**:
Ruff, Vulture, 16 offline contract/governance gates, and eight package wheel
builds. The matching-source checkout is clean. Builds used an external Git
archive and a private environment; no wheels were published or deployed.

The completed build verification records eight valid wheel archives, eight
matching SHA-256 hashes, and **4,827 payload files matching the exact Git export
byte for byte**. Source archive SHA-256:
`ee91f88c1705b09b47a7ce9a651c7e312a5d7645f48545ea5722cfc728c4d0b5`.
The archive's embedded Git commit also matches the source above.

The [evidence record](static-build-qualification-3640fcf.json) preserves all gate
results, individual wheel hashes/payload counts, execution source and runner
hash, and raw artifact hashes. Task-local artifacts remain in
`audit/qualification-final-06-contracts/`. The documentation review rechecked
the source archive digest/commit and reconciled the unchanged result and wheel
manifests. Individual wheel verification was completed by the qualification
worker under the artifact owner's account; the documentation review encountered
a read-only sandbox ACL limit when reopening those wheels and did not change
permissions or repeat builds.

This supersedes the `7d38f76` static/build checkpoint and includes the Qdrant
current-client repair. Runtime, browser, interpreter compatibility, and native
service results remain separate. In particular, the
[PostgreSQL secure rerun](postgresql-secure-gap-blocked-aa37a5a.md)
has its own recovery and completion evidence; passing static/build checks do not
qualify it.
