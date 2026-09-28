(() => {
  'use strict';
  const root=document.getElementById('task-files');if(!root)return;
  const el=id=>document.getElementById('tf-'+id),base='/api/v1/task-files',goal=root.dataset.goal;
  let context=null,state=null,review=null,request=null,busy=false,locked=false;
  const make=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node;};
  const say=text=>{el('status').textContent=text;};
  // The file service explains refusals in sentences written for people; anything else gets a plain fallback.
  const plain=(detail,fallback)=>typeof detail==='string'&&detail.length<=400&&/^[A-Z]/.test(detail)&&!/[_{}<>]|HTTP|Traceback/.test(detail)?detail:fallback;
  const size=bytes=>bytes<1024?bytes+' bytes':bytes<1048576?(bytes/1024).toFixed(1)+' KB':(bytes/1048576).toFixed(1)+' MB';
  function clear(){review=null;request=null;el('files').replaceChildren();el('review').hidden=true;el('confirm').checked=false;el('enable-confirm').checked=false;el('disable-confirm').checked=false;}
  function update(){root.setAttribute('aria-busy',String(busy));root.querySelectorAll('button,input').forEach(node=>node.disabled=busy||locked);el('preview').disabled=busy||locked||!state?.has_history;el('enable').disabled=busy||locked||!el('enable-confirm').checked;el('disable').disabled=busy||locked||!el('disable-confirm').checked;el('restore').disabled=busy||locked||!review||!request||!el('confirm').checked||review.operation?.state==='complete';}
  function deny(){clear();state=null;locked=true;el('enable-panel').hidden=true;el('disable-panel').hidden=true;update();}
  async function api(path,method='GET',body){const response=await fetch(base+path,{method,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json',...(context?{'X-Lightwork-File-Context':context}:{})},...(body?{body:JSON.stringify({...body,context,confirm:true})}:{})});
    if([401,403,404].includes(response.status)){deny();throw new Error('Current task access is unavailable. Sign in and reload this task.');}
    if(response.status>=500)throw new Error('The file recovery result could not be confirmed. Refresh to inspect the retained request before retrying.');
    const raw=await response.text();let data;try{if(raw.length>2*1024*1024)throw new Error();data=JSON.parse(raw);}catch(_){throw new Error('File recovery returned an unreadable response. Refresh before continuing.');}
    if(!response.ok){if(response.status===409&&/account or workspace/.test(data.detail||''))deny();throw new Error(plain(data.detail,'File recovery was refused. Review the changes again.'));}
    if(!data||typeof data!=='object'||!/^[a-f0-9]{64}$/.test(data.context))throw new Error('The file recovery context could not be verified.');
    if(context&&context!==data.context){deny();throw new Error('Your account or workspace changed. Reload this task.');}context=data.context;return data;
  }
  async function run(action){if(busy||locked)return;busy=true;update();try{await action();}catch(error){el('confirm').checked=false;el('enable-confirm').checked=false;el('disable-confirm').checked=false;say(error.message);el('status').focus();}finally{busy=false;update();}}
  async function refresh(){clear();state=await api('/'+goal);el('enable-panel').hidden=state.capture_enabled||!state.can_enable;el('disable-panel').hidden=!state.capture_enabled||!state.can_enable;say(state.has_history?'Saved originals are available. Stop this task and any task using the same folder, then review the changes.':state.capture_enabled?'File protection is on. No originals are saved for this task yet; they are kept during its next run on this computer.':'File protection is off, so capture is off. An administrator can turn it on here before the next task run.');}
  function show(data){if(data.goal_id!==Number(goal)||!/^[a-f0-9]{64}$/.test(data.version)||!Array.isArray(data.files)||data.files.length>100||data.files.some(row=>typeof row.path!=='string'||typeof row.diff!=='string'||!row.current||!row.restored))throw new Error('The reviewed file list could not be verified. Refresh before restoring.');
    clear();review=data;const op=data.operation||{};request=op.state==='applying'?{version:op.version,request_id:op.request_id}:{version:data.version,request_id:crypto.randomUUID()};
    if(!/^[a-f0-9]{64}$/.test(request.version)||!/^[A-Za-z0-9_-]{8,100}$/.test(request.request_id))throw new Error('The retained recovery request could not be verified.');
    for(const row of data.files){const card=make('article','');card.append(make('h2',row.path),make('p',(row.current.exists?'Now: '+size(row.current.bytes):'Not there now')+' · '+(row.restored.exists?'After restoring: '+size(row.restored.bytes):'This file was created by the task and will be removed')),make('pre',row.diff||'No text differences.'));
      if(row.preview_truncated)card.append(make('p','This preview is shortened. Complete file fingerprints are checked before restoration.'));
      const details=make('details','');details.append(make('summary','File fingerprints'),make('p','Current: '+(row.current.sha256||'absent')),make('p','Restored: '+(row.restored.sha256||'absent')));card.append(details);el('files').append(card);}
    el('review').hidden=!data.files.length;el('operation').textContent=op.state==='applying'?'An earlier restore was interrupted. This button retries exactly that restore and checks each remaining file.':op.state==='complete'?'These files were already restored. The receipt has been kept.':'Restoring applies only to the changes shown here.';
    el('restore').textContent=op.state==='applying'?'Retry the interrupted restore':'Restore reviewed files';say(data.files.length?'Review each listed change before confirming.':'This run did not save any file changes that can be restored.');update();
  }
  el('refresh').addEventListener('click',()=>run(refresh));
  el('preview').addEventListener('click',()=>run(async()=>{clear();show(await api('/'+goal+'/preview'));}));
  el('confirm').addEventListener('change',update);el('enable-confirm').addEventListener('change',update);el('disable-confirm').addEventListener('change',update);
  el('enable').addEventListener('click',()=>run(async()=>{if(!el('enable-confirm').checked)return;await api('/enable','POST',{});await refresh();say('File protection is on (capture enabled) for future task runs. No task was started.');}));
  el('disable').addEventListener('click',()=>run(async()=>{if(!el('disable-confirm').checked)return;if(typeof window.mvConfirm==='function'&&!await window.mvConfirm('Turn off file protection? Future task changes to files can\'t be undone here. Copies already kept stay.',{okText:'Turn off'}))return;await api('/disable','POST',{});await refresh();say('File protection is off for future task runs. Copies already kept were not deleted.');}));
  el('restore').addEventListener('click',()=>run(async()=>{if(!review||!request||!el('confirm').checked)return;const result=await api('/'+goal+'/restore','POST',request);if(result.operation?.state!=='complete'||result.operation.request_id!==request.request_id||result.operation.version!==request.version)throw new Error('The recovery receipt did not match this reviewed request. Refresh before continuing.');clear();say('The reviewed files were restored and the receipt was kept. No task was restarted.');}));
  run(refresh);
})();
