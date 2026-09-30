// Lightwork VS Code extension (MVP).
//
// Minimum-surface integration: sidebar tree view of recent runs +
// commands that shell out to the user's local `maverick` CLI. No
// daemon, no embedded Python, no auth: we trust the user's local
// `maverick` install and re-use its world model.
//
// Future versions will speak to `maverick serve` over a local REST
// API for richer features (streaming run output, plan-tree updates),
// but the shell-out path is good enough for v0.1.

import { spawn } from "child_process";
import { createHash } from "crypto";
import * as vscode from "vscode";
import { watchEvents } from "./eventWatch";

function getCli(): string {
  return vscode.workspace.getConfiguration("maverick").get<string>("cliPath", "maverick");
}

const TERMINAL_CONTROL_RE = /(?:\x1B\][^\x07\x1B]*(?:\x07|\x1B\\|$))|(?:\x1B\[[0-?]*[ -/]*[@-~])|(?:\x1B[@-Z\\-_])|[\x00-\x1F\x7F-\x9F]/g;

function stripTerminalControl(text: string): string {
  return text.replace(TERMINAL_CONTROL_RE, "");
}

function getCwd(): string | undefined {
  const useWs = vscode.workspace.getConfiguration("maverick").get<boolean>("workspaceCwd", true);
  if (!useWs) return undefined;
  const folders = vscode.workspace.workspaceFolders;
  return folders && folders.length > 0 ? folders[0].uri.fsPath : undefined;
}

function runCliCapture(args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const cli = getCli();
    const cwd = getCwd();
    const child = spawn(cli, args, { cwd });
    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (data: Buffer) => {
      stdout += data.toString();
    });
    child.stderr.on("data", (data: Buffer) => {
      stderr += data.toString();
    });
    child.on("error", reject);
    child.on("close", (code: number | null, signal: NodeJS.Signals | null) => {
      if (code !== 0 || signal !== null) {
        reject(new Error(`Lightwork command was not acknowledged (exit ${code}, signal ${signal}).\n${stderr}`));
        return;
      }
      resolve(stdout);
    });
  });
}

function runCliStream(args: string[], onLine: (line: string) => void): Promise<number> {
  return new Promise((resolve, reject) => {
    const cli = getCli();
    const cwd = getCwd();
    const child = spawn(cli, args, { cwd });
    let buf = "";
    const flush = (data: Buffer) => {
      buf += data.toString();
      let idx: number;
      while ((idx = buf.indexOf("\n")) >= 0) {
        onLine(buf.slice(0, idx));
        buf = buf.slice(idx + 1);
      }
    };
    child.stdout.on("data", flush);
    child.stderr.on("data", flush);
    child.on("error", reject);
    child.on("close", (code: number | null, signal: NodeJS.Signals | null) => {
      if (buf) onLine(buf);
      if (code === null || signal !== null) reject(new Error(`Lightwork command ended without acknowledgement (signal ${signal}).`));
      else resolve(code);
    });
  });
}

async function checkedHaltCommand(command: "halt" | "unhalt"): Promise<void> {
  const output = await runCliCapture([command, "--json"]);
  let result;
  try { result = JSON.parse(output); }
  catch { throw new Error("Local halt-file acknowledgement was unreadable. Update the CLI and review its state."); }
  if (result.command !== command || result.acknowledged !== true || result.scope !== "local" ||
      result.halt_file_present !== (command === "halt") || result.work_stopped !== false) {
    throw new Error("Local halt-file state was not confirmed. Check the CLI before retrying.");
  }
}

// One run = one episode, as emitted by `maverick runs --json`. Keep in
// sync with the record built in maverick/cli.py::runs.
interface RunRecord {
  episode_id: number;
  goal_id: number;
  goal_title: string | null;
  goal_status: string | null;
  outcome: string | null;
  running: boolean;
  started_at: number | null;
  ended_at: number | null;
  duration_s: number | null;
  cost_dollars: number;
  input_tokens: number;
  output_tokens: number;
  tool_calls: number;
}

class RunItem extends vscode.TreeItem {
  constructor(public readonly run: RunRecord) {
    const title = stripTerminalControl(run.goal_title ?? `goal ${run.goal_id}`);
    super(`#${run.episode_id} ${title}`, vscode.TreeItemCollapsibleState.None);
    const state = run.running ? "running" : run.outcome ?? "done";
    const dur = run.duration_s != null ? `${run.duration_s.toFixed(1)}s` : "—";
    this.description = `${state} · $${run.cost_dollars.toFixed(4)}`;
    const tooltip = new vscode.MarkdownString();
    tooltip.appendMarkdown(`**Run #${run.episode_id}** (goal #${run.goal_id})\n\n`);
    tooltip.appendMarkdown("Goal: ");
    tooltip.appendText(title);
    tooltip.appendMarkdown(
      `\n\nState: \`${state}\`` +
      `\n\nCost: \`$${run.cost_dollars.toFixed(4)}\`` +
      `\n\nTokens: \`${run.input_tokens} in / ${run.output_tokens} out\`` +
      `\n\nTool calls: \`${run.tool_calls}\`` +
      `\n\nDuration: \`${dur}\``,
    );
    this.tooltip = tooltip;
    this.iconPath = new vscode.ThemeIcon(
      run.running ? "sync"
      : state === "completed" || state === "succeeded" || state === "done" ? "check"
      : state === "failed" || state === "blocked" || state === "error" ? "error"
      : "circle-outline",
    );
    this.contextValue = "maverickRun";
  }
}

