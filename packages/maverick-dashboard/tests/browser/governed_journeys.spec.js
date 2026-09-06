const {test,expect}=require('@playwright/test');

test.beforeEach(async({context,page,baseURL})=>{
  await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(baseURL).origin?route.continue():route.abort('blockedbyclient'));
  page.__uncaught=[];page.on('pageerror',error=>page.__uncaught.push(error.message));
});
test.afterEach(async({page,context},info)=>{
  await context.unrouteAll({behavior:'ignoreErrors'});
  await info.attach('rendered-state',{body:await page.screenshot({fullPage:true}),contentType:'image/png'});
  expect(page.__uncaught).toEqual([]);
});
async function fits(page){
  const result=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));
  expect(result.scroll).toBeLessThanOrEqual(result.width+1);
}
async function ok(response){expect(response.ok(),await response.text()).toBeTruthy();return response.json();}

test('consent picker reviews exact prior versions and prepares an unsaved next version',async({page,request})=>{
  const API='/api/v1/consent/publishing';
  for(const version of [1,2])await ok(await request.post(API+'/purposes/browser-analytics/versions',{data:{version,expected_current_version:version-1,
    idempotency_key:'browser-purpose-'+version,title_key:'purpose.title',description_key:'purpose.description',lawful_basis:'consent',required:false,default_choice:version===1?'denied':'granted'}}));
  await page.setViewportSize({width:375,height:812});await page.goto('/consent/publishing');
  const form=page.locator('#cpp-resource'),picker=form.locator('.cpp-picker').first();
  await picker.locator('summary').first().click();
  await picker.getByRole('searchbox').fill('browser-analytics');await picker.getByRole('searchbox').press('Enter');
  await expect(picker.getByRole('status')).toContainText('1 matching');
  await picker.getByRole('listbox',{name:'Matching saved records'}).selectOption({index:0});
  await expect(picker.getByRole('combobox',{name:'Saved version'})).toBeVisible();
  await picker.getByRole('combobox',{name:'Saved version'}).selectOption({label:'Version 2 · latest'});
  await expect(picker.getByRole('status')).toContainText('Compared with version 1');
  await picker.getByRole('combobox',{name:'Saved version'}).selectOption({label:'Version 1'});
  await expect(picker.getByRole('status')).toContainText('first saved version');
  let published=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().includes(API))published++;});
  await picker.getByRole('button',{name:'Prepare next version from this copy'}).click();
  await expect(form.locator('[name="resource_id"]')).toHaveValue('browser-analytics');
  const payload=JSON.parse(await form.locator('[name="payload"]').inputValue());
  expect(payload.version).toBe(3);expect(payload.expected_current_version).toBe(2);expect(payload.default_choice).toBe('denied');
  expect(published).toBe(0);await fits(page);
});

test('a failed recovery save retries the exact operation and survives a page reload',async({page})=>{
  await page.goto('/workflow-builder');const recovery=page.locator('.draft-recovery');
  await expect(recovery).toContainText('Draft recovery is ready');
  const attempts=[];
  const interrupted=async route=>{
    if(route.request().method()!=='PUT')return route.continue();
    attempts.push(route.request().postDataJSON());
    if(attempts.length===1)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Synthetic connection interruption'})});
    return route.continue();
  };
  await page.route('**/api/v1/drafts/*',interrupted);
  await page.locator('#wf-brief').fill('Recover my incomplete workflow after a failed connection');
  await expect(recovery).toContainText('Synthetic connection interruption');
  await expect(page.locator('#wf-brief')).toHaveValue('Recover my incomplete workflow after a failed connection');
  await recovery.getByRole('button',{name:'Retry recovery'}).click();
  await expect(recovery).toContainText('Recovery copy saved');expect(attempts).toHaveLength(2);expect(attempts[1]).toEqual(attempts[0]);
  await page.unroute('**/api/v1/drafts/*',interrupted);await page.reload();
  await expect(recovery).toContainText('A recovery copy is available');
  await recovery.getByRole('button',{name:'Restore recovery copy'}).click();await page.locator('#mv-confirm-ok').click();
  await expect(page.locator('#wf-brief')).toHaveValue('Recover my incomplete workflow after a failed connection');
  await expect(page.locator('#wf-status')).not.toContainText('Drafting');await fits(page);
});

