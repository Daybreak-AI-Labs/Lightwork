{% raw %}
(function () {
  'use strict';
  var SVG = 'http://www.w3.org/2000/svg';
  var W = 176, H = 64;                 // node card size
  var KIND_ICON = { agent: '🧠', action: '⚙️', branch: '🔀', foreach: '🔁',
                    parallel: '🔱', approval: '✅', delay: '⏱', subflow: '📎',
                    switch: '🔃', setvar: '📝', try: '🛡️' };

  var flow = { id: '', name: '', start: '', nodes: [] };
  var rootFlow = flow;  // the top-level flow (save/run/history operate on this)
  var stack = [];       // drill-in frames: {flow, selected} for each level above root
  var crumbs = ['Main flow'];   // breadcrumb labels; length === stack.length + 1
  var proposals = {};   // node_id -> {to_kind, reason, inferred_tool} from grounded per-node outcomes
  var impacts = {};     // node_id -> {changed, applied, impact} measured before/after a rewrite
  var signals = { nodes: {}, trigger: null };   // grounded per-node stats + trigger reliability
  var runState = {};    // node_id -> {status, outcome} from the last/live run
  var runCursor = null; // node a paused run is waiting on
  var toolCatalog = {}; // tool name -> {description, params[]} for the action picker
  var selected = null;
  var selectedEdge = null;  // {from, port} when an edge is selected (click to select, Del to cut)
  var view = { x: 40, y: 20, k: 1 };
  var idSeq = 0;
  var undoStack = [], redoStack = [], pendingSnap = null;   // serialized rootFlow snapshots
  var chatHistory = [];     // copilot transcript: {role, content}
  var lastRunId = null;     // most recent run started here (grounds "why did it fail?")

  var canvas = document.getElementById('fd-canvas');
  var vp = document.getElementById('fd-viewport');
  var gNodes = document.getElementById('fd-nodes');
  var gEdges = document.getElementById('fd-edges');
  var tempEdge = document.getElementById('fd-temp-edge');
  var props = document.getElementById('fd-props');
  var msg = document.getElementById('fd-msg');
  var nameInput = document.getElementById('fd-name');
  var idInput = document.getElementById('fd-id');

  function el(tag, attrs, text) {
    var e = document.createElementNS(SVG, tag);
    for (var k in (attrs || {})) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }

  // ---- undo/redo: whole-root snapshots (the same serialized form a save uses),
  // so a chat edit, a draft, or a hand edit all undo the same way ----
  function snapState() {
    return JSON.stringify(FDCore.cloneFlow({
      id: idInput.value || rootFlow.id, name: nameInput.value || rootFlow.name,
      start: rootFlow.start, nodes: rootFlow.nodes }));
  }
  function pushHistory() {
    var s = snapState();
    if (undoStack[undoStack.length - 1] === s) return;
    undoStack.push(s);
    if (undoStack.length > 50) undoStack.shift();
    redoStack = [];
  }
  function histFocus() { pendingSnap = snapState(); }
  function histBlur() {
    if (pendingSnap && pendingSnap !== snapState()) {
      undoStack.push(pendingSnap);
      if (undoStack.length > 50) undoStack.shift();
      redoStack = [];
    }
    pendingSnap = null;
  }
  function restoreState(s) {
    var f = JSON.parse(s);
    stack = []; crumbs = ['Main flow']; renderCrumbs();
    flow = { id: f.id || '', name: f.name || '', start: f.start || '', nodes: [] };
    (f.nodes || []).forEach(function (nd, i) { flow.nodes.push(normalizeNode(nd, i)); });
    rootFlow = flow;
    nameInput.value = flow.name; idInput.value = flow.id;
    selected = null; selectedEdge = null;
    autoLayout(); render(); renderProps();
  }
  function undo() {
    if (!undoStack.length) { say('Nothing to undo.'); return; }
    redoStack.push(snapState());
    restoreState(undoStack.pop());
    say('↶ undone');
  }
  function redo() {
    if (!redoStack.length) { say('Nothing to redo.'); return; }
    undoStack.push(snapState());
    restoreState(redoStack.pop());
    say('↷ redone');
  }
  function nodeById(id) { for (var i = 0; i < flow.nodes.length; i++) if (flow.nodes[i].id === id) return flow.nodes[i]; return null; }
  function newId() { var id; do { id = 'n' + (idSeq++); } while (nodeById(id)); return id; }
  function say(t, err) { msg.textContent = t || ''; msg.style.color = err ? 'var(--danger)' : 'var(--muted)'; }
  function trunc(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }

  function summary(n) {
    if (n.kind === 'agent') return trunc(n.brief || '(no brief)', 30);
    if (n.kind === 'action') return trunc(n.tool || '(no tool)', 30);
    if (n.kind === 'branch') return trunc(n.condition || '(condition)', 30);
    if (n.kind === 'foreach') return 'for each ' + trunc(n.items || '…', 20);
    if (n.kind === 'parallel') return (n.branches ? n.branches.length : 0) + ' branches';
    if (n.kind === 'approval') return trunc(n.prompt || '(approve?)', 30);
    if (n.kind === 'delay') return (n.seconds || 0) + 's';
    if (n.kind === 'subflow') return '↳ ' + trunc(n.flow_ref || '(flow)', 24);
    if (n.kind === 'switch') return (n.cases ? n.cases.length : 0) + ' cases';
    if (n.kind === 'setvar') return 'set ' + trunc(Object.keys(n.assignments || {}).join(', ') || '…', 22);
    if (n.kind === 'try') return 'try / catch';
    return n.kind;
  }
  function ports(n) {
    if (n.kind === 'branch') return [
      { key: 'if_true', x: 50, cls: 't', label: 'yes' },
      { key: 'if_false', x: W - 50, cls: 'f', label: 'no' }];
    return [{ key: 'next', x: W / 2, cls: '', label: '' }];
  }
  function runClass(id) {
    if (id === runCursor) return ' run-active';        // where the run is waiting
    var rs = runState[id]; if (!rs) return '';
    if (rs.outcome === 1) return ' run-ok';
    if (rs.outcome === 0) return ' run-err';
    return ' run-info';                                // a control step that ran
  }
  function runBadge(id) {
    if (id === runCursor) return '⏳';
    var rs = runState[id]; if (!rs) return '';
    if (rs.outcome === 1) return '🟢';
    if (rs.outcome === 0) return '🔴';
    if (rs.status === 'true') return '✓';
    if (rs.status === 'false') return '✗';
    return '🟠';
  }

  function render() {
    vp.setAttribute('transform', 'translate(' + view.x + ',' + view.y + ') scale(' + view.k + ')');
    gEdges.textContent = ''; gNodes.textContent = '';
    // edges
    flow.nodes.forEach(function (n) {
      ports(n).forEach(function (p) {
        var tgt = n[p.key]; if (!tgt) return;
        var t = nodeById(tgt); if (!t) return;
        var px = n.x + p.x, py = n.y + H, tx = t.x + W / 2, ty = t.y;
        var d = 'M' + px + ' ' + py + ' C ' + px + ' ' + (py + 46) + ', ' + tx + ' ' + (ty - 46) + ', ' + tx + ' ' + ty;
        var isSel = selectedEdge && selectedEdge.from === n.id && selectedEdge.port === p.key;
        gEdges.appendChild(el('path', { class: 'fd-edge ' + p.cls + (isSel ? ' selected' : ''), d: d }));
        // invisible fat twin so a 2px curve is actually clickable (click = select,
        // Delete = cut; a second click on the same edge also cuts)
        var hit = el('path', { class: 'fd-edge-hit', d: d });
        hit.setAttribute('data-edge-from', n.id); hit.setAttribute('data-edge-port', p.key);
        gEdges.appendChild(hit);
        if (p.label) gEdges.appendChild(el('text', { class: 'fd-edge-label', x: px + 4, y: py + 14 }, p.label));
      });
    });
    // nodes
    flow.nodes.forEach(function (n) {
      var g = el('g', { class: 'fd-node' + (n.id === selected ? ' selected' : '') + (n.id === flow.start ? ' start' : '') + runClass(n.id),
                        transform: 'translate(' + n.x + ',' + n.y + ')' });
      g.setAttribute('data-node', n.id);
      g.appendChild(el('rect', { class: 'card', width: W, height: H, rx: 10 }));
      g.appendChild(el('rect', { class: 'swatch k-' + n.kind, x: 0, y: 0, width: 8, height: H, rx: 4 }));
      g.appendChild(el('text', { class: 'kind', x: 16, y: 20 }, (KIND_ICON[n.kind] || '') + ' ' + n.kind));
      g.appendChild(el('text', { x: 16, y: 40 }, trunc(n.label || summary(n), 26)));
      var badge = runBadge(n.id);
      if (badge) g.appendChild(el('text', { class: 'runbadge', x: W - 20, y: 20 }, badge));
      if (proposals[n.id]) {
        var lamp = el('text', { class: 'runbadge', x: W - (badge ? 40 : 20), y: 20 }, '💡');
        lamp.appendChild(el('title', {}, 'Learned suggestion — select this node to review it'));
        g.appendChild(lamp);
      }
      if (n.id === flow.start) g.appendChild(el('text', { x: 16, y: 56, class: 'fd-edge-label' }, '▶ start'));
      ports(n).forEach(function (p) {
        var c = el('circle', { class: 'port', cx: p.x, cy: H, r: 6 });
        c.setAttribute('data-port', p.key); c.setAttribute('data-node', n.id);
        g.appendChild(c);
      });
      gNodes.appendChild(g);
    });
  }

  // ---- coordinate + interaction ----
  function worldPt(evt) {
    var r = canvas.getBoundingClientRect();
    return { x: (evt.clientX - r.left - view.x) / view.k, y: (evt.clientY - r.top - view.y) / view.k };
  }
  var drag = null;   // {kind:'node'|'pan'|'connect', ...}

  canvas.addEventListener('mousedown', function (evt) {
    var portEl = evt.target.closest('[data-port]');
    var nodeEl = evt.target.closest('[data-node]');
    var edgeEl = evt.target.closest('[data-edge-from]');
    if (portEl) {
      drag = { kind: 'connect', from: portEl.getAttribute('data-node'), port: portEl.getAttribute('data-port') };
      evt.preventDefault(); return;
    }
    if (edgeEl) {
      var ef = edgeEl.getAttribute('data-edge-from'), ep = edgeEl.getAttribute('data-edge-port');
      if (selectedEdge && selectedEdge.from === ef && selectedEdge.port === ep) {
        deleteEdge(selectedEdge);              // second click on a selected edge cuts it
      } else {
        selectedEdge = { from: ef, port: ep };
        selected = null; renderProps(); render();
        say('Edge selected — click it again (or press Delete) to cut it.');
      }
      evt.preventDefault(); return;
    }
    selectedEdge = null;
    if (nodeEl) {
      var id = nodeEl.getAttribute('data-node'); selectNode(id);
      var n = nodeById(id), p = worldPt(evt);
      drag = { kind: 'node', id: id, dx: p.x - n.x, dy: p.y - n.y };
      evt.preventDefault(); return;
    }
    drag = { kind: 'pan', sx: evt.clientX - view.x, sy: evt.clientY - view.y };
    canvas.classList.add('panning');
    selectNode(null);
  });
  function deleteEdge(edge) {
    var n = nodeById(edge.from);
    if (n) { pushHistory(); n[edge.port] = null; }
    selectedEdge = null; render(); renderProps();
    say('Edge removed.');
  }
  canvas.addEventListener('dblclick', function (evt) {
    var nodeEl = evt.target.closest('[data-node]'); if (!nodeEl) return;
    var n = nodeById(nodeEl.getAttribute('data-node')); if (!n) return;
    if (n.kind === 'foreach') { evt.preventDefault(); enterSub(n, 'foreach'); }
    else if (n.kind === 'parallel') { evt.preventDefault(); enterSub(n, 'parallel', 0); }
  });
  window.addEventListener('mousemove', function (evt) {
    if (!drag) return;
    if (drag.kind === 'node') {
      var n = nodeById(drag.id); if (!n) return;
      var p = worldPt(evt); n.x = Math.round(p.x - drag.dx); n.y = Math.round(p.y - drag.dy); render();
    } else if (drag.kind === 'pan') {
      view.x = evt.clientX - drag.sx; view.y = evt.clientY - drag.sy; render();
    } else if (drag.kind === 'connect') {
      var from = nodeById(drag.from); if (!from) return;
      var pd = ports(from).filter(function (x) { return x.key === drag.port; })[0] || { x: W / 2 };
      var px = from.x * view.k + view.x + (pd.x) * view.k, py = (from.y + H) * view.k + view.y;
      var r = canvas.getBoundingClientRect();
      var mx = evt.clientX - r.left, my = evt.clientY - r.top;
      tempEdge.style.display = ''; tempEdge.removeAttribute('transform');
      tempEdge.setAttribute('d', 'M' + ((px)) + ' ' + (py) + ' L ' + mx + ' ' + my);
      // The temp edge is built in screen space but lives inside the (translated +
      // scaled) viewport, so apply the viewport's exact inverse: undo the scale
      // AND the translate under that scale -> scale(1/k) THEN translate(-x,-y)
      // (SVG applies the rightmost transform first).
      tempEdge.setAttribute('transform', 'scale(' + (1 / view.k) + ') translate(' + (-view.x) + ',' + (-view.y) + ')');
    }
  });
  window.addEventListener('mouseup', function (evt) {
    canvas.classList.remove('panning');
    tempEdge.style.display = 'none';
    if (drag && drag.kind === 'connect') {
      var nodeEl = evt.target.closest('[data-node]');
      if (nodeEl) {
        var to = nodeEl.getAttribute('data-node'), from = nodeById(drag.from);
        if (from && to !== from.id) { pushHistory(); from[drag.port] = to; renderProps(); render(); }
      }
    }
    drag = null;
  });
  canvas.addEventListener('wheel', function (evt) {
    evt.preventDefault();
    var f = evt.deltaY < 0 ? 1.1 : 0.9;
    var nk = Math.max(0.35, Math.min(2.2, view.k * f));
    var r = canvas.getBoundingClientRect(), mx = evt.clientX - r.left, my = evt.clientY - r.top;
    view.x = mx - (mx - view.x) * (nk / view.k); view.y = my - (my - view.y) * (nk / view.k);
    view.k = nk; render();
  }, { passive: false });

  // ---- nodes ----
  function addNode(kind) {
    pushHistory();
    var n = { id: newId(), kind: kind, x: Math.round((-view.x + 220) / view.k), y: Math.round((-view.y + 80) / view.k),
              next: null, params: {}, brief: '', tool: '', condition: '', if_true: null, if_false: null,
              items: '', var: 'item', limit: 0, concurrency: 1, prompt: '', seconds: 0, flow_ref: '', retries: 0, on_error: null, timeout: 0,
              output: '', label: '', cases: [], assignments: {}, body: null, branches: [] };
    // place under the currently selected node if any
    var sel = selected && nodeById(selected);
    if (sel) { n.x = sel.x; n.y = sel.y + 110; if (!sel.next && sel.kind !== 'branch') sel.next = n.id; }
    if (!flow.nodes.length) flow.start = n.id;
    flow.nodes.push(n); selectNode(n.id); render();
  }
  function deleteNode(id) {
    pushHistory();
    flow.nodes = flow.nodes.filter(function (n) { return n.id !== id; });
    flow.nodes.forEach(function (n) { ['next', 'if_true', 'if_false', 'on_error'].forEach(function (k) { if (n[k] === id) n[k] = null; }); });
    if (flow.start === id) flow.start = flow.nodes.length ? flow.nodes[0].id : '';
    selectNode(null); render();
  }
  function duplicateNode(id) {
    var src = nodeById(id); if (!src) return;
    pushHistory();
    var copy = normalizeNode(JSON.parse(JSON.stringify(FDCore.serializeNode(src))), 0);
    copy.id = newId();
    copy.x = (src.x || 0) + 30; copy.y = (src.y || 0) + 40;
    copy.next = null; copy.if_true = null; copy.if_false = null; copy.on_error = null;
    flow.nodes.push(copy); selectNode(copy.id); render();
    say('Duplicated as ' + copy.id + '.');
  }
  function selectNode(id) { selected = id; if (id) selectedEdge = null; renderProps(); render(); }
  function zoomToFit() {
    var r = canvas.getBoundingClientRect();
    view = FDCore.fitView(flow.nodes, r.width, r.height, W, H, 40);
    render();
  }

  // ---- nested sub-flow editing (foreach body / parallel branches) ----
  // Pure serialization/normalization lives in FDCore (flow_designer_core.js) so
  // it's unit-testable under node without a DOM.
  var normalizeNode = FDCore.normalizeNode;
  function normalizeFlow(f) {
    // Upgrade a raw nested-flow dict's nodes into editable node objects (in place,
    // so the parent node's body/branches[i] keeps the same reference).
    f.nodes = (f.nodes || []).map(normalizeNode);
    if (!f.start && f.nodes.length) f.start = f.nodes[0].id;
  }
  function commitFlowRaw(f) {
    // flush any buffered JSON textareas for the nodes of ONE flow (current view)
    (f.nodes || []).forEach(function (n) {
      if (n._paramsRaw !== undefined) { try { n.params = n._paramsRaw.trim() ? JSON.parse(n._paramsRaw) : {}; } catch (e) { /* reported on save */ } delete n._paramsRaw; }
    });
  }
  function enterSub(node, kind, index) {
    commitFlowRaw(flow);
    var sub, label;
    if (kind === 'foreach') {
      if (!node.body) node.body = { id: '', name: 'loop body', start: '', nodes: [] };
      sub = node.body; label = '🔁 ' + (node.label || 'for-each') + ' body';
    } else {
      node.branches = node.branches || [];
      if (!node.branches[index]) node.branches[index] = { id: '', name: 'branch ' + (index + 1), start: '', nodes: [] };
      sub = node.branches[index]; label = '🔱 ' + (node.label || 'parallel') + ' · branch ' + (index + 1);
    }
    normalizeFlow(sub);
    stack.push({ flow: flow, selected: selected });
    crumbs.push(label);
    flow = sub; selected = null;
    autoLayout(); renderCrumbs(); render(); renderProps();
  }
  function exitToDepth(d) {
    commitFlowRaw(flow);
    while (stack.length > d) { var fr = stack.pop(); flow = fr.flow; selected = fr.selected; }
    crumbs.length = d + 1;
    renderCrumbs(); render(); renderProps();
  }
  function renderCrumbs() {
    var c = document.getElementById('fd-crumbs'); if (!c) return;
    c.textContent = '';
    if (!stack.length) { c.classList.remove('on'); return; }   // hidden at the root
    c.classList.add('on');
    crumbs.forEach(function (label, i) {
      if (i) c.appendChild(document.createTextNode(' ▸ '));
      if (i < crumbs.length - 1) {
        var b = document.createElement('button'); b.type = 'button'; b.className = 'fd__crumb';
        b.textContent = label; b.addEventListener('click', function () { exitToDepth(i); });
        c.appendChild(b);
      } else {
        var s = document.createElement('span'); s.className = 'fd__crumb fd__crumb--cur'; s.textContent = label; c.appendChild(s);
      }
    });
  }

  // ---- property panel ----
  function field(label, inputEl) {
    var wrap = document.createElement('div'); wrap.className = 'field';
    var l = document.createElement('label'); l.textContent = label; wrap.appendChild(l); wrap.appendChild(inputEl); return wrap;
  }
  function input(val, oninput, ph) {
    var i = document.createElement('input'); i.className = 'input'; i.value = val || ''; if (ph) i.placeholder = ph;
    i.addEventListener('input', function () { oninput(i.value); });
    i.addEventListener('focus', histFocus); i.addEventListener('blur', histBlur);
    return i;
  }
  function textarea(val, oninput, ph) {
    var t = document.createElement('textarea'); t.className = 'input'; t.value = val || ''; if (ph) t.placeholder = ph;
    t.addEventListener('input', function () { oninput(t.value); });
    t.addEventListener('focus', histFocus); t.addEventListener('blur', histBlur);
    return t;
  }
  function select(val, opts, onchange) {
    var s = document.createElement('select'); s.className = 'input';
    opts.forEach(function (o) { var op = document.createElement('option'); op.value = o.v; op.textContent = o.t; if (o.v === val) op.selected = true; s.appendChild(op); });
    s.addEventListener('change', function () { onchange(s.value); }); return s;
  }
  function renderProps() {
    props.textContent = '';
    if (!selected) {
      var p = document.createElement('p'); p.className = 'fd__empty';
      p.textContent = "Select a node to edit it, or add a step. Connect steps by dragging from a node's bottom dot to another node.";
      props.appendChild(p); return;
    }
    var n = nodeById(selected); if (!n) return;
    var head = document.createElement('div'); head.style.fontWeight = '600'; head.style.marginBottom = 'var(--space-3)';
    head.textContent = (KIND_ICON[n.kind] || '') + ' ' + n.kind + ' node';
    props.appendChild(head);
    props.appendChild(field('Label (optional)', input(n.label, function (v) { n.label = v; render(); }, 'shown on the card')));
    if (n.kind === 'agent') {
      var briefTa = textarea(n.brief, function (v) { n.brief = v; render(); }, 'Summarize {{issue_body}} and draft a reply');
      props.appendChild(withPills(n.id, field('Brief (what the agent should do)', briefTa), briefTa));
    }
    if (n.kind === 'action') {
      var toolIn = input(n.tool, function (v) { n.tool = v; renderProps(); render(); }, 'search connectors…');
      toolIn.setAttribute('list', 'fd-tools');
      props.appendChild(field('Connector / tool', toolIn));
      var meta = toolCatalog[n.tool];
      if (meta && meta.description) { var th = document.createElement('p'); th.className = 'toolhint'; th.textContent = meta.description; props.appendChild(th); }
      // Known params -> a labeled input each (no raw JSON for a recognized tool)
      var known = (meta && meta.params) || [];
      if (known.length) {
        known.forEach(function (pk) {
          var pin = input(n.params[pk] != null ? String(n.params[pk]) : '',
            function (v) { if (v === '') delete n.params[pk]; else n.params[pk] = v; }, '');
          props.appendChild(withPills(n.id, field(pk, pin), pin));
        });
        props.appendChild(hint('Use {{key}} to insert an earlier step’s result.'));
      } else {
        var pta = textarea(jsonOr(n.params), function (v) { n._paramsRaw = v; });
        props.appendChild(withPills(n.id, field('Params (JSON)', pta), pta));
      }
    }
    if (n.kind === 'branch') {
      var condIn = input(n.condition, function (v) { n.condition = v; render(); }, 'amount > 100');
      props.appendChild(withPills(n.id, field('Condition', condIn), condIn, true));
      props.appendChild(hint('Drag the green (yes) and red (no) dots to the next steps.'));
    }
    if (n.kind === 'foreach') {
      props.appendChild(field('Items (data key with a list)', input(n.items, function (v) { n.items = v; render(); }, 'rows')));
      props.appendChild(field('Loop variable', input(n.var, function (v) { n.var = v; }, 'item')));
      props.appendChild(field('Max iterations (0 = all)', input(String(n.limit || ''), function (v) { n.limit = parseInt(v, 10) || 0; }, '0')));
      props.appendChild(field('Concurrency (1 = one at a time)', input(String(n.concurrency || ''), function (v) { n.concurrency = parseInt(v, 10) || 1; }, '1')));
      var bodyN = (n.body && n.body.nodes && n.body.nodes.length) || 0;
      var eb = document.createElement('button'); eb.className = 'btn btn--ghost'; eb.type = 'button';
      eb.style.width = '100%'; eb.textContent = '✏️ Edit loop body (' + bodyN + (bodyN === 1 ? ' step)' : ' steps)') + ' ↳';
      eb.addEventListener('click', function () { enterSub(n, 'foreach'); });
      props.appendChild(field('Loop body (visual)', eb));
      props.appendChild(hint('Opens the body as its own canvas. It can set _break to true to stop early.'));
    }
    if (n.kind === 'parallel') {
      var wrap = document.createElement('div'); wrap.className = 'field';
      var wl = document.createElement('label'); wl.textContent = 'Branches (each runs concurrently)'; wrap.appendChild(wl);
      (n.branches || []).forEach(function (br, bi) {
        var cnt = (br && br.nodes && br.nodes.length) || 0;
        var rowb = document.createElement('div'); rowb.style.display = 'flex'; rowb.style.gap = '4px'; rowb.style.marginBottom = '4px';
        var edit = document.createElement('button'); edit.className = 'btn btn--ghost'; edit.type = 'button'; edit.style.flex = '1';
        edit.textContent = '✏️ Branch ' + (bi + 1) + ' (' + cnt + ')'; edit.addEventListener('click', function () { enterSub(n, 'parallel', bi); });
        var rm = document.createElement('button'); rm.className = 'btn btn--ghost'; rm.type = 'button'; rm.textContent = '🗑';
        rm.title = 'Remove branch ' + (bi + 1);
        rm.addEventListener('click', function () { n.branches.splice(bi, 1); renderProps(); render(); });
        rowb.appendChild(edit); rowb.appendChild(rm); wrap.appendChild(rowb);
      });
      var add = document.createElement('button'); add.className = 'btn btn--ghost'; add.type = 'button'; add.style.width = '100%';
      add.textContent = '+ Add branch';
      add.addEventListener('click', function () { n.branches = n.branches || []; n.branches.push({ id: '', name: 'branch ' + (n.branches.length + 1), start: '', nodes: [] }); renderProps(); render(); });
      wrap.appendChild(add); props.appendChild(wrap);
      props.appendChild(hint('Each branch is an isolated sub-flow; they run in parallel and their outputs merge.'));
    }
    if (n.kind === 'approval') props.appendChild(field('Prompt (what a human approves)', input(n.prompt, function (v) { n.prompt = v; render(); }, 'Send this reply?')));
    if (n.kind === 'delay') props.appendChild(field('Seconds', input(String(n.seconds || ''), function (v) { n.seconds = parseFloat(v) || 0; render(); }, '60')));
    if (n.kind === 'subflow') {
      props.appendChild(field('Flow to run (saved flow id)', input(n.flow_ref, function (v) { n.flow_ref = v; render(); }, 'onboard-customer')));
      props.appendChild(hint('Runs another saved flow here, sharing this flow’s data. Cyclic references are blocked.'));
    }
    if (n.kind === 'switch') {
      var nodeOpts = function () { return [{ v: '', t: '(default → next)' }].concat(flow.nodes.filter(function (m) { return m.id !== n.id; }).map(function (m) { return { v: m.id, t: (m.label || m.id) }; })); };
      var sw = document.createElement('div'); sw.className = 'field';
      var swl = document.createElement('label'); swl.textContent = 'Cases (first match wins)'; sw.appendChild(swl);
      n.cases = n.cases || [];
      n.cases.forEach(function (c, ci) {
        var rowc = document.createElement('div'); rowc.style.display = 'flex'; rowc.style.gap = '4px'; rowc.style.marginBottom = '4px';
        var cond = input(c.condition, function (v) { c.condition = v; render(); }, 'tier == gold'); cond.style.flex = '2';
        var goto = select(c.goto || '', nodeOpts(), function (v) { c.goto = v || null; render(); }); goto.style.flex = '1';
        var rm = document.createElement('button'); rm.className = 'btn btn--ghost'; rm.type = 'button'; rm.textContent = '🗑';
        rm.addEventListener('click', function () { n.cases.splice(ci, 1); renderProps(); render(); });
        rowc.appendChild(cond); rowc.appendChild(goto); rowc.appendChild(rm); sw.appendChild(rowc);
      });
      var addc = document.createElement('button'); addc.className = 'btn btn--ghost'; addc.type = 'button'; addc.style.width = '100%';
      addc.textContent = '+ Add case';
      addc.addEventListener('click', function () { n.cases.push({ condition: '', goto: null }); renderProps(); render(); });
      sw.appendChild(addc); props.appendChild(sw);
      props.appendChild(hint('If no case matches, the flow follows this node’s default arrow.'));
    }
    if (n.kind === 'setvar') {
      var sv = document.createElement('div'); sv.className = 'field';
      var svl = document.createElement('label'); svl.textContent = 'Set data keys (value can use {{…}})'; sv.appendChild(svl);
      n.assignments = n.assignments || {};
      Object.keys(n.assignments).forEach(function (k) {
        var rowv = document.createElement('div'); rowv.style.display = 'flex'; rowv.style.gap = '4px'; rowv.style.marginBottom = '4px';
        var keyIn = input(k, null, 'key'); keyIn.style.flex = '1';
        keyIn.addEventListener('change', function () { var nv = keyIn.value.trim(); if (nv && nv !== k) { n.assignments[nv] = n.assignments[k]; delete n.assignments[k]; renderProps(); } });
        var valIn = input(n.assignments[k], function (v) { n.assignments[k] = v; }, '{{add(count,1)}}'); valIn.style.flex = '2';
        var rm = document.createElement('button'); rm.className = 'btn btn--ghost'; rm.type = 'button'; rm.textContent = '🗑';
        rm.addEventListener('click', function () { delete n.assignments[k]; renderProps(); render(); });
        rowv.appendChild(keyIn); rowv.appendChild(valIn); rowv.appendChild(rm); sv.appendChild(rowv);
      });
      var addv = document.createElement('button'); addv.className = 'btn btn--ghost'; addv.type = 'button'; addv.style.width = '100%';
      addv.textContent = '+ Add assignment';
      addv.addEventListener('click', function () { var key = 'var' + (Object.keys(n.assignments).length + 1); n.assignments[key] = ''; renderProps(); render(); });
      sv.appendChild(addv); props.appendChild(sv);
    }
    if (n.kind === 'try') {
      var tbN = (n.body && n.body.nodes && n.body.nodes.length) || 0;
      var tb = document.createElement('button'); tb.className = 'btn btn--ghost'; tb.type = 'button'; tb.style.width = '100%';
      tb.textContent = '✏️ Edit protected body (' + tbN + (tbN === 1 ? ' step)' : ' steps)') + ' ↳';
      tb.addEventListener('click', function () { enterSub(n, 'foreach'); });   // reuse body drill-in
      props.appendChild(field('Body (visual)', tb));
      props.appendChild(hint('If anything inside the body fails, the flow jumps to the “On failure” step below and sets {{_error}}.'));
    }
    if (['agent', 'action', 'foreach', 'parallel', 'subflow'].indexOf(n.kind) >= 0)
      props.appendChild(field('Output key (optional)', input(n.output, function (v) { n.output = v; }, 'store result under…')));
    // error handling: retry (work nodes) then route on failure (work + subflow + try)
    if (n.kind === 'agent' || n.kind === 'action') {
      props.appendChild(field('Retries on failure', input(String(n.retries || ''), function (v) { n.retries = parseInt(v, 10) || 0; }, '0')));
      props.appendChild(field('Timeout (seconds, 0 = none)', input(String(n.timeout || ''), function (v) { n.timeout = parseFloat(v) || 0; }, '0')));
    }
    if (['agent', 'action', 'subflow', 'try'].indexOf(n.kind) >= 0) {
      var errOpts = [{ v: '', t: '(none — continue)' }].concat(flow.nodes.filter(function (m) { return m.id !== n.id; }).map(function (m) { return { v: m.id, t: (m.label || m.id) }; }));
      props.appendChild(field('On failure, go to', select(n.on_error || '', errOpts, function (v) { n.on_error = v || null; render(); })));
    }
    // last run outcome for this node (from the live/last run overlay)
    var rs = runState[n.id];
    if (rs || n.id === runCursor) {
      var badge = runBadge(n.id);
      var rl = document.createElement('p'); rl.className = 'fd__empty'; rl.style.marginTop = 'var(--space-2)';
      rl.textContent = 'Last run: ' + (n.id === runCursor ? 'waiting here'
        : (rs.status || '') + (rs.outcome != null ? ' (' + (rs.outcome === 1 ? 'ok' : 'failed') + ')' : '')) + ' ' + badge;
      props.appendChild(rl);
    }
    // grounded signal for this node across ALL its runs (not just the last):
    // an approval's human accept-rate, a work node's success rate.
    var sig = signals.nodes[n.id];
    if (sig && sig.n) {
      var sl = document.createElement('p'); sl.className = 'fd__empty'; sl.style.marginTop = 'var(--space-2)';
      var pct = Math.round(sig.mean * 100);
      if (n.kind === 'approval') sl.textContent = '🧑‍⚖️ humans approved ' + pct + '% of ' + sig.n + ' decisions';
      else sl.textContent = '📊 succeeds ' + pct + '% over ' + sig.n + ' runs';
      props.appendChild(sl);
    }
    // measured impact of a prior self-rewrite on this node (before vs. after)
    var imp = impacts[n.id];
    if (imp && imp.changed && imp.impact) {
      var d = imp.impact.delta;
      var il = document.createElement('p'); il.className = 'fd__empty'; il.style.marginTop = 'var(--space-2)';
      il.textContent = '📈 since → ' + (imp.applied && imp.applied.to_kind) + ': '
        + (typeof d === 'number' ? (d >= 0 ? '+' : '') + Math.round(d * 100) + '% vs before'
          : 'measuring (' + (imp.impact.after && imp.impact.after.n || 0) + ' runs so far)');
      props.appendChild(il);
    }
    // self-rewrite proposal for this node (from grounded per-node outcomes)
    var prop = proposals[n.id];
    if (prop) {
      var pbox = document.createElement('div');
      pbox.style.cssText = 'margin-top:var(--space-3);padding:var(--space-2);border:1px solid var(--accent);border-radius:8px;';
      var pl = document.createElement('p'); pl.className = 'fd__empty'; pl.style.marginBottom = 'var(--space-2)';
      pl.textContent = '💡 ' + prop.reason;
      // Persisted apply: saves a new version + logs the change so its impact is measured.
      var pb = document.createElement('button'); pb.className = 'btn btn--primary'; pb.type = 'button';
      pb.textContent = 'Apply & track (→ ' + prop.to_kind
        + (prop.inferred_tool ? ' · ' + prop.inferred_tool : '') + ')';
      pb.addEventListener('click', function () { applyProposal(n, prop); });
      pbox.appendChild(pl); pbox.appendChild(pb); props.appendChild(pbox);
    }
    // actions
    var row = document.createElement('div'); row.style.display = 'flex'; row.style.gap = 'var(--space-2)'; row.style.marginTop = 'var(--space-3)'; row.style.flexWrap = 'wrap';
    var mk = document.createElement('button'); mk.className = 'btn btn--ghost'; mk.type = 'button'; mk.textContent = 'Make start';
    mk.addEventListener('click', function () { flow.start = n.id; render(); });
    var dup = document.createElement('button'); dup.className = 'btn btn--ghost'; dup.type = 'button'; dup.textContent = '⧉ Duplicate';
    dup.title = 'Ctrl+D';
    dup.addEventListener('click', function () { duplicateNode(n.id); });
    var del = document.createElement('button'); del.className = 'btn btn--ghost'; del.type = 'button'; del.textContent = '🗑 Delete';
    del.addEventListener('click', function () { deleteNode(n.id); });
    row.appendChild(mk); row.appendChild(dup); row.appendChild(del); props.appendChild(row);
  }
  function hint(t) { var p = document.createElement('p'); p.className = 'fd__empty'; p.textContent = t; return p; }

  // ---- data pills: what THIS node can reference, one click to insert ----
  function availableKeys(nodeId) {
    var keys = FDCore.upstreamOutputs(flow, nodeId);
    try {   // the sample-input keys are the trigger payload's shape
      var raw = document.getElementById('fd-sample').value.trim();
      if (raw) Object.keys(JSON.parse(raw)).forEach(function (k) {
        if (keys.indexOf(k) < 0) keys.push(k);
      });
    } catch (e) { /* not JSON yet -- no payload pills */ }
    return keys;
  }
  function insertAtCursor(target, text) {
    var s = target.selectionStart, e = target.selectionEnd, v = target.value;
    if (s == null) { target.value = v + text; }
    else { target.value = v.slice(0, s) + text + v.slice(e); target.selectionStart = target.selectionEnd = s + text.length; }
    target.dispatchEvent(new Event('input', { bubbles: false }));
    target.focus();
  }
  function pillsRow(nodeId, target, raw) {
    var keys = availableKeys(nodeId);
    if (!keys.length) return null;
    var row = document.createElement('div'); row.className = 'fd__pills';
    keys.forEach(function (k) {
      var b = document.createElement('button'); b.type = 'button'; b.className = 'fd__pill';
      b.textContent = raw ? k : '{{' + k + '}}'; b.title = 'Insert ' + (raw ? k : '{{' + k + '}}');
      b.addEventListener('mousedown', function (evt) { evt.preventDefault(); });   // keep target's focus
      b.addEventListener('click', function () { insertAtCursor(target, raw ? k : '{{' + k + '}}'); });
      row.appendChild(b);
    });
    return row;
  }
  function withPills(nodeId, fieldEl, target, raw) {
    var pills = pillsRow(nodeId, target, raw);
    if (pills) fieldEl.appendChild(pills);
    return fieldEl;
  }
  function jsonOr(v) { try { return v && (typeof v === 'object') ? JSON.stringify(v, null, 2) : (v || ''); } catch (e) { return ''; } }

  // ---- serialize / api ----
  function walkFlows(f, fn) {
    // depth-first over a flow and every nested body/branch sub-flow
    fn(f);
    (f.nodes || []).forEach(function (n) {
      if (n.body) walkFlows(n.body, fn);
      (n.branches || []).forEach(function (b) { walkFlows(b, fn); });
    });
  }
  function commitRawFields() {
    // parse the buffered params JSON across the root AND every nested sub-flow
    var bad = [];
    walkFlows(rootFlow, function (f) {
      (f.nodes || []).forEach(function (n) {
        if (n._paramsRaw !== undefined) { try { n.params = n._paramsRaw.trim() ? JSON.parse(n._paramsRaw) : {}; } catch (e) { bad.push(n.id + '.params'); } delete n._paramsRaw; }
      });
    });
    return bad;
  }
  var serializeFlow = FDCore.serializeFlow;
  function toApiFlow() {
    var notifyEl = document.getElementById('fd-notify');
    var out = serializeFlow({ id: (idInput.value || rootFlow.id || '').trim(),
      name: nameInput.value || rootFlow.name || 'Untitled flow', start: rootFlow.start, nodes: rootFlow.nodes });
    out.notify = !!(notifyEl && notifyEl.checked);
    out.max_seconds = parseFloat((document.getElementById('fd-maxsec') || {}).value) || 0;
    out.max_dollars = parseFloat((document.getElementById('fd-maxdollars') || {}).value) || 0;
    out.schedule = ((document.getElementById('fd-schedule') || {}).value || '').trim();
    return out;
  }
  function fromApiFlow(f) {
    runState = {}; runCursor = null; impacts = {};
    stack = []; crumbs = ['Main flow']; renderCrumbs();
    signals = { nodes: {}, trigger: null };
    var sigEl = document.getElementById('fd-signal'); if (sigEl) sigEl.hidden = true;
    var lg = document.getElementById('fd-legend'); if (lg) lg.classList.remove('on');
    flow = { id: f.id || '', name: f.name || '', start: f.start || '', nodes: [], max_seconds: f.max_seconds || 0 };
    var notifyEl = document.getElementById('fd-notify'); if (notifyEl) notifyEl.checked = !!f.notify;
    var msEl = document.getElementById('fd-maxsec'); if (msEl) msEl.value = f.max_seconds || '';
    var mdEl = document.getElementById('fd-maxdollars'); if (mdEl) mdEl.value = f.max_dollars || '';
    var schEl = document.getElementById('fd-schedule'); if (schEl) schEl.value = f.schedule || '';
    (f.nodes || []).forEach(function (nd, i) { flow.nodes.push(normalizeNode(nd, i)); });
    rootFlow = flow;
    autoLayout();
    nameInput.value = flow.name; idInput.value = flow.id;
    selected = null; selectedEdge = null; view = { x: 40, y: 20, k: 1 }; render(); renderProps();
  }
  function autoLayout() {
    var need = flow.nodes.some(function (n) { return !n.x && !n.y; });
    if (!need) return;
    var col = {}, depth = {}, seen = {};
    var order = [], queue = flow.start ? [flow.start] : (flow.nodes[0] ? [flow.nodes[0].id] : []);
    while (queue.length) {
      var id = queue.shift(); if (seen[id]) continue; seen[id] = 1;
      var n = nodeById(id); if (!n) continue; order.push(n);
      var d = depth[id] || 0;
      ['next', 'if_true', 'if_false'].forEach(function (k) { if (n[k] && !seen[n[k]]) { depth[n[k]] = d + 1; queue.push(n[k]); } });
    }
    flow.nodes.forEach(function (n) { if (!(n.id in depth) && n.id !== flow.start) depth[n.id] = 0; });
    var rowCount = {};
    flow.nodes.forEach(function (n) {
      var d = depth[n.id] || 0; rowCount[d] = (rowCount[d] || 0);
      n.x = 60 + rowCount[d] * 220; n.y = 40 + d * 110; rowCount[d]++;
    });
  }

  function apiFetch(url, opts) {
    return fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
  }
  function slugify(s) {
    return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60);
  }
  function save(cb) {
    var bad = commitRawFields();
    if (bad.length) { say('Fix invalid JSON in: ' + bad.join(', '), true); return; }
    var body = toApiFlow();
    if (!body.id) { body.id = slugify(body.name); idInput.value = body.id; }   // auto-id from the name
    if (!body.id) { say('Give the flow a name (top-left).', true); return; }
    if (!body.nodes.length) { say('Add at least one step.', true); return; }
    say('Saving…');
    apiFetch('/api/v1/flows', { method: 'POST', body: JSON.stringify({ flow: body }) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) { say(res.j.detail || 'Save failed', true); return; }
        rootFlow.id = body.id; say('Saved ✓'); if (cb) cb();
      }).catch(function () { say('Save failed', true); });
  }
  function run(dryRun) {
    var data = {};
    var raw = document.getElementById('fd-sample').value.trim();
    if (raw) { try { data = JSON.parse(raw); } catch (e) { say('Sample input is not valid JSON.', true); return; } }
    if (!dryRun && !window.confirm('Run for real? This creates real goals and calls real connectors.')) return;
    save(function () {
      apiFetch('/api/v1/flows/' + encodeURIComponent(rootFlow.id) + '/run', { method: 'POST', body: JSON.stringify({ data: data, dry_run: !!dryRun }) })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j.run_id) { say('Run started'); return; }
          runState = {}; runCursor = null; lastRunId = j.run_id;
          say((dryRun ? '🧪 Dry run ' : 'Running ') + '(run ' + j.run_id.slice(0, 8) + '…)');
          document.getElementById('fd-legend').classList.add('on');
          pollRun(j.run_id, 20);
        }).catch(function () { say('Run failed', true); });
    });
  }
  function runUrl(runId) {
    return '/flows/' + encodeURIComponent(rootFlow.id) + '/runs/' + encodeURIComponent(runId);
  }
  function sayWithRunLink(text, runId) {
    say(text);
    var a = document.createElement('a'); a.href = runUrl(runId); a.textContent = 'view run →';
    a.style.marginLeft = '6px';
    msg.appendChild(a);
  }
  function pollRun(runId, tries) {
    apiFetch('/api/v1/flows/runs/' + encodeURIComponent(runId))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (run) {
        if (!run) return;
        runState = run.nodes || {};
        runCursor = (run.status === 'paused_approval' || run.status === 'paused_delay') ? run.cursor : null;
        render(); if (selected) renderProps();
        if (run.status === 'paused_approval') { sayWithRunLink('⏸ waiting for approval —', runId); return; }
        if (run.status === 'failed') {
          sayWithRunLink(runSummary(run) + ' · ask the 💬 Copilot “why did it fail?” ·', runId);
          return;
        }
        if (['completed', 'rejected'].indexOf(run.status) >= 0) { sayWithRunLink(runSummary(run) + ' ·', runId); return; }
        if (tries > 0) setTimeout(function () { pollRun(runId, tries - 1); }, 900);
        else sayWithRunLink('Still running —', runId);
      }).catch(function () {});
  }
  function runSummary(run) {
    var ok = 0, err = 0, ns = run.nodes || {};
    for (var k in ns) { if (ns[k].outcome === 1) ok++; else if (ns[k].outcome === 0) err++; }
    var head = run.status === 'completed' ? '✓ completed' : (run.status === 'rejected' ? '✋ rejected' : '⚠ ' + (run.error || run.status));
    var cost = run.cost_dollars ? ' · $' + Number(run.cost_dollars).toFixed(2) : '';
    var origin = (run.origin && run.origin !== 'manual') ? ' · via ' + run.origin : '';
    return head + ' · ' + ok + ' ok' + (err ? ', ' + err + ' failed' : '') + cost + origin;
  }
  function draft() {
    var desc = document.getElementById('fd-nl').value.trim();
    if (!desc) { say('Describe your workflow first.', true); return; }
    say('Drafting… (a few seconds)');
    apiFetch('/api/v1/flows/draft', { method: 'POST', body: JSON.stringify({ description: desc, flow_id: idInput.value || '' }) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) { say(res.j.detail || 'Draft failed', true); return; }
        pushHistory();                    // the pre-draft canvas is one Ctrl+Z away
        fromApiFlow(res.j.flow);
        say((res.j.notes && res.j.notes.length) ? res.j.notes[0] : 'Drafted ✓ — edit and save.');
      }).catch(function () { say('Draft failed', true); });
  }
  function load(id) {
    apiFetch('/api/v1/flows/' + encodeURIComponent(id)).then(function (r) {
      if (!r.ok) { say('Could not load flow', true); return; }
      return r.json().then(fromApiFlow);
    }).catch(function () { say('Could not load flow', true); });
    loadSuggestions(id);
  }
  function loadSuggestions(id) {
    // self-rewrite suggestions (harden a reliable agent / soften a flaky action)
    apiFetch('/api/v1/flows/' + encodeURIComponent(id) + '/proposals').then(function (r) {
      return r.ok ? r.json() : { proposals: [] };
    }).then(function (j) {
      proposals = {};
      (j.proposals || []).forEach(function (p) { proposals[p.node_id] = p; });
      if (j.proposals && j.proposals.length) say('💡 ' + j.proposals.length + ' learned suggestion(s) — select a node to see them.');
      if (selected) renderProps();
    }).catch(function () {});
    // measured impact of changes already applied (before/after), via the aggregate
    apiFetch('/api/v1/flows/insights').then(function (r) { return r.ok ? r.json() : { flows: [] }; })
      .then(function (j) {
        impacts = {};
        (j.flows || []).forEach(function (f) {
          if (f.flow_id !== id) return;
          (f.changes || []).forEach(function (ch) { impacts[ch.node_id] = { changed: true, applied: ch, impact: ch.impact }; });
        });
        if (selected) renderProps();
      }).catch(function () {});
    // grounded learning signals: per-node success, approval accept-rate, trigger reliability
    apiFetch('/api/v1/flows/' + encodeURIComponent(id) + '/signals').then(function (r) {
      return r.ok ? r.json() : { nodes: {}, trigger: null };
    }).then(function (j) {
      signals = { nodes: j.nodes || {}, trigger: j.trigger || null };
      var lg = document.getElementById('fd-signal');
      if (lg) {
        if (signals.trigger && signals.trigger.n) {
          lg.textContent = '📊 triggered runs succeed ' + Math.round(signals.trigger.mean * 100) +
            '% (' + signals.trigger.n + ')';
          lg.hidden = false;
        } else { lg.hidden = true; }
      }
      if (selected) renderProps();
    }).catch(function () {});
  }
  function applyProposal(n, prop) {
    // Persisted apply: save the current graph, then enact the swap server-side so
    // it becomes a new version and its before/after impact is measured. Hardening
    // needs a tool: the node's own, else the one the loop inferred it keeps calling.
    var tool = n.tool || (prop.inferred_tool || '');
    if (prop.to_kind === 'action' && !tool) {
      say('Give this step a Tool first, then Apply (an action needs one).', true); return;
    }
    save(function () {
      var body = { node_id: n.id, to_kind: prop.to_kind, tool: tool, brief: n.brief || n.label || '' };
      apiFetch('/api/v1/flows/' + encodeURIComponent(rootFlow.id) + '/apply', { method: 'POST', body: JSON.stringify(body) })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok) { say(res.j.detail || 'Apply failed', true); return; }
          delete proposals[n.id];
          say('Applied ✓ — now v' + res.j.version + '. Impact appears as new runs land.');
          load(rootFlow.id);                          // reload the new version + refresh
        }).catch(function () { say('Apply failed', true); });
    });
  }
  function showHistory() {
    if (!rootFlow.id) { say('Save the flow first.', true); return; }
    apiFetch('/api/v1/flows/' + encodeURIComponent(rootFlow.id) + '/versions')
      .then(function (r) { return r.ok ? r.json() : { versions: [] }; })
      .then(function (j) {
        props.textContent = '';
        var h = document.createElement('div'); h.style.fontWeight = '600'; h.style.marginBottom = 'var(--space-3)';
        h.textContent = '⏱ Version history';
        props.appendChild(h);
        (j.versions || []).forEach(function (v) {
          var row = document.createElement('div'); row.className = 'field';
          var kinds = Object.keys(v.kinds || {}).map(function (k) { return v.kinds[k] + ' ' + k; }).join(', ');
          var lbl = document.createElement('div'); lbl.textContent = 'v' + v.version + ' — ' + v.nodes + ' nodes (' + kinds + ')';
          row.appendChild(lbl);
          var rb = document.createElement('button'); rb.className = 'btn btn--ghost'; rb.type = 'button'; rb.textContent = 'Roll back to v' + v.version;
          rb.addEventListener('click', function () { rollback(v.version); });
          row.appendChild(rb); props.appendChild(row);
        });
      }).catch(function () { say('Could not load history', true); });
  }
  function rollback(version) {
    apiFetch('/api/v1/flows/' + encodeURIComponent(rootFlow.id) + '/rollback', { method: 'POST', body: JSON.stringify({ version: version }) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) { say(res.j.detail || 'Rollback failed', true); return; }
        say('Rolled back — now v' + res.j.version + '.'); load(rootFlow.id);
      }).catch(function () { say('Rollback failed', true); });
  }

  var EXAMPLES = [
    'When a new GitHub issue is opened, summarize it and post to Slack, but ask me before posting',
    'Every morning, pull yesterday’s Stripe charges and email me a one-paragraph summary',
    'When a HubSpot deal is marked won, create an onboarding task in Asana and notify the team',
  ];
  function buildExamples() {
    var box = document.getElementById('fd-examples'); if (!box) return;
    EXAMPLES.forEach(function (ex) {
      var c = document.createElement('button'); c.type = 'button'; c.className = 'fd__chip';
      c.textContent = '✨ ' + trunc(ex, 40); c.title = ex;
      c.addEventListener('click', function () { var nl = document.getElementById('fd-nl'); nl.value = ex; nl.focus(); });
      box.appendChild(c);
    });
  }
  // ---- copilot chat: converse to build, edit, explain, and repair the flow ----
  function chatMsgEl(role, text) {
    var d = document.createElement('div'); d.className = 'fd__chat-msg ' + role; d.textContent = text; return d;
  }
  function chatAppend(role, text) {
    var log = document.getElementById('fd-chat-log');
    log.appendChild(chatMsgEl(role, text));
    log.scrollTop = log.scrollHeight;
  }
  function placeNewNodes(f) {
    // Give canvas positions to nodes the copilot just added (patched nodes keep
    // theirs), so an AI edit lands NEXT TO its neighbors instead of re-laying
    // out the user's whole arrangement.
    var byId = {};
    (f.nodes || []).forEach(function (n) { byId[n.id] = n; });
    var maxY = 40;
    (f.nodes || []).forEach(function (n) { if (n.y) maxY = Math.max(maxY, n.y); });
    (f.nodes || []).forEach(function (n) {
      if (n.x || n.y) return;
      var pred = (f.nodes || []).filter(function (m) {
        return m !== n && (m.next === n.id || m.if_true === n.id || m.if_false === n.id || m.on_error === n.id);
      })[0];
      if (pred && (pred.x || pred.y)) { n.x = (pred.x || 0) + 24; n.y = (pred.y || 0) + 110; }
      else { n.x = 60; n.y = maxY + 110; }
      maxY = Math.max(maxY, n.y);
    });
  }
  function chatSend() {
    var inp = document.getElementById('fd-chat-input');
    var text = (inp.value || '').trim();
    if (!text) return;
    var bad = commitRawFields();
    if (bad.length) { say('Fix invalid JSON in: ' + bad.join(', '), true); return; }
    inp.value = '';
    chatAppend('user', text);
    chatHistory.push({ role: 'user', content: text });
    chatAppend('note', '…thinking');
    var body = { message: text, flow: toApiFlow(), history: chatHistory.slice(-12), run_id: lastRunId || '' };
    apiFetch('/api/v1/flows/chat', { method: 'POST', body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        var log = document.getElementById('fd-chat-log');
        log.removeChild(log.lastChild);                       // the …thinking note
        if (!res.ok) { chatAppend('note', res.j.detail || 'The copilot call failed.'); return; }
        var j = res.j;
        chatAppend('assistant', j.reply || 'Done.');
        chatHistory.push({ role: 'assistant', content: j.reply || '' });
        (j.notes || []).forEach(function (nt) { chatAppend('note', nt); });
        if (j.flow) {
          pushHistory();                                      // "undo what the AI did" is one Ctrl+Z
          placeNewNodes(j.flow);
          var keep = { x: view.x, y: view.y, k: view.k };
          fromApiFlow(j.flow);
          view = keep; render();
          if (j.applied && j.applied.length) chatAppend('note', '✏️ ' + j.applied.join(' · '));
          say('🤖 Copilot edited the flow — review and Save.');
        }
      }).catch(function () {
        var log = document.getElementById('fd-chat-log');
        if (log.lastChild && log.lastChild.textContent === '…thinking') log.removeChild(log.lastChild);
        chatAppend('note', 'The copilot call failed.');
      });
  }
  function chatToggle(showOnly) {
    var box = document.getElementById('fd-chat');
    if (showOnly) box.classList.add('on'); else box.classList.toggle('on');
    if (box.classList.contains('on')) document.getElementById('fd-chat-input').focus();
  }

  function loadTools() {
    apiFetch('/api/v1/flows/tools').then(function (r) { return r.ok ? r.json() : { tools: [] }; })
      .then(function (j) {
        var dl = document.getElementById('fd-tools'); if (dl) dl.textContent = '';
        (j.tools || []).forEach(function (t) {
          toolCatalog[t.name] = { description: t.description || '', params: t.params || [] };
          if (dl) { var o = document.createElement('option'); o.value = t.name; if (t.description) o.label = t.description; dl.appendChild(o); }
        });
        if (selected) renderProps();
      }).catch(function () {});
  }

  // ---- wire ----
  document.querySelectorAll('[data-add]').forEach(function (b) {
    b.addEventListener('click', function () { addNode(b.getAttribute('data-add')); });
  });
  document.getElementById('fd-draft').addEventListener('click', draft);
  document.getElementById('fd-save').addEventListener('click', function () { save(); });
  document.getElementById('fd-run').addEventListener('click', function () { run(true); });
  document.getElementById('fd-run-real').addEventListener('click', function () { run(false); });
  document.getElementById('fd-history').addEventListener('click', showHistory);
  document.getElementById('fd-undo').addEventListener('click', undo);
  document.getElementById('fd-redo').addEventListener('click', redo);
  document.getElementById('fd-fit').addEventListener('click', zoomToFit);
  document.getElementById('fd-chat-toggle').addEventListener('click', function () { chatToggle(); });
  document.getElementById('fd-chat-close').addEventListener('click', function () { chatToggle(); });
  document.getElementById('fd-chat-form').addEventListener('submit', function (e) { e.preventDefault(); chatSend(); });
  document.getElementById('fd-nl').addEventListener('keydown', function (e) { if (e.key === 'Enter') draft(); });
  nameInput.addEventListener('input', function () { rootFlow.name = nameInput.value; });
  // keyboard: Delete removes the selected node/edge, Esc deselects, Ctrl+Z/Y
  // undo/redo, Ctrl+D duplicates (all no-ops while typing in a field)
  document.addEventListener('keydown', function (e) {
    var tag = (e.target && e.target.tagName) || '';
    var typing = tag === 'INPUT' || tag === 'TEXTAREA' || (e.target && e.target.isContentEditable);
    var mod = e.ctrlKey || e.metaKey;
    if (mod && !typing && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if (mod && !typing && (e.key === 'y' || e.key === 'Y')) { e.preventDefault(); redo(); return; }
    if (mod && !typing && (e.key === 'd' || e.key === 'D') && selected) { e.preventDefault(); duplicateNode(selected); return; }
    if (e.key === 'Escape' && !typing) { selected = null; selectedEdge = null; render(); renderProps(); }
    else if ((e.key === 'Delete' || e.key === 'Backspace') && !typing) {
      if (selectedEdge) { e.preventDefault(); deleteEdge(selectedEdge); }
      else if (selected) { e.preventDefault(); deleteNode(selected); }
    }
  });

  buildExamples();
  loadTools();
  var editId = canvas.getAttribute('data-flow-id');
  if (editId) load(editId); else { render(); renderProps(); }
})();
{% endraw %}
