const {test, expect} = require('@playwright/test');

test.afterEach(async ({page}) => {
  await page.request.post('/__browser_fixture/specialist-draft/disarm').catch(() => {});
});

test('Agent Factory reviews and starts one real task with the exact unpublished draft', async ({page, context, baseURL}) => {
  const origin = new URL(baseURL).origin;
  await context.route('**/*', route => new URL(route.request().url()).origin === origin
    ? route.continue() : route.abort('blockedbyclient'));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));

  const armed = await page.request.post('/__browser_fixture/specialist-draft/arm');
  expect(armed.ok()).toBe(true);
  expect(await armed.json()).toMatchObject({synthetic: true, provider_calls: 0, connected_service_calls: 0});

  await page.goto('/agents?name=cx_triage#specialist-drafts');
  await expect(page.locator('[data-sd-status]')).toContainText('drafts are ready');
  const task = 'Summarize the approved request and identify the next review step.';
  await page.locator('[data-sd-run-task]').fill(task);
  await page.getByRole('button', {name: 'Review and run this draft on a real task'}).click();

  const review = page.locator('[data-sd-review]');
  await expect(review).toContainText('Real task');
  await expect(review).toContainText(task);
  await expect(review).toContainText('Up to $2.00 and 10 minutes');
  await expect(review).toContainText('selected company knowledge and connected tools');
  await review.getByRole('checkbox', {name: 'I reviewed every setting and consequence above.'}).check();
  await review.getByRole('button', {name: 'Confirm this decision'}).click();

  await expect(page.locator('[data-sd-result]')).toContainText('Task in My Work');
  const taskLink = page.locator('[data-sd-result] a[href^="/goals/"]');
  await expect(taskLink).toHaveAttribute('href', /\/goals\/\d+/);
  const deadline = Date.now() + 30000;
  let result;
  while (Date.now() < deadline) {
    const response = await page.request.get('/__browser_fixture/specialist-draft');
    expect(response.ok(), await response.text()).toBeTruthy();
    result = await response.json();
    const failed = result.jobs.find(job => job.state === 'failed');
    if (failed) throw new Error(`The local task worker failed: ${failed.problem}`);
    if (result.goal_status === 'done') break;
    await page.waitForTimeout(250);
  }
  expect(result.goal_status, JSON.stringify(result)).toBe('done');
  expect(result).toMatchObject({
    synthetic: true,
    provider_calls: 0,
    connected_service_calls: 0,
    worker_calls: 1,
    goal_status: 'done',
    owner: 'user:browser-reviewer',
    domain: 'cx_triage',
    workspace: 'browser-workspace',
  });
  expect(result.profile).toMatchObject({name: 'cx_triage'});
  await expect(page.locator('#agent-form')).toHaveAttribute('data-has-base', '1');
  expect(errors).toEqual([]);
});

test('Chat Copilot opens the same reviewed task path for an unpublished specialist draft', async ({page, context, baseURL}) => {
  test.setTimeout(120000);
  const origin = new URL(baseURL).origin;
  await context.route('**/*', route => new URL(route.request().url()).origin === origin
    ? route.continue() : route.abort('blockedbyclient'));
  const seeded = await page.request.post('/__browser_fixture/specialist-draft/seed');
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  expect(await seeded.json()).toMatchObject({synthetic: true, saved: true, provider_calls: 0, connected_service_calls: 0});
  const armed = await page.request.post('/__browser_fixture/specialist-draft/arm');
  expect(armed.ok()).toBe(true);
  await page.goto('/chat');
  await page.locator('#cs-side [data-copilot-toggle]').click();
  const runDraft = page.locator('#mv-product-operation-buttons')
    .getByRole('button', {name: 'Run a real task with an unpublished specialist draft', exact: true});
  await expect(runDraft).toBeEnabled();
  await runDraft.click();

  const form = page.locator('#mv-product-operation-form');
  await expect(form).toContainText('Run a real task with an unpublished specialist draft');
  expect(await (await page.request.get('/__browser_fixture/specialist-draft')).json()).toMatchObject({worker_calls: 0});
  const draft = form.getByRole('combobox', {name: 'Saved draft', exact: true});
  await expect(draft.locator('option')).toHaveCount(1);
  await expect(draft.locator('option').first()).toContainText('Chat entry test draft');
  await form.getByRole('textbox', {name: 'Real task for this unpublished draft', exact: true})
    .fill('Summarize the approved request and identify the next review step.');
  const reviewResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/copilot/product-operations/prepare'));
  await form.getByRole('button', {name: 'Review change', exact: true}).click();
  const review = await (await reviewResponse).json();
  await expect(form.locator('.specialist-draft-review-row')).toHaveCount(review.changes.length);
  await expect(form.locator('.specialist-draft-review-row').first()).toContainText('Decision');
  await expect(form.locator('.specialist-draft-review-row').last()).toContainText('If the response is lost');
  await expect(form).toContainText('Up to $2.00 and 10 minutes');
  await form.getByRole('checkbox', {name: 'I reviewed these exact changes and want to apply them.'}).check();
  await form.getByRole('button', {name: 'Apply reviewed change', exact: true}).click();
  await expect(form).toContainText('Task in My Work');
  const deadline = Date.now() + 30000;
  let result;
  while (Date.now() < deadline) {
    const response = await page.request.get('/__browser_fixture/specialist-draft');
    expect(response.ok(), await response.text()).toBeTruthy();
    result = await response.json();
    const failed = result.jobs.find(job => job.state === 'failed');
    if (failed) throw new Error(`The local task worker failed: ${failed.problem}`);
    if (result.goal_status === 'done') break;
    await page.waitForTimeout(250);
  }
  expect(result.goal_status, JSON.stringify(result)).toBe('done');
  expect(result).toMatchObject({
    synthetic: true,
    provider_calls: 0,
    connected_service_calls: 0,
    worker_calls: 1,
    goal_status: 'done',
    owner: 'user:browser-reviewer',
    domain: 'browser_chat_draft',
    workspace: 'browser-workspace',
  });
  expect(result.required_suites).toContain('customer_experience');
});