test('real field validation marks the rejected input and preserves the other enrollment fields',async({page})=>{
  await page.goto('/consent/publishing');const form=page.locator('#cpp-property');
  await form.locator('[name="consent_group_id"]').fill('not a valid identifier!');
  await form.locator('[name="idempotency_key"]').fill('browser-invalid-enrollment');
  const rejected=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().endsWith('/properties/web-main'));
  await form.getByRole('button',{name:'Enroll / revise property'}).click();expect((await rejected).status()).toBe(422);
  const field=form.locator('[name="consent_group_id"]');await expect(field).toHaveAttribute('aria-invalid','true');await expect(field).toBeFocused();
  await expect(form.getByRole('alert')).toContainText(/Consent group/);
  await expect(form.locator('[name="web_domains"]')).toHaveValue('example.com');
  await expect(form.locator('[name="idempotency_key"]')).toHaveValue('browser-invalid-enrollment');
  await field.fill('browser-consumer-group');await expect(field).not.toHaveAttribute('aria-invalid','true');
  await page.setViewportSize({width:320,height:800});await fits(page);
});

test('workspace recovery creates a real local snapshot, downloads it and drills without live restore',async({page})=>{
  await page.setViewportSize({width:375,height:812});await page.goto('/workspace/recovery');
  await expect(page.locator('#wrec-key-create')).toBeEnabled();
  const key=page.waitForEvent('download');await page.locator('#wrec-key-create').click();
  expect((await key).suggestedFilename()).toBe('Lightwork-recovery-key.txt');
  await expect(page.locator('#wrec-create')).toBeEnabled();await page.locator('#wrec-create').click();
  await expect(page.locator('#wrec-status')).toContainText('Snapshot created and verified');
  await page.locator('#wrec-inspect').click();await expect(page.locator('#wrec-result')).toContainText('files verified');
  const archive=page.waitForEvent('download');await page.locator('#wrec-download').click();
  expect((await archive).suggestedFilename()).toMatch(/^Lightwork-snapshot-.+\.tgz$/);
  await page.locator('#wrec-drill').click();await expect(page.locator('#wrec-status')).toContainText('Local restore test passed');
  await expect(page.locator('#wrec-result')).toContainText('Your live workspace was not changed');
  await expect(page.locator('#wrec-prepare')).toBeDisabled();await fits(page);
});

test('pilot preparation freezes four unrun tasks and preserves an observation after failed save',async({page},info)=>{
  await page.goto('/outcome-pilots');
  await page.locator('#op-plan-details > summary').click();
  await page.getByText('Prepare a task without starting it',{exact:true}).click();
  const preparation=page.locator('#op-prepare');
  await expect(page.locator('#op-specialist option')).not.toHaveCount(1);
  for(let i=1;i<=4;i++){
    await preparation.locator('[name="title"]').fill('Browser prepared case '+i);
    await page.locator('#op-specialist').selectOption({index:1});
    await preparation.locator('[name="description"]').fill('Synthetic held task; no provider execution.');
    await preparation.getByRole('button',{name:'Save prepared task'}).click();
    await expect(page.locator('#op-prepared')).toContainText('Prepared: Browser prepared case '+i);
    await expect(preparation.getByRole('button')).toBeEnabled();
  }
  const plan=page.locator('#op-plan');
  await plan.locator('[name="title"]').fill('Browser comparison pilot');await plan.locator('[name="customer"]').fill('Synthetic internal team');
  await plan.locator('[name="workflow"]').fill('Review identical prepared cases');await plan.locator('[name="criteria"]').fill('Human-reviewed evidence and all limitations recorded');
  for(let i=0;i<4;i++)await page.locator('#op-planned-cases select').nth(i).selectOption({label:'Task: Browser prepared case '+(i+1)});
  await plan.getByRole('button',{name:'Save and freeze plan'}).click();
  await expect(page.locator('#op-report')).toContainText('0 accepted / 4 planned');
  const details=page.locator('#op-report > details').first();await details.locator('summary').click();
  const form=details.locator('form');await form.locator('[name="baseline_minutes"]').fill('75');
  await form.locator('[name="evidence"]').fill('Synthetic timing sheet; no customer evidence');await form.locator('[name="note"]').fill('Partial observation; no claim of savings');
  const reject=route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Synthetic interruption before save'})});
  await page.route('**/api/v1/outcome-pilots/*/cases/*',reject);
  await form.getByRole('button',{name:'Save observation'}).click();await expect(page.locator('#op-status')).toContainText('Synthetic interruption');
  await expect(form.locator('[name="baseline_minutes"]')).toHaveValue('75');await expect(form.locator('[name="note"]')).toHaveValue('Partial observation; no claim of savings');
  await page.unroute('**/api/v1/outcome-pilots/*/cases/*',reject);
  await form.getByRole('button',{name:'Save observation'}).click();await expect(page.locator('#op-status')).toContainText('Saved.');
  await expect(page.locator('#op-report')).toContainText('0 current complete pairs');
  const costs=page.locator('#op-cost-form');await costs.getByRole('button',{name:'Find billing receipts'}).click();
  await expect(costs.getByRole('status')).toContainText('matching source lines');
  await costs.locator('select').selectOption({index:1});
  await expect(costs).toContainText('Net admitted amount: USD 10.000000001');
  await costs.locator('input[type="number"]').nth(0).fill('25');await costs.locator('input[type="number"]').nth(1).fill('75');
  await costs.locator('textarea').fill('Synthetic internal attribution only; not provider invoice acceptance.');await costs.locator('input[type="checkbox"]').check();
  const confirmLabel=costs.locator('label:has(input[type="checkbox"])');
  const checkboxBox=await confirmLabel.locator('input').boundingBox(),captionBox=await confirmLabel.locator('span').boundingBox();
  expect(checkboxBox.x+checkboxBox.width).toBeLessThanOrEqual(captionBox.x);
  await costs.getByRole('button',{name:'Save reviewed allocation'}).click();
  await expect(page.locator('#op-provider-costs')).toContainText('Current allocated provider cost: USD 10.000000001');
  await page.getByText('Current allocated provider cost: USD 10.000000001',{exact:true}).scrollIntoViewIfNeeded();
  await info.attach('allocated-provider-cost',{body:await page.screenshot(),contentType:'image/png'});
  const download=page.waitForEvent('download');await page.locator('#op-export').click();
  expect((await download).suggestedFilename()).toBe('workflow-pilot-report.json');
  await page.setViewportSize({width:320,height:800});await fits(page);
});

