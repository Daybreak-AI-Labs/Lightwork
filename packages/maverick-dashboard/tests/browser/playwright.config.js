const {defineConfig}=require('@playwright/test');
const os=require('node:os'),path=require('node:path');
// On Windows use a workspace-owned temporary root. Some managed user TEMP
// directories prevent Chromium cache cleanup even after the browser exits.
if(process.platform==='win32'){
  const temporary=process.env.LIGHTWORK_BROWSER_TEMP||path.resolve(__dirname,'../../../..','.browser-runtime');
  require('node:fs').mkdirSync(temporary,{recursive:true});
  process.env.TEMP=temporary;process.env.TMP=temporary;
}
const port=Number(process.env.LIGHTWORK_BROWSER_PORT||8769);
// Capture this test-only opt-in before the server clears LIGHTWORK_* variables.
const showVendorSetup=process.env.LIGHTWORK_BROWSER_SHOW_VENDOR_SETUP==='1'?' --show-vendor-setup':'';
module.exports=defineConfig({
  testDir:'.',testMatch:'*.spec.js',fullyParallel:false,forbidOnly:!!process.env.CI,workers:1,retries:0,globalTimeout:240000,
  outputDir:process.env.LIGHTWORK_BROWSER_OUTPUT||path.join(os.tmpdir(),'lightwork-browser-results'),
  timeout:45000,expect:{timeout:10000},
  reporter:[['list'],['html',{outputFolder:'browser-report',open:'never'}],['json',{outputFile:'test-results/results.json'}]],
  use:{baseURL:`http://127.0.0.1:${port}`,extraHTTPHeaders:{Origin:`http://127.0.0.1:${port}`},browserName:'chromium',headless:true,trace:'retain-on-failure',screenshot:'only-on-failure'},
  webServer:{command:`python server.py --port ${port}${showVendorSetup}`,url:`http://127.0.0.1:${port}/livez`,reuseExistingServer:false,timeout:120000},
});
