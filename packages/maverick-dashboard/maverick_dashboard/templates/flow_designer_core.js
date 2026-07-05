// Pure, DOM-free flow (de)serialization for the visual designer. Kept in its
// own file so it can be unit-tested under node (see tests/js/flow_designer_core.test.js)
// AND included into the designer template. No DOM, no globals beyond FDCore.
var FDCore = (function () {
  'use strict';
  var NODE_DEFAULTS = {
    params: {}, brief: '', tool: '', condition: '', if_true: null, if_false: null,
    items: '', var: 'item', limit: 0, prompt: '', seconds: 0, flow_ref: '',
    retries: 0, on_error: null, timeout: 0, output: '', label: '',
    body: null, branches: [], next: null
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

  return { normalizeNode: normalizeNode, serializeNode: serializeNode, serializeFlow: serializeFlow };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = FDCore;
