from pathlib import Path
root=Path.cwd();changes={}
def edit(p,a,b,count=1):
 s=changes.get(p,(root/p).read_text(encoding='utf-8'));assert s.count(a)==count,(p,s.count(a));changes[p]=s.replace(a,b)
helptext='Build alternative local rules and a complete checking order from one owned published rule. Independently review every change before publication, and retain the exact previous release for restoration.'
edit('packages/maverick-core/maverick/config.py','        "recovery_visibility": cfg.get("recovery_visibility", False) is True,','        "governed_rule_evolution": cfg.get("governed_rule_evolution", False) is True,\n        "recovery_visibility": cfg.get("recovery_visibility", False) is True,')
edit('packages/maverick-dashboard/maverick_dashboard/settings_store.py','    "recovery_visibility": False,','    "governed_rule_evolution": False,\n    "recovery_visibility": False,')
edit('packages/maverick-core/tests/test_features_wiring.py','        "recovery_visibility": False,','        "governed_rule_evolution": False,\n        "recovery_visibility": False,',2)
for p in ['packages/maverick-dashboard/maverick_dashboard/settings_store.py','packages/maverick-dashboard/maverick_dashboard/plain_labels.py']:
 edit(p,'    "recovery_visibility": "Practice with fictional sign-offs','    "governed_rule_evolution": "'+helptext+'",\n    "recovery_visibility": "Practice with fictional sign-offs')
