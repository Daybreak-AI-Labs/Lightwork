/* Daybreak Labs site — the stage.
   1. Home hero: circuit traces in the brand gradient draw themselves in,
      agent "actions" race along them through five gates into the sunrise;
      the ones that pass are signed, the ones that fail stop at a gate.
   2. Cortex lattice (canvas.lattice): the company brain, a slowly turning
      lattice whose nodes light as the workforce writes to it.
   3. Scroll reveals for .rv elements.
   Pure canvas, no dependencies. Honors prefers-reduced-motion (one static
   frame, no reveal animation) and pauses when scrolled out of view. */
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var MONO = "11px 'JetBrains Mono', ui-monospace, Menlo, monospace";
  var GRAD = [[47, 124, 255], [139, 92, 246], [255, 138, 43]];
  function mix(t) { t = Math.max(0, Math.min(1, t)); var i = t < .5 ? 0 : 1, f = t < .5 ? t * 2 : (t - .5) * 2, a = GRAD[i], b = GRAD[i + 1];
    return [a[0] + (b[0] - a[0]) * f | 0, a[1] + (b[1] - a[1]) * f | 0, a[2] + (b[2] - a[2]) * f | 0]; }

  /* ---- 1. the stage ---- */
  var stage = document.querySelector("canvas.neon");
  if (stage && stage.getContext) (function () {
    var ctx = stage.getContext("2d"), W, H, dpr = Math.min(window.devicePixelRatio || 1, 1.5), traces = [], parts = [], t0 = performance.now();
    var sun = { x: 0, y: 0 }, signed = 0, denied = 0, visible = true, raf = 0, lastT = 0;
    var GX = [0.42, 0.50, 0.58, 0.66, 0.74], GATES = ["CAPABILITY", "POLICY", "SHIELD", "BUDGET", "AUDIT"];
    var elSigned = document.getElementById("hud-signed"), elDenied = document.getElementById("hud-denied");
    function build() {
      W = stage.clientWidth; H = stage.clientHeight; stage.width = W * dpr; stage.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      sun = { x: W * .86, y: H * .46 }; traces = []; parts = [];
      for (var i = 0; i < 34; i++) {
        var y = H * .06 + Math.random() * H * .8, x = -20, pts = [[x, y]];
        while (x < sun.x - 60) {
          x += 60 + Math.random() * 180; pts.push([x, y]);
          if (Math.random() < .55 && x < sun.x - 200) { var ny = Math.max(H * .05, Math.min(H * .9, y + (Math.random() < .5 ? -1 : 1) * (30 + Math.random() * 90))); x += Math.abs(ny - y); pts.push([x, ny]); y = ny; }
        }
        var ang = Math.atan2(sun.y - y, sun.x - x); pts.push([sun.x - Math.cos(ang) * 70, sun.y - Math.sin(ang) * 70]);
        var L = [0]; for (var k = 1; k < pts.length; k++) L.push(L[k - 1] + Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]));
        traces.push({ pts: pts, L: L, total: L[L.length - 1], delay: Math.random() * .9 });
      }
    }
    function at(tr, d) { var L = tr.L, p = tr.pts; if (d >= tr.total) return p[p.length - 1]; for (var k = 1; k < L.length; k++) if (d <= L[k]) { var f = (d - L[k - 1]) / (L[k] - L[k - 1] || 1); return [p[k - 1][0] + (p[k][0] - p[k - 1][0]) * f, p[k - 1][1] + (p[k][1] - p[k - 1][1]) * f]; } return p[p.length - 1]; }
    function partial(tr, frac) { var d = tr.total * frac, out = [tr.pts[0]]; for (var k = 1; k < tr.L.length; k++) { if (tr.L[k] <= d) out.push(tr.pts[k]); else { out.push(at(tr, d)); break; } } return out; }
    function draw(now) {
      var el = (now - t0) / 1000, dt = Math.min(.05, (now - lastT) / 1000 || 0); lastT = now; var prog = reduce ? 1 : Math.min(1, el / 2.6);
      ctx.clearRect(0, 0, W, H);
      // sunrise
      var g = ctx.createRadialGradient(sun.x, sun.y, 0, sun.x, sun.y, H * .55);
      g.addColorStop(0, "rgba(255,150,60," + (.55 * prog) + ")"); g.addColorStop(.25, "rgba(255,120,40," + (.22 * prog) + ")"); g.addColorStop(.6, "rgba(139,92,246," + (.08 * prog) + ")"); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      ctx.save(); ctx.translate(sun.x, sun.y); ctx.lineWidth = 1;
      for (var r = 0; r < 18; r++) { var a = (r / 18) * Math.PI * 2 + el * .03; ctx.strokeStyle = "rgba(255,170,90," + (.10 * prog) + ")"; ctx.beginPath(); ctx.moveTo(Math.cos(a) * 90, Math.sin(a) * 90); ctx.lineTo(Math.cos(a) * (150 + ((r * 37) % 80)), Math.sin(a) * (150 + ((r * 37) % 80))); ctx.stroke(); }
      ctx.restore();
      // gates
      for (var gi = 0; gi < 5; gi++) { var gx = W * GX[gi]; ctx.strokeStyle = "rgba(124,155,255," + (.13 * prog) + ")"; ctx.setLineDash([3, 7]); ctx.beginPath(); ctx.moveTo(gx, H * .05); ctx.lineTo(gx, H * .9); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = "rgba(180,200,255," + (.75 * prog) + ")"; ctx.font = MONO; ctx.textAlign = "center"; ctx.fillText(GATES[gi], gx, H * .93); }
      // traces: one horizontal brand gradient shared by every trace
      ctx.lineCap = "round"; ctx.lineJoin = "round";
      var lgs = [.16, .9].map(function (al) { var lg = ctx.createLinearGradient(0, 0, sun.x, 0); lg.addColorStop(0, "rgba(47,124,255," + al + ")"); lg.addColorStop(.5, "rgba(139,92,246," + al + ")"); lg.addColorStop(1, "rgba(255,138,43," + al + ")"); return lg; });
      for (var i = 0; i < traces.length; i++) { var tr = traces[i], f = reduce ? 1 : Math.max(0, Math.min(1, (el - tr.delay) / 2.2)); if (f <= 0) continue; var pts = partial(tr, f);
        for (var pass = 0; pass < 2; pass++) { ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); for (var k = 1; k < pts.length; k++) ctx.lineTo(pts[k][0], pts[k][1]); ctx.strokeStyle = lgs[pass]; ctx.lineWidth = pass ? 1.2 : 6; ctx.stroke(); }
        for (k = 1; k < pts.length - 1; k++) { ctx.strokeStyle = "rgba(255,255,255,.2)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(pts[k][0], pts[k][1], 2.4, 0, 6.283); ctx.stroke(); }
        var e = pts[pts.length - 1]; ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(e[0], e[1], 1.8, 0, 6.283); ctx.fill(); }
      // actions
      if (!reduce && prog >= 1) {
        if (Math.random() < .11) { var t = traces[Math.random() * traces.length | 0]; parts.push({ tr: t, d: 0, v: 260 + Math.random() * 220, fate: Math.random() < .16 ? "deny" : "ok", gate: 1 + (Math.random() * 3 | 0), dead: 0 }); }
        for (var p = parts.length - 1; p >= 0; p--) { var a2 = parts[p]; if (a2.dead) { a2.dead += dt; if (a2.dead > .7) parts.splice(p, 1); continue; } a2.d += a2.v * dt; var pos = at(a2.tr, a2.d);
          if (a2.fate === "deny" && pos[0] >= W * GX[a2.gate]) { a2.dead = .01; a2.pos = pos; denied++; if (elDenied) elDenied.textContent = denied + " denied at the gate"; continue; }
          if (a2.d >= a2.tr.total) { parts.splice(p, 1); signed++; if (elSigned) elSigned.textContent = signed.toLocaleString(); continue; } a2.pos = pos; }
      }
      for (p = 0; p < parts.length; p++) { var q = parts[p], pos2 = q.pos; if (!pos2) continue; var col = q.dead ? "255,92,92" : "255,255,255";
        var gg = ctx.createRadialGradient(pos2[0], pos2[1], 0, pos2[0], pos2[1], 9); gg.addColorStop(0, "rgba(" + col + ",.65)"); gg.addColorStop(1, "rgba(" + col + ",0)"); ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(pos2[0], pos2[1], 9, 0, 6.283); ctx.fill();
        ctx.fillStyle = "rgba(" + col + ",1)"; ctx.beginPath(); ctx.arc(pos2[0], pos2[1], 2.2, 0, 6.283); ctx.fill();
        if (q.dead) { ctx.fillStyle = "#FF5C5C"; ctx.font = MONO; ctx.textAlign = "center"; ctx.fillText("DENY", pos2[0], pos2[1] - 14); } }
      if (!reduce) raf = visible ? requestAnimationFrame(draw) : 0;
    }
    build(); window.addEventListener("resize", build);
    if ("IntersectionObserver" in window) new IntersectionObserver(function (es) { visible = es[0].isIntersecting; if (visible && !raf && !reduce) { lastT = performance.now(); raf = requestAnimationFrame(draw); } }, { threshold: .02 }).observe(stage);
    raf = requestAnimationFrame(draw);
  })();

  /* ---- 2. Cortex: a fibonacci lattice, lit by the workforce ---- */
  var solos = document.querySelectorAll("canvas.lattice");
  Array.prototype.forEach.call(solos, function (cv) {
    if (!cv.getContext) return;
    var ctx = cv.getContext("2d"), W = 640, H = 480, dpr = Math.min(window.devicePixelRatio || 1, 2), n = 520, pts = [], links = [], lit = [], t = 3, raf = 0, visible = true, last = 0;
    cv.width = W * dpr; cv.height = H * dpr; ctx.scale(dpr, dpr);
    for (var i = 0; i < n; i++) { var y = 1 - (i / (n - 1)) * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963; pts.push([Math.cos(th) * r, y, Math.sin(th) * r]); lit.push(0); }
    for (i = 0; i < n; i++) { var best = []; for (var j = 0; j < n; j++) { if (i === j) continue; var p = pts[i], q = pts[j], d = (p[0] - q[0]) * (p[0] - q[0]) + (p[1] - q[1]) * (p[1] - q[1]) + (p[2] - q[2]) * (p[2] - q[2]);
      if (best.length < 3 || d < best[2][0]) { best.push([d, j]); best.sort(function (a, b) { return a[0] - b[0]; }); if (best.length > 3) best.length = 3; } }
      for (var k = 0; k < best.length; k++) if (best[k][1] > i) links.push([i, best[k][1]]); }
    function draw(now) {
      var dt = Math.min(.05, (now - last) / 1000 || 0); last = now; t += dt;
      ctx.clearRect(0, 0, W, H); ctx.fillStyle = "#0A0D15"; ctx.fillRect(0, 0, W, H);
      var g = ctx.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, H * .6); g.addColorStop(0, "rgba(139,92,246,.22)"); g.addColorStop(.5, "rgba(255,138,43,.08)"); g.addColorStop(1, "rgba(0,0,0,0)"); ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      var R = H * .36, cx = W / 2, cy = H / 2, ay = t * .12, ax = Math.sin(t * .07) * .35, P = [];
      for (var i = 0; i < n; i++) { var p = pts[i], x = p[0] * Math.cos(ay) - p[2] * Math.sin(ay), z = p[0] * Math.sin(ay) + p[2] * Math.cos(ay), y = p[1] * Math.cos(ax) - z * Math.sin(ax); z = p[1] * Math.sin(ax) + z * Math.cos(ax); var s = 1 / (1.9 - z * .8); P.push([cx + x * R * s, cy + y * R * s, z, s]); }
      ctx.lineWidth = 1;
      for (var l = 0; l < links.length; l++) { var a = P[links[l][0]], b = P[links[l][1]], c = mix(((a[2] + b[2]) / 2 + 1) / 2), al = .10 + ((a[2] + b[2]) / 2 + 1) / 2 * .5; ctx.strokeStyle = "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + al + ")"; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
      if (!reduce && Math.random() < .08) { var pick = Math.random() * n | 0; if (P[pick][2] > -.1) lit[pick] = 1; }
      for (i = 0; i < n; i++) { var q2 = P[i], cc = mix((q2[2] + 1) / 2);
        if (lit[i] > .02) { var gg = ctx.createRadialGradient(q2[0], q2[1], 0, q2[0], q2[1], 10 + lit[i] * 10); gg.addColorStop(0, "rgba(255,255,255," + lit[i] * .9 + ")"); gg.addColorStop(1, "rgba(255,255,255,0)"); ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(q2[0], q2[1], 10 + lit[i] * 10, 0, 6.283); ctx.fill(); lit[i] *= .96; }
        ctx.fillStyle = "rgba(" + cc[0] + "," + cc[1] + "," + cc[2] + "," + (.3 + (q2[2] + 1) / 2 * .7) + ")"; ctx.beginPath(); ctx.arc(q2[0], q2[1], 1.1 + q2[3] * 1.5, 0, 6.283); ctx.fill(); }
      ctx.fillStyle = "#8E97AC"; ctx.font = MONO; ctx.textAlign = "left"; ctx.fillText("CORTEX · documents · decisions · lessons · the record", 20, H - 18);
      if (!reduce) raf = visible ? requestAnimationFrame(draw) : 0;
    }
    if ("IntersectionObserver" in window) new IntersectionObserver(function (es) { visible = es[0].isIntersecting; if (visible && !raf && !reduce) { last = performance.now(); raf = requestAnimationFrame(draw); } }, { threshold: .05 }).observe(cv);
    raf = requestAnimationFrame(draw);
  });

  /* ---- 3. reveals ---- */
  var rv = document.querySelectorAll(".rv");
  if (rv.length) { if (reduce || !("IntersectionObserver" in window)) { for (var i = 0; i < rv.length; i++) rv[i].classList.add("in"); }
    else { var io = new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }); }, { threshold: .12, rootMargin: "0px 0px -8% 0px" }); for (i = 0; i < rv.length; i++) io.observe(rv[i]); } }
})();
