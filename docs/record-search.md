# Business record search

The Ctrl+K / Cmd+K finder includes current business records as well as allowed
navigation pages. Search by name, reference, state, or displayed version; filter
by vendors, evidence, assessments, decisions, tasks, or reports. Selecting a
result opens the exact record in its ordinary review screen. Search never runs,
approves, downloads, or publishes a record.

`features.record_search` defaults to true and has a Settings switch and installer
prompt. Disabling it retains the local page finder. Browser requests carry an
opaque account/workspace binding. Results are never persisted in browser storage
or a shared search database. POST requests keep search text out of query URLs.

Each search reads the current authorized source projection. Task ownership,
department grants, vendor workflow resource ownership, vault metadata access,
and executive report role views are applied before matching or result counts.
Only explicitly selected metadata fields enter the temporary index. Document
contents, extracted evidence, task descriptions/results, answers, review notes,
provider credentials, storage locators, and download tokens are excluded.

Current sources are the vendor register, vendor onboarding requests and their
persisted evidence/latest human decision, saved questionnaires and current final
decisions, tasks and completed deliverables, governed document vault metadata,
and executive reports. This is not a claim to index every internal store or the
entire decision history. It does not invoke Drive, Jira, model, or other provider
APIs. Existing source integrity and configuration checks remain authoritative.

Search coverage reports current, partial, or unavailable for each selected
source. At most 1,000 authorized metadata entries per source enter one search;
source adapters additionally cap task/questionnaire reads at 1,000 heads and
vault/report lists at 500. Vendor stores retain their existing bounded capacity
checks. A partial index may omit records beyond these limits. Inaccessible or
failed sources expose no record count or underlying error text. Matching totals
describe the searched index, not an exhaustive workspace census. The response
includes its check time; there is no delayed background indexing to wait for.

Paging sends the previous metadata snapshot digest. If records or permissions
change between pages, the server rejects that page and the UI asks for a fresh
search rather than silently skipping/repeating results. A workspace switch or
newer query invalidates pending browser results. All matching source fields and
links are rendered as text with an allowlist of in-app review destinations.

Automated checks use isolated local stores and synthetic records. Node DOM
tests cover keyboard behavior, asynchronous interruption, stale paging, and
workspace binding; they do not substitute for browser rendering or customer
acceptance.
