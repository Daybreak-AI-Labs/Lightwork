const {test,expect}=require('@playwright/test');

async function localOnly(context,baseURL){
  await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(baseURL).origin?route.continue():route.abort('blockedbyclient'));
}
async function fits(page){const widths=await page.evaluate(()=>[document.documentElement.scrollWidth,document.documentElement.clientWidth]);expect(widths[0]).toBeLessThanOrEqual(widths[1]+1);}

test.use({viewport:{width:390,height:844}});

test('a high-risk approval is confirmed in the page on a phone, decided with a toast, then undone',async({page,context,request,baseURL})=>{
  await localOnly(context,baseURL);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const seeded=await request.post('/__browser_fixture/approvals/setup');expect(seeded.status()).toBe(200);
  const {approval_id:id}=await seeded.json();
  await page.goto('/approvals');
  const card=page.locator(`article.approval-item[data-id="${id}"]`);
  await expect(card).toBeVisible();
  await fits(page);
  // The whole card, including its decision buttons, fits inside the phone.
  const cardBox=await card.boundingBox();expect(cardBox.x+cardBox.width).toBeLessThanOrEqual(390);
  const approve=card.locator('button[data-action="approve"]');
  await approve.scrollIntoViewIfNeeded();
  const approveBox=await approve.boundingBox();expect(approveBox.x+approveBox.width).toBeLessThanOrEqual(390);
  await expect(page.locator('#approval-queue')).toContainText("Can't be undone");

  // One click does not decide: the in-page confirmation restates the action.
  await approve.click();
  const dialog=page.locator('#approval-confirm');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Change the owner of 12 accounts to Priya');
  await expect(dialog).toContainText('Salesforce: 12 account records');
  await expect(dialog).toContainText('Up to $2.00');
  await expect(dialog).toContainText("can't be undone");
  await page.locator('#approval-confirm-ok').click();
  await expect(page.locator('#approval-confirm-err')).toContainText('Tick the box');
  await expect(card.locator('.approval-outcome')).toBeHidden();
  await page.locator('#approval-confirm-ack').check();
  await page.locator('#approval-confirm-ok').click();

  await expect(dialog).toBeHidden();
  await expect(page.locator('#mv-toasts')).toContainText('authorized');
  const decided=page.locator('#approval-decided');
  await expect(decided).toBeVisible();
  await expect(decided.locator(`article.approval-item[data-id="${id}"]`)).toBeVisible();
  await expect(decided).toContainText('Approved just now by user:browser-reviewer');
  await expect(page.locator('#approval-queue .mv-empty__title')).toHaveText('Nothing needs your decision right now');
  await fits(page);

  // Undo while the agent is still waiting returns the request to the queue.
  const undo=decided.locator('button.approval-undo');
  await expect(undo).toContainText('Undo');
  await undo.click();
  await expect(page.locator('#mv-toasts')).toContainText('undone');
  await expect(page.locator(`#approval-queue article.approval-item[data-id="${id}"]`)).toBeVisible();
  await expect(decided).toBeHidden();
  const listed=await request.get('/api/v1/approvals');expect((await listed.json()).approvals.map(a=>a.id)).toContain(id);
  expect(errors).toEqual([]);
});
