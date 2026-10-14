import datetime
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

repo = Path.cwd()
files = json.loads((repo.parent / 'audit/core-qualification-files.json').read_text())
files = [p for p in files if not p.endswith(('test_goal_read_projections.py', 'test_domain_discipline.py'))]
runs = []
for number, mode in ((8, 'ordinary'), (9, 'secure-default')):
    name = f'core-qualification-{number:02}'
    path = repo.parent / 'audit' / (name + '.xml')
    suite = ET.parse(path).getroot().find('testsuite')
    assert suite is not None
    assert int(suite.attrib['failures']) == 0 and int(suite.attrib['errors']) == 0
    skips = [{'test': t.attrib['classname'] + '::' + t.attrib['name'], 'reason': s.attrib.get('message', '')}
             for t in suite.iter('testcase') if (s := t.find('skipped')) is not None]
    runs.append({'mode': mode, 'tests': int(suite.attrib['tests']),
                 'passed': int(suite.attrib['tests']) - int(suite.attrib['skipped']),
                 'skipped': int(suite.attrib['skipped']), 'failed': 0, 'errors': 0,
                 'seconds': float(suite.attrib['time']), 'junit': 'audit/' + name + '.xml',
                 'junit_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'skips': skips})
record = {
    'schema': 'lightwork.customer-audit.core-qualification.v1',
    'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'base_sha': 'd822db99ee48aab4fdded47fc799486e8e5ea44f',
    'scope': 'Remaining core and installer ordinary-failure files; identity/privacy/memory learning and conversation projection files were assigned separately.',
    'files': files, 'runs': runs,
    'environment': {'os': 'Windows', 'python': '3.12.12',
                    'source': 'A dedicated venv loads this checkout through a plain .pth and shared dependency site; other-checkout editable hooks are not executed.',
                    'filesystem': 'Actual Windows handles, short unique pytest basetemp and short private task-owned TEMP/TMP for native lxml scratch and nested recovery cleanup.',
                    'live_provider_calls': False, 'production_changes': False},
    'changes': [
        'Restore admission now holds customer workflow, private-content grant, identity/link, pairing, plugin-pin and dispatch authority until current reconciliation; stale backups never silently restore approvals or replay work.',
        'The closed governed-store completeness check includes dashboard declarations. Warning and project-default records are explicitly non-authorizing data; actual SQLite content remains protected.',
        'Persistence inventory classifies 46 newly introduced source modules, their observed write/read seams and exact authority restore dispositions.',
        'Postgres schema expectations cover migrations 42-45; mutation tests verify advisory locking and exact tenant-scoped DELETE.',
        'Runner/fleet/fair-lane tests exercise temporary real WorldModel transactions and ownership, with synthetic LLM/sandbox dependencies and joined threads.',
        'Installer budgets, notification opt-ins and knowledge reindex settings match the implemented explicit defaults.',
        'Owned memory, source-scoped recall, goal-bound conversation turns, theme class tokens, snapshot timestamps and queued notification semantics have current assertions.',
        'Only Windows error 1314 skips the linked-interpreter test; unexpected symlink errors still fail.'
    ],
    'static_validation': ['Ruff on every changed Python file', 'Python AST parsing', 'git diff --check'],
    'limitations': ['No dedicated PostgreSQL or MySQL test services were available; their native driver cases remain skipped.',
                    'This Windows account cannot create symlinks; linked-interpreter qualification remains host-bound.',
                    'Restored new authority stores remain held pending current reconciliation; the registry is not an automatic migration/replay mechanism.',
                    'This focused group does not certify the full repository suite, live providers, signed native releases or multi-host recovery.'],
}
path = repo / 'docs/ux-audit-2026-09-30/core-installer-qualification.json'
path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf8', newline='\n')
print(json.dumps(runs, indent=2))
