import os, pathlib, sys
repo = pathlib.Path(__file__).resolve().parent
venv = repo.parent / '.venv-audit/Scripts'
os.environ['PATH'] = str(venv) + os.pathsep + os.environ.get('PATH', '')
roots = [str(repo), *map(str, (repo / 'packages').iterdir()), str(repo / 'apps/installer-cli')]
sys.path[:0] = roots
os.environ['PYTHONPATH'] = os.pathsep.join(roots)
os.environ['TEMP'] = os.environ['TMP'] = 'C:/Users/Cstep/AppData/Local/Temp'
import pytest
files = ['apps/installer-msi/test_wxs.py'] + [f'packages/maverick-core/tests/test_{name}.py' for name in (
    'crypto_at_rest', 'store_lock_acquisition', 'customer_deployment_paths', 'sandbox_pool_policy',
    'codebase', 'offline_bundle', 'kubernetes_workspace_transfer', 'training_backends', 'document_job_contract')]
raise SystemExit(pytest.main(['-q', '--tb=short', '--basetemp=C:/Users/Cstep/AppData/Local/Temp/lw-native-01',
    '--junitxml=../audit/native-platform-tests-01.xml', *files, *sys.argv[1:]]))
