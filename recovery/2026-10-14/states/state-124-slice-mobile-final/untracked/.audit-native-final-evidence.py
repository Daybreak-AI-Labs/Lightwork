from pathlib import Path
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

root=Path.cwd()
docs=root/'docs/ux-audit-2026-09-30'
base=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
xml=root.parent/'audit/native-client-qualification.xml'
suite=ET.parse(xml).getroot().find('testsuite')
assert suite is not None and suite.attrib['failures']=='0' and suite.attrib['errors']=='0'
log=root.parent/'audit/native-client-contracts.log'
log_text=log.read_text(encoding='utf8')
assert 'tests 60' in log_text and 'pass 60' in log_text and 'fail 0' in log_text
node_record={'source_sha':base,'command':'node --test apps/client-contract-tests/*.test.cjs',
 'passed':60,'failed':0,'skipped':0,'milliseconds':24475.3534,
 'log':'audit/native-client-contracts.log','sha256':hashlib.sha256(log.read_bytes()).hexdigest(),
 'dependencies':{'typescript':'5.3.3','jsdom':'29.1.1','fengari':'0.1.5'},
 'dependency_lock_sha256':hashlib.sha256((root.parent/'audit/client-contract-deps/package-lock.json').read_bytes()).hexdigest(),
 'boundary':'Node24; actual maintained JS/TS/Lua source with controlled child/VSCode/vim/fetch/DOM APIs. TypeScript transpilation is not an installed VSCode extension qualification. No provider or IDE launch.'}
record={'schema':'lightwork.customer-audit.native-client-qualification.v1','date':'2026-09-30',
 'audit_ids':[f'LW-{n}' for n in range(175,196)],'node':node_record,
 'python':{'source_sha':base,'files':['apps/client-contract-tests/test_external_examples.py','packages/maverick-core/tests/test_cli_budget_preview.py'],
   'passed':16,'failed':0,'skipped':0,'external_example_cases':14,'cli_preview_cases':2,'seconds':5.58,
   'junit':'audit/native-client-qualification.xml','sha256':hashlib.sha256(xml.read_bytes()).hexdigest(),
   'boundary':'Synthetic gateway/HTTP/LLM/email and native CLI callback fixtures; no live approval or external tool execution.'},
 'related_evidence':{
   'mobile':'native-build-evidence.json records91 mocked mobile cases, actual-dependency TypeScript, two Android generation passes and Apple/Android autolinking. These are not native SDK compilation or device tests.',
   'jetbrains':'native-build-evidence.json records the actual pinned IntelliJ243 SDK/Kotlin compile,12 loopback JVM transport tests, Gradle check/buildPlugin and unsignedZIP hash.',
   'zed':'native-build-evidence.json records locked Rust1.97.1/API0.7.0 WASIp2 release build plus verified component/input packaging hashes.',
   'sdk':'slices/plugin-adapters.json records24 SDK tests and two actual built-Node interoperability tests at that worker checkpoint; not repeated by this evidence update.',
   'windows_core':'core-installer-qualification.json records33 owned core/installer files:686 passed9 skipped in ordinary and secure-default modes each. This is separate core evidence, not native-client acceptance.',
   'native_platform':'native-platform-qualification.json records10 Windows/platform regression files:259 passed2 skipped in each posture. This is not an Apple/Android/installed-IDE run.'},
 'remaining':['macOS/Xcode Apple targets, Android SDK builds and physical device lifecycle/protected-storage/accessibility checks remain host-bound.',
 'Installed VSCode/JetBrains/Zed/Emacs/Neovim integration, native credential services, screen readers and host lifecycle require separate qualification. Fengari exercised real Lua with a mocked vim API; it is not Neovim.',
 'Signed artifacts, clean-machine installation and release-owner distribution/privacy/export/support approvals remain separate. Local ZIP/WASM artifacts are unsigned engineering outputs.',
 'Human language and consequence-copy review is not replaced by DOM tests.',
 'Focused counts overlap other reports and must not be summed into a unique repository total. Integrated final-source qualification is owned by the consolidated PR.']}
(docs/'native-client-qualification.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf8',newline='\n')