class RunsProvider implements vscode.TreeDataProvider<RunItem>, vscode.Disposable {
  private _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  refresh() {
    this._onDidChange.fire();
  }

  dispose() {
    this._onDidChange.dispose();
  }

  getTreeItem(el: RunItem): vscode.TreeItem {
    return el;
  }

  async getChildren(): Promise<RunItem[]> {
    let out: string;
    try {
      out = await runCliCapture(["runs", "--json"]);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      showFailure("Could not load your Lightwork runs. Check that Lightwork is installed on this computer.", msg);
      return [];
    }
    let rows: RunRecord[];
    try {
      rows = JSON.parse(out.trim() || "[]") as RunRecord[];
    } catch {
      showFailure("Lightwork returned an unreadable run list. Try refreshing it.", "Could not parse maverick runs --json output.");
      return [];
    }
    return rows.map((r) => new RunItem(r));
  }
}

function showFailure(message: string, error: unknown): void {
  const out = getOutput();
  out.appendLine(message);
  out.appendLine(stripTerminalControl(error instanceof Error ? error.message : String(error)));
  void vscode.window.showErrorMessage(message, "View details").then(choice => {
    if (choice === "View details") out.show(true);
  });
}

let outputChannel: vscode.OutputChannel | undefined;

function getOutput(): vscode.OutputChannel {
  if (!outputChannel) {
    outputChannel = vscode.window.createOutputChannel("Lightwork");
  }
  return outputChannel;
}

async function startGoalCommand() {
  const goal = await vscode.window.showInputBox({
    prompt: "Describe the goal for the agent",
    placeHolder: 'e.g. "review my latest commit and find bugs"',
    ignoreFocusOut: true,
  });
  if (!goal) return;
  const out = getOutput();
  out.show(true);
  out.appendLine(`> maverick start "${goal}"`);
  try {
    const code = await runCliStream(["start", goal], (line) => out.appendLine(line));
    out.appendLine(`[exit ${code}]`);
  } catch (e: unknown) {
    showFailure("Could not start your Lightwork task.", e);
  }
}

async function statusCommand() {
  try {
    const txt = await runCliCapture(["status"]);
    const out = getOutput();
    out.show(true);
    out.append(txt);
  } catch (e: unknown) {
    showFailure("Could not load Lightwork status.", e);
  }
}

async function exportCommand() {
  const idStr = await vscode.window.showInputBox({
    prompt: "Goal ID to export",
    placeHolder: "e.g. 42",
    validateInput: (v) => (/^\d+$/.test(v.trim()) ? null : "must be a number"),
  });
  if (!idStr) return;
  const dest = await vscode.window.showSaveDialog({
    defaultUri: vscode.Uri.file(`goal-${idStr.trim()}.json`),
    filters: { JSON: ["json"] },
  });
  if (!dest) return;
  try {
    await runCliCapture(["export", idStr.trim(), "-o", dest.fsPath]);
    vscode.window.showInformationMessage(`Exported goal ${idStr} → ${dest.fsPath}`);
  } catch (e: unknown) {
    showFailure("Could not export this Lightwork task.", e);
  }
}

// --- live-run streaming (SSE from the local dashboard) -------------------
//
// `maverick.watchRun` tails a run's events live into an output channel via
// the dashboard's SSE endpoint (GET /api/v1/goals/{id}/events/stream). Plain
// Node http (no deps), manual SSE parse, exponential backoff reconnect, and
// a stop command. The dashboard URL/token come from machine-scoped settings.

let liveAbort: (() => void) | null = null;
let watchGeneration = 0;
const watchCursors = new Map<string, number>();
const watchOutputs: vscode.OutputChannel[] = [];

function dashboardBase(): string {
  return vscode.workspace
    .getConfiguration("maverick")
    .get<string>("dashboardUrl", "http://127.0.0.1:8765")
    .replace(/\/$/, "");
}

function isLoopbackHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  return host === "localhost" || host === "127.0.0.1" || host === "::1" || host === "[::1]";
}

async function shouldSendDashboardToken(url: URL, token: string): Promise<boolean> {
  if (!token) return false;
  if (isLoopbackHost(url.hostname)) return true;

  const choice = await vscode.window.showWarningMessage(
    `Send the configured Lightwork dashboard token to ${url.origin}? Only continue if you trust this dashboard URL.`,
    { modal: true },
    "Send token",
  );
  return choice === "Send token";
}

