const base=require('./playwright.config');
const python=require('./python-runtime');
module.exports={...base,testMatch:'.probe-ledger.spec.js',webServer:{...base.webServer,command:JSON.stringify(python)+' .probe-ledger-server.py --port '+process.env.LIGHTWORK_BROWSER_PORT}};
