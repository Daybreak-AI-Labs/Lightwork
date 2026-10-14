import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(__file__).parent
def run_record(name):
    path = root.parent / 'audit' / name
    xml = ET.parse(path).getroot()
    suite = next(xml.iter('testsuite'))
    return {
        'junit': 'audit/' + name,
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'tests': int(suite.attrib['tests']),
        'passed': int(suite.attrib['tests']) - int(suite.attrib['failures']) - int(suite.attrib['errors']) - int(suite.attrib['skipped']),
        'failures': int(suite.attrib['failures']), 'errors': int(suite.attrib['errors']),
        'skipped': int(suite.attrib['skipped']), 'seconds': float(suite.attrib['time']),
        'skips': [{'name': case.attrib['name'], 'reason': case.find('skipped').attrib['message']}
                  for case in xml.iter('testcase') if case.find('skipped') is not None],
    }

files = [
    'packages/maverick-core/tests/test_tenant_lifecycle_recovery.py',
    'packages/maverick-core/tests/test_tenant_lifecycle_reservations.py',
    'packages/maverick-core/tests/test_backup.py',
    'packages/maverick-core/tests/test_backup_accounting_guard.py',
    'packages/maverick-core/tests/test_recovery_authority_registry.py',
    'packages/maverick-core/tests/test_persistence_authority_inventory.py',
    'packages/maverick-core/tests/test_workspace_bootstrap_recovery.py',
    'packages/maverick-core/tests/test_installation_accounting_recovery.py',
]
source_files = ['packages/maverick-core/maverick/backup.py', 'packages/maverick-core/maverick/recovery_authorities.py',
                files[0], 'docs/operations/persistence-authorities.json', 'docs/workspace-recovery.md']
report = {
    'schema': 'lightwork.tenant-lifecycle-recovery-qualification.v1',
    'date': '2026-09-30',
    'base_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    'coordinated_ux_inventory_source_sha': 'b6d19b46e831ce373dd97d8ec9e9c5186a366855',
    'changed_files_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in source_files},
    'boundary': 'Installation-wide namespace claims and deletion tombstones survive purge, signed full/additive restore and transaction recovery. An older authenticated backup is not current tenant admission.',
    'implementation': [
        'Explicit policies protect .tenant-namespaces/<shard>/<sha256 namespace key>.json and .tenant-deletions/<sha256 namespace key>.json in comparison and bootstrap.',
        'Full replacement refuses any existing lifecycle barrier absent or different in the archive before payload publication. Force does not bypass this check.',
        'Lifecycle files publish atomically without replacement; a concurrent claimant wins and is rechecked. Existing barriers are never overwritten or unlinked by restore.',
        'Rollback preserves barriers even when an older journal recorded existed=false. A missing original barrier is restored without replacement. Ordinary payload rollback remains intact.',
        'Bootstrap preserves exact historical bytes as unresolved global-start blockers. No automatic tenant rebind or readmission is introduced.',
        'Inventory records both current UX/feedback v2 tenant storage and retained unread legacy files. Only the requested owners changed; unrelated inventory entries are byte-equivalent after JSON parsing.',
    ],
    'new_regression_models': 13,
    'cohort_files': files,
    'command': 'python -m pytest ' + ' '.join(files) + ' -q',
    'ordinary_initial': run_record('tenant-lifecycle-recovery-02.xml'),
    'ordinary_correction': {
        'resolved_failure': 'The pre-existing missing goal_feedback_store.py persistence owner was added as ordinary customer data, as separately requested by the parent. All other ordinary cohort cases passed.',
        'rerun': run_record('tenant-lifecycle-inventory-03.xml'),
    },
    'secure_default': run_record('tenant-lifecycle-recovery-04.xml'),
    'environment': 'Windows native private-file/atomic-publication handles, matching-checkout Python3.12 venv, short isolated basetemp, task-local TEMP/TMP before Python. Secure run sets MAVERICK_TEST_SECURE_DEFAULT=1.',
    'static_checks': ['Ruff for the two implementation modules and new regression file passed', 'Python AST and inventory JSON parse passed', 'git diff --check passed', 'All unrelated persistence inventory entries preserved exactly'],
    'limits': [
        'Two symlink fixture cases skip because symlink creation is unavailable on this Windows host; no filesystem guard was weakened.',
        'A failed restore can intentionally leave newly published reservations/tombstones and stay in recovery mode. Identity history is not released to make a retry appear clean.',
        'No production restore, provider, installer/MSI execution, credentials or native signing changes. Installation boundaries in regression fixtures are synthetic.',
        'This focused source cohort does not replace consolidated final-branch or other-platform qualification.',
    ],
}
path = root / 'docs/ux-audit-2026-09-30/tenant-lifecycle-recovery-qualification.json'
path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
