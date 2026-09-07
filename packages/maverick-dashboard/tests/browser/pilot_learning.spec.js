const {test,expect}=require('@playwright/test');
const fs=require('node:fs/promises');

async function localOnly(context,baseURL){
  await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(baseURL).origin?route.continue():route.abort('blockedbyclient'));
}
async function fits(page){const widths=await page.evaluate(()=>[document.documentElement.scrollWidth,document.documentElement.clientWidth]);expect(widths[0]).toBeLessThanOrEqual(widths[1]+1);}
async function downloadedReport(page){
  const pending=page.waitForEvent('download');await page.locator('#op-export').click();
  const download=await pending;return JSON.parse(await fs.readFile(await download.path(),'utf8'));
}

test('protected pilot enforces child exclusion and independently reviews exact local evidence without claiming prior exposure',async({page,context,browser,request,baseURL},info)=>{
  await localOnly(context,baseURL);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const seeded=await request.post('/__browser_fixture/pilot-learning/setup');expect(seeded.status()).toBe(200);
  const fixture=await seeded.json();expect(fixture.provider_calls).toBe(0);
  await page.setViewportSize({width:320,height:850});await page.goto('/outcome-pilots');
  const ownerContext=await page.locator('#outcome-pilots').getAttribute('data-context');
  await expect(page.locator('#op-planned-cases select')).toHaveCount(4);
  await page.locator('#op-plan-details > summary').click();const plan=page.locator('#op-plan');
  await plan.locator('[name="title"]').fill('Protected synthetic workflow pilot');
  await plan.locator('[name="customer"]').fill('Synthetic acceptance team');
  await plan.locator('[name="workflow"]').fill('Compare exact representative synthetic cases.');
  await plan.locator('[name="criteria"]').fill('Human checks the retained outcome; no model or provider is invoked by this test.');
  await page.locator('#op-learning-mode').selectOption('exclude_local_learning');
  await page.locator('#op-prior-exposure').fill('These synthetic cases are new locally; previous model exposure and provider training remain unverified.');
  for(let index=0;index<4;index++)await page.locator('#op-planned-cases select').nth(index).selectOption('goal:'+fixture.goals[index]);
  await fits(page);
  const created=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/v1/outcome-pilots');
  await plan.getByRole('button',{name:'Save and freeze plan'}).click();const creation=await created;expect(creation.status()).toBe(200);
  const pilot=await creation.json();await expect(page.locator('#op-report')).toContainText('Automatic local learning exclusion is recorded');
  const report=await downloadedReport(page);expect(report.state).toBe('frozen');
  expect(report.learning_policy.automatic_local_exclusion_enforced).toBe(true);
  expect(report.cases.filter(c=>c.allocation==='holdout')).toHaveLength(2);
  expect(report.holdout_exposure_verified).toBe(false);expect(report.learning_policy.provider_training_verified).toBe(false);
  expect(report.learning_policy.manual_imports_or_relabelled_copies_covered).toBe(false);
  await info.attach('protected-plan-report',{body:JSON.stringify(report,null,2),contentType:'application/json'});
  const probe=await request.post('/__browser_fixture/pilot-learning/'+pilot.id+'/probe');expect(probe.status()).toBe(200);
  const learning=await probe.json();expect(learning).toMatchObject({parent_excluded:true,child_excluded:true,held_capture:false,development_capture:true,business_child_exists:true,provider_calls:0});
  expect(learning.stored_goal_ids).toEqual([learning.development_goal]);
  await info.attach('actual-local-learning-probe',{body:JSON.stringify(learning,null,2),contentType:'application/json'});
  await fits(page);await info.attach('pilot-phone',{body:await page.screenshot({fullPage:true}),contentType:'image/png'});

  const reviewer=await browser.newContext({baseURL,viewport:{width:320,height:850},extraHTTPHeaders:{Origin:baseURL,'X-Browser-Principal':'browser-learning-reviewer'}});
  try{
    await localOnly(reviewer,baseURL);const second=await reviewer.newPage();second.on('pageerror',e=>errors.push(e.message));
    await second.goto('/outcome-pilots');await expect(second.locator('#op-planned-cases select')).toHaveCount(4);
    const reviewContext=await second.locator('#outcome-pilots').getAttribute('data-context');expect(reviewContext).not.toBe(ownerContext);
    const reviewPath='/api/v1/outcome-pilots/independent-review/'+pilot.id;
    expect((await request.get(reviewPath)).status()).toBe(403);
    expect((await reviewer.request.get('/api/v1/outcome-pilots/'+pilot.id)).status()).toBe(404);
    expect((await request.get(reviewPath,{headers:{'X-Browser-Principal':'browser-colleague'}})).status()).toBe(403);
    await second.getByText('Independently review a pilot’s local learning policy',{exact:true}).click();
    await second.locator('#op-review-reference').fill(pilot.id);await second.locator('#op-review-load').click();
    await expect(second.locator('#op-independent-report')).toContainText('All evaluation task exclusion receipts match');
    const evidence=await (await reviewer.request.get(reviewPath)).json();
    const body={expected_revision:evidence.revision,evidence_version:evidence.learning_policy.evidence_version,operation_id:'browser-independent-negative-0001',decision:'accepted_local_scope',notes:'Reviewed local policy receipts; prior and provider exposure are unknown.',confirm:true};
    expect((await reviewer.request.post(reviewPath,{headers:{Origin:'https://wrong-origin.example','X-Lightwork-Pilot-Context':reviewContext},data:body})).status()).toBe(403);
    expect((await reviewer.request.post(reviewPath,{headers:{'X-Lightwork-Pilot-Context':ownerContext},data:body})).status()).toBe(409);
    const independent=second.locator('#op-independent-report form');await independent.locator('select').selectOption('accepted_local_scope');
    await independent.locator('textarea').fill(body.notes);await independent.locator('input[type="checkbox"]').check();await fits(second);
    await info.attach('independent-review-phone',{body:await second.screenshot({fullPage:true}),contentType:'image/png'});
    const saved=second.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===reviewPath);
    await independent.getByRole('button',{name:'Record independent review'}).click();expect((await saved).status()).toBe(200);
    await expect(second.locator('#op-independent-report')).toContainText('Independent review recorded');
    await page.locator('#op-refresh').click();await expect(page.locator('#op-report')).toContainText('browser-learning-reviewer');
    const reviewed=await downloadedReport(page);expect(reviewed.learning_policy.independent_review.current).toBe(true);
    expect(reviewed.learning_policy.independent_review.actor).toBe('user:browser-learning-reviewer');
    await second.locator('#op-review-load').click();await expect(second.locator('#op-independent-report form')).toBeVisible();
    const stale=second.locator('#op-independent-report form');await stale.locator('select').selectOption('accepted_local_scope');
    await stale.locator('textarea').fill('This review must refuse after the original source input changes.');await stale.locator('input[type="checkbox"]').check();
    expect((await request.post('/__browser_fixture/pilot-learning/'+pilot.id+'/change')).status()).toBe(200);
    const refused=second.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===reviewPath);
    await stale.getByRole('button',{name:'Record independent review'}).click();expect((await refused).status()).toBe(409);
    await expect(second.locator('#op-independent-report')).toBeEmpty();await expect(second.locator('#op-review-reference')).toHaveValue('');
    await expect(second.locator('#op-review-load')).toBeDisabled();
    await page.locator('#op-refresh').click();await expect(page.locator('#op-report')).toContainText('evidence changed; review again');
    const changed=await downloadedReport(page);expect(changed.learning_policy.automatic_local_exclusion_enforced).toBe(false);
    expect(changed.learning_policy.independent_review.current).toBe(false);expect(changed.holdout_exposure_verified).toBe(false);
    await info.attach('changed-evidence-report',{body:JSON.stringify(changed,null,2),contentType:'application/json'});
    await page.setViewportSize({width:1280,height:900});await fits(page);
    await info.attach('pilot-desktop-after-source-change',{body:await page.screenshot({fullPage:true}),contentType:'image/png'});
  }finally{await reviewer.close();}
  expect(errors).toEqual([]);
});
