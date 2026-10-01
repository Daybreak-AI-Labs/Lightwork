// Real shipped catalogs and both HTML shells; local synthetic budget data only.
const {test,expect}=require('@playwright/test');
const http=require('node:http'),crypto=require('node:crypto');
const catalogPath=/\/static\/lightwork-i18n-catalog\/([a-z]{2})\/([0-9a-f]{64})\.js$/;
const labels={en:'Add details',fr:'Ajouter des pr\u00e9cisions',de:'Details hinzuf\u00fcgen',ja:'\u8a73\u7d30\u3092\u8ffd\u52a0',zh:'\u6dfb\u52a0\u8be6\u7ec6\u4fe1\u606f',ar:'Add details'};

async function localOnly(context,baseURL){
  await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(baseURL).origin?route.continue():route.abort('blockedbyclient'));
}
async function syntheticBudget(page){
  await page.route('**/api/v1/budget/preview*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
    context:'synthetic-catalog-budget',effective:{max_dollars:1,max_wall_seconds:60},
    headroom_complete:false,admission_allowed:false,reset_at:'2030-01-01T00:00:00Z',observed_at:1900000000,
  })}));
}
async function chooseInterface(page,language){
  await page.goto('/chat');await page.locator('#prefs-menu > summary').click();
  await page.locator('#prefs-device-only').check();
  const saved=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/v1/preferences/display');
  const picker=page.locator('[data-ui-preference="interface_language"]');
  await picker.selectOption(language);expect((await saved).status()).toBe(200);
  await expect(picker).toHaveAttribute('data-saved-value',language);
  await expect(picker).toBeEnabled();
}

for(const language of Object.keys(labels))test(`shipped ${language} catalog initializes chat and shared-shell controls with honest language regions`,async({page,context,baseURL},info)=>{
  await localOnly(context,baseURL);await syntheticBudget(page);
  await context.setExtraHTTPHeaders({Origin:baseURL,'Accept-Language':language});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await chooseInterface(page,language);
  const catalog=page.waitForResponse(response=>catalogPath.test(new URL(response.url()).pathname));
  await page.reload();const response=await catalog;expect(response.status()).toBe(200);
  const body=await response.body(),path=new URL(response.url()).pathname,match=path.match(catalogPath);
  expect(match[1]).toBe(language);expect(crypto.createHash('sha256').update(body).digest('hex')).toBe(match[2]);
  expect(response.headers()['cache-control']).toContain('immutable');
  expect(response.headers()['content-type']).toContain('application/javascript');
  expect(response.headers()['set-cookie']).toBeUndefined();
  const metadata=await page.locator('#lightwork-i18n-data').textContent();
  expect(JSON.parse(metadata)).toMatchObject({language});expect(JSON.parse(metadata).catalog).toBeUndefined();
  expect(await page.evaluate(()=>window.LightworkJourneyCatalog.language)).toBe(language);
  const actual=language==='ar'?'en':language;
  await expect(page.locator('.composer details > summary [data-product-copy]')).toHaveText(labels[language]);
  await expect(page.locator('.composer details > summary [data-product-copy]')).toHaveAttribute('lang',actual);
  // This status is created by the actual budget controller after the external
  // script loads, rather than being a server-rendered translation alone.
  await page.locator('[data-budget-launch] > summary').click();
  await expect(page.locator('[data-budget-launch] [role="status"]')).toHaveAttribute('lang',actual);
  await expect(page.locator('[data-budget-summary] a')).toHaveCount(1);
  await expect(page.locator('[data-budget-summary] a')).toHaveAttribute('lang',actual);
  await page.goto('/preferences');
  await expect(page.locator('[data-pref="interface_language"]')).toBeVisible();
  await expect(page.locator('[data-pref="interface_language"]').locator('..').locator('[data-product-copy]').first()).toHaveAttribute('lang',actual);
  expect(await page.locator('script[src*="/static/lightwork-i18n-catalog/"]').getAttribute('src')).toBe(path);
  expect(await page.evaluate(()=>window.LightworkI18n.text('Add details'))).toBe(labels[language]);
  expect(errors).toEqual([]);
  await info.attach('catalog-language',{body:JSON.stringify({language,actual,path,bytes:body.length}),contentType:'application/json'});
});

