from pathlib import Path
root=Path(r'C:\Users\Cstep\Documents\Codex\2026-09-10\new-chat\work\lightwork-2529-integration-20261005')
changes={}
def edit(p,a,b,count=1):
 s=changes.get(p,(root/p).read_text(encoding='utf-8'));assert s.count(a)==count,(p,s.count(a));changes[p]=s.replace(a,b)
key='applicability_regions';label='Checked answer regions';helptext='Check both local rule versions across every supported starting situation. Only current separately permitted expected answers can choose a version; unclear situations remain for review.'
cfg='packages/maverick-core/maverick/config.py'
edit(cfg,'        "recovery_visibility": cfg.get("recovery_visibility", False) is True,','        "applicability_regions": cfg.get("applicability_regions", False) is True,\n        "recovery_visibility": cfg.get("recovery_visibility", False) is True,')
edit('packages/maverick-dashboard/maverick_dashboard/settings_store.py','    "recovery_visibility": False,','    "applicability_regions": False,\n    "recovery_visibility": False,')
edit('packages/maverick-core/tests/test_features_wiring.py','        "recovery_visibility": False,','        "applicability_regions": False,\n        "recovery_visibility": False,',2)
for p in ['packages/maverick-dashboard/maverick_dashboard/settings_store.py','packages/maverick-dashboard/maverick_dashboard/plain_labels.py']:
 edit(p,'    "recovery_visibility": "Practice with fictional sign-offs','    "applicability_regions": "'+helptext+'",\n    "recovery_visibility": "Practice with fictional sign-offs')
