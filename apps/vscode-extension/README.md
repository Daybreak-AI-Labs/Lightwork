# Lightwork for VS Code

The sidebar shows recent **local CLI** runs. Dashboard search, task cancellation,
resume and live event streaming use a separately named, authenticated dashboard
connection. A local task ID is not assumed to identify the same remote task.

## Setup

1. Install Lightwork from a reviewed checkout with `pip install -e ./packages/maverick-core`, then `maverick init`. Do not install an unreserved public distribution name.
2. In `apps/vscode-extension`, run `npm install` and `npm run compile` using the manifest's declared TypeScript and VS Code API versions. Launch an extension development host (F5), or use your reviewed `vsce` packaging tool.
3. Choose **Lightwork: Select local workspace** for a multi-root window. Every start reviews its exact folder and requested cost/time caps. Executable configuration remains machine-scoped; local execution requires workspace trust.
4. Use **Configure or rotate dashboard connection**, then **Test dashboard connection**. Credentials live in VS Code SecretStorage, bound to the exact dashboard base address. Remote bearer transport requires HTTPS; destination consent cannot override it.

A legacy `maverick.dashboardToken` is removed only after secure save and readback.
If migration fails, the original setting remains usable and the UI explains how
to repair it. **Disconnect dashboard** removes that saved token and clears owned
private views. Changing the host requires its own saved credential and destination
verification. Tokens are never included in connection labels, request keys or
extension exports. A protected-storage migration may invalidate an existing watch;
choose the task again after migration.

## Commands

<!-- Generated from package.json contributes.commands; update together. -->
| Command | Action |
|---|---|
| `maverick.start` | Start goal... |
| `maverick.status` | Show status |
| `maverick.halt` | Arm local emergency stop (checkpoints) |
| `maverick.unhalt` | Clear emergency stop |
| `maverick.refreshRuns` | Refresh runs |
| `maverick.openExport` | Export run as JSON... |
| `maverick.watchRun` | Watch run live |
| `maverick.stopWatch` | Stop live watch |
| `maverick.selectWorkspace` | Select local workspace |
| `maverick.configureDashboard` | Configure or rotate dashboard connection |
| `maverick.testDashboard` | Test dashboard connection |
| `maverick.disconnectDashboard` | Disconnect dashboard |
| `maverick.cancelTask` | Cancel selected dashboard task |
| `maverick.resumeTask` | Resume selected dashboard task |
| `maverick.runActions` | Choose local task action |

Choose a sidebar row for watch/export actions without typing its task ID. Dashboard
task selection searches authorized titles/content and displays the connection name
and address. Results are bounded to 100 per query; refine the query for older work.

Local status/export/control commands have a 30-second deadline and a 1-MiB output
bound. Local starts have a deadline based on the reviewed run duration and
cancellable progress. Cancellation or extension unload stops local supervision;
it does not prove that remote effects stopped. Unknown/nonzero starts remain held
in extension state, with any reported task ID, until the user reconciles recent
local runs. Duplicate start activations are fenced. No uncertain action retries
automatically.

Task cancellation targets exactly one selected dashboard task; the local emergency
stop remains a separate checkpoint-based control. Resume acknowledges scheduling,
not successful completion. Requested start caps are visible; this extension does
not yet preview the resolved administrator ceiling before dispatch.

## Configuration and troubleshooting

`maverick.cliPath` and dashboard address/name are machine settings. Optional
`maverick.maxDollars` and `maverick.maxWallSeconds` prefill each start review.
`maverick.workspaceCwd` controls local folder scoping. Use protected connection
commands for credentials rather than settings JSON.

If local commands fail, verify the selected folder and CLI installation, then
inspect recent runs before restarting paid work. If dashboard access fails, test
or repair its connection; denied or changed connections discard private views.
SSE streams drain replay history, reconnect from the last accepted cursor, and
require terminal acknowledgement. Streaming is implemented; plan-tree and inline
approval interfaces remain future work.

Python/TypeScript syntax and static review do not qualify VS Code extension-host
behavior. Regression tests are authored but intentionally unrun in this audit;
no package, provider request or release was performed. See the [client lifecycle
contract](../../docs/client-request-lifecycle.md).

## License

The current extension follows the root [Lightwork Proprietary License](../../LICENSE).
Its explicit clause preserves MIT grants for specific previously published MIT
versions only. The current manifest references that governing license.
