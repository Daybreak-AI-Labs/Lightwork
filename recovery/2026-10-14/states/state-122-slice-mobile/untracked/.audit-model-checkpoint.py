import json
from pathlib import Path
p=Path('docs/ux-audit-2026-09-30/slices/mobile.json')
rows=json.loads(p.read_text(encoding='utf-8'))
row=next(r for r in rows if r['id']=='LW-140')
row.update(implementation_evidence='model_guidance.py projects only typed provider capability evidence and existing provenance-bearing prices; bounded model_discovery preserves metadata; picker filters tools/images/self-hosted/unknown, preserves selected/manual entry and existing allow-list enforcement, warns about unsupported/unknown capabilities, labels stale/expired rates, context/output/latency unknown. Config models.guidance plus All settings and installer question added. Existing bounded availability refresh is accurately labelled as discovery, not generation.', validation='2026-09-30: Python AST parsed six changed/new model files; standalone JavaScript syntax passed; git diff --check passed. Model metadata regressions authored unrun. No live discovery/provider/browser tests run.', remaining='Explicit paid bounded generation/capability test and authorized Copilot operation coverage remain unimplemented; discovery is not claimed as a generation test. Browser/runtime validation deferred.')
p.write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n',encoding='utf-8',newline='\n')