async function watchRunCommand(): Promise<void> {
  const generation = ++watchGeneration;
  liveAbort?.(); liveAbort = null;
  const goalId = await vscode.window.showInputBox({
    prompt: "Goal id to watch live",
    validateInput: (v) => (/^\d+$/.test(v.trim()) ? null : "numeric goal id"),
  });
  if (!goalId || generation !== watchGeneration) return;
  let url: URL;
  try {
    url = new URL(`${dashboardBase()}/api/v1/goals/${goalId.trim()}/events/stream`);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("unsafe URL");
  } catch { vscode.window.showErrorMessage("Dashboard URL must use HTTP(S) without embedded credentials."); return; }
  const channel = vscode.window.createOutputChannel(`Lightwork run #${goalId}`);
  channel.show(true);
  const configuredToken = vscode.workspace.getConfiguration("maverick").get<string>("dashboardToken", "");
  const token = (await shouldSendDashboardToken(url, configuredToken)) ? configuredToken : "";
  if (generation !== watchGeneration) { channel.dispose(); return; }
  const http = url.protocol === "https:" ? await import("https") : await import("http");
  if (generation !== watchGeneration) { channel.dispose(); return; }
  const key = createHash("sha256").update(url.href + "\0" + token).digest("hex");
  if (!watchCursors.has(key) && watchCursors.size >= 16) watchCursors.delete(watchCursors.keys().next().value!);
  watchOutputs.push(channel);
  if (watchOutputs.length > 4) watchOutputs.shift()!.dispose();
  let outputBytes = 0;
  const append = (line: string) => {
    if (generation !== watchGeneration) return;
    const safe = stripTerminalControl(line).slice(0, 262144);
    outputBytes += safe.length;
    if (outputBytes > 1000000) {
      channel.clear(); outputBytes = safe.length;
      channel.appendLine("[Earlier output cleared; open the dashboard for full history.]");
    }
    channel.appendLine(safe);
  };
  const cursor = watchCursors.get(key) || 0;
  if (cursor) append(`[Resuming after event ${cursor}; earlier output is in the previous watch or dashboard.]`);
  liveAbort = watchEvents({url, headers: token ? {Authorization: `Bearer ${token}`} : {}, cursor,
    connect: http.get, onEvent: append, onCursor: value => { if (generation === watchGeneration) watchCursors.set(key, value); },
    onNotice: line => append(`[${line}]`), onStop: reason => append(`[${reason}]`)});
}

export function activate(context: vscode.ExtensionContext): void {
  const runs = new RunsProvider();

  context.subscriptions.push(
    // Push the provider (disposes its EventEmitter) and the tree-view
    // registration so a reload/deactivate tears them down instead of leaking.
    runs,
    vscode.window.registerTreeDataProvider("maverick.runs", runs),
    vscode.commands.registerCommand("maverick.start", startGoalCommand),
    vscode.commands.registerCommand("maverick.status", statusCommand),
    vscode.commands.registerCommand("maverick.halt", async () => {
      try {
        await checkedHaltCommand("halt");
        vscode.window.showInformationMessage("Lightwork kill switch armed. Running work stops at its next checkpoint.");
      } catch (e: unknown) {
        // A silent failure here is dangerous: the user would believe the
        // agent was halted when the CLI call actually failed.
        showFailure("Could not stop Lightwork.", e);
      }
    }),
    vscode.commands.registerCommand("maverick.unhalt", async () => {
      try {
        await checkedHaltCommand("unhalt");
        vscode.window.showInformationMessage("Lightwork kill switch cleared. This does not restart prior tasks.");
      } catch (e: unknown) {
        showFailure("Could not clear the Lightwork emergency stop.", e);
      }
    }),
    vscode.commands.registerCommand("maverick.openExport", exportCommand),
    vscode.commands.registerCommand("maverick.refreshRuns", () => runs.refresh()),
    vscode.commands.registerCommand("maverick.watchRun", watchRunCommand),
    vscode.commands.registerCommand("maverick.stopWatch", () => {
      watchGeneration++;
      if (liveAbort) {
        liveAbort();
        liveAbort = null;
      } else {
        vscode.window.showInformationMessage("No live watch running.");
      }
    }),
    vscode.workspace.onDidChangeConfiguration(event => {
      if (event.affectsConfiguration("maverick.dashboardUrl") || event.affectsConfiguration("maverick.dashboardToken")) {
        liveAbort?.(); liveAbort = null; watchGeneration++;
        for (const channel of watchOutputs.splice(0)) channel.dispose();
      }
    }),
  );
}

export function deactivate(): void {
  if (liveAbort) liveAbort();
  liveAbort = null; watchGeneration++;
  for (const channel of watchOutputs.splice(0)) channel.dispose();
  if (outputChannel) outputChannel.dispose();
}
