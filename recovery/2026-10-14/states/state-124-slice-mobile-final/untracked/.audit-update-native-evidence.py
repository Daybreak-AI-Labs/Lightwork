import hashlib
import json
from pathlib import Path

root = Path(__file__).parent
evidence_path = root / 'docs/ux-audit-2026-09-30/native-client-qualification.json'
evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
sdk = root / 'sdks/plugin-ts'
example = sdk / 'examples/minimal'
evidence['sdk'] = {
    'source_sha': 'dfb2905bed1abe1c344343092f3cf1eafc0d45eb',
    'build_command': 'node ../slice-workbench/sdks/plugin-ts/node_modules/typescript/bin/tsc -p sdks/plugin-ts/tsconfig.json --typeRoots ../slice-workbench/sdks/plugin-ts/node_modules/@types',
    'test_command': 'node --test sdks/plugin-ts/dist/test/*.test.js',
    'typescript': '7.0.2', '@types/node': '26.6.3',
    'passed': 24, 'failed': 0, 'skipped': 0, 'milliseconds': 263.0051,
    'boundary': 'Actual SDK compilation and controlled Node protocol/stream tests; reused verified task-local compiler dependencies. This is not a fresh published SDK package installation or external plugin-host qualification.',
}
evidence['minimal_example'] = {
    'source_sha': 'dfb2905bed1abe1c344343092f3cf1eafc0d45eb',
    'directory': 'sdks/plugin-ts/examples/minimal',
    'commands': ['npm install --ignore-scripts --no-audit --no-fund', 'npm run build', 'npm run describe'],
    'npm_invocation': 'C:/Program Files/nodejs/node.exe C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js',
    'typescript': json.loads((example / 'node_modules/typescript/package.json').read_text())['version'],
    '@types/node': json.loads((example / 'node_modules/@types/node/package.json').read_text())['version'],
    'dependency_lock_sha256': hashlib.sha256((example / 'package-lock.json').read_bytes()).hexdigest(),
    'compiled_entry_sha256': hashlib.sha256((example / 'dist/plugin.js').read_bytes()).hexdigest(),
    'build_exit_code': 0, 'describe_exit_code': 0,
    'observed_description': {'protocol': 'maverick-plugin/2', 'execution': {'replay': 'never', 'deadline': 'cooperative', 'framingBytes': 1048576}, 'tool_names': ['greet']},
    'boundary': 'Pinned example compiler/types installed task-locally with lifecycle scripts disabled; SDK file dependency compiled first. Describe returned metadata only. No tool, provider, installed extension or distribution/signing action ran.',
}
evidence['related_evidence']['sdk'] = 'The sdk and minimal_example sections record fresh local SDK compilation, 24 passing protocol tests and a pinned example build/describe. slices/plugin-adapters.json separately records two built-Node interoperability cases from that worker checkpoint.'
evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
path = root / 'docs/ux-audit-2026-09-30/slices/native-integrations.json'
rows = json.loads(path.read_text(encoding='utf-8'))
for row in rows:
    if row['id'] == 'LW-194':
        row['validation'] = '2026-09-30: actual SDK TypeScript7.0.2 compilation and all24 compiled Node protocol tests passed on source dfb2905; @types/node26.6.3. Two earlier built-Node interoperability cases are separately recorded in slices/plugin-adapters.json. All60 maintained client contracts also passed with controlled host APIs. Exact commands, dependencies and scope: native-client-qualification.json.'
    if row['id'] == 'LW-195':
        row['validation'] += ' The SDK compiled and all24 protocol cases passed. The minimal example installed pinned TypeScript7.0.2 and @types/node26.0.0 with scripts disabled, compiled, and returned protocol2/greet metadata through npm run describe; no tool invocation. Dependency lock and compiled entry hashes are in native-client-qualification.json.'
        row['remaining'] = 'Published package installation, packaged clean-host IDE installation and distribution qualification remain separate. Local build/describe and controlled host tests do not establish installed IDE, device or accessibility acceptance.'
path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
