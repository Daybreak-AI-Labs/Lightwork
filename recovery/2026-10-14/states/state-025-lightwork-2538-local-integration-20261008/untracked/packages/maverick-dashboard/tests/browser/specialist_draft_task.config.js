const {defineConfig}=require('@playwright/test'),fs=require('node:fs'),path=require('node:path');
const python=require('./python-runtime'),port=Number(process.env.LIGHTWORK_BROWSER_PORT||8886);
if(process.platform==='win32'){
  const temporary=path.resolve(__dirname,'../../../..','.browser-runtime');
  fs.mkdirSync(temporary,{recursive:true});
  process.env.TEMP=temporary;process.env.TMP=temporary;
}
module.exports=defineConfig({testDir:'.',testMatch:'specialist_draft_task.spec.js',workers:1,retries:0,timeout:120000,
  outputDir:path.resolve(__dirname,'../../../..','.browser-specialist-draft-results'),reporter:[['list']],
  use:{baseURL:`http://127.0.0.1:${port}`,extraHTTPHeaders:{Origin:`http://127.0.0.1:${port}`},
    browserName:'chromium',headless:true,actionTimeout:15000,screenshot:'only-on-failure',trace:'retain-on-failure'},
  webServer:{command:`${JSON.stringify(python)} specialist_draft_task_server.py --port ${port}`,
    url:`http://127.0.0.1:${port}/livez`,reuseExistingServer:false,timeout:120000,gracefulShutdown:{signal:'SIGTERM',timeout:10000}}});
