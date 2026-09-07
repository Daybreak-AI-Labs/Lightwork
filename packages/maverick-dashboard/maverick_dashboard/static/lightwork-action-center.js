(() => {
  "use strict";
  const root = document.getElementById("action-center");
  if (!root) return;
  const form = document.getElementById("ac-filters"), list = document.getElementById("ac-items");
  const status = document.getElementById("ac-status"), summary = document.getElementById("ac-summary");
  const coverage = document.getElementById("ac-coverage"), previous = document.getElementById("ac-previous");
  const next = document.getElementById("ac-next"), page = document.getElementById("ac-page");
  let model = null, offset = 0, busy = false, generation = 0, expired = false;
  const labels = {approval:"Approvals",signoff:"Sign-offs",assessment:"Assessments",project_review:"Project reviews"};
  function node(tag, text, className) {
    const el = document.createElement(tag); if (text !== undefined) el.textContent = text;
    if (className) el.className = className; return el;
  }
  function pending(value) {
    busy = value; root.setAttribute("aria-busy", String(value));
    root.querySelectorAll("button,select").forEach(el => { el.disabled = value || expired; });
    if (!value && !expired) { previous.disabled = offset === 0; next.disabled = !model || !model.has_more; }
  }
  function invalidate() {
    expired = true; ++generation; model = null; list.replaceChildren(); coverage.replaceChildren(); summary.textContent = ""; page.textContent = "";
    pending(false);
  }
  async function request(url, options) {
    const response = await fetch(url, {credentials:"same-origin", cache:"no-store", ...options,
      headers:{"X-Lightwork-Action-Context":root.dataset.context, ...(options && options.headers)}});
    if ([401,403].includes(response.status)) {
      invalidate(); throw new Error("Your sign-in or access changed. Sign in again and reload Action Center.");
    }
    let value;
    try { value = await response.json(); } catch (_) { throw new Error("The result could not be read. Refresh the list before trying again."); }
    if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Could not load your actions. Refresh the list.");
    return value;
  }
  function render() {
    list.replaceChildren(); coverage.replaceChildren();
    summary.textContent = `${model.counts.unread} unread · ${model.counts.overdue} overdue · ${model.counts.snoozed} snoozed`;
    model.coverage.forEach(source => {
      if (source.status !== "current") coverage.append(node("li", `${labels[source.kind]} ${source.status === "partial" ? "include only the most recent 2,000 source records. Open the full module for older records." : "could not be loaded. Refresh or open that module; this list is incomplete."}`));
    });
    if (!model.items.length) list.append(node("p", model.coverage.some(source=>source.status !== "current") ? "No items are visible in this filter. Some sources are incomplete or unavailable." : "No items match this filter."));
    model.items.forEach(item => {
      const article = node("article", undefined, "panel"); article.dataset.item = item.id; article.style.marginBlock = "var(--space-3)";
      article.append(node("h2", item.title));
      article.append(node("p", `${labels[item.kind]} · ${item.assignment === "mine" ? "Assigned to you" : "Available to review"} · ${item.read ? "Read" : "Unread"}`));
      article.append(node("p", item.reason, "muted"));
      if (item.due_at) article.append(node("p", `${item.overdue ? "Overdue" : "Due"}: ${new Date(item.due_at * 1000).toLocaleString()}`, item.overdue ? "badge warn" : "muted"));
      if (item.snoozed) article.append(node("p", `Snoozed until ${new Date(item.snoozed_until * 1000).toLocaleString()}`, "muted"));
      const actions = node("div"); actions.style.cssText = "display:flex;flex-wrap:wrap;gap:var(--space-2);align-items:center;";
      if (item.assessment_id) {
        const review = node("button", "Review assessment", "btn btn--primary"); review.type="button";
        review.addEventListener("click", () => {
          if (window.mvAssessmentReview) window.mvAssessmentReview.open(item.assessment_id);
          else status.textContent = "The assessment reviewer could not load. Open the assessment from its department workspace.";
        }); actions.append(review);
      } else {
        const link = node("a", "Review", "btn btn--primary"); link.href = item.href; actions.append(link);
      }
      const read = node("button", item.read ? "Mark unread" : "Mark read", "btn"); read.type="button";
      read.dataset.preference="read";
      read.setAttribute("aria-label", `${item.read ? "Mark unread" : "Mark read"}: ${item.title}`);
      read.addEventListener("click", () => save(item, !item.read, item.snoozed ? item.snoozed_until : 0, "read")); actions.append(read);
      const snooze = node("button", item.snoozed ? "Show now" : "Snooze for one day", "btn"); snooze.type="button";
      snooze.dataset.preference="snooze";
      snooze.setAttribute("aria-label", `${item.snoozed ? "Show now" : "Snooze for one day"}: ${item.title}`);
      snooze.addEventListener("click", () => save(item, item.read, item.snoozed ? 0 : Date.now()/1000+86400, "snooze")); actions.append(snooze);
      article.append(actions); list.append(article);
    });
    page.textContent = model.total ? `${offset+1}–${Math.min(offset+model.limit,model.total)} of ${model.total}` : "0 items";
  }
  async function load(focus) {
    if (busy || expired) return;
    const attempt = ++generation;
    pending(true); status.textContent = "Loading your actions…";
    try {
      const query = new URLSearchParams({view:form.elements.view.value, kind:form.elements.kind.value,
        assignment:form.elements.assignment.value, offset:String(offset), limit:"50"});
      const result = await request(`/api/v1/action-center?${query}`);
      if (attempt !== generation) return;
      if (result.context !== root.dataset.context) { invalidate(); throw new Error("The account or workspace changed. Reload Action Center."); }
      model = result; render(); status.textContent = "List refreshed.";
    } catch (error) {
      model = null; list.replaceChildren(); coverage.replaceChildren(); summary.textContent = ""; page.textContent = "";
      status.textContent = error.message;
    } finally {
      if (attempt === generation) pending(false);
      if (focus && !expired) {
        const rows = [...list.querySelectorAll('article')];
        const row = rows.find(row => row.dataset.item === focus.id) || rows[Math.min(focus.index, rows.length-1)];
        const target = row?.querySelector('[data-preference="'+focus.kind+'"]') || status;
        if (target === status) status.tabIndex = -1;
        target.focus();
      }
    }
  }
  async function save(item, read, snoozedUntil, kind) {
    if (busy || expired || !model) return;
    const focus = {id:item.id, index:model.items.indexOf(item), kind};
    pending(true); status.textContent = "Saving your preference…";
    try {
      await request(`/api/v1/action-center/${encodeURIComponent(item.id)}/preference`, {method:"PUT",
        headers:{"Content-Type":"application/json", "X-Lightwork-Action-Context":model.context},
        body:JSON.stringify({expected_revision:model.revision, source_version:item.source_version,
          operation_id:crypto.randomUUID(), read, snoozed_until:snoozedUntil})});
      pending(false); await load(focus);
    } catch (error) { status.textContent = error.message; pending(false); }
  }
  form.addEventListener("submit", event=>{event.preventDefault(); if (!busy) {offset=0; load();}});
  form.addEventListener("change", ()=>{if (!busy) {offset=0; load();}});
  previous.addEventListener("click", ()=>{if (!busy) {offset=Math.max(0,offset-50); load();}});
  next.addEventListener("click", ()=>{if (!busy && model && model.has_more) {offset+=50; load();}});
  window.addEventListener("pageshow", event=>{if (event.persisted) {++generation; model=null; list.replaceChildren(); busy=false; load();}});
  load();
})();
