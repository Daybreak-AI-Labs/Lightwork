import json, platform, subprocess, xml.etree.ElementTree as ET
from pathlib import Path
repo=Path(__file__).resolve().parent
records=[]
for number, posture in [('02','legacy-default'),('03','secure-default')]:
    xml=repo.parent/'audit'/f'native-platform-tests-{number}.xml'
    tree=ET.parse(xml)
    tests=tree.findall('.//testcase')
    skips=[{'test':x.attrib['name'],'reason':x.find('skipped').get('message')} for x in tests if x.find('skipped') is not None]
    failures=[x.attrib['name'] for x in tests if x.find('failure') is not None or x.find('error') is not None]
    if failures: raise RuntimeError(f'Unresolved {posture} failures: {failures}')
    records.append({'posture':posture,'total':len(tests),'passed':len(tests)-len(skips),
                    'skipped':skips,'failures':0,'junit':xml.name})
data={
 'date':'2026-09-30','host':platform.platform(),'python':platform.python_version(),
 'source_base':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
 'scope':['apps/installer-msi/test_wxs.py']+[f'packages/maverick-core/tests/test_{name}.py' for name in (
   'crypto_at_rest','store_lock_acquisition','customer_deployment_paths','sandbox_pool_policy','codebase',
   'offline_bundle','kubernetes_workspace_transfer','training_backends','document_job_contract')],
 'runs':records,
 'execution':{
   'runner':'python -m pytest -q --tb=short <scope files> --basetemp=<unique short Windows Temp root> --junitxml=<report>',
   'posture_switch':'MAVERICK_TEST_SECURE_DEFAULT=1 for secure repeat',
   'environment':'All eight project packages available from reviewed checkout; pytest9.1.1; task-local pip25.0.1 prerequisite restored with bundled ensurepip using workspace TEMP.',
   'filesystem':'Authorized local execution preserved real Win32 parent-handle guards; no reparse, locking or authority check bypasses.',
 },
 'corrections':[
  'Memory encryption fixtures inspect the current owner-scoped store; legacy plaintext is only accepted inside that scope and unrelated shared-root content remains unreadable.',
  'Grant-store lock fixture now requires the current global authority fence while asserting unwind on ordinary error and BaseException.',
  'Runner lifecycle uses a real transactional WorldModel for current handoff/identity checks; daemon and LLM effects remain synthetic.',
  'Code-vault synthetic backend uses the active test interpreter for Python commands instead of Windows bare-name executable search.',
  'Offline bundle exact-shape test includes explicit bounded cache policy and its revision.',
 ],
 'limits':[
  'Two existing symlink tests remain skipped because this Windows host does not provide symlink privilege.',
  'Synthetic backends and local throwaway repositories only: no live provider, Docker/Kubernetes cluster, cloud deployment, host systemd unit, signing or publication.',
  'This validates the listed platform group, not the entire repository or installed native clients.',
 ]}
(repo/'docs/ux-audit-2026-09-30/native-platform-qualification.json').write_bytes((json.dumps(data,indent=2)+'\n').encode())
print(json.dumps(records,indent=2))
