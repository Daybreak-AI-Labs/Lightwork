const {test,expect}=require('@playwright/test');
const start=require('./acceptance-server');

test('keyboard submission cannot default a fresh role grant to admin and an explicit custom role is recorded',async({browser},info)=>{
  test.setTimeout(180000);
  const app=await start(browser,info);
  try{
    const person=(await app.state()).unassigned,page=app.page;
    await page.goto('/users');
    let form=page.locator('form[action="/users/set"]').filter({has:page.getByRole('button',{name:'Give role',exact:true})});
    await form.locator('select[name=principal]').selectOption(person);
    await expect(form.locator('select[name=role]')).toHaveValue('');
    let roleWrites=0;page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/users/set')roleWrites++;});
    await form.getByRole('button',{name:'Give role',exact:true}).focus();
    await page.keyboard.press('Enter');
    await expect(form.locator('select[name=role]')).toBeFocused();
    expect(roleWrites).toBe(0);expect((await app.state()).unassigned_role).toBeNull();
    const custom=page.locator('form[action="/users/roles/save"]');
    await custom.locator('input[name=name]').fill('evidence-reviewer');
    await custom.locator('input[value=audit]').check();
    await custom.locator('input[name=description]').fill('Synthetic acceptance role: inspect history only.');
    await custom.getByRole('button',{name:'Save role',exact:true}).click();
    form=page.locator('form[action="/users/set"]').filter({has:page.getByRole('button',{name:'Give role',exact:true})});
    await form.locator('select[name=principal]').selectOption(person);
    await expect(form.locator('select[name=role]')).toHaveValue('');
    await form.locator('select[name=role]').selectOption('evidence-reviewer');
    await form.getByRole('button',{name:'Give role',exact:true}).focus();
    await page.keyboard.press('Enter');
    await expect.poll(async()=>(await app.state()).unassigned_role).toBe('evidence-reviewer');
    expect(roleWrites).toBe(1);
    await expect(page.locator('body')).toContainText('Evidence reviewer');
  }finally{await app.close();}
});
