# Software development: the code vault

Put a company's repositories in Lightwork. They are held there sealed, and
swarms add and update features on branches. Nothing reaches the main branch or
the company's git host until a person approves the change.

```bash
maverick code add payments https://git.example.com/acme/payments.git \
  --test "python -m pytest -q" --token-env ACME_GIT_TOKEN
maverick code feature payments "Add refund reasons" -d "Store a reason code on every refund"
maverick code proposals            # pending | approved | rejected | merged
maverick code show <id>            # diff, test output, independent review
# approve codebase_merge #<n> in the dashboard's approvals queue, then:
maverick code merge --approved     # merge every approved proposal into the vault
maverick code push <id>            # push the branch to the git host for a pull request
maverick code sync payments        # bring in upstream commits
maverick code export payments ./payments   # plaintext clone for the owner
```

## Protection

- **At rest.** Each repository is one git bundle (every branch and tag). It
  is sealed with the tenant's at-rest key, bound to `codebase/<name>`, when
  encryption is on; proposals and their branch bundles are sealed the same
  way.
- **In use.** Plaintext exists only in a private (0700) working copy for as
  long as a task needs it. The copy is removed when the task ends.
- **In flight.** Every run over the code starts labelled `code:<repo>` for
  [information-flow control](ifc.md). The repository's protection mode
  (`--protect`, default `[codebase] default_protect = "block"`) is enforced
  whatever the global `[ifc]` mode. An agent that holds the code cannot send
  it through `email`, `http_fetch`, `web_search`, `mcp_*` or any other
  external tool.
- **Credentials** are never stored. A repository names an environment
  variable (`--token-env`), read at use time and handed to git through a
  short-lived askpass helper, never argv or a persisted URL. A source URL
  containing credentials is refused.
- **Git hardening.** Git runs without user or system config, fsmonitor, or
  inherited secrets. A run that leaves git filter or hook metadata in the
  working copy is discarded, not stored.

## A feature request, end to end

1. A private working copy on a new `lightwork/<title>-<id>` branch.
2. A goal runs the governed swarm with that working copy as its sandbox
   workdir, under the run's labels.
3. The change is committed on the branch. The repository's test command runs
   through the sandbox.
4. The independent reviewer reads the diff. It uses a cross-family model where
   possible.
5. The sealed proposal (diff, tests, review, branch bundle) parks a
   `codebase_merge` approval, which states the test result and the review
   verdict. A change the swarm did not make produces an `empty` proposal and
   no approval.
6. `merge` requires the approval to be approved. The merge is `--no-ff`, so it
   is one reviewable commit. A merge that no longer applies cleanly leaves the
   vault untouched and marks the proposal `conflict`. `push` also requires the
   approval.

## Dashboard

**Operate → Code** (`/code`, operate permission) lists vaulted repositories and
each proposal's files, test result, review verdict and approval state. It
reads one summary (`GET /api/v1/code/summary`) that never includes code, diffs
or test output. Approve in the approvals queue; merges run through
`maverick code merge --approved`.

## Configuration

```toml
[codebase]
enable = true              # off by default: it stores your source
default_protect = "block"  # block | approve | audit for runs over vaulted code
```

## Limits

- Tests run through the configured sandbox backend; use a container backend
  for untrusted test suites.
- The vault keeps one bundle per repository. Very large monorepos pay the
  bundle cost on every checkout.
- `push` sends the branch; opening the pull request is left to the git host.