edit('packages/maverick-dashboard/maverick_dashboard/plain_labels.py','    "recovery_visibility": "Understand a missing reply",','    "applicability_regions": "'+label+'",\n    "recovery_visibility": "Understand a missing reply",')
wizard='apps/installer-cli/maverick_installer/wizard.py'
edit(wizard,'        "allow_recovery_visibility": _q_confirm(','        "allow_applicability_regions": _q_confirm(\n            "Enable checked answer regions? '+helptext+'", default=False,\n        ),\n        "allow_recovery_visibility": _q_confirm(')
edit(wizard,'    if advanced.get("allow_recovery_visibility") is True:','    if advanced.get("allow_applicability_regions") is True:\n        _feature_locks.append("applicability_regions = true")\n    if advanced.get("allow_recovery_visibility") is True:')
app='packages/maverick-dashboard/maverick_dashboard/app.py'
edit(app,'    operator_acquisition_api,','    applicability_regions_api,\n    operator_acquisition_api,')
edit(app,'app.include_router(operator_acquisition_api.router)','app.include_router(operator_acquisition_api.router)\napp.include_router(applicability_regions_api.router)')
edit('packages/maverick-dashboard/maverick_dashboard/product_operations.py','        product_operations_operator_acquisition,','        product_operations_applicability_regions,\n        product_operations_operator_acquisition,',2)
edit('packages/maverick-dashboard/maverick_dashboard/page_assets.py','    "lightwork-operator-acquisition.js",','    "lightwork-operator-acquisition.js",\n    "lightwork-applicability-regions.js",')
edit('packages/maverick-core/maverick/recovery_authorities.py','    "operator-acquisition": "operator_acquisition_source_authority",','    "operator-acquisition": "operator_acquisition_source_authority",\n    "local-rule-applicability": "applicability_regions_source_authority",')
visibility='packages/maverick-dashboard/maverick_dashboard/ui_visibility.py'
edit(visibility,'    {"path": "/learning/acquisition",','    {"path": "/learning/applicability", "label": "Checked answer regions", "group": "Observe",\n     "icon": "learned", "floor": "operate", "default": "operate", "in_nav": False},\n    {"path": "/learning/acquisition",')
edit(visibility,'    "/learning/acquisition": ("Agents", "Learning"),','    "/learning/applicability": ("Agents", "Learning"),\n    "/learning/acquisition": ("Agents", "Learning"),')
edit('packages/maverick-dashboard/maverick_dashboard/templates/learning.html',"    {% if can_open_page('/learning/acquisition') %}","    {% if can_open_page('/learning/applicability') %}<a class=\"btn btn--ghost\" href=\"/learning/applicability\">Checked answer regions</a>{% endif %}\n    {% if can_open_page('/learning/acquisition') %}")
guide='''    ("/learning/applicability", "Checked answer regions", "Find which supported starting situations have an independently expected answer from the current or proposed version.",
     "Choose the owned rule and complete comparisons with separate case reuse permission. Review every starting situation, then separately answer hypothetical questions or try checked routing.",
     "Both immutable versions run across the complete bounded domain. An independent complete comparison checks routing; current source and case permissions apply at every step.",
     "Missing or conflicting expected answers need review. Hypothetical answers are local tests, not observed customer outcomes. This publishes nothing automatically and proves no future business result.",
     "Where does each version give the expected answer?", "Which starting situation still needs my judgment?"),
'''
edit('packages/maverick-dashboard/maverick_dashboard/copilot_context.py','    ("/learning/acquisition",',guide+'    ("/learning/acquisition",')
js='packages/maverick-dashboard/maverick_dashboard/static/lightwork-product-operations.js'
block='''        if (entry[0] === 'applicability_form') {
          var regionForm = entry[1], regionStarted = generation, regionSelection = JSON.stringify(context());
          if (!regionForm || Array.isArray(regionForm) || Object.keys(regionForm).sort().join(',') !== 'context,flow,original' ||
              typeof regionForm.context !== 'string' || !/^[a-f0-9]{64}$/.test(regionForm.context) ||
              typeof regionForm.flow !== 'string' || regionForm.flow.length < 1 || regionForm.flow.length > 128 ||
              typeof regionForm.original !== 'string' || regionForm.original && !/^APR-[a-f0-9]{32}$/.test(regionForm.original)) return;
          var regionDestination = node('dd', undefined, list), regionControl = window.LightworkApplicabilityRegions;
          if (regionControl && typeof regionControl.open === 'function') button('Open complete checked answer regions', regionDestination, async function (event) {
            var isCurrent = function () { return active(regionStarted) && JSON.stringify(context()) === regionSelection; };
            if (!isCurrent() || busy) return;
            var regionButton = event.currentTarget; regionButton.disabled = true;
            try {
              var opened = await regionControl.open(regionForm, isCurrent);
              if (!isCurrent()) return;
              status.textContent = opened ? 'The complete checked answer form is open.' : 'Your entered work is kept. Review the current controls.';
              if (opened) { var close = document.getElementById('mv-copilot-close'); if (close) close.click(); }
            } catch (_) { if (isCurrent()) status.textContent = 'The checked answer form could not be opened. Your entered work is kept.'; }
            finally { if (isCurrent() && regionButton.isConnected) regionButton.disabled = false; }
          });
          else { var regionLink = node('a', 'Open complete checked answer regions', regionDestination); regionLink.href = '/learning/applicability?' + new URLSearchParams({flow: regionForm.flow, original: regionForm.original}).toString() + '#apr-author-h'; regionLink.className = 'btn btn--ghost'; }
          return;
        }
'''
edit(js,"        if (entry[0] === 'operator_acquisition_author') {",block+"        if (entry[0] === 'operator_acquisition_author') {")
edit(js,"              : item.id.startsWith('learning.acquisition.')", "              : ['learning.qualify_answer_regions', 'learning.answer_region_question', 'learning.withdraw_region_answer', 'learning.run_answer_regions'].includes(item.id)\n                ? !!(window.LightworkApplicabilityRegions && window.LightworkApplicabilityRegions.renderReview &&\n                     window.LightworkApplicabilityRegions.renderReview(formHost, review.changes) === true)\n              : item.id.startsWith('learning.acquisition.')")
for p,s in changes.items():(root/p).write_text(s,encoding='utf-8',newline='\n')
print('P04 shared hooks written:',len(changes))