edit('packages/maverick-dashboard/maverick_dashboard/plain_labels.py','    "recovery_visibility": "Understand a missing reply",','    "governed_rule_evolution": "Rewrite and check local rules",\n    "recovery_visibility": "Understand a missing reply",')
wizard='apps/installer-cli/maverick_installer/wizard.py'
edit(wizard,'        "allow_recovery_visibility": _q_confirm(','        "allow_governed_rule_evolution": _q_confirm(\n            "Enable rewriting and checking local rules? '+helptext+'", default=False,\n        ),\n        "allow_recovery_visibility": _q_confirm(')
edit(wizard,'    if advanced.get("allow_recovery_visibility") is True:','    if advanced.get("allow_governed_rule_evolution") is True:\n        _feature_locks.append("governed_rule_evolution = true")\n    if advanced.get("allow_recovery_visibility") is True:')
app='packages/maverick-dashboard/maverick_dashboard/app.py'
edit(app,'    operator_acquisition_api,','    governed_rule_evolution_api,\n    operator_acquisition_api,')
edit(app,'app.include_router(operator_acquisition_api.router)','app.include_router(operator_acquisition_api.router)\napp.include_router(governed_rule_evolution_api.router)')
edit('packages/maverick-dashboard/maverick_dashboard/product_operations.py','        product_operations_operator_acquisition,','        product_operations_governed_rule_evolution,\n        product_operations_operator_acquisition,',2)
edit('packages/maverick-dashboard/maverick_dashboard/page_assets.py','    "lightwork-operator-acquisition.js",','    "lightwork-operator-acquisition.js",\n    "lightwork-governed-rule-evolution.js",')
edit('packages/maverick-core/maverick/recovery_authorities.py','    "operator-acquisition": "operator_acquisition_source_authority",','    "operator-acquisition": "operator_acquisition_source_authority",\n    "governed-rule-evolution": "governed_rule_evolution_source_authority",')
visibility='packages/maverick-dashboard/maverick_dashboard/ui_visibility.py'
edit(visibility,'    {"path": "/learning/acquisition",','    {"path": "/learning/rule-evolution", "label": "Rewrite and check local rules", "group": "Observe",\n     "icon": "learned", "floor": "operate", "default": "operate", "in_nav": False},\n    {"path": "/learning/acquisition",')
edit(visibility,'    "/learning/acquisition": ("Agents", "Learning"),','    "/learning/rule-evolution": ("Agents", "Learning"),\n    "/learning/acquisition": ("Agents", "Learning"),')
edit('packages/maverick-dashboard/maverick_dashboard/templates/learning.html',"    {% if can_open_page('/learning/acquisition') %}","    {% if can_open_page('/learning/rule-evolution') %}<a class=\"btn btn--ghost\" href=\"/learning/rule-evolution\">Rewrite and check local rules</a>{% endif %}\n    {% if can_open_page('/learning/acquisition') %}")
guide='''    ("/learning/rule-evolution", "Rewrite and check local rules", "Build and check alternative decision routes and a complete checking order from your owned published rule.",
     "Choose the complete source, permission and bounded wrong-answer examples. Build alternatives, inspect every comparison and independently accept a change before activating it. Check the active release or restore the exact retained one.",
     "An unchanged complete-domain comparison remains outside both evolving artifacts. Every passing rule checks every declared situation, and current owner, source permission and work allowance bind each admission.",
     "Agreement with your original rule is not independent business truth. Equal or slower work remains visible. Lost replies use exact original custody; unknown effects never repeat automatically.",
     "Which alternative keeps every expected answer?", "How can I restore the previous published rule?"),
'''
edit('packages/maverick-dashboard/maverick_dashboard/copilot_context.py','    ("/learning/acquisition",',guide+'    ("/learning/acquisition",')
js='packages/maverick-dashboard/maverick_dashboard/static/lightwork-product-operations.js'
block='''        if (entry[0] === 'governed_rule_evolution_author') {
          var evolutionAuthor = entry[1], evolutionStarted = generation, evolutionSelection = JSON.stringify(context());
          if (!evolutionAuthor || Array.isArray(evolutionAuthor) || Object.keys(evolutionAuthor).sort().join(',') !== 'binding,context,flow' ||
              typeof evolutionAuthor.context !== 'string' || !/^[a-f0-9]{64}$/.test(evolutionAuthor.context) ||
              typeof evolutionAuthor.binding !== 'string' || !/^[a-f0-9]{64}$/.test(evolutionAuthor.binding) ||
              typeof evolutionAuthor.flow !== 'string' || evolutionAuthor.flow.length < 1 || evolutionAuthor.flow.length > 256) return;
          var evolutionDestination = node('dd', undefined, list), evolutionControl = window.LWGovernedRuleEvolution;
          if (evolutionControl && typeof evolutionControl.openAuthor === 'function') button('Open all rule evolution controls', evolutionDestination, async function (event) {
            var isCurrent = function () { return active(evolutionStarted) && JSON.stringify(context()) === evolutionSelection; };
            if (!isCurrent() || busy) return;
            var evolutionButton = event.currentTarget; evolutionButton.disabled = true;
            try {
              var opened = await evolutionControl.openAuthor(evolutionAuthor, {isCurrent: isCurrent});
              if (!isCurrent()) return;
              status.textContent = opened ? 'The complete protected rule form is open.' : 'Your entered rule work is kept. Review current controls.';
              if (opened) { var close = document.getElementById('mv-copilot-close'); if (close) close.click(); }
            } catch (_) { if (isCurrent()) status.textContent = 'The rule form could not be opened. Your entered work is kept.'; }
            finally { if (isCurrent() && evolutionButton.isConnected) evolutionButton.disabled = false; }
          });
          else { var evolutionLink = node('a', 'Open all rule evolution controls', evolutionDestination); evolutionLink.href = '/learning/rule-evolution?flow=' + encodeURIComponent(evolutionAuthor.flow) + '#gre-author'; evolutionLink.className = 'btn btn--ghost'; }
          return;
        }
'''
edit(js,"        if (entry[0] === 'operator_acquisition_author') {",block+"        if (entry[0] === 'operator_acquisition_author') {")
edit(js,"              : item.id.startsWith('learning.acquisition.')", "              : item.id.startsWith('learning.rule_evolution.')\n                ? !!(window.LWGovernedRuleEvolution && window.LWGovernedRuleEvolution.renderReview &&\n                     window.LWGovernedRuleEvolution.renderReview(formHost, review.changes) === true)\n              : item.id.startsWith('learning.acquisition.')")
for p,s in changes.items():(root/p).write_text(s,encoding='utf-8',newline='\n')
print('P16 shared hooks written:',len(changes))
