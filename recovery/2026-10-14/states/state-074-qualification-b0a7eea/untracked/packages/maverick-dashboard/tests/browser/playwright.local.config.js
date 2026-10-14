const cfg=require('./playwright.config');
module.exports={...cfg,use:{...cfg.use,launchOptions:{executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'}}};
