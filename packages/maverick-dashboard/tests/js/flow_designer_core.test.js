// Unit tests for the designer's pure flow serialization (FDCore). Run with:
//   node --test packages/maverick-dashboard/tests/js/
// Covers the nested foreach-body / parallel-branch drill-in serialization the
// visual designer relies on, which is otherwise DOM-bound and untested.
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const FDCore = require(path.join(__dirname, '..', '..', 'maverick_dashboard',
  'templates', 'flow_designer_core.js'));

test('normalizeNode fills defaults and an id fallback', () => {
  const n = FDCore.normalizeNode({ kind: 'agent', brief: 'hi' }, 2);
  assert.equal(n.id, 'n2');
  assert.equal(n.kind, 'agent');
  assert.equal(n.brief, 'hi');
  assert.deepEqual(n.params, {});
  assert.equal(n.next, null);
});

test('serializeNode omits empty scalars and keeps set ones', () => {
  const o = FDCore.serializeNode(FDCore.normalizeNode({ id: 'a', kind: 'agent', brief: 'do it' }, 0));
  assert.equal(o.id, 'a');
  assert.equal(o.kind, 'agent');
  assert.equal(o.brief, 'do it');
  // empty scalars and empty params are dropped; non-empty defaults (var) are kept
  assert.ok(!('tool' in o) && !('params' in o) && !('condition' in o) && !('next' in o));
});

test('serializeFlow round-trips a nested foreach body', () => {
  const flow = {
    id: 'f', name: 'F', start: 'a',
    nodes: [FDCore.normalizeNode({
      id: 'a', kind: 'foreach', items: 'rows',
      body: { id: '', name: 'body', start: 'x',
        nodes: [FDCore.normalizeNode({ id: 'x', kind: 'agent', brief: 'per {{item}}' }, 0)] }
    }, 0)]
  };
  const out = FDCore.serializeFlow(flow);
  assert.equal(out.nodes[0].kind, 'foreach');
  assert.equal(out.nodes[0].body.nodes[0].brief, 'per {{item}}');
  assert.equal(out.nodes[0].body.start, 'x');
});

test('serializeFlow keeps non-empty parallel branches, drops empty ones', () => {
  const flow = {
    id: 'f', name: 'F', start: 'p',
    nodes: [FDCore.normalizeNode({
      id: 'p', kind: 'parallel',
      branches: [
        { id: '', name: 'b1', start: 'l', nodes: [FDCore.normalizeNode({ id: 'l', kind: 'agent', brief: 'left' }, 0)] },
        { id: '', name: 'b2', start: '', nodes: [] }  // empty -> dropped
      ]
    }, 0)]
  };
  const out = FDCore.serializeFlow(flow);
  assert.equal(out.nodes[0].branches.length, 1);
  assert.equal(out.nodes[0].branches[0].nodes[0].brief, 'left');
});

test('a foreach whose body has no nodes drops the body entirely', () => {
  const flow = {
    id: 'f', name: 'F', start: 'a',
    nodes: [FDCore.normalizeNode({ id: 'a', kind: 'foreach', items: 'rows',
      body: { id: '', name: 'body', start: '', nodes: [] } }, 0)]
  };
  const out = FDCore.serializeFlow(flow);
  assert.ok(!('body' in out.nodes[0]));   // reverts to the engine's "no body" validation
});

test('serializeNode works on a raw (never-normalized) nested dict', () => {
  // a flow loaded from the server: nested nodes are raw dicts, not normalized
  const raw = { id: 'a', kind: 'foreach', items: 'r',
    body: { id: '', name: '', start: 'x', nodes: [{ id: 'x', kind: 'agent', brief: 'b' }] } };
  const o = FDCore.serializeNode(raw);
  assert.equal(o.body.nodes[0].brief, 'b');
});

test('cloneFlow is a deep, independent snapshot', () => {
  const flow = { id: 'f', name: 'F', start: 'a',
    nodes: [FDCore.normalizeNode({ id: 'a', kind: 'agent', brief: 'one' }, 0)] };
  const snap = FDCore.cloneFlow(flow);
  flow.nodes[0].brief = 'changed';
  assert.equal(snap.nodes[0].brief, 'one');
});

test('upstreamOutputs returns only keys from nodes that reach the target', () => {
  const flow = { id: 'f', name: 'F', start: 'a', nodes: [
    FDCore.normalizeNode({ id: 'a', kind: 'agent', brief: 'x', output: 'summary', next: 'b' }, 0),
    FDCore.normalizeNode({ id: 'b', kind: 'branch', condition: 'x == 1', if_true: 'c', if_false: 'd' }, 1),
    FDCore.normalizeNode({ id: 'c', kind: 'action', tool: 't', output: 'sent' }, 2),
    FDCore.normalizeNode({ id: 'd', kind: 'agent', brief: 'y', output: 'other' }, 3),
  ] };
  // c sees a's output (a -> b -> c) but not d's (a sibling branch arm)
  assert.deepEqual(FDCore.upstreamOutputs(flow, 'c'), ['summary']);
  // a has nothing upstream; its own output is not offered to itself
  assert.deepEqual(FDCore.upstreamOutputs(flow, 'a'), []);
});

test('upstreamOutputs follows on_error routing and survives cycles', () => {
  const flow = { id: 'f', name: 'F', start: 'a', nodes: [
    FDCore.normalizeNode({ id: 'a', kind: 'action', tool: 't', output: 'r', on_error: 'h', next: 'a' }, 0),
    FDCore.normalizeNode({ id: 'h', kind: 'agent', brief: 'recover' }, 1),
  ] };
  assert.deepEqual(FDCore.upstreamOutputs(flow, 'h'), ['r']);
});

test('serializeNode carries switch cases, setvar assignments, foreach concurrency', () => {
  const sw = FDCore.serializeNode(FDCore.normalizeNode(
    { id: 's', kind: 'switch', cases: [{ condition: 'x == 1', goto: 'g' }] }, 0));
  assert.deepEqual(sw.cases, [{ condition: 'x == 1', goto: 'g' }]);
  const sv = FDCore.serializeNode(FDCore.normalizeNode(
    { id: 'v', kind: 'setvar', assignments: { n: '{{add(c,1)}}' } }, 0));
  assert.deepEqual(sv.assignments, { n: '{{add(c,1)}}' });
  const fe = FDCore.serializeNode(FDCore.normalizeNode(
    { id: 'f', kind: 'foreach', items: 'r', concurrency: 4 }, 0));
  assert.equal(fe.concurrency, 4);
  // concurrency of 1 (the default) is omitted
  const fe1 = FDCore.serializeNode(FDCore.normalizeNode(
    { id: 'f', kind: 'foreach', items: 'r', concurrency: 1 }, 0));
  assert.ok(!('concurrency' in fe1));
});

test('fitView centers and clamps zoom', () => {
  const v = FDCore.fitView([{ x: 0, y: 0 }, { x: 1000, y: 800 }], 600, 400, 176, 64, 40);
  assert.ok(v.k >= 0.35 && v.k <= 1);
  // fitting a tiny flow never zooms IN past 1:1
  const v2 = FDCore.fitView([{ x: 10, y: 10 }], 800, 600, 176, 64, 40);
  assert.equal(v2.k, 1);
  // empty flow -> default view
  assert.deepEqual(FDCore.fitView([], 800, 600, 176, 64, 40), { x: 40, y: 20, k: 1 });
});
