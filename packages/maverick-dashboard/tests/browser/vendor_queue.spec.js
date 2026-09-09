const {test, expect} = require('@playwright/test');

const pages = {
  '': {
    cases: [
      {id: 'VON-00000000000000000000000000000001', vendor_name: 'Northstar vendor with a deliberately long customer-visible name that must wrap', stage: 'intake', renewal_of: null, owner: 'procurement', updated_at: 1800000001},
      {id: 'VON-00000000000000000000000000000002', vendor_name: 'Beacon AI', stage: 'pending_review', renewal_of: null, owner: 'procurement', updated_at: 1800000002},
    ],
    has_more: true,
    next_cursor: 'cursor-page-two',
  },
  'cursor-page-two': {
    cases: [
      {id: 'VON-00000000000000000000000000000003', vendor_name: 'Atlas AI', stage: 'reconciliation_required', renewal_of: null, owner: 'procurement', updated_at: 1800000003},
      {id: 'VON-00000000000000000000000000000004', vendor_name: 'Harbor AI', stage: 'accepted', renewal_of: null, owner: 'procurement', updated_at: 1800000004},
    ],
    has_more: true,
    next_cursor: 'cursor-page-three',
  },
  'cursor-page-three': {
    cases: [
      {id: 'VON-00000000000000000000000000000005', vendor_name: 'Cedar AI', stage: 'cancelled', renewal_of: null, owner: 'procurement', updated_at: 1800000005},
    ],
    has_more: false,
    next_cursor: null,
  },
};

test.beforeEach(async ({context, page, baseURL}) => {
  await context.route('**/*', route => new URL(route.request().url()).origin === new URL(baseURL).origin
    ? route.continue() : route.abort('blockedbyclient'));
  page.__uncaught = [];
  page.on('pageerror', error => page.__uncaught.push(error.message));
  await page.route('**/api/v1/vendor-onboarding/cases*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || url.pathname !== '/api/v1/vendor-onboarding/cases')
      return route.continue();
    const view = url.searchParams.get('view') || 'all';
    const page = view === 'unresolved' ? {
      cases: [pages['cursor-page-two'].cases[0]], has_more: false, next_cursor: null,
    } : pages[url.searchParams.get('cursor') || ''];
    await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({
      schema: 'lightwork.vendor-onboarding.case-page.v1', view, limit: 25,
      record_reads: page.cases.length, scan_complete: !page.has_more,
      order: 'case_id_ascending', ...page,
    })});
  });
});

test.afterEach(async ({page, context}, info) => {
  await info.attach('vendor-queue-rendered', {body: await page.screenshot({fullPage: true}), contentType: 'image/png'});
  expect(page.__uncaught).toEqual([]);
  await context.unrouteAll({behavior: 'ignoreErrors'});
});

for (const width of [1280, 320]) {
  test(`${width}px queue navigates three bounded pages and filters unresolved work`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    await page.goto('/third-party/vendor-onboarding');
    await expect(page.locator('#vo-page-status')).toContainText('Page 1');
    await expect(page.locator('#vo-cases button')).toHaveCount(2);
    await expect(page.locator('#vo-cases')).toContainText('Owner procurement');
    await expect(page.locator('#vo-cases')).toContainText('Last progress');
    await expect(page.locator('#vo-page-previous')).toBeDisabled();
    await page.locator('#vo-page-next').click();
    await expect(page.locator('#vo-page-status')).toContainText('Page 2');
    await expect(page.locator('#vo-cases')).toContainText('reconciliation required');
    await page.locator('#vo-page-next').click();
    await expect(page.locator('#vo-page-status')).toContainText('Page 3');
    await expect(page.locator('#vo-page-next')).toBeDisabled();
    await page.locator('#vo-page-previous').click();
    await expect(page.locator('#vo-page-status')).toContainText('Page 2');
    await page.locator('#vo-case-filter').selectOption('unresolved');
    await expect(page.locator('#vo-page-status')).toContainText('Page 1 · 1 request shown · unresolved view');
    await expect(page.locator('#vo-cases button')).toHaveCount(1);
    await expect(page.locator('#vo-cases')).toContainText('Atlas AI');
    await expect(page.locator('#vo-page-previous')).toBeDisabled();
    const size = await page.evaluate(() => ({
      client: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth,
    }));
    expect(size.scroll).toBeLessThanOrEqual(size.client + 1);
  });
}
