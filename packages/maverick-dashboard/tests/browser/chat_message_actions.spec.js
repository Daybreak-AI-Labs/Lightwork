const {test, expect} = require('@playwright/test');

test('a saved answer exposes the product and its print, copy, download and share actions', async ({page, context, request}, info) => {
  const fixture = await (await request.get('/__browser_fixture')).json();
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.addInitScript(() => { window.print = () => { window.__printed = true; }; });
  await page.goto(`/chat/goal/${fixture.review_goal}`);

  const product = page.getByRole('navigation', {name: 'Lightwork product'});
  await expect(product.getByRole('link', {name: 'Flows'})).toBeVisible();
  await expect(product.getByRole('link', {name: 'Tasks'})).toBeVisible();
  const answer = page.locator('#result');
  await expect(answer).toContainText('Synthetic captured deliverable');

  await page.getByRole('button', {name: 'Copy answer'}).click();
  await expect(page.locator('#answer-action-msg')).toHaveText('Copied.');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    'Synthetic captured deliverable. Evidence limitations remain explicit.'
  );

  await page.getByRole('button', {name: 'Print conversation'}).click();
  expect(await page.evaluate(() => window.__printed)).toBe(true);
  await page.emulateMedia({media: 'print'});
  await expect(page.locator('#cs-side')).toHaveCSS('display', 'none');
  await expect(answer).toBeVisible();
  await info.attach('print-view', {body: await page.screenshot({fullPage: true}), contentType: 'image/png'});
  await page.emulateMedia({media: 'screen'});

  const downloaded = page.waitForEvent('download');
  await page.getByRole('link', {name: 'Download answer'}).click();
  expect((await downloaded).suggestedFilename()).toBe(`lightwork-answer-${fixture.review_goal}.md`);

  await page.getByRole('button', {name: 'Share', exact: true}).click();
  await expect(page.locator('#share-panel')).toHaveAttribute('open', '');
  await page.getByRole('button', {name: 'Create share link'}).click();
  const link = page.locator('#share-url');
  await expect(link).toBeVisible();
  await page.getByRole('button', {name: 'Copy link'}).click();
  await expect(page.locator('#share-msg')).toContainText('Link copied');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(await link.inputValue());
  await info.attach('answer-actions', {body: await page.screenshot(), contentType: 'image/png'});

  await page.setViewportSize({width: 390, height: 844});
  await expect(page.getByRole('button', {name: 'Menu', exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Menu', exact: true}).click();
  await expect(product.getByRole('link', {name: 'Flows'})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Close menu'})).toBeVisible();
  await expect(page.locator('main')).toHaveAttribute('inert', '');
  const firstNavigation = page.locator('#cs-side .cs-brand');
  await expect(firstNavigation).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  expect(await page.evaluate(() => document.getElementById('cs-side').contains(document.activeElement))).toBe(true);
  await page.keyboard.press('Tab');
  await expect(firstNavigation).toBeFocused();
  await page.getByRole('button', {name: 'Close menu'}).click();
  await expect(page.getByRole('button', {name: 'Menu', exact: true})).toBeFocused();
  await expect(page.locator('#cs-side')).toHaveCSS('visibility', 'hidden');
  await expect(page.getByRole('button', {name: 'Print conversation'})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391);
  await info.attach('answer-actions-mobile', {body: await page.screenshot(), contentType: 'image/png'});
});

test('message actions arrive after live completion without losing a reply or prior answers', async ({page, context, request}) => {
  const fixture = await (await request.post('/__browser_fixture/chat')).json();
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto(`/chat/goal/${fixture.goal_id}`);
  const earlier = page.getByRole('group', {name: 'Earlier answer actions'});
  await earlier.getByRole('button', {name: 'Copy answer'}).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('Earlier answer as originally delivered.');
  const priorDownload = page.waitForEvent('download');
  await earlier.getByRole('link', {name: 'Download answer'}).click();
  const savedPrior = await priorDownload;
  const stream = await savedPrior.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  expect(Buffer.concat(chunks).toString('utf8')).toBe('Earlier answer as originally delivered.');
  await page.locator('#answer-input').fill('Keep this unsent reply.');
  await request.post(`/__browser_fixture/chat/${fixture.goal_id}/finish`);
  const actions = page.getByRole('group', {name: 'Answer actions', exact: true});
  await expect(actions.getByRole('button', {name: 'Copy answer'})).toBeVisible();
  await expect(page.locator('#answer-input')).toHaveValue('Keep this unsent reply.');
  await actions.getByRole('button', {name: 'Copy answer'}).click();
  await expect.poll(() => page.evaluate(async () => (await navigator.clipboard.readText()).replace(/\r\n/g, '\n'))).toBe('## Finished answer\n\nDelivery is planned for Friday.');
  await actions.getByRole('button', {name: 'Edit & save'}).click();
  await page.locator('#edit-text').fill('Unsaved draft that must not replace the delivered answer.');
  await actions.getByRole('button', {name: 'Copy answer'}).click();
  await expect.poll(() => page.evaluate(async () => (await navigator.clipboard.readText()).replace(/\r\n/g, '\n'))).toBe('## Finished answer\n\nDelivery is planned for Friday.');
  const downloaded = page.waitForEvent('download');
  await actions.getByRole('link', {name: 'Download answer'}).click();
  expect((await downloaded).suggestedFilename()).toBe(`lightwork-answer-${fixture.goal_id}.md`);
  await actions.getByRole('button', {name: 'Share', exact: true}).click();
  await page.getByRole('button', {name: 'Create share link'}).click();
  await expect(page.locator('#share-url')).toBeVisible();
  await expect(page.locator('#qa-thread')).toContainText('Use Friday.');
  await page.emulateMedia({media: 'print'});
  await expect(page.locator('#qa-thread')).toBeVisible();
  await expect(page.locator('#qa-thread')).toContainText('Which delivery date should I use?');
});

test('a failed action refresh gives a working retry and preserves the next reply', async ({page, request}) => {
  const fixture = await (await request.post('/__browser_fixture/chat')).json();
  await page.goto(`/chat/goal/${fixture.goal_id}`);
  await page.locator('#answer-input').fill('Preserve my next message through a connection failure.');
  await page.route(`**/chat/goal/${fixture.goal_id}`, route => route.fulfill({status: 503, body: 'Synthetic temporary failure'}));
  await request.post(`/__browser_fixture/chat/${fixture.goal_id}/finish`);
  await expect(page.locator('#answer-refresh-retry')).toBeVisible();
  await expect(page.locator('#answer-input')).toHaveValue('Preserve my next message through a connection failure.');
  await page.unroute(`**/chat/goal/${fixture.goal_id}`);
  await page.locator('#answer-refresh-retry').click();
  await expect(page.getByRole('group', {name: 'Answer actions', exact: true})).toBeVisible();
  await expect(page.locator('#answer-input')).toHaveValue('Preserve my next message through a connection failure.');
});

test('printing wraps wide tables and long code instead of clipping the answer', async ({page, request}, info) => {
  const fixture = await (await request.get('/__browser_fixture')).json();
  await page.goto(`/chat/goal/${fixture.review_goal}`);
  await page.evaluate(() => {
    const answer = document.getElementById('result');
    const table = document.createElement('table');
    table.id = 'wide-print-table';
    const row = table.insertRow();
    for (let i = 0; i < 8; i++) row.insertCell().textContent = 'LongUnbrokenCell'.repeat(12);
    const code = document.createElement('pre');
    code.id = 'wide-print-code'; code.textContent = 'LongUnbrokenCode'.repeat(120);
    answer.append(table, code);
    for (let i = 0; i < 80; i++) {
      const paragraph = document.createElement('p');
      paragraph.textContent = `Printed paragraph ${i + 1}: all content must remain available across pages.`;
      answer.appendChild(paragraph);
    }
  });
  await page.emulateMedia({media: 'print'});
  for (const selector of ['#wide-print-table', '#wide-print-code']) {
    const size = await page.locator(selector).evaluate(node => ({width: node.clientWidth, content: node.scrollWidth}));
    expect(size.content, selector).toBeLessThanOrEqual(size.width + 1);
  }
  const pdf = await page.pdf({format: 'A4', printBackground: true});
  expect(pdf.length).toBeGreaterThan(10000);
  await info.attach('long-conversation-print', {body: pdf, contentType: 'application/pdf'});
});

test('a customer can revoke an existing share while the answer is being replaced', async ({page, request}) => {
  const fixture = await (await request.post('/__browser_fixture/chat')).json();
  const shareResponse = await request.post(`/api/v1/goals/${fixture.root_id}/share`);
  expect(shareResponse.status()).toBe(201);
  const share = await shareResponse.json();
  await request.post(`/__browser_fixture/chat/${fixture.root_id}/restart`);
  await page.goto(`/chat/goal/${fixture.root_id}`);
  await page.locator('#share-panel summary').click();
  await expect(page.getByRole('button', {name: 'Create share link'})).toHaveCount(0);
  await page.getByRole('button', {name: 'Revoke', exact: true}).click();
  await page.getByRole('button', {name: 'Revoke link', exact: true}).click();
  await expect(page.locator('#share-list li')).toHaveCount(0);
  await request.post(`/__browser_fixture/chat/${fixture.root_id}/finish`);
  expect((await request.get(share.url)).status()).toBe(404);
});
