import re
from pathlib import Path

core = Path('packages/maverick-core/tests')
path = core / 'test_runner_identity.py'
text = path.read_text(encoding='utf-8').replace('from types import SimpleNamespace\n', '')
for name in ('propagates_execution_identity', 'can_schedule_by_caller_principal'):
    text = text.replace(f'def test_run_goal_in_thread_{name}(monkeypatch):', f'def test_run_goal_in_thread_{name}(monkeypatch, tmp_path):')
text = re.sub(r'    class FakeWorld:\n.*?\n(?=    (?:def fake_run_goal_sync|class FakeSemaphore))',
    lambda match: '    db = tmp_path / "world.db"\n'
    '    with world_model.WorldModel(db) as world:\n'
    '        goal_id = world.create_goal("fixture task", owner="user:alice",\n'
    f'                                    execution_principal="{re.search(chr(34) + "user:[^" + chr(34) + "]+" + chr(34), match.group()).group()[1:-1]}")\n\n',
    text, flags=re.S)
# WorldModel has no context-manager contract; close the seed explicitly.
text = text.replace('    with world_model.WorldModel(db) as world:\n        goal_id = world.create_goal',
    '    world = world_model.WorldModel(db)\n    goal_id = world.create_goal')
text = text.replace('    def fake_run_goal_sync(*args, **kwargs):\n        captured.update(kwargs)',
    '    def fake_run_goal_sync(*args, **kwargs):\n        args[1].set_goal_status(args[3], "done")\n        captured.update(kwargs)')
text = text.replace('lambda *a, **k: FakeWorld()', 'lambda *a, **k: world_model.WorldModel(db)')
text = text.replace('    monkeypatch.setattr(llm_mod, "LLM", lambda: object())',
    '    world.close()\n    monkeypatch.setattr(llm_mod, "LLM", lambda: object())')
text = text.replace('        42,', '        goal_id,')
path.write_text(text, encoding='utf-8', newline='\n')

path = core / 'test_fleet_supervisor.py'
text = path.read_text(encoding='utf-8')
for name in ('threads_capability_into_ctx', 'closes_sandbox', 'default_capability_is_none'):
    text = text.replace(f'def test_run_goal_in_thread_{name}(monkeypatch):', f'def test_run_goal_in_thread_{name}(monkeypatch, tmp_path):')
def fleet_world(match):
    principal = re.search('execution_principal="([^"]+)"', match.group()).group(1)
    return ('    db = tmp_path / "world.db"\n'
            '    world = world_model.WorldModel(db)\n'
            f'    goal_id = world.create_goal("fixture task", owner="{principal}", execution_principal="{principal}")\n'
            '    world.close()\n\n')
text = re.sub(r'    class FakeWorld:\n.*?\n(?=    (?:def fake_run_goal_sync|class FakeSandbox))', fleet_world, text, flags=re.S)
text = text.replace('world=FakeWorld(),', 'world=args[1],')
text = text.replace('    def fake_run_goal_sync(*args, **kwargs):\n',
                    '    def fake_run_goal_sync(*args, **kwargs):\n        args[1].set_goal_status(args[3], "done")\n')
text = text.replace('lambda *a, **k: FakeWorld()', 'lambda *a, **k: world_model.WorldModel(db)')
text = text.replace('runner.run_goal_in_thread(7', 'runner.run_goal_in_thread(goal_id')
text = text.replace('    monkeypatch.setattr(orchestrator, "run_goal_sync", lambda *args, **kwargs: completed.append(True))',
    '    def complete(_llm, world, _budget, goal_id, **_kwargs):\n'
    '        world.set_goal_status(goal_id, "done")\n'
    '        completed.append(True)\n\n'
    '    def close_world(world):\n'
    '        world.close()\n'
    '        closed["world"] = True\n\n'
    '    monkeypatch.setattr(world_model, "close_world_if_owned", close_world)\n'
    '    monkeypatch.setattr(orchestrator, "run_goal_sync", complete)')
path.write_text(text, encoding='utf-8', newline='\n')

path = core / 'test_runner_fair_concurrency.py'
text = path.read_text(encoding='utf-8')
text = text.replace('def _stub_run(monkeypatch, *,', 'def _stub_run(monkeypatch, tmp_path, *,')
text = text.replace('def test_one_user_full_lane_does_not_block_another(monkeypatch):',
                    'def test_one_user_full_lane_does_not_block_another(monkeypatch, tmp_path):')
text = re.sub(r'    class _Goal:\n.*?(?=    class _Sandbox:)',
    '    from maverick.world_model import WorldModel\n\n'
    '    db = tmp_path / "world.db"\n'
    '    world = WorldModel(db)\n'
    '    for owner in ("user:alice", "user:alice", "user:alice", "user:bob"):\n'
    '        world.create_goal("fixture task", owner=owner, execution_principal=owner)\n'
    '    world.close()\n\n', text, flags=re.S)
text = text.replace('lambda *a, **k: _World()', 'lambda *a, **k: WorldModel(db)')
text = text.replace('world.get_goal(goal_id).status = "done"', 'world.set_goal_status(goal_id, "done")')
text = text.replace('_stub_run(monkeypatch, gate=', '_stub_run(monkeypatch, tmp_path, gate=')
text = text.replace('fire("user:bob", 99)', 'fire("user:bob", 4)').replace('== [99]', '== [4]')
# Join every runner before the fixture releases its DB or monkeypatches.
text = text.replace('    def fire(user_id, goal_id):\n        threading.Thread(',
    '    threads = []\n\n    def fire(user_id, goal_id):\n        thread = threading.Thread(')
text = text.replace('        ).start()', '        )\n        threads.append(thread)\n        thread.start()')
text = text.replace('        time.sleep(0.2)', '        for thread in threads:\n            thread.join(timeout=10)\n            assert not thread.is_alive()')
path.write_text(text, encoding='utf-8', newline='\n')
