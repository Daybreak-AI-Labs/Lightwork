const {test, expect} = require('@playwright/test');

test('automations renders saved starts and loads schedule setup after the shared UI initializes', async ({page, context, baseURL}) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => new URL(route.request().url()).origin === new URL(baseURL).origin
    ? route.continue() : route.abort());
  await page.route('**/api/v1/schedules', route => route.fulfill({json: {
    context: 'synthetic-workspace',
    schedules: [{id: 7, title: 'Daily intake review', cron: '0 9 * * *', zone: 'UTC', state: 'active'}],
  }}));
  await page.route('**/api/v1/triggers', route => route.fulfill({json: {
    secret_configured: true,
    triggers: [{name: 'New request', template: 'triage', template_title: 'Triage new requests'}],
  }}));
  await page.route('**/api/v1/automation-runs?*', route => route.fulfill({json: {summary: {}, runs: []}}));
  await page.route('**/api/v1/schedules/options', route => route.fulfill({json: {
    workflows: [{name: 'triage', title: 'Triage new requests', params: ['subject']}],
    tasks: [], zones: ['UTC'],
  }}));

  await page.goto('/automations');
  await expect(page.locator('#auto-sched-list')).toContainText('Daily intake review');
  await expect(page.locator('#auto-trig-list')).toContainText('New request');
  await page.locator('#auto-new-sched > summary').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#auto-new-workflow option[value="triage"]')).toHaveText('Triage new requests');
  await page.locator('#auto-new-workflow').selectOption('triage');
  await expect(page.locator('#auto-new-params input[data-param="subject"]')).toBeVisible();
  expect(errors).toEqual([]);
});
