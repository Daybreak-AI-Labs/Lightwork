const {chromium,expect}=require('C:/Users/Cstep/Documents/Codex/2026-09-10/new-chat/work/lightwork-pr2519-layer-20260928/packages/maverick-dashboard/tests/browser/node_modules/@playwright/test');
const fs=require('fs'),API='/api/v1/learning/portable-rules';
(async()=>{
  const browser=await chromium.launch({headless:true}),errors=[],surfaces=[];
  for(const [baseURL,actor] of [['http://127.0.0.1:8915','browser-reviewer'],['http://127.0.0.1:8916','browser-learning-reviewer']]){
    const context=await browser.newContext({baseURL,viewport:{width:320,height:900},extraHTTPHeaders:{Origin:baseURL,'X-Browser-Principal':actor}});
    await context.route('**/*',route=>new URL(route.request().url()).origin===baseURL?route.continue():route.abort('blockedbyclient'));
    context.on('page',page=>page.on('pageerror',error=>errors.push(error.stack)));
    surfaces.push({context,page:await context.newPage(),initial:await(await context.request.get('/__browser_fixture/portable/state')).json()});
  }
  const [source,target]=surfaces,sourceFixture=source.initial.fixtures['private-source'],targetFixture=target.initial.fixtures['local-target'];
  expect(Object.keys(source.initial.fixtures)).toEqual(['private-source']);expect(Object.keys(target.initial.fixtures)).toEqual(['local-target']);
  async function retain(surface,fixture){
    await surface.page.goto('/learning/recheck-rules?workflow='+fixture.flow);
    await expect(surface.page.locator('#rq-versions')).toContainText(fixture.name,{timeout:30000});
    for(const run of fixture.runs){
      await surface.page.locator('[name=practice]').selectOption(run);await surface.page.locator('#rq-retain').click();
      await expect(surface.page.locator('#rq-review')).toContainText('complete local check');await surface.page.locator('#rq-review input[type=checkbox]').check();
      await surface.page.locator('#rq-review').getByRole('button',{name:'Confirm complete evidence check',exact:true}).click();
      await expect(surface.page.locator('#rq-result')).toContainText('Both paths finished with exactly the same complete values.',{timeout:30000});
      await surface.page.locator('#rq-refresh').click();await expect(surface.page.locator('#rule-requalification')).toHaveAttribute('aria-busy','false');
    }
  }
  await retain(source,sourceFixture);await retain(target,targetFixture);
  await source.page.goto('/learning/portable-rules?flow=private-source');
  await expect(source.page.locator('#pc-cases [data-case]')).toHaveCount(2,{timeout:30000});
  for(const box of await source.page.locator('#pc-cases [data-selected]').all())await box.check();
  let shareReview;source.page.on('response',async response=>{if(response.url().endsWith(API+'/review')&&response.ok())shareReview=await response.json();});
  await source.page.locator('#pc-review').click();await expect(source.page.locator('#pc-preview')).toContainText('Complete shared rule structure');
  await source.page.locator('#pc-preview input[type=checkbox]').check();await source.page.locator('#pc-preview [data-confirm]').click();
  await expect(source.page.locator('#pc-result')).toContainText('The exact original result is retained.',{timeout:30000});
  const download=await source.context.request.get(API+'/download/'+shareReview.operation);expect(download.ok(),await download.text()).toBe(true);
  const raw=await download.text(),exported=JSON.parse(raw);
  for(const privateValue of ['private_amount','private-source','1000000000000','999999999998',...sourceFixture.runs])expect(raw).not.toContain(privateValue);
  expect(Object.keys(exported).sort()).toEqual(['schema','templates']);
  await target.page.goto('/learning/portable-rules?view=qualify&flow=local-target');await expect(target.page.locator('#pc-cases [data-case]')).toHaveCount(2);
  await target.page.locator('#pc-file').setInputFiles({name:'shared.lightwork',mimeType:'application/octet-stream',buffer:Buffer.from(raw)});
  await expect(target.page.locator('#pc-file-status')).toContainText('Structural file opened');
  for(const row of await target.page.locator('#pc-cases [data-case]').all()){
    await row.locator('[data-selected]').check();const text=await row.textContent(),input=text.match(/local amount: (\d+)/)[1];
    const answer=(BigInt(input)*BigInt(targetFixture.factor)).toString();await row.locator('[data-answer]').fill(answer);expect(await row.locator('[data-answer]').inputValue()).toBe(answer);
  }
  let qualifyReview;target.page.on('response',async response=>{if(response.url().endsWith(API+'/review')&&response.ok())qualifyReview=await response.json();});
  await target.page.locator('#pc-review').click();await expect(target.page.locator('#pc-preview')).toContainText('3 complete bounded input combinations');
  await target.page.locator('#pc-preview input[type=checkbox]').check();let lost=false;
  await target.context.route('**'+API+'/apply',async route=>{const response=await route.fetch();expect(response.ok(),await response.text()).toBe(true);lost=true;await route.abort('connectionfailed');});
  await target.page.locator('#pc-preview [data-confirm]').click();await expect(target.page.locator('#pc-status')).toContainText('Read the original outcome',{timeout:30000});
  await target.context.unroute('**'+API+'/apply');await target.page.reload();await expect(target.page.locator('#pc-history')).toHaveValue(qualifyReview.operation);
  await target.page.locator('#pc-read').click();await expect(target.page.locator('#pc-result')).toContainText('Qualified within these finite local input bounds');
  const qualified=await(await target.context.request.get(API+'/original/'+qualifyReview.operation)).json();
  expect(qualified.result.qualified).toBe(true);expect(qualified.result.ordinary_comparison_matched).toBe(true);expect(qualified.result.domain_points).toBe(3);
  expect(qualified.result.cases.every(row=>row.agreement&&row.complete&&typeof row.expected==='string'&&row.actual===row.expected)).toBe(true);
  const before=await(await target.context.request.get('/__browser_fixture/portable/state')).json();
  const replay=await target.context.request.post(API+'/apply',{data:{token:qualifyReview.token,confirmed:true}});expect(replay.ok(),await replay.text()).toBe(true);
  const after=await(await target.context.request.get('/__browser_fixture/portable/state')).json();expect(after.calls.length).toBe(before.calls.length);expect(lost).toBe(true);
  const otherInstallation=await(await source.context.request.get(API+'/original/'+qualifyReview.operation)).json();
  expect(otherInstallation.confirmed).toBe(false);expect(otherInstallation.result).toBeUndefined();
  await target.page.locator('#pc-inputs [data-input]').fill('999999999998');await target.page.locator('#pc-run-review').click();
  await expect(target.page.locator('#pc-preview')).toContainText('Complete named input values');await target.page.locator('#pc-preview input[type=checkbox]').check();
  await target.page.locator('#pc-preview [data-confirm]').click();const nativeAnswer=(999999999998n*999999999999n).toString();
  await expect(target.page.locator('#pc-result')).toContainText('Local rule answer: '+nativeAnswer);
  await target.page.locator('#pc-result').scrollIntoViewIfNeeded();await target.page.screenshot({path:'.browser-runtime/portable-independent-native-phone.png'});
  expect(await target.page.evaluate(()=>document.documentElement.scrollWidth)).toBe(320);
  await target.page.locator('#pc-history').selectOption(qualifyReview.operation);await target.page.locator('#pc-read').click();await expect(target.page.locator('#pc-run')).toBeVisible();
  await target.page.locator('#mv-copilot-fab').click();await target.page.locator('#mv-product-operation-buttons').getByRole('button',{name:'Run a qualified local rule',exact:true}).click();
  const copilot=target.page.locator('#mv-product-operation-form');await expect(copilot).toContainText('local amount');
  await copilot.getByLabel('local amount',{exact:false}).fill('999999999997');await copilot.getByRole('button',{name:'Review change',exact:true}).click();
  await expect(copilot).toContainText('Complete named input values');await copilot.locator('input[type=checkbox]').last().check();
  await copilot.getByRole('button',{name:'Apply reviewed change',exact:true}).click();const copilotAnswer=(999999999997n*999999999999n).toString();
  await expect(copilot).toContainText('Local rule answer');await expect(copilot).toContainText(copilotAnswer);await target.page.screenshot({path:'.browser-runtime/portable-independent-copilot-phone.png'});
  expect(await target.page.evaluate(()=>document.documentElement.scrollWidth)).toBe(320);expect(errors).toEqual([]);
  const final=await(await target.context.request.get('/__browser_fixture/portable/state')).json();expect(final.provider_calls).toBe(0);
  fs.writeFileSync('.browser-runtime/portable-independent-installations-evidence.json',JSON.stringify({installations:surfaces.map(row=>row.initial),exported,shareReview,qualifyReview,qualified,before,after,final,lost_reply_recovered:true,exact_replay_native_runs:0,nativeAnswer,copilotAnswer,errors},null,2));
  await browser.close();
})().catch(error=>{console.error(error);process.exit(1)});
