const {test, expect} = require('@playwright/test');

test('a provider failure stops the task and offers a connection route', async ({page, request}) => {
  const created = await request.post('/__browser_fixture/chat');
  expect(created.status(), await created.text()).toBe(200);
  const {goal_id} = await created.json();
  await page.goto(`/chat/goal/${goal_id}`);

  const failed = await request.post(`/__browser_fixture/chat/${goal_id}/provider-fail`);
  expect(failed.status(), await failed.text()).toBe(200);
  await expect(page.locator('#status')).toHaveAttribute('data-status', 'blocked');
  await expect(page.locator('#status')).toHaveText('Stopped');
  await expect(page.locator('#halt-card')).toBeVisible();
  await expect(page.locator('#halt-reason')).toContainText("couldn't reach the AI service");
  await expect(page.locator('#halt-action-link')).toHaveAttribute('href', '/settings?focus=ai-provider#settings-integrations');

  await page.reload();
  await expect(page.locator('#status')).toHaveText('stopped');
  await expect(page.locator('#halt-action-link')).toHaveAttribute('href', '/settings?focus=ai-provider#settings-integrations');
});
