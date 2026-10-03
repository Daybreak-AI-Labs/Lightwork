const {test, expect} = require('@playwright/test');

test.beforeEach(async ({context, page, baseURL}) => {
  await context.route('**/*', route => new URL(route.request().url()).origin === new URL(baseURL).origin
    ? route.continue() : route.abort('blockedbyclient'));
  page.__errors = [];
  page.on('pageerror', error => page.__errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') page.__errors.push(message.text()); });
});

test.afterEach(async ({page}, info) => {
  const screenshot = info.outputPath('learned-skill-admission.png');
  await page.screenshot({path: screenshot, fullPage: true});
  await info.attach('learned-skill-admission', {path: screenshot, contentType: 'image/png'});
  expect(page.__errors).toEqual([]);
});

test('administrator reviews a local suggestion, admits its exact version and withdraws it', async ({page, request}) => {
  const setup = await request.post('/__browser_fixture/learned-skills/setup');
  expect(setup.ok()).toBeTruthy();
  const source = await setup.json();
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('/skills');
  const suggestion = page.locator('#to-learned-list');
  await expect(suggestion).toContainText('Customer renewal timing');
  await expect(suggestion).toContainText('Check customer renewal timing with the account owner');
  await expect(suggestion.locator('a[href="/chat/goal/' + source.goal_id + '"]')).toBeVisible();
  const denied = await request.get('/api/v1/skills/learned-candidates',
    {headers: {'X-Browser-Principal': 'browser-colleague'}});
  expect(denied.status()).toBe(403);

  await suggestion.getByRole('button', {name: 'Add this skill'}).click();
  const confirm = page.getByRole('dialog', {name: /Add.*agents.*skills/});
  await expect(confirm).toBeVisible();
  await confirm.getByRole('button', {name: 'Add skill'}).click();
  await expect(page.locator('#to-learned-status')).toContainText(/Added customer renewal timing/i);
  await expect(page.locator('#to-learned-added')).toContainText(/customer renewal timing/i);
  await expect(page.locator('#installed-skills [data-skill="customer-renewal-timing"]')).toBeVisible();

  await page.reload();
  await expect(page.locator('#to-learned-added')).toContainText(/customer renewal timing/i);
  await expect(page.locator('#to-learned-list')).toBeEmpty();
  await page.locator('#to-learned-added').getByRole('button', {name: /Remove customer renewal timing/i}).click();
  const remove = page.getByRole('dialog', {name: /Remove.*agents.*skills/});
  await expect(remove).toBeVisible();
  await remove.getByRole('button', {name: 'Remove skill'}).click();
  await expect(page.locator('#to-learned-status')).toContainText(/Removed customer renewal timing/i);
  await expect(page.locator('#installed-skills [data-skill="customer-renewal-timing"]')).toHaveCount(0);

  await page.setViewportSize({width: 320, height: 900});
  const bounds = await page.evaluate(() => ({width: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth}));
  expect(bounds.scroll).toBeLessThanOrEqual(bounds.width + 1);
});
