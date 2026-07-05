// Pure, DOM-free flow (de)serialization for the visual designer. Kept in its
// own file so it can be unit-tested under node (see tests/js/flow_designer_core.test.js)
// AND included into the designer template. No DOM, no globals beyond FDCore.
var FDCore = (function () {
  'use strict';
  var NODE_DEFAULTS = {
    params: {}, brief: '', tool: '', condition: '', if_true: null, if_false: null,
    items: '', var: 'item', limit: 0, concurrency: 1, prompt: '', seconds: 0, flow_ref: '',
    retries: 0, on_error: null, timeout: 0, output: '', label: '',
    cases: [], assignments: {}, body: null, branches: [], next: null
  };
  var SCALARS = ['next', 'tool', 'brief', 'condition', 'if_true', 'if_false',
    'items', 'var', 'prompt', 'flow_ref', 'on_error', 'output', 'label'];

  // Upgrade a raw node dict into an editable node object (defaults + id fallback).
  function normalizeNode(nd, i) {
    var n = {}, k;
    for (k in NODE_DEFAULTS) n[k] = NODE_DEFAULTS[k];
    for (k in (nd || {})) n[k] = nd[k];
    n.id = n.id || ('n' + i);
    return n;
  }

  // Serialize ONE node to the wire dict the server's Flow.from_dict accepts.
  // Recurses into nested body/branches; drops empty sub-flows so they revert to
  // the engine's "no body/branches" validation. Reads only known fields, so it
  // works on both normalized node objects AND raw (never-edited) node dicts.
  function serializeNode(n) {
    var o = { id: n.id, kind: n.kind };
    SCALARS.forEach(function (k) {
      if (n[k] !== '' && n[k] != null) o[k] = n[k];
    });
    if (n.params && Object.keys(n.params).length) o.params = n.params;
    if (n.seconds) o.seconds = n.seconds;
    if (n.retries) o.retries = n.retries;
    if (n.timeout) o.timeout = n.timeout;
    if (n.limit) o.limit = n.limit;
    if (n.concurrency && n.concurrency !== 1) o.concurrency = n.concurrency;
    if (n.cases && n.cases.length) o.cases = n.cases;
    if (n.assignments && Object.keys(n.assignments).length) o.assignments = n.assignments;
    if (n.body && n.body.nodes && n.body.nodes.length) o.body = serializeFlow(n.body);
    if (n.branches && n.branches.length) {
      var bs = n.branches.map(serializeFlow).filter(function (b) { return b.nodes.length; });
      if (bs.length) o.branches = bs;
    }
    if (n.x || n.y) { o.x = n.x; o.y = n.y; }
    return o;
  }

  function serializeFlow(f) {
    return {
      id: f.id || '', name: f.name || '', start: f.start || '',
      nodes: (f.nodes || []).map(serializeNode)
    };
  }

  // A deep, independent snapshot of a flow (the undo/redo unit). Serialized
  // form, so restoring goes through normalizeNode like a server load.
  function cloneFlow(f) {
    return JSON.parse(JSON.stringify(serializeFlow(f)));
  }

  // Output keys visible to `nodeId` from nodes that can run BEFORE it (any node
  // that reaches it via next/if_true/if_false/on_error) -- the data pills for
  // {{key}} insertion. Deterministic order: graph order of the flow's nodes.
  function upstreamOutputs(f, nodeId) {
    var nodes = f.nodes || [];
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    function reaches(fromId, toId) {
      var seen = {}, queue = [fromId];
      while (queue.length) {
        var id = queue.shift();
        if (id === toId) return true;
        if (!id || seen[id]) continue;
        seen[id] = 1;
        var n = byId[id];
        if (!n) continue;
        ['next', 'if_true', 'if_false', 'on_error'].forEach(function (k) {
          if (n[k]) queue.push(n[k]);
        });
      }
      return false;
    }
    var keys = [];
    nodes.forEach(function (n) {
      if (n.id !== nodeId && n.output && reaches(n.id, nodeId) && keys.indexOf(n.output) < 0)
        keys.push(n.output);
    });
    return keys;
  }

  // The view {x, y, k} that fits every node into a cw x ch canvas with padding.
  // Zoom is clamped to the designer's wheel range; an empty flow gets the
  // default view.
  function fitView(nodes, cw, ch, nodeW, nodeH, pad) {
    if (!nodes || !nodes.length) return { x: 40, y: 20, k: 1 };
    pad = pad == null ? 40 : pad;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodes.forEach(function (n) {
      minX = Math.min(minX, n.x || 0); minY = Math.min(minY, n.y || 0);
      maxX = Math.max(maxX, (n.x || 0) + nodeW); maxY = Math.max(maxY, (n.y || 0) + nodeH);
    });
    var w = Math.max(1, maxX - minX), h = Math.max(1, maxY - minY);
    var k = Math.min((cw - 2 * pad) / w, (ch - 2 * pad) / h);
    k = Math.max(0.35, Math.min(1, k));
    return {
      x: Math.round((cw - w * k) / 2 - minX * k),
      y: Math.round((ch - h * k) / 2 - minY * k),
      k: k
    };
  }

  return { normalizeNode: normalizeNode, serializeNode: serializeNode,
           serializeFlow: serializeFlow, cloneFlow: cloneFlow,
           upstreamOutputs: upstreamOutputs, fitView: fitView };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = FDCore;
