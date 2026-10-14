# Production operator CLI contract

The first production profile has two different CLI boundaries:

1. The production surface manifest controls which commands a trained human
   operator can see and invoke. That surface is default-deny, but admission is
   not a machine-compatibility promise.
2. `maverick operator-contract --json` publishes the smaller subset that an
   automation client may depend on. Commands absent from that document have no
   automation compatibility promise, even when production help shows them.

The command emits canonical JSON bound to the active production profile, source
version, and exact SHA-256 digest of the reviewed
`production-surface-manifest.json`. Treat a digest change as a contract
requalification event. The document validates its own registry against the
live Click tree and the production command classifications before it prints; a
missing command, option drift, new argument, duplicate entry, or non-core path
fails closed.

## Automation-safe subset

The initial contract is intentionally read-only:

| Path | Required automation invocation | Output |
|---|---|---|
| `operator-contract` | `maverick operator-contract --json` | canonical `lightwork.production-operator-cli.v1` object |
| `preflight` | `maverick preflight --profile run --json` | `lightwork.operator-preflight.v1` readiness object |
| `whoami` | `maverick whoami --principal PRINCIPAL --json` | effective-principal/capability object |
| `runs` | `maverick runs --limit LIMIT --json` | stable run array |
| `spend` | `maverick spend --limit LIMIT --tag-field FIELD --top TOP --json` | spend attribution object |
| `capability revocations` | `maverick capability revocations --json` | principal-keyed revocation object |

The emitted contract is authoritative for exact options, fixed values, output
shape, state class, control applicability, and exit meanings. JSON member order
and whitespace are not promised for commands other than `operator-contract`;
automation must parse JSON rather than compare bytes.

Exit `0` means the command completed and emitted its declared successful
schema. Exit `1` does not establish success: it covers a negative gate result,
an execution failure, or a production precondition failure. Exit `2` means a
CLI usage error or command-visibility refusal. Other process exits are abnormal
runtime termination and carry no compatibility promise.

## Deliberate exclusions

Mutating and destructive commands are not in the initial automation subset.
Their presence in production help does not make their human text output stable,
and a `--dry-run`, confirmation flag, or operation ID is promised only when the
machine contract explicitly declares it. Root options such as `--db` and
`--model` are also outside the automation contract.

Repository validation proves source-level command/option/policy consistency.
Deployment qualification must still exercise the contract in the immutable
release image with the pinned manifest, production identity, shared Postgres,
and the operator's process supervisor. It must also verify how that supervisor
preserves exit status and captures stdout/stderr without exposing secrets.