test('Chrome reuses the public catalog across shells and conditional reads return no private state',async({browser,baseURL},info)=>{
  test.setTimeout(90000);
  // Playwright routing disables the HTTP cache. Use a rejecting local proxy
  // instead: only loopback bypasses it, and external requests are never sent.
  let denied=0;const sockets=new Set();
  const proxy=http.createServer((request,response)=>{denied++;response.writeHead(502);response.end();});
  proxy.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});
  proxy.on('connect',(request,socket)=>{denied++;socket.end('HTTP/1.1 502 Bad Gateway\r\n\r\n');});
  await new Promise(resolve=>proxy.listen(0,'127.0.0.1',resolve));
  let context;
  try{
    context=await browser.newContext({baseURL,locale:'fr',extraHTTPHeaders:{Origin:baseURL},
      proxy:{server:`http://127.0.0.1:${proxy.address().port}`,bypass:'127.0.0.1'}});
    const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
    await chooseInterface(page,'fr');await page.reload();
    const path=await page.locator('script[src*="/static/lightwork-i18n-catalog/"]').getAttribute('src');
    expect(path).toMatch(/\/fr\/[0-9a-f]{64}\.js$/);
    expect(await page.evaluate(()=>window.LightworkI18n.text('Add details'))).toBe(labels.fr);
    await page.goto('/preferences');
    await expect(page.locator('[data-pref="interface_language"]')).toBeVisible();
    const timing=await page.evaluate(asset=>{
      const item=performance.getEntriesByName(new URL(asset,location.origin).href).find(entry=>entry.initiatorType==='script');
      return item&&{transferSize:item.transferSize,encodedBodySize:item.encodedBodySize,decodedBodySize:item.decodedBodySize};
    },path);
    expect(timing).toBeTruthy();expect(timing.decodedBodySize).toBeGreaterThan(0);expect(timing.transferSize).toBe(0);
    const response=await context.request.get(path),etag=response.headers().etag;
    expect(response.status()).toBe(200);expect(etag).toMatch(/^W\/"[0-9a-f]{64}"$/);
    expect(response.headers()['set-cookie']).toBeUndefined();
    const cached=await context.request.get(path,{headers:{'If-None-Match':etag}});
    expect(cached.status()).toBe(304);expect((await cached.body()).length).toBe(0);
    expect((await context.request.get(path.replace(/[0-9a-f]{64}\.js$/,'0'.repeat(64)+'.js'))).status()).toBe(404);
    // Prove the cache-preserving outbound boundary refuses an external fetch.
    const probe=await context.newPage();let rejected=false;
    try{await probe.goto('https://browser-outbound.invalid/probe',{timeout:10000});}catch{rejected=true;}
    await probe.close();expect(rejected).toBe(true);
    expect(denied).toBeGreaterThan(0);expect(errors).toEqual([]);
    await info.attach('real-cache-evidence',{body:JSON.stringify({path,timing,etag,externalProxyRejections:denied}),contentType:'application/json'});
  }finally{
    try{if(context)await context.close();}finally{for(const socket of sockets)socket.destroy();await new Promise(resolve=>proxy.close(resolve));}
  }
});

test('an unavailable catalog keeps dynamic product fallback marked as English',async({page,context,baseURL})=>{
  await localOnly(context,baseURL);await syntheticBudget(page);
  await context.setExtraHTTPHeaders({Origin:baseURL,'Accept-Language':'fr'});
  await chooseInterface(page,'fr');
  await page.route('**/static/lightwork-i18n-catalog/**',route=>route.abort('failed'));
  await page.reload();await page.locator('[data-budget-launch] > summary').click();
  await expect(page.locator('.composer details > summary [data-product-copy]')).toHaveText(labels.fr);
  await expect(page.locator('[data-budget-summary] a')).toHaveAttribute('lang','en');
  expect(await page.evaluate(()=>window.LightworkI18n.text('Add details'))).toBe(labels.en);
});
