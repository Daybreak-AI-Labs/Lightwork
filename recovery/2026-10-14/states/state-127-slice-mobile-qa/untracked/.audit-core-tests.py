import json
import os
import pathlib
import sys

repo = pathlib.Path(__file__).resolve().parent
os.environ['PATH'] = str(pathlib.Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')
roots = [str(repo), *map(str, (repo / 'packages').iterdir()), str(repo / 'apps/installer-cli')]
sys.path[:0] = roots
os.environ['PYTHONPATH'] = os.pathsep.join(roots)
os.environ['TEMP'] = os.environ['TMP'] = str(repo.parent / '.t')
import pytest
files = json.loads((repo.parent / 'audit/core-qualification-files.json').read_text())
files = [p for p in files if not p.endswith(('test_goal_read_projections.py', 'test_domain_discipline.py'))]
raise SystemExit(pytest.main(['-q', '--tb=short', '-m', 'not heavy_concurrency',
    '--basetemp=C:/Users/Cstep/AppData/Local/Temp/lw-core-03',
    '--junitxml=../audit/core-qualification-03.xml', *files, *sys.argv[1:]]))
