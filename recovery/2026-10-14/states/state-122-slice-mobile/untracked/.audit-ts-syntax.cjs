const fs=require('node:fs'),path=require('node:path');
const ts=require('C:/Users/Cstep/Documents/Codex/2026-07-13/new-chat/work/Lightwork/examples/clients/typescript/node_modules/typescript/lib/typescript.js');
const files=fs.readdirSync('apps/mobile-companion/src',{recursive:true}).filter(f=>/\.tsx?$/.test(f)).map(f=>path.join('apps/mobile-companion/src',f));files.push('apps/mobile-companion/App.tsx');
let failed=false;
for(const f of files){const result=ts.transpileModule(fs.readFileSync(f,'utf8'),{fileName:f,compilerOptions:{jsx:ts.JsxEmit.React,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},reportDiagnostics:true});for(const d of result.diagnostics||[]){failed=true;console.log(f,ts.flattenDiagnosticMessageText(d.messageText,' '));}}
if(failed)process.exitCode=1;else console.log('TypeScript syntax parsed:',files.length,'files (no execution).');