test('held GRC delivery clears expired access and requires fresh human confirmation without resending',async({page,request},info)=>{
  const fixture=await (await request.get('/__browser_fixture')).json();
  await page.setViewportSize({width:375,height:812});await page.goto('/security/governance');
  const form=page.locator('#gov-grc-recovery');await form.locator('[name="delivery"]').selectOption(fixture.grc_delivery);
  await page.locator('#gov-grc-inspect').click();await expect(page.locator('#gov-grc-confirm')).toBeEnabled();
  await expect(page.locator('#gov-grc-comparison')).toContainText('matches');
  expect(await page.locator('#gov-grc-comparison').evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBeTruthy();
  await page.locator('#gov-grc-comparison').scrollIntoViewIfNeeded();
  await info.attach('held-grc-comparison',{body:await page.screenshot(),contentType:'image/png'});
  await form.locator('[name="note"]').fill('Retain this unsaved review during a temporary interruption.');
  const interrupted=route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Synthetic review interruption'})});
  await page.route('**/grc-sync/outbound/*/resolve',interrupted);
  await page.locator('#gov-grc-confirm').click();await expect(page.locator('#gov-grc-recovery-status')).toContainText('Synthetic review interruption');
  await expect(form.locator('[name="note"]')).toHaveValue('Retain this unsaved review during a temporary interruption.');
  await expect(page.locator('#gov-grc-comparison')).toBeEmpty();await expect(page.locator('#gov-grc-confirm')).toBeDisabled();
  await page.unroute('**/grc-sync/outbound/*/resolve',interrupted);
  await page.locator('#gov-grc-inspect').click();await expect(page.locator('#gov-grc-confirm')).toBeEnabled();
  const expired=route=>route.fulfill({status:401,contentType:'text/html',body:'<p>Synthetic expired session</p>'});
  await page.route('**/grc-sync/outbound/*/resolve',expired);
  await page.locator('#gov-grc-confirm').click();await expect(page.locator('#gov-grc-recovery-status')).toContainText('session or permissions changed');
  await expect(page.locator('#gov-grc-comparison')).toBeEmpty();await expect(form.locator('[name="note"]')).toHaveValue('');
  await expect(form.locator('[name="delivery"]')).toHaveValue('');await expect(page.locator('#gov-grc-inspect')).toBeDisabled();
  await page.unroute('**/grc-sync/outbound/*/resolve',expired);await page.reload();
  await form.locator('[name="delivery"]').selectOption(fixture.grc_delivery);
  await page.locator('#gov-grc-inspect').click();await expect(page.locator('#gov-grc-confirm')).toBeEnabled();
  await form.locator('[name="note"]').fill('Synthetic local reviewer compared the exact held attempt and recorded remote fields.');
  await page.locator('#gov-grc-confirm').click();await expect(page.locator('#gov-grc-recovery-status')).toContainText('No content was resent');
  const after=await (await request.get('/__browser_fixture')).json();expect(after.grc_writes).toBe(fixture.grc_writes);
  await fits(page);
});
