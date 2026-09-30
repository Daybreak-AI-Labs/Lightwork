/* Explicit restart review, then one receipt-bound admission. Never auto retry. */
(function(){
  'use strict';
  const form=document.getElementById('halt-resume-form');if(!form)return;
  const button=document.getElementById('halt-resume'),budget=document.getElementById('halt-resume-budget'),message=document.getElementById('halt-resume-msg');
  const root='/api/v1/goals/'+form.dataset.goal,context=form.dataset.context,key='lightwork:restart:'+context+':'+form.dataset.goal;
  let prepared=null,busy=false,uncertain=false,received=false;
  const check=document.createElement('button');check.type='button';check.className='btn btn--ghost';check.textContent='Check restart receipt';check.hidden=true;form.appendChild(check);
  function pointer(){try{const value=sessionStorage.getItem(key);return value&&/^[a-f0-9]{32}$/.test(value)?value:null;}catch(_){return null;}}
  function remember(id){try{sessionStorage.setItem(key,id);}catch(_){message.textContent='Keep this page open until its restart receipt is confirmed.';}}
  function forget(){try{sessionStorage.removeItem(key);}catch(_){}check.hidden=true;}
  async function request(path,body){const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),30000);try{const response=await fetch(root+path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:abort.signal,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const value=await response.json().catch(()=>null);if(response.redirected||!value)throw new Error('Restart outcome is not confirmed. Keep the receipt reference and check current progress.');if(!response.ok){const error=new Error(value.detail&&value.detail.message||typeof value.detail==='string'&&value.detail||'Restart could not be accepted. Review current task progress.');error.known=response.status<500;throw error;}if(value.context&&value.context!==context){prepared=null;forget();const error=new Error('Your account or workspace changed. Reopen this task before restarting.');error.known=true;throw error;}return value;}finally{clearTimeout(timer);}}
  function confirmed(value){if(!value.received)throw new Error('No restart receipt is confirmed yet. The original request may still be finishing.');received=true;uncertain=false;forget();button.disabled=true;budget.disabled=false;message.textContent='Restart admission is recorded. Check task progress to confirm execution. No second attempt was dispatched.';}
  async function checkReceipt(){if(busy)return;const id=prepared&&prepared.operation_id||pointer();if(!id)return;busy=true;try{confirmed(await request('/resume-receipt/'+id));}catch(error){message.textContent=error.message;}finally{busy=false;}}
  check.onclick=checkReceipt;
  button.textContent='Review restart';
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(busy||received||button.disabled||!form.reportValidity())return;
    if(!prepared&&pointer()){message.textContent='An earlier restart needs a receipt check before another attempt.';check.hidden=false;return;}
    busy=true;button.disabled=true;const raw=budget.value.trim(),limit=raw?Number(raw):null;
    try{
      if(!prepared||!uncertain&&prepared.max_dollars!==limit){
        const review=await request('/resume-review');if(review.blocked)throw new Error(review.blocked);
        if(review.context!==context)throw new Error('Account or workspace changed. Reopen this task.');
        prepared={operation_id:crypto.randomUUID().replace(/-/g,''),expected_context:context,expected_updated_at:review.expected_updated_at,expected_owner:review.expected_owner,max_dollars:limit};
        message.textContent=review.title+'. '+review.consequence+' Reviewed attempt limit: '+(limit===null?'current workspace defaults':'$'+limit)+'. Select Confirm restart to authorize this attempt.';
        button.textContent='Confirm restart';return;
      }
      budget.disabled=true;remember(prepared.operation_id);check.hidden=false;message.textContent='Submitting the reviewed restart once.';
      const value=await request('/resume-reviewed',prepared);confirmed(value);
    }catch(error){message.textContent=error.message;if(error.known&&!uncertain){prepared=null;forget();budget.disabled=false;button.textContent='Review restart';}else if(prepared&&pointer()){uncertain=true;button.textContent='Retry same restart';message.textContent+=' Check its receipt or retry this exact request; no automatic retry will occur.';}}
    finally{busy=false;if(!received)button.disabled=false;}
  });
  if(pointer()){check.hidden=false;message.textContent='Checking the earlier restart receipt. No task will be started by this check.';checkReceipt();}
}());
