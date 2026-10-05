const {test, expect} = require('@playwright/test');

test('Show work presents readable activity and useful findings after live updates and replay', async ({page, request}) => {
  const created = await request.post('/__browser_fixture/chat');
  const {goal_id} = await created.json();
  await page.goto(`/chat/goal/${goal_id}`);
  expect((await request.post(`/__browser_fixture/chat/${goal_id}/activity`)).status()).toBe(200);
  await expect(page.locator('#events')).toContainText('Requested more information from you.');
  await page.locator('#work-activity > summary').click();
  await expect(page.locator('#events')).toContainText('The invoice matches the purchase order.');
  await expect(page.locator('#events')).not.toContainText(/INTERNAL PROMPT|private model scratch|tool_output|QUEUED|Prior failures/);
  await page.reload();
  await expect(page.locator('#events')).toContainText('Requested more information from you.');
  await expect(page.locator('#events')).not.toContainText(/INTERNAL PROMPT|private model scratch|tool_output|QUEUED|Prior failures/);
});
