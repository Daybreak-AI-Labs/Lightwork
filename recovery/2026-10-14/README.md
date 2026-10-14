# Lightwork local recovery archive — 2026-10-14

This archive preserves divergent local development states without combining them into the current product source. It is recovery material, not a merge-ready implementation or a claim that old changes pass current tests.

The inspection covered 100 independent Lightwork repositories and their 160 registered worktrees. Fifty explicit source-verifier test repositories were excluded. Current GitHub branches, tags, and 2,462 pull-request head refs were checked so previously published work was not mistaken for unpublished history.

The payload contains:

- A bounded Git bundle preserving 289 commits across 110 local tips that were not reachable from the inspected GitHub refs.
- 27 nonempty binary-safe patches, keeping staged and unstaged changes separate.
- 758 selected untracked source and small evidence files, including authored synthetic document-test fixtures.
- Per-state base commits, hashes, exclusions, and restoration instructions.

Patch restoration was checked against each exact base in disposable Git indexes. All 29 states with exported patch pairs passed; empty patch files are omitted from the payload. A second validation used a fresh GitHub-only repository with no object alternates: the bundle verified and imported, all 110 preservation refs matched exactly, all 118 archive base commits were present, and all 26 states containing nonempty patches applied successfully. See `remote-only-verification.json`. This verifies recovery mechanics, not runtime behavior or compatibility with today's main branch.

Generated environments, build/test caches, and two incomplete checkouts containing only `.git` were excluded. Ignored credentials, local runtime data, and non-Git source snapshots are not represented by this archive. No original working tree was reset, stashed, cleaned, or edited during preparation.

Offline secret-pattern review covered the patches, selected untracked files, and all 588 new blob objects introduced by the unpublished history. Reviewed matches were synthetic test credentials, redaction canaries, and key-header fixtures. No live credential was identified. Historical source content is preserved faithfully and can contain authored local paths or fixture identifiers; newly authored archive metadata omits absolute workstation paths.

See [RESTORE.md](RESTORE.md), [manifest.json](manifest.json), and [secret-review.json](secret-review.json). Keep the original directories until a downloaded copy of the recovery PR has been verified and the specific states needed have been restored successfully. This archive does not authorize cleanup or merging old changes into current code.