p=docs/'slices/native-integrations.json';rows=json.loads(p.read_text())
node='2026-09-30: all60 maintained client-contract Node cases passed with pinned TypeScript5.3.3/jsdom29.1.1/Fengari0.1.5; mock host APIs and actual source transpilation/Lua execution only. See native-client-qualification.json for sourceSHA and log hash.'
python='2026-09-30:14 controlled external-agent/REST example cases passed, covering approval denial/expiry/staleness/halt, single truthful finish, paginated terminal acknowledgement, auth loss and original-key receipt recovery. Two native CLI budget-preview callback cases also passed; no provider or paid work.'
for row in rows:
    number=int(row['id'].split('-')[1])
    if number in (175,176,177,192):
        row['validation']=python
        row['remaining']='Live-service/customer lifecycle qualification remains separate; the local examples used synthetic gateway, HTTP, LLM and email boundaries. Integrated final-source regression still applies.'
    elif number==189:
        row['validation']='2026-09-30: real IntelliJ Community2024.3.6/build243 SDK compile and12 synthetic loopback JVM transport tests passed; Gradle8.13 check/buildPlugin and python build.py produced the unsigned ZIP. Exact dependency locks, commands and hashes: native-build-evidence.json.'
        row['remaining']='Installed IDE/PasswordSafe, credential revocation, disposal, screen-reader and supported-host compatibility qualification remains open. Pinned build project is supplied and has built; no signed release or installed IDE is claimed.'
    elif number==190:
        row['validation']='2026-09-30: both test_cli_budget_preview.py cases passed through CliRunner (local policy without guessed principal; disabled policy refusal). '+node
        row['remaining']='Installed extension confirmation, real policy/profile changes between preview and start/resume, and selected-task cancellation qualification remain separate. A mock callback test is not production budget enforcement qualification.'
    elif number==194:
        row['validation']='SDK npm test24 passed, and two built-Node interoperability cases passed after SDK compilation at the plugin adapter checkpoint; see slices/plugin-adapters.json. '+node
        row['remaining']='Published package installation and customer plugin-host qualification remain separate from local compiled protocol fixtures; no installed external plugin was invoked.'
    else:
        row['validation']=node
        row['remaining']='Installed editor/native credential-store/accessibility and actual host lifecycle qualification remain open. Mock contracts are passed; local tests are authorized and are not deferred by user instruction.'
        if number==191:row['remaining']='Actual Lua source passed in Fengari with a controlled vim API. Native Neovim and Emacs/ERT, UI responsiveness, cancellation and editor lifecycle still need installed-host qualification.'
        if number==195:row['remaining']='Manifest/README/source contracts are reviewed and maintained Node tests pass. SDK minimal-example pinned build/describe and packaged clean-host installation need their own exact build evidence; IDE/distribution qualification is not inferred from Node mocks.'
p.write_text(json.dumps(rows,indent=2)+'\n',encoding='utf8',newline='\n')

p=docs/'slices/mobile.json';rows=json.loads(p.read_text())
for row in rows:
    number=int(row['id'].split('-')[1])
    if number in (178,180,181,182,183):
        row['validation']='2026-09-30: Apple source review, bounded transport/retention/cancellation regressions and supplied shared Swift plus hosted watch/vision XCTest targets. The pinned XcodeGen project and entry points now remove manual target assembly. macOS/Xcode/Apple SDKs are unavailable on this Windows host, so these Swift targets have not compiled or executed. Separate91 mobile Node mocks and TypeScript/generation results do not qualify Apple behavior; see native-build-evidence.json and native-client-qualification.json.'
    elif number==184:
        row['validation']+=' Current evidence consolidation: native-client-qualification.json records60 Node and16 Python client cases, separately from core686-per-posture and native-platform259-per-posture groups; none substitutes for installed-device/editor qualification.'
    elif number==185:
        row['validation']='2026-09-30: pairing/auth Python11/11 and mobile79/79 at the pairing checkpoint; later mobile suite91/91 plus actual-dependency TypeScript and native generation/autolinking passed, as recorded in native-build-evidence.json. Apple XCTest targets are supplied but require macOS/Xcode. Local tests are authorized; native TLS/keychain/device qualification remains unavailable on this host.'
        row['remaining']='Registry integration is complete. Integrated final-source auth/middleware qualification is owned by the consolidated PR. Real keychain, TLS transport, installed-device, signing, accessibility and revocation/cache timing remain separate checks. Pairing uses an expiring human-readable device code. Offline revocation cannot reach a disconnected device before its previously accepted retention expiry.'
p.write_text(json.dumps(rows,indent=2)+'\n',encoding='utf8',newline='\n')

p=root/'apps/client-contract-tests/README.md';s=p.read_text(encoding='utf8')
s=s.replace("Tests are authored **but not run**, following the user's instruction\nto build first and test later. No native SDK, browser, accessibility or live-provider\nqualification is implied by source review.","Local qualification is now authorized. All 60 Node cases and all 14 controlled\nexternal Python example cases passed on 2026-09-30; two CLI budget-preview cases\nalso passed. The exact source, pinned dependency versions, log hashes and limits\nare in [native-client-qualification.json](../../docs/ux-audit-2026-09-30/native-client-qualification.json).\nThese use controlled host/transport APIs, including real Lua source in Fengari.\nThey do not qualify installed IDEs, devices, accessibility or live providers.")
p.write_text(s,encoding='utf8',newline='\n')
p=root/'apps/client-contract-tests/test_external_examples.py';s=p.read_text(encoding='utf8').replace('Controlled contracts for the maintained docs; authored, not executed in this audit.','Controlled contracts for maintained examples, using synthetic external boundaries.')
p.write_text(s,encoding='utf8',newline='\n')
