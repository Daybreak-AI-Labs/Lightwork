from pathlib import Path
import json
import subprocess

p = Path('docs/operations/persistence-authorities.json')
data = json.loads(p.read_text(encoding='utf8'))
old = json.loads(subprocess.check_output(['git', 'show', 'HEAD:' + p.as_posix()], text=True, encoding='utf8'))
old_paths = {r['path'] for r in old['entries']}
data['entries'] = old['entries'] + [r for r in data['entries'] if r['path'] not in old_paths]
p.write_text(json.dumps(data, indent=2, ensure_ascii=True) + '\n', encoding='utf8', newline='\n')

p = Path('packages/maverick-core/tests/test_cli_ops_kernel_work.py')
s = p.read_text(encoding='utf8').replace('def test_goal_bundle_is_what_the_export_command_writes(tmp_path):',
    'def test_goal_bundle_is_what_the_export_command_writes(tmp_path, monkeypatch):')
s = s.replace('    from maverick.goal_bundle import build_goal_bundle\n',
    '    from maverick.goal_bundle import build_goal_bundle\n\n'
    '    # Compare the same snapshot preparation instant, including its receipt.\n'
    '    monkeypatch.setattr("maverick.goal_archive.time.time", lambda: 1_790_000_000.0)\n', 1)
p.write_text(s, encoding='utf8', newline='\n')
p = Path('packages/maverick-core/tests/test_templates.py')
s = p.read_text(encoding='utf8').replace('match="missing required params"', 'match=r"depth: enter a value\\.")ERROR')
s = s.replace(')ERROR):', '):')
p.write_text(s, encoding='utf8', newline='\n')
p = Path('packages/maverick-core/tests/test_q3_2026_batch3.py')
s = p.read_text(encoding='utf8').replace('assert "sent" in out', 'assert out == "queued; delivery not yet confirmed (1 backend)"')
s = s.replace('assert "sent (2 backends)" in out', 'assert out == "queued; delivery not yet confirmed (2 backends)"')
p.write_text(s, encoding='utf8', newline='\n')
p = Path('packages/maverick-core/tests/test_q2_2026_batch4.py')
s = p.read_text(encoding='utf8').replace('import importlib.util\n', 'import importlib.util\nimport re\n')
s = s.replace('    assert \'class="theme-dark\' in resp.text', '    assert "theme-dark" in _body_classes(resp.text)')
s = s.replace('    assert f\'class="theme-{theme}\' in resp.text', '    assert f"theme-{theme}" in _body_classes(resp.text)')
s = s.replace('    assert \'class="theme-light\' in resp2.text', '    assert "theme-light" in _body_classes(resp2.text)')
anchor = 'def test_dashboard_default_theme_is_dark(dashboard_client):'
s = s.replace(anchor, '''def _body_classes(html):
    body = re.search(r'<body\\b[^>]*\\bclass="([^"]*)"', html)
    assert body is not None
    return body.group(1).split()


''' + anchor)
p.write_text(s, encoding='utf8', newline='\n')
