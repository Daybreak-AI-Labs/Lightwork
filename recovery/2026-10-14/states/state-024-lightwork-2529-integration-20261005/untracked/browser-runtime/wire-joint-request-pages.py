from pathlib import Path
root=Path("C:\\Users\\Cstep\\Documents\\Codex\\2026-09-10\\new-chat\\work\\lightwork-2529-integration-20261005")
def edit(path, old, new, count=1):
    target=root/path
    text=target.read_text(encoding='utf-8')
    assert text.count(old)==count,(path,text.count(old))
    target.write_text(text.replace(old,new),encoding='utf-8',newline='\n')
flags=[
('procedure_interactions','Check local rules together','Compare two owned local rules in both orders and check all supported starting values before any combined use.'),
('request_program','Prepare an answer for this request','Check bounded arrangements of your existing taught rules against permitted original cases for one request. Nothing is published automatically.'),
('commitment_economic_alternatives','Compare additional-work economics','Compare reported offer prices and full incremental costs with currently protected commitments. Results remain conditional on your stated facts.'),
('workflow_step_checks','Try one workflow step','Check one owned taught step against ordinary entered values without running the whole workflow or any connected action.'),
]
cfg='packages/maverick-core/maverick/config.py'
edit(cfg,'        "recovery_visibility": cfg.get("recovery_visibility", False) is True,',
    '\n'.join('        "'+key+'": cfg.get("'+key+'", False) is True,' for key,_,_ in flags)+'\n        "recovery_visibility": cfg.get("recovery_visibility", False) is True,')
defaults='    "recovery_visibility": False,'
edit('packages/maverick-dashboard/maverick_dashboard/settings_store.py',defaults,
    '\n'.join('    "'+key+'": False,' for key,_,_ in flags)+'\n'+defaults)
edit('packages/maverick-core/tests/test_features_wiring.py','        "recovery_visibility": False,',
    '\n'.join('        "'+key+'": False,' for key,_,_ in flags)+'\n        "recovery_visibility": False,',2)
for path in ['packages/maverick-dashboard/maverick_dashboard/settings_store.py','packages/maverick-dashboard/maverick_dashboard/plain_labels.py']:
    marker='    "recovery_visibility": "Practice with fictional sign-offs'
    target=root/path;text=target.read_text(encoding='utf-8');assert text.count(marker)==1
    insert='\n'.join('    "'+key+'": "'+helptext+'",' for key,_,helptext in flags)+'\n'
    target.write_text(text.replace(marker,insert+marker),encoding='utf-8',newline='\n')
edit('packages/maverick-dashboard/maverick_dashboard/plain_labels.py','    "recovery_visibility": "Understand a missing reply",',
    '\n'.join('    "'+key+'": "'+label+'",' for key,label,_ in flags)+'\n    "recovery_visibility": "Understand a missing reply",')
wizard='apps/installer-cli/maverick_installer/wizard.py'
edit(wizard,'        "allow_recovery_visibility": _q_confirm(',
    '\n'.join('        "allow_'+key+'": _q_confirm(\n            "Enable '+label.lower()+'? '+helptext+'", default=False,\n        ),' for key,label,helptext in flags)+'\n        "allow_recovery_visibility": _q_confirm(')
edit(wizard,'    if advanced.get("allow_recovery_visibility") is True:',
    '\n'.join('    if advanced.get("allow_'+key+'") is True:\n        _feature_locks.append("'+key+' = true")' for key,_,_ in flags)+'\n    if advanced.get("allow_recovery_visibility") is True:')
app='packages/maverick-dashboard/maverick_dashboard/app.py'
edit(app,'    recovery_visibility_api,','    procedure_interactions_api,\n    request_program_api,\n    recovery_visibility_api,')
edit(app,'app.include_router(recovery_visibility_api.router)','app.include_router(recovery_visibility_api.router)\napp.include_router(procedure_interactions_api.router)\napp.include_router(request_program_api.router)')
ops='packages/maverick-dashboard/maverick_dashboard/product_operations.py'
edit(ops,'        product_operations_recovery_visibility,','        product_operations_procedure_interactions,\n        product_operations_request_program,\n        product_operations_recovery_visibility,',2)
edit('packages/maverick-dashboard/maverick_dashboard/page_assets.py','    "lightwork-recovery-visibility.js",','    "lightwork-recovery-visibility.js",\n    "lightwork-procedure-interactions.js",\n    "lightwork-request-program.js",')
visibility='packages/maverick-dashboard/maverick_dashboard/ui_visibility.py'
marker='    {"path": "/learning/recovery-visibility",'
edit(visibility,marker,'    {"path": "/learning/rules-together", "label": "Check local rules together", "group": "Observe",\n     "icon": "learned", "floor": "operate", "default": "operate", "in_nav": False},\n    {"path": "/learning/request-program", "label": "Prepare an answer for this request", "group": "Observe",\n     "icon": "learned", "floor": "operate", "default": "operate", "in_nav": False},\n'+marker)
edit(visibility,'    "/learning/recovery-visibility": ("Agents", "Learning"),','    "/learning/recovery-visibility": ("Agents", "Learning"),\n    "/learning/rules-together": ("Agents", "Learning"),\n    "/learning/request-program": ("Agents", "Learning"),')
edit('packages/maverick-dashboard/maverick_dashboard/templates/learning.html',"""    {% if can_open_page('/learning/recovery-visibility') %}""","""    {% if can_open_page('/learning/rules-together') %}<a class="btn btn--ghost" href="/learning/rules-together">Check local rules together</a>{% endif %}
    {% if can_open_page('/learning/request-program') %}<a class="btn btn--ghost" href="/learning/request-program">Prepare an answer for this request</a>{% endif %}
    {% if can_open_page('/learning/recovery-visibility') %}""")
edit('packages/maverick-core/maverick/recovery_authorities.py','    "recovery-visibility-practices": "recovery_visibility_source_authority",','    "recovery-visibility-practices": "recovery_visibility_source_authority",\n    "procedure-interaction-requests": "procedure_interactions_source_authority",\n    "request-specific-programs": "request_program_source_authority",\n    "commitment-economic-alternatives": "commitment_economic_alternatives_source_authority",')
guides="""    ("/learning/rules-together", "Check local rules together", "Find where two owned local rules can run together without changing their separate answers.",
     "Choose two rules, their permitted original cases and bounded starting values. Review both orders and all native comparisons, then use only the separately qualified arrangement.",
     "Every current source and original-case permission is checked again. Both results must agree with independently run separate rules.",
     "This bounded local check cannot authorize connected actions, publish workflows or establish future accuracy. Ambiguous orders remain unresolved.",
     "Which order keeps both answers?", "Which starting values make the rules conflict?"),
    ("/learning/request-program", "Prepare an answer for this request", "Check arrangements of your existing taught steps against permitted real cases for one request.",
     "Describe the request, choose named taught steps and current values, and supply explicit desired answers for permitted original cases. Review every proposed arrangement and the complete comparisons.",
     "The native calculation uses owned published local rules and current source permission. Unsupported or conflicting arrangements cannot produce a definite answer.",
     "A request-specific answer publishes no workflow. Read the original after a lost reply; do not repeat an unknown calculation.",
     "Which checked arrangement answers this request?", "What additional real case would separate the remaining answers?"),
"""
edit('packages/maverick-dashboard/maverick_dashboard/copilot_context.py','    ("/learning/recovery-visibility",',guides+'    ("/learning/recovery-visibility",')
print('Joint-rule and request-specific native pages, flags, permissions, assets and discovery mounted.')

