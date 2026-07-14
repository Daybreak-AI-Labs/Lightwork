# Lightwork GitHub Action

Run a Lightwork agent swarm inside a GitHub workflow — on a PR, on a
schedule, or on demand — under a **hard spend cap**. It installs
Lightwork from the action's own pinned source checkout and runs `maverick
start` with the inputs you give it, then writes the final answer to the job
summary and exposes it as a step output. It never falls back to an
unqualified public-index install of a Lightwork distribution.

```yaml
- uses: Day-AI-Labs/Lightwork/deploy/github-action@<full-40-character-commit-sha>
  with:
    goal: "Summarize the changes in this PR and flag anything risky."
    max-dollars: "0.50"
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

> Replace the placeholder with a reviewed 40-character commit SHA. Do not use
> a mutable branch or tag for a workflow that receives provider secrets.

## Inputs

| Input | Default | Description |
|---|---|---|
| `goal` | — | Freeform goal (the `maverick start` TITLE). Provide this **or** `template`. |
| `description` | `""` | Optional longer description for a freeform goal. |
| `template` | — | A bundled/installed goal template name (e.g. `code-review`, `write-tests`). See [`benchmarks/example-templates/`](../../benchmarks/example-templates/). |
| `params` | `""` | Template parameters, one `key=value` per line. |
| `max-dollars` | `1.0` | Hard USD spend cap. The swarm refuses to exceed it. |
| `max-wall-seconds` | — | Optional wall-clock cap. |
| `model` | — | Override the model (e.g. `claude-sonnet-4-6`). |
| `sandbox` | `local` | Shell sandbox backend. A CI runner is already an ephemeral VM, so `local` is the default. |
| `anthropic-api-key` | `""` | Exported as `ANTHROPIC_API_KEY`. For other providers, leave blank and set that provider's env var on the job. |
| `version` | `""` | Optional expected version of the source bundled with the pinned action. A mismatch fails closed. |
| `python-version` | `3.12` | Python to set up. |
| `step-summary` | `true` | Write the final answer to the job summary. |
| `dry-run` | `false` | Install + print the resolved command, but don't run the swarm (no LLM calls, no spend). |

## Outputs

| Output | Description |
|---|---|
| `result` | The swarm's final answer. |
| `result-file` | Path to a file with the full run output. |

## Examples

### Review every pull request

```yaml
name: Lightwork review
on: pull_request
permissions:
  contents: read
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7
      - uses: Day-AI-Labs/Lightwork/deploy/github-action@<full-40-character-commit-sha>
        with:
          template: code-review
          params: |
            branch=${{ github.head_ref }}
          max-dollars: "0.75"
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Post the result as a PR comment

`result` is a step output, so you can chain it:

```yaml
      - id: maverick
        uses: Day-AI-Labs/Lightwork/deploy/github-action@<full-40-character-commit-sha>
        with:
          goal: "Review the diff on this PR and list concrete risks."
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
      - uses: actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3 # v9
        with:
          script: |
            github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: context.issue.number,
              body: process.env.RESULT,
            })
        env:
          RESULT: ${{ steps.maverick.outputs.result }}
```

(Posting a comment needs `permissions: pull-requests: write` on the job.)

### Use another provider

Leave `anthropic-api-key` blank and set the provider's env var on the job —
composite-action steps inherit it:

```yaml
    env:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    steps:
      - uses: Day-AI-Labs/Lightwork/deploy/github-action@<full-40-character-commit-sha>
        with:
          goal: "..."
          model: gpt-4.1
```

## Notes

- **Budget is the safety rail.** `max-dollars` is a hard cap enforced by the
  kernel; a runaway goal stops rather than running up a bill. Start small.
- **Keys are secrets.** Pass provider keys via `${{ secrets.* }}`, never
  inline. The action exports the key only for the run step.
- **Inputs are injection-safe.** Every input is passed through the
  environment and quoted, not interpolated into the shell, so a goal or
  param containing shell metacharacters can't execute.
- The full swarm needs a provider key, so it isn't exercised in this repo's
  CI; the `dry-run` path (install + command resolution) is.
