from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _run(code: str, tmp_path: Path, *, forced: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if forced:
        env["CRA_PSO_STANDALONE"] = "1"
    else:
        env.pop("CRA_PSO_STANDALONE", None)
    env.update(
        {
            "CRA_PSO_STORE": str(tmp_path / "records.json"),
            "MAVERICK_HOME": str(tmp_path / "maverick-home"),
            "PYTHONPATH": os.pathsep.join(
                (str(HERE), str(HERE.parents[1] / "packages" / "maverick-core"))
            ),
        }
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=HERE,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_forced_standalone_never_imports_maverick(tmp_path):
    result = _run(
        """
import app, backend, sys
assert backend.STANDALONE is True
assert backend.FORCED_STANDALONE is True
summary = backend.caps_summary()
assert summary['authority'] == 'unsigned-local'
assert summary['analysis_backend'] == 'vendored-rules'
assert summary['integrated_governance'] is False
assert summary['network_filing'] is False
assert summary['legal_decision_automation'] is False
assert summary['platform_authority_used'] is False
assert set(backend.CAPABILITY_BINDINGS) == set(backend.CAPS)
assert all(row['active'] is backend.CAPS[row['id']] for row in summary['capabilities'])
assert not any(name == 'maverick' or name.startswith('maverick.') for name in sys.modules)
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_auto_mode_adapts_only_core_clock_declarations(tmp_path):
    result = _run(
        """
import maverick.cra_ops as core
import os
from pathlib import Path
calls = []
for name in ('create_product','create_release','open_case','draft_report','approve_report','record_submission_receipt'):
    setattr(core, name, lambda *args, _name=name, **kwargs: calls.append(_name))
import backend
assert backend.PLATFORM_ENGINE is True
assert backend.caps_summary()['analysis_backend'] == 'lightwork-core-rules'
product = backend.create_product('Router','Acme','2030-01-01',0)
assert product['revision'] == 1
assert calls == []
assert backend.caps_summary()['platform_authority_used'] is False
assert not Path(os.environ['MAVERICK_HOME']).exists()
""",
        tmp_path,
        forced=False,
    )
    assert result.returncode == 0, result.stderr


def test_bounded_cyclonedx_and_spdx_normalization(tmp_path):
    result = _run(
        """
from cra_engine import normalize_sbom
cdx = normalize_sbom({
  'bomFormat':'CycloneDX','specVersion':'1.6','serialNumber':'urn:uuid:demo',
  'components':[
    {'type':'library','bom-ref':'app','name':'app','version':'1.0','properties':[{'name':'ignored_token','value':'never retained'}]},
    {'type':'library','bom-ref':'lib','name':'lib','version':'2.0','purl':'pkg:pypi/lib@2.0'},
  ],
  'dependencies':[{'ref':'app','dependsOn':['lib']}],
})
assert cdx['format'] == 'cyclonedx' and cdx['component_count'] == 2
assert cdx['dependencies'] == [['app','lib']]
assert cdx['raw_document_persisted'] is False
assert cdx['resolution_claim'] == 'declared_components_only_not_resolved_transitive'
assert 'never retained' not in str(cdx)
spdx = normalize_sbom({
  'spdxVersion':'SPDX-2.3','documentNamespace':'https://example.invalid/spdx/demo',
  'packages':[
    {'SPDXID':'SPDXRef-App','name':'app','versionInfo':'1','licenseDeclared':'MIT'},
    {'SPDXID':'SPDXRef-Lib','name':'lib','versionInfo':'2','licenseConcluded':'Apache-2.0'},
  ],
  'relationships':[{'spdxElementId':'SPDXRef-App','relationshipType':'DEPENDS_ON','relatedSpdxElement':'SPDXRef-Lib'}],
})
assert spdx['format'] == 'spdx' and spdx['spec_version'] == '2.3'
assert spdx['dependencies'] == [['SPDXRef-App','SPDXRef-Lib']]
assert len(spdx['source_document_sha256']) == 64 and len(spdx['normalized_sha256']) == 64
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_sbom_rejects_broken_graph_conflicts_size_and_depth(tmp_path):
    result = _run(
        """
from cra_engine import normalize_sbom
bad = [
 {'bomFormat':'CycloneDX','specVersion':'1.6','components':[{'bom-ref':'a','name':'a'}],'dependencies':[{'ref':'a','dependsOn':['missing']}]},
 {'bomFormat':'CycloneDX','specVersion':'1.6','components':[{'bom-ref':'a','name':'a'},{'bom-ref':'a','name':'different'}]},
 {'bomFormat':'CycloneDX','specVersion':'1.6','components':[{'name':'x' * (513 * 1024)}]},
]
for item in bad:
    try: normalize_sbom(item)
    except ValueError: pass
    else: raise AssertionError('invalid SBOM accepted')
deep = current = {}
for _ in range(14):
    current['next'] = {}
    current = current['next']
deep.update({'bomFormat':'CycloneDX','specVersion':'1.6','components':[]})
try: normalize_sbom(deep)
except ValueError as exc: assert 'nesting' in str(exc)
else: raise AssertionError('deep SBOM accepted')
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_vulnerability_workflow_human_gates_clocks_and_digest_binding(tmp_path):
    result = _run(
        """
import socket
from fastapi.testclient import TestClient
from app import app

client = TestClient(app, base_url='http://127.0.0.1')
client.__enter__()
product = client.post('/api/products', json={
 'name':'Edge Router','manufacturer':'Acme','support_end_date':'2032-12-31','expected_revision':0,
}).json()
assert product['cra_applicability']['status'] == 'pending_human_decision'
decision = client.post(f"/api/products/{product['id']}/applicability", json={
 'decision':'applicable','reviewer':'legal owner','rationale':'Human review of product scope','expected_revision':1,
})
assert decision.status_code == 200
release = client.post('/api/releases', json={
 'product_id':product['id'],'version':'4.2','released_at':'2027-01-01T00:00:00Z',
 'sbom':{'bomFormat':'CycloneDX','specVersion':'1.6','components':[{'bom-ref':'router','name':'router','version':'4.2'}],'dependencies':[]},
 'expected_revision':2,
}).json()
assert release['sbom']['raw_document_persisted'] is False
case = client.post('/api/cases', json={
 'product_id':product['id'],'release_id':release['id'],'case_type':'actively_exploited_vulnerability',
 'awareness_at':'2027-01-30T12:00:00Z','title':'Potential exploited parser flaw','expected_revision':3,
}).json()
assert case['reportability']['status'] == 'pending_human_decision'
assert case['clock_state'] == 'pending_human_decision' and case['clocks'] == {}
pending_workspace = client.get('/')
assert 'Not active — pending human reportability decision' in pending_workspace.text
assert '2027-01-31T12:00:00Z' not in pending_workspace.text
blocked = client.post('/api/reports', json={
 'case_id':case['id'],'stage':'early_warning','summary':'Draft','facts':{},'author':'security','expected_revision':4,
})
assert blocked.status_code == 422 and 'human' in blocked.text
case = client.post(f"/api/cases/{case['id']}/reportability", json={
 'decision':'reportable','reviewer':'product counsel','rationale':'Human Article 14 assessment','expected_revision':4,
}).json()
assert case['clock_state'] == 'active'
assert case['clocks']['early_warning']['due_at'] == '2027-01-31T12:00:00Z'
assert case['clocks']['full_notification']['due_at'] == '2027-02-02T12:00:00Z'
assert case['clocks']['final_report']['state'] == 'awaiting_anchor'
reportable_workspace = client.get('/')
assert '2027-01-31T12:00:00Z' in reportable_workspace.text
case = client.post(f"/api/cases/{case['id']}/anchors", json={
 'anchor':'corrective_measure_available_at','occurred_at':'2027-02-10T12:00:00Z','actor':'PSIRT','expected_revision':5,
}).json()
assert case['clocks']['final_report']['due_at'] == '2027-02-24T12:00:00Z'
report = client.post('/api/reports', json={
 'case_id':case['id'],'stage':'final_report','summary':'Corrective release available',
 'facts':{'fixed_version':'4.2.1'},'author':'PSIRT','expected_revision':6,
}).json()
wrong = client.post(f"/api/reports/{report['id']}/decision", json={
 'decision':'approve','reviewer':'counsel','rationale':'Reviewed','packet_sha256':'0'*64,'expected_revision':7,
})
assert wrong.status_code == 422
approved = client.post(f"/api/reports/{report['id']}/decision", json={
 'decision':'approve','reviewer':'counsel','rationale':'Reviewed exact packet',
 'packet_sha256':report['packet_sha256'],'expected_revision':7,
}).json()
assert approved['approval']['packet_sha256'] == report['packet_sha256']
wrong_receipt = client.post(f"/api/reports/{report['id']}/receipts", json={
 'packet_sha256':'f'*64,'receipt_reference':'external-CSIRT-wrong',
 'submitted_at':'2027-02-24T13:00:00Z','recorded_by':'filing operator','expected_revision':8,
})
assert wrong_receipt.status_code == 422
original = socket.socket.connect
socket.socket.connect = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network call attempted'))
receipt = client.post(f"/api/reports/{report['id']}/receipts", json={
 'packet_sha256':report['packet_sha256'],'receipt_reference':'external-CSIRT-123',
 'submitted_at':'2027-02-24T13:00:00Z','recorded_by':'filing operator','expected_revision':8,
})
socket.socket.connect = original
assert receipt.status_code == 200, receipt.text
saved = receipt.json()
assert saved['packet_sha256'] == approved['approval']['packet_sha256']
assert saved['network_call_made'] is False
assert saved['filing_performed_by_lightwork'] is False
client.__exit__(None, None, None)
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_incident_final_clock_is_one_calendar_month_after_full_submission(tmp_path):
    result = _run(
        """
from cra_engine import project_article14_clocks
case = {
 'id':'case-1','case_type':'severe_incident','awareness_at':'2027-01-28T10:15:00Z',
 'reportability':{'status':'reportable'},
 'full_notification_submitted_at':'2027-01-31T18:30:00Z',
}
clocks = project_article14_clocks(case)
assert clocks['early_warning']['due_at'] == '2027-01-29T10:15:00Z'
assert clocks['full_notification']['due_at'] == '2027-01-31T10:15:00Z'
assert clocks['final_report']['anchor'] == 'full_notification_submission'
assert clocks['final_report']['state'] == 'awaiting_anchor'
case['full_notification_submission_receipt'] = {
 'type':'submission_receipt','report_id':'report-1','case_id':'case-1',
 'stage':'full_notification','packet_sha256':'a'*64,
 'approved_packet_sha256':'a'*64,
 'approval_decided_at':'2027-01-31T18:00:00Z',
 'submitted_at':'2027-01-31T18:30:00Z',
}
clocks = project_article14_clocks(case)
assert clocks['final_report']['due_at'] == '2027-02-28T18:30:00Z'
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_local_receipt_cannot_predate_exact_packet_approval(tmp_path):
    result = _run(
        """
from datetime import timedelta
from cra_engine import (
    _iso, _parse_timestamp, create_report_draft, decide_report,
    record_submission_receipt,
)

case = {
 'type':'cra_case','id':'case-chronology','case_type':'severe_incident',
 'product_id':'product-1','release_id':'release-1',
 'awareness_at':'2020-01-01T00:00:00Z','clocks':{},
 'reportability':{'status':'reportable'},
}
report = create_report_draft(
    case, 'full_notification', 'Reviewed notification', {}, 'PSIRT'
)
report['id'] = 'report-chronology'
approved = decide_report(
    report, 'approve', 'counsel', 'Reviewed exact packet',
    report['packet_sha256'],
)
approval_at = _parse_timestamp(approved['approval']['decided_at'], 'approval')
try:
    record_submission_receipt(
        approved,
        approved['packet_sha256'],
        'backdated-attestation',
        _iso(approval_at - timedelta(seconds=1)),
        'operator',
    )
except ValueError as exc:
    assert 'exact-packet approval' in str(exc)
else:
    raise AssertionError('receipt predating approval was accepted')
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_severe_incident_anchor_requires_atomic_approved_full_receipt(tmp_path):
    result = _run(
        """
import json
import os
from pathlib import Path
from fastapi.testclient import TestClient
from app import app

client = TestClient(app, base_url='http://127.0.0.1')
product = client.post('/api/products', json={
 'name':'Controller','manufacturer':'Acme','support_end_date':'2032-12-31',
 'expected_revision':0,
}).json()
client.post(f"/api/products/{product['id']}/applicability", json={
 'decision':'applicable','reviewer':'counsel','rationale':'Reviewed scope',
 'expected_revision':1,
})
release = client.post('/api/releases', json={
 'product_id':product['id'],'version':'1.0','released_at':'2027-01-01T00:00:00Z',
 'sbom':{'bomFormat':'CycloneDX','specVersion':'1.6','components':[],'dependencies':[]},
 'expected_revision':2,
}).json()
case = client.post('/api/cases', json={
 'product_id':product['id'],'release_id':release['id'],'case_type':'severe_incident',
 'awareness_at':'2027-01-28T10:15:00Z','title':'Potential severe incident',
 'expected_revision':3,
}).json()
case = client.post(f"/api/cases/{case['id']}/reportability", json={
 'decision':'reportable','reviewer':'counsel','rationale':'Human Article 14 decision',
 'expected_revision':4,
}).json()
manual = client.post(f"/api/cases/{case['id']}/anchors", json={
 'anchor':'full_notification_submitted_at','occurred_at':'2027-01-31T18:30:00Z',
 'actor':'operator','expected_revision':5,
})
assert manual.status_code == 422 and 'only by an approved' in manual.text
assert client.get('/api/records').json()['revision'] == 5

report = client.post('/api/reports', json={
 'case_id':case['id'],'stage':'full_notification','summary':'Reviewed notification',
 'facts':{'incident_status':'confirmed'},'author':'PSIRT','expected_revision':5,
}).json()
unapproved = client.post(f"/api/reports/{report['id']}/receipts", json={
 'packet_sha256':report['packet_sha256'],'receipt_reference':'premature',
 'submitted_at':'2027-01-31T18:30:00Z','recorded_by':'operator',
 'expected_revision':6,
})
assert unapproved.status_code == 422 and 'approved report' in unapproved.text
approved = client.post(f"/api/reports/{report['id']}/decision", json={
 'decision':'approve','reviewer':'counsel','rationale':'Reviewed exact packet',
 'packet_sha256':report['packet_sha256'],'expected_revision':6,
}).json()
premature_time = client.post(f"/api/reports/{report['id']}/receipts", json={
 'packet_sha256':report['packet_sha256'],'receipt_reference':'bad-time',
 'submitted_at':'2027-01-27T18:30:00Z','recorded_by':'operator',
 'expected_revision':7,
})
assert premature_time.status_code == 422 and 'cannot precede' in premature_time.text
assert client.get('/api/records').json()['revision'] == 7

receipt = client.post(f"/api/reports/{report['id']}/receipts", json={
 'packet_sha256':approved['packet_sha256'],'receipt_reference':'SRP-accepted',
 'submitted_at':'2027-01-31T18:30:00Z','recorded_by':'operator',
 'expected_revision':7,
})
assert receipt.status_code == 200, receipt.text
receipt = receipt.json()
state = client.get('/api/records').json()
assert state['revision'] == receipt['revision'] == 8
saved_case = next(row for row in state['records'] if row.get('id') == case['id'])
anchor = saved_case['full_notification_submission_receipt']
assert anchor['report_id'] == report['id']
assert anchor['packet_sha256'] == approved['approval']['packet_sha256']
assert anchor['submitted_at'] == receipt['submitted_at']
assert saved_case['clocks']['final_report']['anchor_at'] == receipt['submitted_at']
assert saved_case['clocks']['final_report']['due_at'] == '2027-02-28T18:30:00Z'

store_path = Path(os.environ['CRA_PSO_STORE'])
persisted = json.loads(store_path.read_text(encoding='utf-8'))
stored_receipt = next(
    row for row in persisted['records'] if row.get('type') == 'submission_receipt'
)
stored_receipt['approval_decided_at'] = '2099-01-01T00:00:00Z'
store_path.write_text(json.dumps(persisted), encoding='utf-8')
try:
    client.get('/api/records')
except ValueError as exc:
    assert 'exact approved report' in str(exc)
else:
    raise AssertionError('tampered stored receipt was exported')
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_applicability_is_an_enforced_one_time_human_gate(tmp_path):
    result = _run(
        """
import backend
product = backend.create_product('Controller','Acme','2030-01-01',0)
release = backend.create_release(product['id'],'1','2027-01-01T00:00:00Z',{
 'bomFormat':'CycloneDX','specVersion':'1.6','components':[],'dependencies':[],
},1)
try:
    backend.open_case(product['id'],release['id'],'severe_incident','2027-01-01T00:00:00Z','Potential incident',2)
except ValueError as exc: assert 'human must mark the product applicable' in str(exc)
else: raise AssertionError('undecided product opened a CRA case')
decided = backend.decide_product(product['id'],'applicable','counsel','Reviewed product scope',2)
assert decided['cra_applicability']['human_decision'] is True
try:
    backend.decide_product(product['id'],'not_applicable','other','Changed mind',3)
except ValueError as exc: assert 'already been decided' in str(exc)
else: raise AssertionError('product applicability was silently overwritten')
case = backend.open_case(product['id'],release['id'],'severe_incident','2027-01-01T00:00:00Z','Potential incident',3)
assert case['reportability']['status'] == 'pending_human_decision'
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_not_applicable_product_cannot_open_case_or_project_clocks(tmp_path):
    result = _run(
        """
import backend
product = backend.create_product('Excluded controller','Acme','2030-01-01',0)
product = backend.decide_product(
    product['id'], 'not_applicable', 'counsel', 'Documented exclusion', 1,
)
release = backend.create_release(product['id'],'1','2027-01-01T00:00:00Z',{
 'bomFormat':'CycloneDX','specVersion':'1.6','components':[],'dependencies':[],
},2)
try:
    backend.open_case(
        product['id'], release['id'], 'severe_incident',
        '2027-01-01T00:00:00Z', 'Must remain outside CRA clocks', 3,
    )
except ValueError as exc:
    assert 'human must mark the product applicable' in str(exc)
else:
    raise AssertionError('not-applicable product opened a CRA case with clocks')
assert backend.list_records('cra_case') == []
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_not_reportable_case_never_persists_or_renders_deadlines(tmp_path):
    result = _run(
        """
from fastapi.testclient import TestClient
from app import app

client = TestClient(app, base_url='http://127.0.0.1')
product = client.post('/api/products', json={
 'name':'Reviewed controller','manufacturer':'Acme','support_end_date':'2030-01-01',
 'expected_revision':0,
}).json()
client.post(f"/api/products/{product['id']}/applicability", json={
 'decision':'applicable','reviewer':'counsel','rationale':'Reviewed product scope',
 'expected_revision':1,
})
release = client.post('/api/releases', json={
 'product_id':product['id'],'version':'1','released_at':'2027-01-01T00:00:00Z',
 'sbom':{'bomFormat':'CycloneDX','specVersion':'1.6','components':[],'dependencies':[]},
 'expected_revision':2,
}).json()
case = client.post('/api/cases', json={
 'product_id':product['id'],'release_id':release['id'],
 'case_type':'actively_exploited_vulnerability',
 'awareness_at':'2027-03-01T09:00:00Z','title':'Reviewed non-reportable case',
 'expected_revision':3,
}).json()
assert case['clocks'] == {} and case['clock_state'] == 'pending_human_decision'
assert '2027-03-02T09:00:00Z' not in client.get('/').text
case = client.post(f"/api/cases/{case['id']}/reportability", json={
 'decision':'not_reportable','reviewer':'counsel',
 'rationale':'Human review found the Article 14 trigger absent',
 'expected_revision':4,
}).json()
assert case['clocks'] == {} and case['clock_state'] == 'not_active'
workspace = client.get('/')
assert 'Not active — case marked not reportable' in workspace.text
assert '2027-03-02T09:00:00Z' not in workspace.text
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_vex_is_local_draft_and_sensitive_report_fields_are_rejected(tmp_path):
    result = _run(
        """
from cra_engine import create_product, create_release, create_vex_draft, create_report_draft
product = {**create_product('P','M','2030-01-01'),'id':'p1'}
release = {**create_release(product,'1','2027-01-01T00:00:00Z',{'spdxVersion':'SPDX-2.3','packages':[],'relationships':[]}), 'id':'r1'}
vex = create_vex_draft(product, release, 'CVE-2027-0001', 'under_investigation', 'Analysis in progress', 'PSIRT')
assert vex['workflow_status'] == 'draft' and vex['authority'] == 'unsigned_local'
case = {'type':'cra_case','id':'c1','case_type':'severe_incident','product_id':'p1','release_id':'r1','awareness_at':'2027-01-01T00:00:00Z','clocks':{},'reportability':{'status':'reportable'}}
try: create_report_draft(case,'early_warning','Summary',{'api_key':'do-not-store'},'PSIRT')
except ValueError as exc: assert 'sensitive field' in str(exc)
else: raise AssertionError('secret-shaped field accepted')
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_http_limits_origin_cas_and_about_disclosure(tmp_path):
    result = _run(
        """
from fastapi.testclient import TestClient
from app import app
client = TestClient(app, base_url='http://127.0.0.1')
about = client.get('/about')
for marker in ('Capability and authority matrix','unsigned local only','No automatic legal decision or filing','Integrated Lightwork only / prohibited','packet digest'):
    assert marker.lower() in about.text.lower(), marker
body = {'name':'P','manufacturer':'M','support_end_date':'2030-01-01','expected_revision':0}
assert client.post('/api/products', json={**body,'password':'x'}).status_code == 422
assert client.post('/api/products', json={k:v for k,v in body.items() if k != 'expected_revision'}).status_code == 422
assert client.post('/api/products', json={**body,'expected_revision':False}).status_code == 422
assert client.post('/api/products', content='{}', headers={'Content-Type':'text/plain'}).status_code == 415
assert client.post('/api/products', json=body, headers={'Origin':'https://evil.example'}).status_code == 403
saved = client.post('/api/products', json=body, headers={'Origin':'http://127.0.0.1'})
assert saved.status_code == 200
assert client.post('/api/products', json=body).status_code == 409
huge = b'{"name":"' + b'x' * (641 * 1024) + b'"}'
assert client.post('/api/products', content=huge, headers={'Content-Type':'application/json'}).status_code == 413
health = client.get('/healthz').json()
assert health['automatic_legal_decisions'] is False and health['network_regulator_filing'] is False
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_workspace_renders_after_records_and_browser_surface_is_hardened(tmp_path):
    result = _run(
        """
from fastapi.testclient import TestClient
from app import app

client = TestClient(app, base_url='http://127.0.0.1')
saved = client.post('/api/products', json={
 'name':'Router','manufacturer':'Acme','support_end_date':'2030-01-01',
 'expected_revision':0,
})
assert saved.status_code == 200, saved.text
workspace = client.get('/')
assert workspace.status_code == 200, workspace.text
assert 'pending_human_decision' in workspace.text
assert workspace.headers['x-frame-options'] == 'DENY'
assert workspace.headers['x-content-type-options'] == 'nosniff'
assert workspace.headers['referrer-policy'] == 'no-referrer'
assert workspace.headers['cache-control'] == 'no-store'
csp = workspace.headers['content-security-policy']
assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
assert client.get('/docs').status_code == 404
assert client.get('/redoc').status_code == 404
assert client.get('/openapi.json').status_code == 404
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_local_store_cas_is_cross_instance_safe_and_private(tmp_path):
    result = _run(
        """
import json, os, stat, threading
from pathlib import Path
from cra_engine import LocalStore
path = Path(os.environ['CRA_PSO_STORE'])
stores = LocalStore(path), LocalStore(path)
barrier = threading.Barrier(2)
results = []
def save(store, record_id):
    barrier.wait()
    try:
        store.save({'id':record_id,'type':'product'},0); results.append('saved')
    except ValueError as exc:
        assert 'revision changed' in str(exc); results.append('conflict')
threads = [threading.Thread(target=save,args=pair) for pair in zip(stores,('one','two'))]
for thread in threads: thread.start()
for thread in threads: thread.join()
state = json.loads(path.read_text(encoding='utf-8'))
assert sorted(results) == ['conflict','saved']
assert state['revision'] == 1 and len(state['records']) == 1
if os.name != 'nt': assert stat.S_IMODE(path.stat().st_mode) == 0o600
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_local_store_rejects_oversize_commit_without_replacing_prior_state(tmp_path):
    result = _run(
        """
from pathlib import Path
import os
from cra_engine import LocalStore, MAX_LOCAL_STORE_BYTES

path = Path(os.environ['CRA_PSO_STORE'])
store = LocalStore(path)
store.save({'id':'kept','type':'product','name':'safe'}, 0)
before = path.read_bytes()
try:
    store.save({'id':'too-large','type':'product','blob':'x' * MAX_LOCAL_STORE_BYTES}, 1)
except ValueError as exc:
    assert 'would exceed' in str(exc)
else:
    raise AssertionError('oversize store commit was accepted')
assert path.read_bytes() == before
assert store.current_revision() == 1
assert [row['id'] for row in store.list_records()] == ['kept']
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_release_manifest_and_forced_launcher_contract():
    manifest = json.loads((HERE / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["entrypoint"] == "serve_standalone.py"
    expected_files = set(manifest["files"])
    if (HERE / "RELEASE-METADATA.json").is_file():
        metadata = json.loads(
            (HERE / "RELEASE-METADATA.json").read_text(encoding="utf-8")
        )
        metadata_files = {
            "release-manifest.json",
            *manifest["files"],
            *(Path(path).name for path in manifest["shared_files"]),
        }
        assert set(metadata["files"]) == metadata_files
        assert metadata["entrypoint"] == manifest["entrypoint"]
        assert metadata["authority_boundary"].startswith("unsigned local")
        for relative, binding in metadata["files"].items():
            payload = (HERE / relative).read_bytes()
            assert binding == {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        assert (HERE / "LICENSE").is_file()
        assert (HERE / "TRADEMARK.md").is_file()
        expected_files.update(
            Path(path).name for path in manifest["shared_files"]
        )
        expected_files.add("RELEASE-METADATA.json")
    assert expected_files == {
        str(path.relative_to(HERE)).replace("\\", "/")
        for path in HERE.rglob("*")
        if path.is_file()
        and path.name not in {"release-manifest.json"}
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
        and not path.name.endswith(".pyc")
        and path.name != ".gitignore"
    }
    launcher = (HERE / "serve_standalone.py").read_text(encoding="utf-8")
    assert launcher.index('os.environ["CRA_PSO_STANDALONE"] = "1"') < launcher.index(
        "import uvicorn"
    )
    app_source = (HERE / "app.py").read_text(encoding="utf-8")
    assert "import maverick" not in app_source
    assert "STANDALONE" not in app_source and "PLATFORM_ENGINE" not in app_source


def test_headerless_stream_stops_before_buffering_oversized_body(tmp_path):
    result = _run(
        """
import asyncio
from starlette.requests import Request
from request_limits import MAX_REQUEST_BYTES, PayloadError, read_bounded_json
chunks = [b'{' + b'x' * 400000, b'y' * 400000, b'never-read']
calls = 0
async def receive():
    global calls
    chunk = chunks[calls]; calls += 1
    return {'type':'http.request','body':chunk,'more_body':calls < len(chunks)}
scope = {'type':'http','method':'POST','scheme':'http','path':'/api/products','query_string':b'',
 'headers':[(b'host',b'127.0.0.1'),(b'content-type',b'application/json')],
 'server':('127.0.0.1',80),'client':('127.0.0.1',1234)}
try: asyncio.run(read_bounded_json(Request(scope, receive)))
except PayloadError as exc: assert exc.status_code == 413
else: raise AssertionError('oversized stream was accepted')
assert calls == 2 and MAX_REQUEST_BYTES < 800000
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
