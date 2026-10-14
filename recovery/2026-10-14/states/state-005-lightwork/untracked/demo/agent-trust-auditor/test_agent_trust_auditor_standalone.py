from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _run(code: str, tmp_path: Path):
    env = os.environ.copy()
    env.update(
        {
            "AGENT_TRUST_AUDITOR_STANDALONE": "1",
            "AGENT_TRUST_AUDITOR_STORE": str(tmp_path / "derived.json"),
            "PYTHONPATH": str(HERE),
        }
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=HERE,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _auto(code: str, tmp_path: Path):
    env = os.environ.copy()
    env.pop("AGENT_TRUST_AUDITOR_STANDALONE", None)
    core = HERE.parents[1] / "packages" / "maverick-core"
    env.update(
        {
            "AGENT_TRUST_AUDITOR_STORE": str(tmp_path / "derived.json"),
            "MAVERICK_HOME": str(tmp_path / "maverick-home"),
            "PYTHONPATH": os.pathsep.join((str(HERE), str(core))),
        }
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=HERE,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_forced_standalone_blocks_every_maverick_import(tmp_path):
    code = r"""
import importlib.abc
import sys

class BlockMaverick(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'maverick' or fullname.startswith('maverick.'):
            raise AssertionError(f'forbidden import attempted: {fullname}')
        return None

sys.meta_path.insert(0, BlockMaverick())
import app, backend
assert backend.STANDALONE is True
assert backend.FORCED_STANDALONE is True
summary = backend.caps_summary()
assert summary['forced_standalone'] is True
assert summary['vendored_analysis'] is True
assert summary['core_analysis'] is False
assert summary['analysis_backend'] == 'vendored'
assert summary['integrated_governance'] is False
assert summary['authority'] == 'unsigned-local-derived-only'
assert set(backend.CAPABILITY_BINDINGS) == set(backend.CAPS)
assert all(row['active'] is backend.CAPS[row['id']] for row in summary['capabilities'])
assert not any(name == 'maverick' or name.startswith('maverick.') for name in sys.modules)
"""
    result = _run(code, tmp_path)
    assert result.returncode == 0, result.stderr


def test_deterministic_five_rule_analysis_and_derived_only_store(tmp_path):
    code = r"""
import json
import backend
import trust_engine

declared = [{
    'source': 'inventory.cmdb',
    'source_id': 'agent-7',
    'entity_type': 'agent',
    'display_name': 'Billing helper',
    'owner': '',
    'allowed_tools': ['read_invoice'],
}]
observations = [{
    'observation_id': 'span-123',
    'source': 'inventory.cmdb',
    'source_id': 'agent-7',
    'entity_type': 'agent',
    'display_name': 'Billing helper runtime',
    'observed_at': 100,
    'observed_tools': ['read_invoice', 'delete_invoice'],
    'pin_required': True,
    'pinned': False,
    'allow_all_tools': True,
    'metadata': {'environment': 'production', 'tool_type': 'function'},
}]
first = backend.analyze(declared, observations, now=1000, stale_after_seconds=300)
second = backend.analyze(declared, observations, now=1000, stale_after_seconds=300)
assert first == second
assert {row['finding_type'] for row in first} == {
    'unowned', 'unpinned', 'overprivileged', 'stale', 'undeclared-tool'
}
entity_id = trust_engine.stable_entity_id('inventory.cmdb', 'agent-7')
other = trust_engine.stable_entity_id('runtime.otel', 'agent-7')
assert entity_id != other
assert {row['entity_id'] for row in first} == {entity_id}
for row in first:
    assert row['observation_ids']
    assert {c['observation_id'] for c in row['citations']} == set(row['observation_ids'])
saved = backend.save_derived(first, 0)
state = backend.state()
assert state['revision'] == len(saved) == 5
assert all(row['type'] == 'finding' for row in state['records'])
text = open(backend.STORE.path, encoding='utf-8').read()
for marker in ('span-123', 'production', 'Billing helper runtime'):
    assert marker not in text
assert 'delete_invoice' in text
print(json.dumps({'types': sorted(row['finding_type'] for row in saved)}))
"""
    result = _run(code, tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["types"] == [
        "overprivileged",
        "stale",
        "undeclared-tool",
        "unowned",
        "unpinned",
    ]


def test_api_rejects_sensitive_content_and_never_persists_inputs(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from app import app

client = TestClient(app, base_url='http://127.0.0.1')
base = {
    'declared_assets': [{
        'source': 'cmdb', 'source_id': 'a1', 'entity_type': 'agent',
        'owner': 'security', 'allowed_tools': ['search'],
    }],
    'observations': [{
        'observation_id': 'o1', 'source': 'cmdb', 'source_id': 'a1',
        'entity_type': 'agent', 'observed_at': 100, 'observed_tools': ['search'],
        'metadata': {'environment': 'test'},
    }],
    'expected_revision': 0,
    'now': 101,
}
accepted = client.post('/api/analyze', json=base)
assert accepted.status_code == 200, accepted.text
assert accepted.json()['findings'] == []
assert accepted.json()['observations_persisted'] is False
assert accepted.json()['declared_assets_persisted'] is False
assert accepted.json()['authority_mutated'] is False
assert client.get('/api/findings').json() == {'revision': 0, 'records': []}
future = dict(base)
future['observations'] = [dict(base['observations'][0], observed_at=1000)]
assert client.post('/api/analyze', json=future).status_code == 422
for key in (
    'credentials', 'access_token', 'prompt', 'tool_arguments',
    'tool_results', 'raw_telemetry', 'request_body', 'api_key',
    'private_key', 'messages', 'completion', 'tool_calls',
):
    hostile = dict(base)
    hostile['observations'] = [dict(base['observations'][0])]
    hostile['observations'][0]['metadata'] = {'environment': 'test', key: 'secret'}
    response = client.post('/api/analyze', json=hostile)
    assert response.status_code == 422, (key, response.text)
    assert 'not accepted' in response.text, (key, response.text)
"""
    result = _run(code, tmp_path)
    assert result.returncode == 0, result.stderr


def test_about_is_explicit_and_no_mutating_or_discovery_routes_exist(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from app import app

client = TestClient(app, base_url='http://127.0.0.1')
text = client.get('/about').text
for marker in (
    'Analysis backend', 'vendored', 'unsigned local derived findings only',
    'Capability and upsell matrix', 'Every advertised capability',
    'Integrated Lightwork only', 'trust_engine.analyze_snapshot vendored rules',
    'does not discover live assets', 'claim certification',
):
    assert marker in text, marker
for path in (
    '/api/discover', '/api/authority', '/api/lifecycle', '/api/evidence-pack',
    '/api/agents/a1/suspend', '/api/findings/f1/close',
):
    assert client.post(path, json={}).status_code == 404, path
"""
    result = _run(code, tmp_path)
    assert result.returncode == 0, result.stderr


def test_browser_surface_is_not_frameable_and_has_no_remote_docs_ui(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from app import app

client = TestClient(app, base_url='http://127.0.0.1')
response = client.get('/')
assert response.status_code == 200
assert response.headers['x-frame-options'] == 'DENY'
assert response.headers['x-content-type-options'] == 'nosniff'
assert response.headers['referrer-policy'] == 'no-referrer'
assert response.headers['cache-control'] == 'no-store'
csp = response.headers['content-security-policy']
assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
assert client.get('/docs').status_code == 404
assert client.get('/redoc').status_code == 404
assert client.get('/openapi.json').status_code == 404
"""
    result = _run(code, tmp_path)
    assert result.returncode == 0, result.stderr


def test_core_mode_reuses_analysis_only_and_never_writes_audit(tmp_path):
    code = r"""
import maverick.audit
audit_calls = []
maverick.audit.record = lambda *args, **kwargs: audit_calls.append((args, kwargs)) or True
import backend
assert backend.CORE_ANALYSIS is True
assert backend.STANDALONE is False
summary = backend.caps_summary()
assert summary['analysis_backend'] == 'lightwork-core'
assert summary['authority'] == 'unsigned-local-derived-only'
declared = [{
    'source': 'cmdb', 'source_id': 'a1', 'entity_type': 'agent',
    'owner': '', 'allowed_tools': [],
}]
observations = [{
    'observation_id': 'o1', 'source': 'cmdb', 'source_id': 'a1',
    'entity_type': 'agent', 'observed_at': 100, 'observed_tools': ['shell'],
    'allow_all_tools': True,
}]
rows = backend.analyze(declared, observations, now=101)
assert {'unowned', 'overprivileged', 'undeclared-tool'} <= {
    row['finding_type'] for row in rows
}
backend.save_derived(rows, 0)
assert audit_calls == []
"""
    result = _auto(code, tmp_path)
    assert result.returncode == 0, result.stderr


def test_http_boundaries_revision_conflict_and_loopback_origin(tmp_path):
    code = r"""
import json
from fastapi.testclient import TestClient
from app import app

client = TestClient(app, base_url='http://127.0.0.1')
valid = {
    'declared_assets': [],
    'observations': [{
        'observation_id': 'o1', 'source': 'runtime', 'source_id': 'a1',
        'entity_type': 'agent', 'observed_at': 100,
    }],
    'expected_revision': 0,
    'now': 101,
}
assert client.post('/api/analyze', json={k: v for k, v in valid.items() if k != 'expected_revision'}).status_code == 422
assert client.post('/api/analyze', json={**valid, 'expected_revision': False}).status_code == 422
plain = client.post(
    '/api/analyze', content=json.dumps(valid), headers={'Content-Type': 'text/plain'}
)
assert plain.status_code == 415
hostile = client.post(
    '/api/analyze', json=valid, headers={'Origin': 'https://evil.example'}
)
assert hostile.status_code == 403
assert client.get('/', headers={'Host': 'evil.example'}).status_code == 400
accepted = client.post(
    '/api/analyze', json=valid, headers={'Origin': 'http://127.0.0.1'}
)
assert accepted.status_code == 200, accepted.text
assert accepted.json()['revision'] == 1
conflict = client.post('/api/analyze', json=valid)
assert conflict.status_code == 409, conflict.text
huge = b'{"declared_assets":[],"observations":[],"padding":"' + b'x' * (513 * 1024) + b'"}'
assert client.post(
    '/api/analyze', content=huge, headers={'content-type': 'application/json'}
).status_code == 413
"""
    result = _run(code, tmp_path)
    assert result.returncode == 0, result.stderr


def test_store_cas_is_cross_instance_safe(tmp_path):
    code = r"""
import json
import os
import threading
from pathlib import Path
from trust_engine import LocalDerivedStore, analyze_snapshot

path = Path(os.environ['AGENT_TRUST_AUDITOR_STORE'])
stores = (LocalDerivedStore(path), LocalDerivedStore(path))
finding = analyze_snapshot(
    [],
    [{
        'observation_id': 'o1', 'source': 'runtime', 'source_id': 'a1',
        'entity_type': 'agent', 'observed_at': 100,
    }],
    now=101,
)[0]
barrier = threading.Barrier(2)
results = []

def save(store):
    barrier.wait()
    try:
        store.save_findings([finding], 0)
        results.append('saved')
    except ValueError as exc:
        assert 'revision changed' in str(exc)
        results.append('conflict')

threads = [threading.Thread(target=save, args=(store,)) for store in stores]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
state = json.loads(path.read_text(encoding='utf-8'))
assert sorted(results) == ['conflict', 'saved']
assert state['revision'] == 1 and len(state['records']) == 1
"""
    result = _run(code, tmp_path)
    assert result.returncode == 0, result.stderr


def test_store_deduplicates_stable_findings_and_evicts_oldest_at_cap(tmp_path):
    code = r"""
import trust_engine
from trust_engine import LocalDerivedStore, analyze_snapshot

store = LocalDerivedStore()
def finding(source_id, observation_id):
    return analyze_snapshot(
        [],
        [{
            'observation_id': observation_id, 'source': 'runtime',
            'source_id': source_id, 'entity_type': 'agent', 'observed_at': 100,
        }],
        now=101,
    )[0]

first = finding('a1', 'o1')
saved = store.save_findings([first], 0)
assert saved[0]['revision'] == 1
same = store.save_findings([first], 1)
assert same[0]['revision'] == 1
assert store.state()['revision'] == 1
assert len(store.list_records()) == 1

updated = finding('a1', 'o2')
changed = store.save_findings([updated], 1)
assert changed[0]['id'] == saved[0]['id'] and changed[0]['revision'] == 2
assert len(store.list_records()) == 1
assert store.list_records()[0]['observation_ids'] != saved[0]['observation_ids']

trust_engine.MAX_DERIVED_FINDINGS = 2
second = finding('a2', 'o3')
third = finding('a3', 'o4')
store.save_findings([second], 2)
store.save_findings([third], 3)
state = store.state()
assert state['revision'] == 4 and len(state['records']) == 2
assert {row['id'] for row in state['records']} == {second['id'], third['id']}
"""
    result = _run(code, tmp_path)
    assert result.returncode == 0, result.stderr


def test_headerless_stream_stops_before_buffering_oversized_body(tmp_path):
    code = r"""
import asyncio
from starlette.requests import Request
from request_limits import PayloadError, read_bounded_json

chunks = [b'{' + b'x' * 300000, b'y' * 300000, b'never-read']
calls = 0

async def receive():
    global calls
    chunk = chunks[calls]
    calls += 1
    return {'type': 'http.request', 'body': chunk, 'more_body': calls < len(chunks)}

scope = {
    'type': 'http', 'method': 'POST', 'scheme': 'http', 'path': '/api/analyze',
    'query_string': b'',
    'headers': [(b'host', b'127.0.0.1'), (b'content-type', b'application/json')],
    'server': ('127.0.0.1', 80), 'client': ('127.0.0.1', 1234),
}
try:
    asyncio.run(read_bounded_json(Request(scope, receive)))
except PayloadError as exc:
    assert exc.status_code == 413
else:
    raise AssertionError('oversized streamed request was accepted')
assert calls == 2
"""
    result = _run(code, tmp_path)
    assert result.returncode == 0, result.stderr


def test_app_never_branches_on_runtime_mode_and_manifest_is_explicit():
    source = (HERE / "app.py").read_text(encoding="utf-8")
    assert "STANDALONE" not in source
    assert "CORE_ANALYSIS" not in source
    assert "import maverick" not in source
    engine = (HERE / "trust_engine.py").read_text(encoding="utf-8")
    assert "import maverick" not in engine
    manifest = json.loads((HERE / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_name"] == "lightwork-agent-trust-auditor"
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
        path.relative_to(HERE).as_posix()
        for path in HERE.rglob("*")
        if path.is_file()
        and path.name not in {"release-manifest.json", ".gitignore"}
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
    }
