const {test,expect}=require('@playwright/test');
const ops='/api/v1/copilot/product-operations';
test('department boards read only owned permitted work and current access at 320px',async({page,request,context,baseURL},info)=>{
 await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(baseURL).origin?route.continue():route.abort('blockedbyclient'));
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const selected=await(await request.get('/__browser_fixture/department-boards')).json();
 let reply=await request.get('/api/v1/dashboards/overview?days=30');expect(reply.ok(),await reply.text()).toBeTruthy();let data=await reply.json();
 expect(data.totals).toEqual({total:3,active:1,done:2,blocked:0});expect(data.recent.map(row=>row.id).sort((a,b)=>a-b)).toEqual([selected.allowed,selected.general,selected.running].sort((a,b)=>a-b));expect(data.scope).toBe('permitted_own');
 const refuse=data.context;
 reply=await request.get('/api/v1/dashboards/spend?days=30');expect(reply.ok(),await reply.text()).toBeTruthy();data=await reply.json();expect(data.totals.dollars).toBe(2);expect(data.totals.runs).toBe(4);expect(data.totals.unknown_prices).toBe(3);expect(data.outcomes).toEqual({success:4,failed:0,running:1});
 const flat=JSON.stringify(data);expect(flat).not.toContain('Private finance');expect(flat).not.toContain('Another employee');expect(flat).not.toContain('Removed specialist');expect([...new Set(data.episodes.map(row=>row.goal_id))].sort((a,b)=>a-b)).toEqual([selected.allowed,selected.general,selected.running].sort((a,b)=>a-b));
 const legacy=await(await request.get('/api/v1/spend')).json();expect(legacy.total.dollars).toBe(2);expect(legacy.total.unknown_prices).toBe(3);expect(legacy.episodes.map(row=>row.goal_id)).not.toContain(selected.refused);
 for(const board of ['workforce','savings','oversight'])expect((await request.get('/api/v1/dashboards/'+board)).status()).toBe(403);
 for(const id of [selected.refused,selected.foreign,selected.removed])expect((await request.get('/api/v1/goals/'+id)).status()).toBe(404);
 await page.setViewportSize({width:320,height:844});await page.goto('/overview');await expect(page.locator('#department-board-status')).toContainText('Checked');await expect(page.locator('#department-board-records')).toContainText('Prepare the supplier review');await expect(page.locator('#department-board-records')).not.toContainText('Private');
 await page.locator('#department-board-days').selectOption('30');await expect(page.locator('#department-board-status')).toContainText('Checked');await expect(page.locator('#department-board-days')).toBeEnabled();
 const catalog=await(await request.post(ops+'/catalog',{data:{page:'/overview',selection:{}}})).json();const item=catalog.operations.find(row=>row.id==='boards.overview.read');expect(item).toBeTruthy();expect(JSON.stringify(item)).not.toContain('supplier review');
 await page.getByRole('button',{name:'Ask Copilot',exact:true}).click();await page.locator('#mv-product-operation-buttons').getByRole('button',{name:'Read my permitted work overview',exact:true}).click();const form=page.locator('#mv-product-operation-form');await form.getByRole('combobox',{name:'Report period',exact:true}).selectOption({label:'30 days'});await form.getByRole('button',{name:'Check current status',exact:true}).click();await expect(form).toContainText('Your tasks, all time');await expect(form).toContainText('3');
 await page.getByRole('button',{name:'Close Copilot',exact:true}).click();await page.locator('#department-board-shell a[href="/spend"]').click();await expect(page.locator('#department-board-metrics')).toContainText('$2.00');await expect(page.locator('#department-board-metrics')).toContainText('Missing prices');await expect(page.locator('#department-board-groups')).toContainText('Price unavailable');
 await page.screenshot({path:info.outputPath('native-department-spend-320px.png'),fullPage:true});expect(await page.locator('#department-board-groups').evaluate(el=>el.getBoundingClientRect().width)).toBeGreaterThan(200);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
 // A lost read is safely repeatable; it cannot erase or mutate task data.
 await context.route('**/api/v1/dashboards/spend?*',route=>route.abort('connectionfailed'));await page.locator('#department-board-refresh').click();await expect(page.locator('#department-board-error')).toBeVisible();await expect(page.locator('#department-board-metrics')).toBeEmpty();await context.unroute('**/api/v1/dashboards/spend?*');await page.locator('#department-board-refresh').click();await expect(page.locator('#department-board-metrics')).toContainText('$2.00');
 // A real inherited department changes after collection while grants stay unchanged.
 await request.post('/__browser_fixture/department-boards/pack-race',{headers:{'X-Browser-Principal':'browser-reviewer'}});
 reply=await request.get('/api/v1/dashboards/spend');expect(reply.status()).toBe(409);expect(await reply.text()).not.toContain('supplier review');
 await request.post('/__browser_fixture/department-boards/restore-pack',{headers:{'X-Browser-Principal':'browser-reviewer'}});
 // A grant changed after the page opened refuses its old account/grant binding.
 await request.post('/__browser_fixture/department-boards/change-grant',{headers:{'X-Browser-Principal':'browser-reviewer'}});
 reply=await request.get('/api/v1/dashboards/spend',{headers:{'X-Lightwork-Board-Context':refuse}});expect(reply.status()).toBe(409);
 await page.locator('#department-board-refresh').click();await expect(page.locator('#department-board-error')).toContainText('department access changed');await expect(page.locator('#department-board-groups')).toBeEmpty();await expect(page.locator('#department-board-refresh')).toBeDisabled();
 await page.reload();await expect(page.locator('#department-board-groups')).toContainText('Private finance forecast');await expect(page.locator('#department-board-groups')).not.toContainText('supplier review');
 // Switching to another account cannot paint that account's numbers into this page.
 await context.setExtraHTTPHeaders({Origin:baseURL,'X-Browser-Principal':'browser-outcome-owner'});await page.evaluate(()=>document.dispatchEvent(new CustomEvent('lightwork:account-context-changed')));await expect(page.locator('#department-board-error')).toContainText('account');await expect(page.locator('#department-board-groups')).toBeEmpty();expect(errors).toEqual([]);
});
