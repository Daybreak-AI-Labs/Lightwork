import hashlib, json, zipfile
from pathlib import Path
repo=Path(__file__).resolve().parent
jet=repo.parent/'audit/jetbrains-build'
zed=repo/'apps/zed-extension'
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
data={
 'audit_ids':['LW-184'],
 'date':'2026-09-30',
 'host':'Windows11 x86_64; task-local toolchains; no global installation',
 'jetbrains':{
  'java':'Temurin21.0.8+9','gradle':'8.13','gradle_plugin':'2.5.0','kotlin':'2.1.20',
  'ide':'IntelliJ Community2024.3.6 build243.26574.91',
  'commands':['check buildPlugin --write-locks','python build.py'],
  'tests':'12 synthetic loopback JVM transport cases passed; compileKotlin/compileTestKotlin passed against real SDK',
  'source_correction':'DashboardSettings.kt Connection name field explicitly uses this@DashboardConfigurable.name to avoid JPanel.name shadowing; parent applied exact one-line correction',
  'wrapper_sha256':sha(repo/'apps/jetbrains-plugin/gradle/wrapper/gradle-wrapper.jar'),
  'gradle_distribution_sha256':'20f1b1176237254a6fc204d8434196fa11a4cfb387567519c61556e8710aed78',
  'artifact':'lightwork-jetbrains-0.1.6.zip',
  'artifact_sha256':sha(jet/'build/distributions/lightwork-jetbrains-0.1.6.zip'),
  'qualified':False,
 },
 'zed':{
  'rust':'1.97.1','host_compiler':'x86_64-pc-windows-gnullvm with bundled rust-lld',
  'target':'wasm32-wasip2','api':'0.7.0 exact','dependency_lock':'apps/zed-extension/Cargo.lock',
  'commands':['cargo build --locked --release --target wasm32-wasip2','python build.py'],
  'wasm_sha256':sha(zed/'target/wasm32-wasip2/release/maverick_zed_extension.wasm'),
  'bundle_sha256':sha(zed/'build/lightwork-zed-preview.zip'),
  'artifact':'lightwork-zed-preview.zip','qualified':False,
 },
 'limits':[
  'Unsigned local artifacts only. No plugin installation, signing, publication, customer access, live provider calls or Lightwork CLI execution.',
  'JetBrains test cases exercise transport with a synthetic loopback HTTP server; they do not qualify PasswordSafe, IDE lifecycle or accessibility.',
  'Zed component builds and source/hash packaging are verified; actual Zed API compatibility, MCP discovery, auth/revocation, CLI absence and accessibility still need installed-host tests.',
  'Apple watch/vision/iOS lack macOS/Xcode; Android lacks SDK. Mobile91 mocked cases and actual-dependency TypeScript pass; native generation/autolinking is not native compilation.',
  'Gradle2.5 plugin emits deprecation/newer-version notices. Pins intentionally retain the validated Gradle8.13/IDE243 target; upgrade and qualify separately.',
  'Signed end-user distribution and release-owner host/support/privacy/export gates are not completed or claimed.',
 ]}
with zipfile.ZipFile(zed/'build/lightwork-zed-preview.zip') as archive:
 manifest=json.loads(archive.read('lightwork-zed/BUILD-MANIFEST.json'))
 for name,digest in manifest['inputs_sha256'].items():
  assert hashlib.sha256(archive.read('lightwork-zed/'+name)).hexdigest()==digest
 assert archive.read('lightwork-zed/extension.wasm')[:8]==b'\x00asm\x0d\x00\x01\x00'
data['zed']['bundle_manifest_verified']=True
dest=repo/'docs/ux-audit-2026-09-30/native-build-evidence.json'
dest.write_bytes((json.dumps(data,indent=2)+'\n').encode())
tracker=repo/'docs/ux-audit-2026-09-30/slices/mobile.json'
rows=json.loads(tracker.read_text(encoding='utf-8'))
row=next(r for r in rows if r['id']=='LW-184')
row['status']='test-needed'
row['implementation_evidence']+=' JetBrains now has complete pinned Gradle8.13 wrapper/checksum, SDK2024.3.6 project, Kotlin2.1.20/Gson2.11.0 dependency lock, local loopback check and unsigned installable ZIP plus SHA/input manifest. Zed exact API0.7.0/Cargo lock/Rust1.97.1 WASIp2 build emits source+component preview archive with compiler/input hashes. Both documented commands build without manual project assembly; compatibility remains deliberately preview and target-specific.'
row['validation']='2026-09-30:91 mobile mocks and installed-dependency TypeScript passed; Android native generation twice and Apple/Android autolinking passed. JetBrains real IntelliJ SDK compile and all12 local synthetic JVM transport cases passed; check buildPlugin and python build.py generated unsigned ZIP. Zed locked release WASIp2 compile and python build.py succeeded; component header and every bundled input hash verified. Python Ruff and git whitespace checks passed. Exact toolchain pins, commands and artifact hashes are in native-build-evidence.json. No installed-editor/device or signed-release qualification is implied.'
row['remaining']='Qualification remains open: Apple/iOS native SDK compilation requires macOS/Xcode; Android SDK unavailable; installed IDE/device auth, revocation, protected storage, accessibility and host lifecycle tests remain unrun. Zed host must support API0.7.0; JetBrains compatibility is limited to compile target243 until installed tests pass. Release owner must select/approve distribution/signing and privacy/export/support gates. No signed end-user installer or publication is delivered; this row is test-needed, not completed distribution.'
tracker.write_bytes((json.dumps(rows,indent=2,ensure_ascii=False)+'\n').encode())
print('Recorded real build evidence and kept184 qualification open')
