(() => {
  'use strict';
  const list=document.getElementById('account-inventory-list'), status=document.getElementById('account-inventory-status');
  if (!list) return;
  let serial=0, context=null, lost=false;
  const node=(tag,text)=>{ const item=document.createElement(tag); item.textContent=text; return item; };
  const clear=()=>{serial++;lost=true;list.replaceChildren();status.textContent='Account access changed. Reload after selecting your workspace.';};
  document.addEventListener('lightwork:connection-access-lost',clear);
  document.addEventListener('lightwork:workspace-changed',clear);
  async function refresh() {
    if(lost)return;
    const attempt=++serial, controller=new AbortController(), timer=setTimeout(()=>controller.abort(),15000);
    status.textContent='Checking account inventory.';
    try {
      const response=await fetch('/api/v1/connections/inventory',{cache:'no-store',signal:controller.signal});
      if(attempt!==serial)return;
      if([401,403].includes(response.status)){clear();return;}
      if(!response.ok)throw new Error();
      const data=await response.json();
      if(attempt!==serial)return;
      if(!Array.isArray(data.accounts)||!Array.isArray(data.coverage)||typeof data.context!=='string')throw new Error();
      if(context&&context!==data.context){clear();return;} context=data.context;
      list.replaceChildren();
      data.accounts.forEach(account=>{
        const item=node('li',''); item.className='mv-row';
        item.append(node('strong',account.name),node('p',account.domain+' · '+account.owner+' · '+account.sharing),node('p',account.identity));
        if(account.expires_at)item.append(node('p',(account.expired?'Expired at ':'Expires at ')+new Date(account.expires_at*1000).toLocaleString()+(account.renews?' · renewal configured':'')));
        const link=node('a','Review or repair this account'); link.href=account.credential_kind==='automation'?'/automations#oauth-connect':'/connections';item.append(link);list.append(item);
      });
      const gaps=data.coverage.filter(item=>item.state!=='available');
      status.textContent=(data.accounts.length ? data.accounts.length+' accounts listed. ' : gaps.length ? 'Account coverage is incomplete. ' : 'No accounts are connected. ')+gaps.map(item=>item.domain+': '+item.state.replace(/-/g,' ')).join('; ');
    }catch(_){if(attempt===serial){list.replaceChildren();status.textContent='Account inventory is unavailable. Refresh before setting up a replacement account.';}}
    finally{clearTimeout(timer);}
  }
  document.getElementById('account-inventory-refresh').addEventListener('click',refresh);
  document.addEventListener('lightwork:connection-saved',refresh);refresh();
})();
