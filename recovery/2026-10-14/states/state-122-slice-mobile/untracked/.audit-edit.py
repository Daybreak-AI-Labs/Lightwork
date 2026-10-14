from pathlib import Path
p=Path('packages/maverick-dashboard/maverick_dashboard/templates/plan_tree.html')
s=p.read_text(encoding='utf-8').replace('<noscript>','<section aria-label="Task steps and relationships">').replace('<div class="tree">','<div class="tree" id="plan-tree-list">').replace('</noscript>','</section>')
a=s.index('    if (!container || typeof cytoscape');b=s.index('    // Map a goal',a)
s=s[:a]+'''    const list = document.getElementById("plan-tree-list");
    function renderList(root) {
      function item(node) {
        const li = document.createElement('li'), link = document.createElement('a');
        link.href = '/chat/goal/' + encodeURIComponent(node.id);
        link.textContent = (node.title || 'Untitled task') + ' — ' + String(node.status || 'Waiting').replace(/_/g, ' ');
        li.appendChild(link);
        if ((node.children || []).length) {
          const children = document.createElement('ul');
          node.children.forEach(child => children.appendChild(item(child)));
          li.appendChild(children);
        }
        return li;
      }
      if (list.dataset.snapshot === JSON.stringify(root)) return;
      const focused = list.contains(document.activeElement) ? document.activeElement.getAttribute('href') : null;
      const ul = document.createElement('ul'); ul.appendChild(item(root));
      list.replaceChildren(ul); list.dataset.snapshot = JSON.stringify(root);
      if (focused) Array.from(list.querySelectorAll('a')).find(link => link.getAttribute('href') === focused)?.focus({preventScroll: true});
    }

'''+s[b:]
s=s.replace('    const cy = cytoscape({','    let cy = null;\n    try { if (container && typeof cytoscape !== "undefined") cy = cytoscape({')
s=s.replace('    cy.on("tap", "node",', '    if (cy) cy.on("tap", "node",')
s=s.replace('      wheelSensitivity: 0.2,\n    });','      wheelSensitivity: 0.2,\n    }); } catch (_) { cy = null; }\n    if (!cy && container) container.hidden = true;')
s=s.replace('        cy.elements().remove();\n        cy.add(toElements(tree));\n        cy.layout({ name: "breadthfirst", directed: true, padding: 20 }).run();','''        renderList(tree);
        if (cy) {
          try {
            cy.elements().remove(); cy.add(toElements(tree));
            cy.layout({ name: "breadthfirst", directed: true, padding: 20 }).run();
          } catch (_) { cy.destroy(); cy = null; container.hidden = true; }
        }''')
s=s.replace('statusEl.textContent = "Live', 'statusEl.textContent = (cy ? "Live map and list: " : "Live task list: ") + "')
p.write_text(s,encoding='utf-8')
p=Path('web/widget/maverick-widget.js');s=p.read_text(encoding='utf-8')
s=s.replace('var panel = el("div", null, {','var panel = el("div", {id: "lightwork-widget-panel", role: "region", "aria-label": TITLE}, {')
s=s.replace('position: "fixed", bottom: "84px", right: "20px", width: "320px",','position: "fixed", bottom: "calc(84px + env(safe-area-inset-bottom, 0px))", insetInlineEnd: "12px", width: "min(320px, calc(100vw - 24px))", boxSizing: "border-box",')
s=s.replace('maxHeight: "60vh",','maxHeight: "min(60dvh, calc(100dvh - 100px))",')
s=s.replace('  var log = el("div", null, {','''  var closeButton = el('button', {type: 'button', textContent: 'Close', 'aria-label': 'Close ' + TITLE}, {marginInlineStart: 'auto', minHeight: '44px'});
  header.style.display = 'flex'; header.style.alignItems = 'center'; header.appendChild(closeButton);
  var log = el("div", {'aria-label': 'Conversation'}, {''')
s=s.replace('flex: "1", padding: "12px", overflowY:', 'flex: "1", minHeight: "0", overflowWrap: "anywhere", padding: "12px", overflowY:')
s=s.replace('placeholder: "Type a goal…", required: true', 'placeholder: "Type a goal…", "aria-label": "Message to Lightwork", required: true')
s=s.replace('{ flex: "1", border: "0", padding: "12px", outline: "none" }','{ flex: "1", minWidth: "0", boxSizing: "border-box", border: "0", padding: "12px" }')
s=s.replace('  panel.appendChild(check);','''  panel.appendChild(check);
  var status = el('p', {role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true'}, {margin: '8px 12px', color: '#111'});
  panel.appendChild(status);''')
s=s.replace('  function line(who, text) {', '''  fab.setAttribute('aria-controls', panel.id); fab.setAttribute('aria-expanded', 'false');
  function line(who, text) {
    if (who === 'Lightwork') status.textContent = text;''')
a=s.index('  fab.addEventListener("click"');b=s.index('  function controls()',a)
s=s[:a]+'''  function setOpen(open) {
    panel.style.display = open ? 'flex' : 'none';
    fab.setAttribute('aria-expanded', String(open));
    if (open) input.focus(); else fab.focus();
  }
  fab.addEventListener('click', function () { setOpen(panel.style.display !== 'flex'); });
  closeButton.addEventListener('click', function () { setOpen(false); });
  panel.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') { event.preventDefault(); setOpen(false); }
  });
  if (window.visualViewport) {
    var fit = function () {
      panel.style.maxHeight = Math.max(80, window.visualViewport.height - 110) + 'px';
      panel.style.bottom = (window.innerHeight - window.visualViewport.height - window.visualViewport.offsetTop + 84) + 'px';
    };
    window.visualViewport.addEventListener('resize', fit); fit();
  }

'''+s[b:]
s=s.replace('    send.disabled = busy || unknown;', '    send.disabled = busy || unknown;\n    form.setAttribute("aria-busy", String(busy));')
s=s.replace('    log.appendChild(row);\n    if (localStorage', '    log.appendChild(row);\n    status.textContent = "Task accepted. Open the task to follow its progress.";\n    if (localStorage')
s=s.replace('    busy = true;\n    line("You", msg);', '    busy = true;\n    status.textContent = "Sending your request. Waiting for its receipt.";\n    line("You", msg);')
p.write_text(s,encoding='utf-8')
p=Path('packages/maverick-dashboard/maverick_dashboard/static/lightwork-ui.js');s=p.read_text(encoding='utf-8');s+='''
// Shared reveal helper respects the person's motion setting on every workspace.
window.mvReveal = function (element, options) {
  if (!element) return;
  var opts = Object.assign({block: 'nearest'}, options || {});
  opts.behavior = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';
  element.scrollIntoView(opts);
  // Only explicit reveal actions use this helper, never background polling.
  if (!element.matches('input, button, select, textarea, a[href], [tabindex]')) element.tabIndex = -1;
  element.focus({preventScroll: true});
};
''';p.write_text(s,encoding='utf-8')
for p in Path('packages/maverick-dashboard/maverick_dashboard/static').glob('*.js'):
 s=p.read_text(encoding='utf-8')
 import re
 s=re.sub(r'(document\.getElementById\("sgi-links-title"\)|box|panel|host|editor|templateForm)\.scrollIntoView\(\{ behavior: "smooth"(?:, block: "start")? \}\)',r'window.mvReveal(\1)',s)
 p.write_text(s,encoding='utf-8')
