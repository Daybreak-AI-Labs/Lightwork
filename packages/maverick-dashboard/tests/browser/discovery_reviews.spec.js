const {test,expect}=require('@playwright/test');
test.beforeEach(async({context,page,baseURL})=>{
  await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(baseURL).origin?route.continue():route.abort('blockedbyclient'));
  page.__uncaught=[];page.on('pageerror',error=>page.__uncaught.push(error.message));page.on('dialog',dialog=>dialog.accept());
});
test.afterEach(async({page,context},info)=>{
  await context.unrouteAll({behavior:'ignoreErrors'});
  await info.attach('rendered-state',{body:await page.screenshot({fullPage:true}),contentType:'image/png'});
  expect(page.__uncaught).toEqual([]);
});
async function fits(page){const s=await page.evaluate(()=>[document.documentElement.scrollWidth,document.documentElement.clientWidth]);expect(s[0]).toBeLessThanOrEqual(s[1]+1);}

test('discovery uses sealed connection references and explicitly rebinds a rotated evidence key',async({page,request},info)=>{
  await page.setViewportSize({width:375,height:812});await page.goto('/connections');
  let credentialDrafts=0;page.on('request',r=>{if(r.method()==='PUT'&&r.url().includes('/api/v1/drafts/'))credentialDrafts++;});
  await page.locator('#cx-preset').selectOption('servicenow_cmdb');await page.locator('#cx-name').fill('browser-managed');
  await page.locator('#cx-token').fill('synthetic-browser-only-credential');await page.locator('#cx-base').fill('https://data.example.com');
  await fits(page);await page.getByRole('button',{name:'Save connection',exact:true}).click();
  await expect(page.locator('#cx-list')).toContainText('browser-managed');await expect(page.locator('#cx-token')).toHaveValue('');
  expect(credentialDrafts).toBe(0);await page.goto('/privacy/data-discovery');
  await page.locator('#pdd-load-setup').click();await expect(page.locator('#pdd-status')).toContainText('Setup choices loaded');
  const key=page.locator('#pdd-key-form');await key.locator('[name="name"]').fill('browser-inventory');
  await key.getByRole('button',{name:'Create or rotate evidence key'}).click();
  await expect(page.locator('#pdd-status')).toContainText('Evidence key saved securely');
  const source=page.locator('#pdd-source');await source.locator('[name="source_id"]').fill('browser-cmdb');
  await source.locator('[name="provider"]').selectOption('servicenow_cmdb');
  const reference=await page.locator('#pdd-connection option').filter({hasText:'browser-managed'}).getAttribute('value');
  await page.locator('#pdd-connection').selectOption(reference);
  await expect(source.locator('[name="origin"]')).toHaveValue('https://data.example.com');
  await source.locator('[name="resource_ref"]').fill('cmdb_ci');await page.locator('#pdd-evidence-key').selectOption({index:1});
  await source.getByRole('button',{name:'Save source settings'}).click();
  await expect(source.locator('[name="expected_revision"]')).toHaveValue('1');
  const before=await (await request.get('/api/v1/privacy/data-discovery/sources/browser-cmdb')).json();
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/v1/privacy/data-discovery/sources/browser-cmdb/test-access');
  await page.locator('#pdd-test-source').click();expect((await response).status()).toBe(200);
  // Assert the exact payload consumed by the app. Chromium can discard its
  // inspector-side response body even after the bounded reader renders it.
  await expect(page.locator('#pdd-status')).toContainText('Saved source access checked');
  const access=JSON.parse(await page.locator('#pdd-result').textContent());
  expect(access.access).toBe('readable');expect(access.raw_data_returned).toBe(false);expect(access.report_created).toBe(false);
  expect(JSON.stringify(access)).not.toContain('never-return-this-value');
  await page.locator('#pdd-key-picker').selectOption({index:1});await key.getByRole('button',{name:'Create or rotate evidence key'}).click();
  await expect(page.locator('#pdd-status')).toContainText('existing sources were not changed');
  const unchanged=await (await request.get('/api/v1/privacy/data-discovery/sources/browser-cmdb')).json();
  expect(unchanged.evidence_key_env).toBe(before.evidence_key_env);expect(unchanged.revision).toBe(1);
  await page.locator('#pdd-evidence-key').selectOption({index:1});await source.getByRole('button',{name:'Save source settings'}).click();
  await expect(source.locator('[name="expected_revision"]')).toHaveValue('2');
  const rebound=await (await request.get('/api/v1/privacy/data-discovery/sources/browser-cmdb')).json();expect(rebound.evidence_key_env).not.toBe(before.evidence_key_env);
  await source.scrollIntoViewIfNeeded();await info.attach('connected-source',{body:await page.screenshot(),contentType:'image/png'});
  await fits(page);
});

test('owner shares an exact captured result and assigned colleague reviews it without live signoff',async({page,browser,baseURL,request},info)=>{
  const fixture=await (await request.get('/__browser_fixture')).json();
  await page.goto('/project-reviews#project='+fixture.project_id);
  await expect(page.locator('#pr-setup')).toBeVisible();
  await page.locator('#pr-member').selectOption('user:browser-colleague');await page.locator('#pr-grant').click();
  await expect(page.locator('#pr-members')).toContainText('user:browser-colleague');
  await page.locator('#pr-goal').selectOption(String(fixture.review_goal));await page.locator('#pr-preview').click();
  await expect(page.locator('#pr-preview-text')).toContainText('Evidence limitations remain explicit');
  await page.locator('#pr-reviewer').selectOption('user:browser-colleague');
  await page.locator('#pr-due').fill(new Date(Date.now()+86400000).toISOString().slice(0,16));
  await page.locator('#pr-note').fill('Review the captured evidence and limitations before responding.');
  const sent=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/packets'));
  await page.locator('#pr-send-button').click();const packet=await (await sent).json();
  await expect(page.locator('#pr-status')).toContainText('Review packet sent');
  const reviewer=await browser.newContext({baseURL,viewport:{width:320,height:800},extraHTTPHeaders:{Origin:baseURL,'X-Browser-Principal':'browser-colleague'}});
  try {
    const review=await reviewer.newPage();review.on('dialog',dialog=>dialog.accept());
    await reviewer.route('**/*',route=>new URL(route.request().url()).origin===new URL(baseURL).origin?route.continue():route.abort('blockedbyclient'));
    await review.goto('/action-center');await expect(review.locator('#content')).toContainText('Browser captured result');
    await review.goto('/project-reviews#packet='+packet.id);await expect(review.locator('#pr-result')).toContainText('Synthetic captured deliverable');
    await review.locator('#pr-action').selectOption('approve');await review.locator('#pr-response-note').fill('Synthetic colleague reviewed the captured result and limitations.');
    await review.locator('#pr-response-button').click();await expect(review.locator('#pr-status')).toContainText('Review response saved');
    await expect(review.locator('#pr-packet-summary')).toContainText('approved');await fits(review);
    await info.attach('assigned-colleague-review',{body:await review.screenshot(),contentType:'image/png'});
    await review.route('**/api/v1/project-reviews**',route=>route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:'Synthetic session expired'})}));
    await review.locator('#pr-refresh').click();await expect(review.locator('#pr-status')).toContainText('Synthetic session expired');
    await expect(review.locator('#pr-packet')).not.toBeVisible();await expect(review.locator('#pr-result')).toHaveText('');await expect(review.locator('#pr-inbox')).toHaveText('');
  } finally {await reviewer.close();}
  const final=await (await request.get('/__browser_fixture')).json();expect(final.review_signoff).toBeNull();
});
