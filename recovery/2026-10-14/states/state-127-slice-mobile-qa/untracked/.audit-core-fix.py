from pathlib import Path

def replace(path, old, new):
    path = Path(path)
    text = path.read_text(encoding='utf-8')
    assert old in text, (path, old)
    path.write_text(text.replace(old, new), encoding='utf-8', newline='\n')

core = 'packages/maverick-core/tests/'
path = Path(core + 'test_postgres_tenant_migrations.py')
body = path.read_text(encoding='utf-8')
body = body.replace('40, 41', '40, 41, 42, 43, 44, 45')
body = body.replace('== [41]', '== [41, 42, 43, 44, 45]')
body = body.replace('pg._PG_SCHEMA_VERSION == 41', 'pg._PG_SCHEMA_VERSION == 45')
body = body.replace('sql, query_params = cursor.calls[0]\n    assert scope in sql',
    'assert "pg_advisory_xact_lock" in cursor.calls[0][0]\n'
    '    sql, query_params = next(call for call in cursor.calls if call[0].lstrip().startswith("DELETE"))\n'
    '    assert scope in sql')
path.write_text(body, encoding='utf-8', newline='\n')
replace(core + 'test_world_owner.py', 'SCHEMA_VERSION == 41', 'SCHEMA_VERSION == 45')
replace(core + 'test_world_owner.py', 'world.candidate_goals(False) if goal.id == child', 'world.candidate_goals(False, owner="user:human-owner") if goal.id == child')
replace(core + 'test_skill_render_caps.py', 'SimpleNamespace(name=name, body=body)', 'SimpleNamespace(name=name, body=body, source="builtin")')
replace(core + 'test_q2_2026_batch4.py', 'assert params == ("tenant-a", 7)', 'assert "owner = %s" in sql\n    assert params == ("", "tenant-a", 7)')
for file in ('test_tenancy.py', 'test_q2_2026_batch2.py'):
    replace(core + file, 'def append_turn(self, conversation_id, role, text):', 'def append_turn(self, conversation_id, role, text, *, goal_id=None):')
