from pathlib import Path
root=Path.cwd();changes={}
def edit(p,a,b,count=1):
 s=changes.get(p,(root/p).read_text(encoding='utf-8'));assert s.count(a)==count,(p,s.count(a));changes[p]=s.replace(a,b)
p='packages/maverick-dashboard/maverick_dashboard/app.py'
edit(p,'    applicability_regions_api,','    agent_override_changes_api,\n    applicability_regions_api,')
edit(p,'app.include_router(applicability_regions_api.router)','app.include_router(applicability_regions_api.router)\napp.include_router(agent_override_changes_api.router, prefix="/api/v1")')
edit('packages/maverick-dashboard/maverick_dashboard/product_operations.py','        product_operations_applicability_regions,','        product_operations_agent_overrides,\n        product_operations_applicability_regions,',2)
edit('packages/maverick-dashboard/maverick_dashboard/page_assets.py','    "lightwork-applicability-regions.js",','    "lightwork-applicability-regions.js",\n    "lightwork-agent-override-changes.js",')
edit('packages/maverick-core/maverick/recovery_authorities.py','    "local-rule-applicability": "applicability_regions_source_authority",','    "local-rule-applicability": "applicability_regions_source_authority",\n    "agent-override-removal-requests": "agent_override_change_authority",')
for p,s in changes.items():(root/p).write_text(s,encoding='utf-8',newline='\n')
print('Agent change native API, closed controls, asset and protected original namespace mounted.')
