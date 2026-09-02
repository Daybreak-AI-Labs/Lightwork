/* Daybreak Labs site — the "gate instrument" figure.
   Draws a live schematic of the one thing the platform does: every agent
   action crosses five gates (capability, policy, shield, budget, audit)
   before it happens, and the ones that pass land in a signed record.
   Pure canvas, no dependencies. Honors prefers-reduced-motion (draws a
   single static frame) and pauses when scrolled out of view. */
(function () {
  "use strict";
  var canvas = document.querySelector("canvas.lab-canvas");
  if (!canvas || !canvas.getContext) return;
  var ctx = canvas.getContext("2d");
  var W = 640, H = 400, dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = W * dpr; canvas.height = H * dpr; ctx.scale(dpr, dpr);

  var C = { line: "#28303D", faint: "#5A6476", muted: "#8A94A8", text: "#C3CAD7",
            blue: "#3D8BFF", ok: "#3DDC97", deny: "#FF6B6B", warn: "#FFB547", panel: "#0F131A" };
  var GATES = ["CAP", "POL", "SHD", "BUD", "AUD"];
  var gateX = [250, 300, 350, 400, 450];           // x of each gate bar
  var laneY = [92, 142, 192, 242, 292];             // action lanes
  var nodes = [];                                   // the agent lattice, left
  for (var r = 0; r < 5; r++) for (var c = 0; c < 4; c++) nodes.push({ x: 48 + c * 46, y: 92 + r * 50 });
  var ledger = [];                                  // signed rows, right
  var actions = [];
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var mono = "11px 'JetBrains Mono', ui-monospace, Menlo, monospace";

  function hash() { var s = ""; for (var i = 0; i < 6; i++) s += "0123456789abcdef"[Math.random() * 16 | 0]; return s; }

  function spawn() {
    var lane = Math.random() * 5 | 0;
    var n = nodes[Math.random() * nodes.length | 0];
    var roll = Math.random();
    var fate = roll < 0.18 ? "deny" : roll < 0.34 ? "human" : "ok";
    var at = fate === "deny" ? [1, 2, 3][Math.random() * 3 | 0] : fate === "human" ? 1 : 4;
    actions.push({ x: n.x, y: n.y, ty: laneY[lane], lane: lane, fate: fate, at: at, t: 0, state: "travel", hold: 0, done: false });
  }

  function step(dt) {
    for (var i = actions.length - 1; i >= 0; i--) {
      var a = actions[i];
      if (a.state === "travel") {
        a.y += (a.ty - a.y) * Math.min(1, dt * 6);
        a.x += 120 * dt;
        var g = -1;
        for (var k = 0; k < 5; k++) if (Math.abs(a.x - gateX[k]) < 2) g = k;
        if (g === a.at && a.fate === "deny") { a.state = "denied"; a.x = gateX[g]; a.hold = 0; }
        else if (g === a.at && a.fate === "human" && !a.held) { a.state = "held"; a.x = gateX[g]; a.hold = 0; a.held = true; }
        else if (a.x > 500) {
          a.state = "landed";
          ledger.unshift({ h: hash(), fate: a.fate === "human" ? "human" : "ok", t: 0 });
          if (ledger.length > 7) ledger.pop();
          actions.splice(i, 1);
        }
      } else if (a.state === "denied") {
        a.hold += dt; if (a.hold > 0.9) actions.splice(i, 1);
      } else if (a.state === "held") {
        a.hold += dt; if (a.hold > 1.1) a.state = "travel";
      }
    }
    for (var j = 0; j < ledger.length; j++) ledger[j].t += dt;
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = C.panel; ctx.fillRect(0, 0, W, H);
    // lattice
    ctx.strokeStyle = C.line; ctx.lineWidth = 1;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if ((i + 1) % 4) { ctx.beginPath(); ctx.moveTo(n.x, n.y); ctx.lineTo(nodes[i + 1].x, nodes[i + 1].y); ctx.stroke(); }
      if (i + 4 < nodes.length) { ctx.beginPath(); ctx.moveTo(n.x, n.y); ctx.lineTo(nodes[i + 4].x, nodes[i + 4].y); ctx.stroke(); }
    }
    for (i = 0; i < nodes.length; i++) { ctx.fillStyle = C.faint; ctx.beginPath(); ctx.arc(nodes[i].x, nodes[i].y, 2.6, 0, 6.283); ctx.fill(); }
    ctx.fillStyle = C.muted; ctx.font = mono; ctx.textAlign = "left";
    ctx.fillText("AGENTS · 2,020 PACKS", 48, 60);
    ctx.fillText("ONE GATE", 250, 60);
    ctx.fillText("OPERATING RECORD", 512, 60);
    // lanes
    ctx.strokeStyle = C.line; ctx.setLineDash([2, 5]);
    for (i = 0; i < 5; i++) { ctx.beginPath(); ctx.moveTo(196, laneY[i]); ctx.lineTo(500, laneY[i]); ctx.stroke(); }
    ctx.setLineDash([]);
    // gates
    for (i = 0; i < 5; i++) {
      ctx.strokeStyle = i === 4 ? C.ok : C.line; ctx.lineWidth = i === 4 ? 1.5 : 1;
      ctx.beginPath(); ctx.moveTo(gateX[i], 76); ctx.lineTo(gateX[i], 316); ctx.stroke();
      ctx.fillStyle = i === 4 ? C.ok : C.muted; ctx.textAlign = "center"; ctx.fillText(GATES[i], gateX[i], 336);
    }
    ctx.lineWidth = 1;
    // ledger
    ctx.textAlign = "left";
    for (i = 0; i < ledger.length; i++) {
      var row = ledger[i], y = 84 + i * 34, alpha = Math.min(1, row.t * 3);
      ctx.globalAlpha = alpha;
      ctx.fillStyle = row.fate === "human" ? C.warn : C.ok; ctx.fillRect(512, y - 4, 3, 18);
      ctx.fillStyle = C.text; ctx.fillText("0x" + row.h, 524, y + 9);
      ctx.fillStyle = row.fate === "human" ? C.warn : C.ok; ctx.fillText(row.fate === "human" ? "SIGNED·HUMAN" : "SIGNED", 524, y + 22);
      ctx.globalAlpha = 1;
      if (i < ledger.length - 1) { ctx.strokeStyle = C.line; ctx.beginPath(); ctx.moveTo(512, y + 27); ctx.lineTo(560, y + 27); ctx.stroke(); }
    }
    // actions
    for (i = 0; i < actions.length; i++) {
      var a = actions[i], col = a.state === "denied" ? C.deny : a.state === "held" ? C.warn : C.blue;
      ctx.strokeStyle = col; ctx.globalAlpha = 0.35; ctx.beginPath(); ctx.moveTo(Math.max(a.x - 40, 40), a.y); ctx.lineTo(a.x, a.y); ctx.stroke(); ctx.globalAlpha = 1;
      ctx.fillStyle = col; ctx.beginPath(); ctx.arc(a.x, a.y, 3.5, 0, 6.283); ctx.fill();
      if (a.state === "denied") { ctx.fillStyle = C.deny; ctx.textAlign = "center"; ctx.fillText("DENY", a.x, a.y - 10); ctx.textAlign = "left"; }
      if (a.state === "held") { ctx.fillStyle = C.warn; ctx.textAlign = "center"; ctx.fillText("REQUIRE_HUMAN", a.x, a.y - 10); ctx.textAlign = "left"; }
    }
    // base line
    ctx.strokeStyle = C.line; ctx.beginPath(); ctx.moveTo(48, 360); ctx.lineTo(592, 360); ctx.stroke();
    ctx.fillStyle = C.faint; ctx.fillText("fig. 01 · five gates on every action · signed · hash-chained · verifiable offline", 48, 380);
  }

  // static frame for reduced motion or before the first tick
  for (var s = 0; s < 4; s++) ledger.push({ h: hash(), fate: s === 2 ? "human" : "ok", t: 9 });
  actions.push({ x: 320, y: laneY[1], ty: laneY[1], lane: 1, fate: "ok", at: 4, t: 0, state: "travel" });
  actions.push({ x: gateX[2], y: laneY[3], ty: laneY[3], lane: 3, fate: "deny", at: 2, t: 0, state: "denied", hold: 0 });
  draw();
  if (reduce) return;

  var last = 0, acc = 0, visible = true, raf = 0;
  function frame(ts) {
    var dt = Math.min(0.05, (ts - last) / 1000 || 0); last = ts;
    acc += dt; if (acc > 1.3) { acc = 0; spawn(); }
    step(dt); draw();
    raf = visible ? requestAnimationFrame(frame) : 0;
  }
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (es) {
      visible = es[0].isIntersecting;
      if (visible && !raf) { last = performance.now(); raf = requestAnimationFrame(frame); }
    }, { threshold: 0.05 }).observe(canvas);
  }
  raf = requestAnimationFrame(frame);
})();
