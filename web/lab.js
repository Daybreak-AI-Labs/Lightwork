/* Daybreak Labs site — the stage.
   1. Circuit stage (canvas.neon): traces in the brand gradient draw
      themselves in, and work items race along them into the sunrise. The
      home hero runs it with five labelled stages and a live count; the
      inner-page heroes run a quieter version behind the headline
      (data-mode="ambient"). Both answer the pointer: the traces light up
      under it, and a click sends a burst of work down the nearest trace.
   2. Cortex lattice (canvas.lattice): the company brain, a slowly turning
      lattice whose nodes light as the workforce writes to it. The pointer
      tilts it and lights the nodes it passes; a click lights a cluster.
   3. Panel spotlight: a soft light follows the pointer across panels.
   4. Scroll reveals for .rv elements.
   Pure canvas, no dependencies. Honors prefers-reduced-motion (one static
   frame, no pointer effects, no reveal animation) and pauses when scrolled
   out of view. */
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var MONO = "11px 'JetBrains Mono', ui-monospace, Menlo, monospace";
  var GRAD = [[47, 124, 255], [139, 92, 246], [255, 138, 43]];
  var SKIP = "a,button,input,textarea,select,summary,label,[role=button]";
  function mix(t) { t = Math.max(0, Math.min(1, t)); var i = t < .5 ? 0 : 1, f = t < .5 ? t * 2 : (t - .5) * 2, a = GRAD[i], b = GRAD[i + 1];
    return [a[0] + (b[0] - a[0]) * f | 0, a[1] + (b[1] - a[1]) * f | 0, a[2] + (b[2] - a[2]) * f | 0]; }
  // distance from point p to segment a-b, and how far along the segment the nearest point is (0..1)
  function segDist(px, py, ax, ay, bx, by) { var dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy, t = L2 ? ((px - ax) * dx + (py - ay) * dy) / L2 : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t; var x = ax + dx * t - px, y = ay + dy * t - py; return [Math.sqrt(x * x + y * y), t, Math.sqrt(L2)]; }
  function skip(e) { return e.target && e.target.closest && e.target.closest(SKIP); }

  /* ---- 1. the stage ---- */
  Array.prototype.forEach.call(document.querySelectorAll("canvas.neon"), function (stage) {
    if (!stage.getContext) return;
    var ambient = stage.getAttribute("data-mode") === "ambient";
    var ctx = stage.getContext("2d"), W = 0, H = 0, dpr = Math.min(window.devicePixelRatio || 1, 1.5), traces = [], parts = [], rings = [], t0 = performance.now();
    var sun = { x: 0, y: 0 }, signed = 0, denied = 0, visible = true, raf = 0, lastT = 0, ptr = { x: 0, y: 0, on: false, a: 0 };
    var GX = [0.42, 0.50, 0.58, 0.66, 0.74], GATES = (stage.getAttribute("data-gates") || "CAPABILITY,POLICY,SHIELD,BUDGET,AUDIT").split(",");
    // data-stop-label / data-stop-tag / data-stop-rgb let a page say what a stopped action means (default: denied, red).
    var STOP_LABEL = stage.getAttribute("data-stop-label") || "denied at the gate", STOP_RGB = stage.getAttribute("data-stop-rgb") || "255,92,92", STOP_TAG = stage.getAttribute("data-stop-tag") || "DENY";
    var elSigned = ambient ? null : document.getElementById("hud-signed"), elDenied = ambient ? null : document.getElementById("hud-denied");
    var NT = ambient ? 22 : 34, SPAWN = ambient ? .06 : .11, STOP = ambient ? 0 : .16, LIGHT = 150;
    function build() {
      var w = stage.clientWidth, h = stage.clientHeight; if (!w || !h || (w === W && h === H)) return;
      W = w; H = h; stage.width = W * dpr; stage.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      sun = ambient ? { x: W * .94, y: H * .42 } : { x: W * .86, y: H * .46 }; traces = []; parts = [];
      for (var i = 0; i < NT; i++) {
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
    function launch(tr, d, v) { var x = at(tr, d)[0], g = -1;
      if (STOP && Math.random() < STOP) for (var gi = 1; gi < 4; gi++) if (W * GX[gi] > x + 8) { g = gi + (Math.random() * (4 - gi) | 0); break; }
      parts.push({ tr: tr, d: d, v: v, fate: g < 0 ? "ok" : "deny", gate: g, dead: 0 }); }
    // a click sends a burst of work down the trace nearest the pointer, from the point nearest the pointer
    function burst(x, y) { var best = null, bd = 1e9;
      for (var i = 0; i < traces.length; i++) { var p = traces[i].pts, L = traces[i].L;
        for (var k = 1; k < p.length; k++) { var s = segDist(x, y, p[k - 1][0], p[k - 1][1], p[k][0], p[k][1]); if (s[0] < bd) { bd = s[0]; best = [traces[i], L[k - 1] + s[1] * (L[k] - L[k - 1])]; } } }
      rings.push({ x: x, y: y, t: 0 }); if (!best) return;
      for (var n = 0; n < 5; n++) launch(best[0], best[1], 250 + n * 45 + Math.random() * 30); }
    // the part of a trace near the pointer glows, fading out along the trace
    function light(pts, k) {
      var ax = pts[k - 1][0], ay = pts[k - 1][1], bx = pts[k][0], by = pts[k][1], s = segDist(ptr.x, ptr.y, ax, ay, bx, by); if (s[0] >= LIGHT || !s[2]) return;
      var half = Math.sqrt(LIGHT * LIGHT - s[0] * s[0]) / s[2], t1 = Math.max(0, s[1] - half), t2 = Math.min(1, s[1] + half), w = (1 - s[0] / LIGHT) * ptr.a;
      var x1 = ax + (bx - ax) * t1, y1 = ay + (by - ay) * t1, x2 = ax + (bx - ax) * t2, y2 = ay + (by - ay) * t2, c = mix(((x1 + x2) / 2) / sun.x).join(",");
      var mid = Math.max(.01, Math.min(.99, (s[1] - t1) / ((t2 - t1) || 1))), lg = ctx.createLinearGradient(x1, y1, x2, y2);
      lg.addColorStop(0, "rgba(" + c + ",0)"); lg.addColorStop(mid, "rgba(" + c + "," + (.55 * w) + ")"); lg.addColorStop(1, "rgba(" + c + ",0)");
      ctx.strokeStyle = lg; ctx.lineWidth = 8; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      lg = ctx.createLinearGradient(x1, y1, x2, y2); lg.addColorStop(0, "rgba(255,255,255,0)"); lg.addColorStop(mid, "rgba(255,255,255," + (.9 * w) + ")"); lg.addColorStop(1, "rgba(255,255,255,0)");
      ctx.strokeStyle = lg; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    }
    function draw(now) {
      raf = 0; if (!W) { build(); if (!W) return; }
      var el = (now - t0) / 1000, dt = Math.min(.05, (now - lastT) / 1000 || 0); lastT = now; var prog = reduce ? 1 : Math.min(1, el / 2.6);
      ctx.clearRect(0, 0, W, H);
      // sunrise
      var g = ctx.createRadialGradient(sun.x, sun.y, 0, sun.x, sun.y, H * .55);
      g.addColorStop(0, "rgba(255,150,60," + (.55 * prog) + ")"); g.addColorStop(.25, "rgba(255,120,40," + (.22 * prog) + ")"); g.addColorStop(.6, "rgba(139,92,246," + (.08 * prog) + ")"); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      ctx.save(); ctx.translate(sun.x, sun.y); ctx.lineWidth = 1;
      for (var r = 0; r < 18; r++) { var a = (r / 18) * Math.PI * 2 + el * .03; ctx.strokeStyle = "rgba(255,170,90," + (.10 * prog) + ")"; ctx.beginPath(); ctx.moveTo(Math.cos(a) * 90, Math.sin(a) * 90); ctx.lineTo(Math.cos(a) * (150 + ((r * 37) % 80)), Math.sin(a) * (150 + ((r * 37) % 80))); ctx.stroke(); }
      ctx.restore();
      // gates (home hero only)
      if (!ambient) for (var gi = 0; gi < 5; gi++) { var gx = W * GX[gi]; ctx.strokeStyle = "rgba(124,155,255," + (.13 * prog) + ")"; ctx.setLineDash([3, 7]); ctx.beginPath(); ctx.moveTo(gx, H * .05); ctx.lineTo(gx, H * .9); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = "rgba(180,200,255," + (.75 * prog) + ")"; ctx.font = MONO; ctx.textAlign = "center"; ctx.fillText(GATES[gi], gx, H * .93); }
      // the pointer's light
      ptr.a += ((ptr.on ? 1 : 0) - ptr.a) * Math.min(1, dt * 6);
      if (ptr.a > .01) { var pg = ctx.createRadialGradient(ptr.x, ptr.y, 0, ptr.x, ptr.y, 190); pg.addColorStop(0, "rgba(139,92,246," + (.16 * ptr.a) + ")"); pg.addColorStop(1, "rgba(139,92,246,0)"); ctx.fillStyle = pg; ctx.fillRect(ptr.x - 190, ptr.y - 190, 380, 380); }
      // traces: one horizontal brand gradient shared by every trace
      ctx.lineCap = "round"; ctx.lineJoin = "round";
      var lgs = [.16, .9].map(function (al) { var lg = ctx.createLinearGradient(0, 0, sun.x, 0); lg.addColorStop(0, "rgba(47,124,255," + al + ")"); lg.addColorStop(.5, "rgba(139,92,246," + al + ")"); lg.addColorStop(1, "rgba(255,138,43," + al + ")"); return lg; });
      for (var i = 0; i < traces.length; i++) { var tr = traces[i], f = reduce ? 1 : Math.max(0, Math.min(1, (el - tr.delay) / 2.2)); if (f <= 0) continue; var pts = partial(tr, f);
        for (var pass = 0; pass < 2; pass++) { ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); for (var k = 1; k < pts.length; k++) ctx.lineTo(pts[k][0], pts[k][1]); ctx.strokeStyle = lgs[pass]; ctx.lineWidth = pass ? 1.2 : 6; ctx.stroke(); }
        for (k = 1; k < pts.length - 1; k++) { ctx.strokeStyle = "rgba(255,255,255,.2)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(pts[k][0], pts[k][1], 2.4, 0, 6.283); ctx.stroke(); }
        if (ptr.a > .01) for (k = 1; k < pts.length; k++) light(pts, k);
        var e = pts[pts.length - 1]; ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(e[0], e[1], 1.8, 0, 6.283); ctx.fill(); }
      // work items
      if (!reduce) {
        if (prog >= 1 && Math.random() < SPAWN) launch(traces[Math.random() * traces.length | 0], 0, 260 + Math.random() * 220);
        for (var p = parts.length - 1; p >= 0; p--) { var a2 = parts[p]; if (a2.dead) { a2.dead += dt; if (a2.dead > .7) parts.splice(p, 1); continue; } a2.d += a2.v * dt; var pos = at(a2.tr, a2.d);
          if (a2.fate === "deny" && pos[0] >= W * GX[a2.gate]) { a2.dead = .01; a2.pos = pos; denied++; if (elDenied) elDenied.textContent = denied + " " + STOP_LABEL; continue; }
          if (a2.d >= a2.tr.total) { parts.splice(p, 1); signed++; if (elSigned) elSigned.textContent = signed.toLocaleString(); continue; } a2.pos = pos; }
      }
      for (p = 0; p < parts.length; p++) { var q = parts[p], pos2 = q.pos; if (!pos2) continue; var col = q.dead ? STOP_RGB : "255,255,255";
        var gg = ctx.createRadialGradient(pos2[0], pos2[1], 0, pos2[0], pos2[1], 9); gg.addColorStop(0, "rgba(" + col + ",.65)"); gg.addColorStop(1, "rgba(" + col + ",0)"); ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(pos2[0], pos2[1], 9, 0, 6.283); ctx.fill();
        ctx.fillStyle = "rgba(" + col + ",1)"; ctx.beginPath(); ctx.arc(pos2[0], pos2[1], 2.2, 0, 6.283); ctx.fill();
        if (q.dead) { ctx.fillStyle = "rgb(" + STOP_RGB + ")"; ctx.font = MONO; ctx.textAlign = "center"; ctx.fillText(STOP_TAG, pos2[0], pos2[1] - 14); } }
      // click ripples
      for (var ri = rings.length - 1; ri >= 0; ri--) { var rg = rings[ri]; rg.t += dt; if (rg.t > .7) { rings.splice(ri, 1); continue; }
        ctx.strokeStyle = "rgba(200,180,255," + (.6 * (1 - rg.t / .7)) + ")"; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(rg.x, rg.y, 6 + rg.t * 110, 0, 6.283); ctx.stroke(); }
      if (!reduce && visible) raf = requestAnimationFrame(draw);
    }
    function wake() { if (!raf && !reduce && visible) { lastT = performance.now(); raf = requestAnimationFrame(draw); } }
    function local(e) { var r = stage.getBoundingClientRect(); ptr.x = e.clientX - r.left; ptr.y = e.clientY - r.top; }
    build(); window.addEventListener("resize", function () { build(); if (!raf) raf = requestAnimationFrame(draw); });
    var host = (stage.closest && stage.closest("section")) || stage.parentNode;
    if (!reduce && host) {
      host.addEventListener("pointermove", function (e) { local(e); ptr.on = e.pointerType !== "touch"; });
      host.addEventListener("pointerleave", function () { ptr.on = false; });
      host.addEventListener("click", function (e) { if (skip(e)) return; local(e); ptr.a = 1; burst(ptr.x, ptr.y); wake(); });
    }
    if ("IntersectionObserver" in window) new IntersectionObserver(function (es) { visible = es[0].isIntersecting; wake(); }, { threshold: .02 }).observe(stage);
    raf = requestAnimationFrame(draw);
  });

  /* ---- 2. Cortex: a fibonacci lattice, lit by the workforce ---- */
  var solos = document.querySelectorAll("canvas.lattice");
  Array.prototype.forEach.call(solos, function (cv) {
    if (!cv.getContext) return;
    var ctx = cv.getContext("2d"), W = 640, H = 480, dpr = Math.min(window.devicePixelRatio || 1, 2), n = 520, pts = [], links = [], lit = [], t = 3, raf = 0, visible = true, last = 0;
    var m = { x: 0, y: 0, on: false, click: false }, tilt = { x: 0, y: 0, tx: 0, ty: 0 }, rings = [];
    cv.width = W * dpr; cv.height = H * dpr; ctx.scale(dpr, dpr);
    for (var i = 0; i < n; i++) { var y = 1 - (i / (n - 1)) * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963; pts.push([Math.cos(th) * r, y, Math.sin(th) * r]); lit.push(0); }
    for (i = 0; i < n; i++) { var best = []; for (var j = 0; j < n; j++) { if (i === j) continue; var p = pts[i], q = pts[j], d = (p[0] - q[0]) * (p[0] - q[0]) + (p[1] - q[1]) * (p[1] - q[1]) + (p[2] - q[2]) * (p[2] - q[2]);
      if (best.length < 3 || d < best[2][0]) { best.push([d, j]); best.sort(function (a, b) { return a[0] - b[0]; }); if (best.length > 3) best.length = 3; } }
      for (var k = 0; k < best.length; k++) if (best[k][1] > i) links.push([i, best[k][1]]); }
    function draw(now) {
      raf = 0; var dt = Math.min(.05, (now - last) / 1000 || 0); last = now; t += dt;
      ctx.clearRect(0, 0, W, H); ctx.fillStyle = "#0A0D15"; ctx.fillRect(0, 0, W, H);
      var g = ctx.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, H * .6); g.addColorStop(0, "rgba(139,92,246,.22)"); g.addColorStop(.5, "rgba(255,138,43,.08)"); g.addColorStop(1, "rgba(0,0,0,0)"); ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      // the pointer tilts the lattice toward it
      tilt.x += (tilt.tx - tilt.x) * Math.min(1, dt * 3); tilt.y += (tilt.ty - tilt.y) * Math.min(1, dt * 3);
      var R = H * .36, cx = W / 2, cy = H / 2, ay = t * .12 + tilt.y, ax = Math.sin(t * .07) * .35 + tilt.x, P = [];
      for (var i = 0; i < n; i++) { var p = pts[i], x = p[0] * Math.cos(ay) - p[2] * Math.sin(ay), z = p[0] * Math.sin(ay) + p[2] * Math.cos(ay), y = p[1] * Math.cos(ax) - z * Math.sin(ax); z = p[1] * Math.sin(ax) + z * Math.cos(ax); var s = 1 / (1.9 - z * .8); P.push([cx + x * R * s, cy + y * R * s, z, s]); }
      ctx.lineWidth = 1;
      for (var l = 0; l < links.length; l++) { var a = P[links[l][0]], b = P[links[l][1]], c = mix(((a[2] + b[2]) / 2 + 1) / 2), al = .10 + ((a[2] + b[2]) / 2 + 1) / 2 * .5; ctx.strokeStyle = "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + al + ")"; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
      if (!reduce && Math.random() < .08) { var pick = Math.random() * n | 0; if (P[pick][2] > -.1) lit[pick] = 1; }
      // nodes under the pointer light as it passes; a click lights a cluster, as if an agent wrote there
      if (m.on || m.click) for (i = 0; i < n; i++) { if (P[i][2] < -.2) continue; var dd = Math.hypot(P[i][0] - m.x, P[i][1] - m.y);
        if (m.click && dd < 64 && Math.random() < .4) lit[i] = Math.max(lit[i], 1 - dd / 96); else if (m.on && dd < 30) lit[i] = Math.max(lit[i], .7 * (1 - dd / 30)); }
      if (m.click) { rings.push({ x: m.x, y: m.y, t: 0 }); m.click = false; }
      for (i = 0; i < n; i++) { var q2 = P[i], cc = mix((q2[2] + 1) / 2);
        if (lit[i] > .02) { var gg = ctx.createRadialGradient(q2[0], q2[1], 0, q2[0], q2[1], 10 + lit[i] * 10); gg.addColorStop(0, "rgba(255,255,255," + lit[i] * .9 + ")"); gg.addColorStop(1, "rgba(255,255,255,0)"); ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(q2[0], q2[1], 10 + lit[i] * 10, 0, 6.283); ctx.fill(); lit[i] *= .96; }
        ctx.fillStyle = "rgba(" + cc[0] + "," + cc[1] + "," + cc[2] + "," + (.3 + (q2[2] + 1) / 2 * .7) + ")"; ctx.beginPath(); ctx.arc(q2[0], q2[1], 1.1 + q2[3] * 1.5, 0, 6.283); ctx.fill(); }
      for (var ri = rings.length - 1; ri >= 0; ri--) { var rg = rings[ri]; rg.t += dt; if (rg.t > .8) { rings.splice(ri, 1); continue; }
        ctx.strokeStyle = "rgba(255,255,255," + (.5 * (1 - rg.t / .8)) + ")"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(rg.x, rg.y, 8 + rg.t * 90, 0, 6.283); ctx.stroke(); }
      ctx.fillStyle = "#8E97AC"; ctx.font = MONO; ctx.textAlign = "left"; ctx.fillText("CORTEX · documents · decisions · lessons · the record", 20, H - 18);
      if (!reduce && visible) raf = requestAnimationFrame(draw);
    }
    function local(e) { var r = cv.getBoundingClientRect(); m.x = (e.clientX - r.left) * W / (r.width || W); m.y = (e.clientY - r.top) * H / (r.height || H); }
    if (!reduce) {
      cv.addEventListener("pointermove", function (e) { local(e); m.on = e.pointerType !== "touch"; tilt.ty = (m.x / W - .5) * 1.2; tilt.tx = (m.y / H - .5) * .7; });
      cv.addEventListener("pointerleave", function () { m.on = false; tilt.tx = tilt.ty = 0; });
      cv.addEventListener("click", function (e) { local(e); m.click = true; });
    }
    if ("IntersectionObserver" in window) new IntersectionObserver(function (es) { visible = es[0].isIntersecting; if (visible && !raf && !reduce) { last = performance.now(); raf = requestAnimationFrame(draw); } }, { threshold: .05 }).observe(cv);
    raf = requestAnimationFrame(draw);
  });

  /* ---- 3. panel spotlight: --mx/--my follow the pointer; styles.css draws the light ---- */
  if (!reduce && window.matchMedia && window.matchMedia("(hover: hover)").matches) {
    var SPOT = ".card,.instrument,.term,.gate .stage,.desk .item";
    document.addEventListener("pointermove", function (e) { var el = e.target && e.target.closest && e.target.closest(SPOT); if (!el) return;
      var r = el.getBoundingClientRect(); el.style.setProperty("--mx", (e.clientX - r.left) + "px"); el.style.setProperty("--my", (e.clientY - r.top) + "px"); }, { passive: true });
  }

  /* ---- 4. reveals ---- */
  var rv = document.querySelectorAll(".rv");
  if (rv.length) { if (reduce || !("IntersectionObserver" in window)) { for (var i = 0; i < rv.length; i++) rv[i].classList.add("in"); }
    else { var io = new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }); }, { threshold: .12, rootMargin: "0px 0px -8% 0px" }); for (i = 0; i < rv.length; i++) io.observe(rv[i]); } }
})();
