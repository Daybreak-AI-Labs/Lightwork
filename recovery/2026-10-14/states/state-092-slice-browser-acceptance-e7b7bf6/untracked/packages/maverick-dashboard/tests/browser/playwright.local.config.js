const cfg=require('./playwright.config');
module.exports={...cfg,
  reporter:[['list'],['html',{outputFolder:process.env.LIGHTWORK_BROWSER_HTML,open:'never'}],['json',{outputFile:process.env.LIGHTWORK_BROWSER_REPORT}]],
  use:{...cfg.use,launchOptions:{executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    env:{...process.env,USERPROFILE:process.env.BROWSER_WINDOWS_PROFILE}}}};
