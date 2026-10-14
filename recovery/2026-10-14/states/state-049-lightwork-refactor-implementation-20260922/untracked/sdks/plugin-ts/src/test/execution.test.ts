import assert from "node:assert/strict";
import { test } from "node:test";
import { PassThrough, Writable } from "node:stream";
import { defineTool, servePlugin, PROTOCOL, MAX_FRAME_BYTES, type ToolDef } from "../index.js";

function request(extra: Record<string, unknown> = {}): string {
  return JSON.stringify({
    protocol: PROTOCOL, id: "logical-1", attempt: 1, replay: "never",
    deadline_unix_ms: Date.now() + 5000, tool: "fixture", args: {}, ...extra,
  }) + "\n";
}

async function run(tool: ToolDef, raw: string): Promise<Record<string, unknown>[]> {
  const input = new PassThrough();
  const output = new PassThrough();
  const chunks: Buffer[] = [];
  output.on("data", chunk => chunks.push(chunk));
  const done = servePlugin([tool], { input, output, argv: [] });
  input.end(raw);
  await done;
  return Buffer.concat(chunks).toString("utf8").split("\n").filter(Boolean).map(line => JSON.parse(line));
}

test("versioned context and effect receipt are explicit; ids do not imply deduplication", async () => {
  let calls = 0;
  const tool = defineTool({ name: "fixture", description: "fixture", inputSchema: {}, handler: (_, context) => {
    calls++;
    assert.equal(context.requestId, "logical-1");
    assert.equal(context.attempt, 1);
    assert.equal(context.replay, "never");
    assert.ok(context.deadlineUnixMs! > Date.now());
    return "ok";
  } });
  const output = await run(tool, request() + request());
  assert.equal(calls, 2); // SDK makes no unsupported durable idempotency claim.
  assert.deepEqual(output[0], { id: "logical-1", attempt: 1, effect: "completed", result: "ok" });
});

test("expired and invalid metadata are rejected before invoking handler", async () => {
  let calls = 0;
  const tool = defineTool({ name: "fixture", description: "fixture", inputSchema: {}, handler: () => { calls++; return "wrong"; } });
  const output = await run(tool, request({ deadline_unix_ms: Date.now() - 1 }) + request({ replay: "idempotent" }) + request({ attempt: 2 }));
  assert.equal(calls, 0);
  assert.ok(output.every(result => result.effect === "not_started" && typeof result.error === "string"));
});

test("deadline requests cancellation but retains handler ownership until cleanup", async () => {
  let cleanup = false;
  let calls = 0;
  const tool = defineTool({ name: "fixture", description: "fixture", inputSchema: {}, handler: async (_, context) => {
    if (++calls === 1) {
      await new Promise<void>(resolve => context.signal.addEventListener("abort", () => resolve(), { once: true }));
      await new Promise(resolve => setTimeout(resolve, 20));
      cleanup = true;
      return "late";
    }
    assert.ok(cleanup);
    return "next";
  } });
  const output = await run(tool, request({ deadline_unix_ms: Date.now() + 40 }) + request({ id: "logical-2" }));
  assert.equal(calls, 2);
  assert.equal(output[0].effect, "unknown");
  assert.equal(output[0].result, undefined);
  assert.equal(output[1].result, "next");
});

test("failure after a possible effect reports unknown without a handler replay", async () => {
  let effects = 0;
  const tool = defineTool({ name: "fixture", description: "fixture", inputSchema: {}, handler: () => { effects++; throw new Error("after effect"); } });
  const output = await run(tool, request());
  assert.equal(effects, 1);
  assert.equal(output[0].effect, "unknown");
  assert.match(String(output[0].result), /^ERROR:/);
});

test("oversized unterminated input and partial EOF fail without dispatch", async () => {
  let calls = 0;
  const tool = defineTool({ name: "fixture", description: "fixture", inputSchema: {}, handler: () => { calls++; return "wrong"; } });
  await assert.rejects(run(tool, "x".repeat(MAX_FRAME_BYTES + 1)), /frame too large/);
  await assert.rejects(run(tool, '{"id":1'), /inside a frame/);
  assert.equal(calls, 0);
});

test("oversized handler output fails after one invocation", async () => {
  let calls = 0;
  const tool = defineTool({ name: "fixture", description: "fixture", inputSchema: {}, handler: () => { calls++; return "x".repeat(MAX_FRAME_BYTES); } });
  await assert.rejects(run(tool, request()), /frame too large/);
  assert.equal(calls, 1);
});

test("output backpressure prevents another handler from starting", async () => {
  let calls = 0;
  let release: (() => void) | undefined;
  const tool = defineTool({ name: "fixture", description: "fixture", inputSchema: {}, handler: () => { calls++; return "ok"; } });
  const input = new PassThrough();
  const output = new Writable({ write(_chunk, _encoding, callback) {
    if (!release) release = () => callback(); else callback();
  } });
  const done = servePlugin([tool], { input, output, argv: [] });
  input.end(request() + request());
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(calls, 1);
  assert.ok(release);
  release();
  await done;
  assert.equal(calls, 2);
});

test("a broken output rejects and does not replay or leak an unhandled error", async () => {
  const tool = defineTool({ name: "fixture", description: "fixture", inputSchema: {}, handler: () => "ok" });
  const input = new PassThrough();
  const output = new Writable({ write(_chunk, _encoding, callback) { callback(new Error("closed output")); } });
  const done = servePlugin([tool], { input, output, argv: [] });
  input.end(request());
  await assert.rejects(done, /closed output/);
});
