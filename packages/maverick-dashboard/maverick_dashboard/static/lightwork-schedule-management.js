(() => {
  'use strict';
  const list=document.getElementById('auto-sched-list'); if(!list)return;
  const node=(tag,text)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;return e;};
  let epoch=0;
  function contextChanged(){epoch++;document.querySelectorAll('[data-schedule-editor]').forEach(e=>e.remove());}
  document.addEventListener('lightwork:workspace-changed',contextChanged);
  document.addEventListener('lightwork:connection-access-lost',contextChanged);
  async function request(url,body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch(url,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,cache:'no-store',signal:controller.signal});
      if([401,403].includes(response.status)){contextChanged();throw new Error('Access changed. Sign in again or ask an administrator.');}
      const data=await response.json();
      if(!response.ok)throw new Error(response.status===409?'This schedule or its evidence changed. Refresh and review it again.':window.mvErrorText?mvErrorText(response,'update the schedule'):'The request was refused. Refresh and review.');
      return data;
    }catch(error){if(error.name==='AbortError'||error instanceof TypeError)throw new Error('Result not confirmed. Refresh schedules before another change. No request was replayed.');throw error;}
    finally{clearTimeout(timer);}
  }
  function refresh(){document.dispatchEvent(new CustomEvent('lightwork:schedules-changed'));}
  function panel(row,title){
    row.querySelector('[data-schedule-editor]')?.remove();
    const box=node('section');box.className='panel';box.dataset.scheduleEditor='true';box.append(node('h3',title));
    const message=node('p');message.setAttribute('role','status');message.setAttribute('aria-live','polite');box.append(message);
    const close=node('button','Close');close.type='button';close.className='btn btn--ghost';close.addEventListener('click',()=>{box.remove();row.querySelector('button')?.focus();});
    box.append(close);row.append(box);return {box,message,close};
  }
  function input(box,label,type,value){
    const wrap=node('label',label),field=node(type==='select'?'select':'input');field.className='input';
    if(type!=='select')field.type=type;
    if(type==='checkbox')field.checked=Boolean(value);else field.value=value||'';
    wrap.append(field);box.append(wrap);return field;
  }
  function button(box,label,action){const b=node('button',label);b.type='button';b.className='btn';b.addEventListener('click',action);box.append(b);return b;}
  async function stateChange(row,schedule,state,control){
    if(control.disabled)return;control.disabled=true;const captured=epoch;
    try{
      const verb={paused:'Pause future runs',active:'Resume from the next future time',retired:'Retire this schedule'}[state];
      if(!await mvConfirm(verb+'? Work already admitted continues. Past occurrences will not be replayed.',{okText:verb}))return;
      if(captured!==epoch)return;
      await request('/api/v1/schedules/definitions/'+encodeURIComponent(schedule.definition_id),{expected_revision:schedule.revision,state,confirm:true});
      if(captured===epoch)refresh();
    }catch(error){const p=panel(row,'Schedule change');p.message.textContent=error.message;}
    finally{if(captured===epoch)control.disabled=false;}
  }
  async function edit(row,schedule){
    const {box,message}=panel(row,'Change future runs');const captured=epoch;
    box.append(node('p','Running work keeps its reviewed instructions. Unattempted future occurrences are replaced by this revision; history remains available.'));
    const title=input(box,'Schedule name','text',schedule.title);title.maxLength=200;
    const changeTime=input(box,'Change the timetable','checkbox',false);
    const repeat=input(box,'Repeat','select','');
    [['daily','Every day'],['weekdays','Weekdays'],['weekly','Selected weekdays'],['monthly','Monthly'],['hourly','Every few hours']].forEach(([v,label])=>repeat.append(new Option(label,v)));
    const at=input(box,'Time','time','09:00'),zone=input(box,'Time zone','select','');
    const hours=input(box,'Hours between runs','number','1');hours.min='1';hours.max='12';
    const day=input(box,'Day of month','number','1');day.min='1';day.max='28';
    const days=node('fieldset');days.append(node('legend','Days of the week'));box.append(days);
    const dayFields=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'].map((label,i)=>{const field=input(days,label,'checkbox',false);field.value=String(i);return field;});
    let reviewed=null,busy=false;
    const preview=button(box,'Review future runs',async()=>{
      if(busy)return;busy=true;preview.disabled=true;save.disabled=true;
      try{
        const body={expected_revision:schedule.revision,state:schedule.state,title:title.value.trim(),confirm:true};
        if(!body.title)throw new Error('Give the schedule a name.');
        if(changeTime.checked){
          body.timetable={repeat:repeat.value,at:at.value,zone:zone.value,every_hours:Number(hours.value),day_of_month:Number(day.value),days:dayFields.filter(f=>f.checked).map(f=>Number(f.value))};
          const result=await request('/api/v1/schedules/preview',{timetable:body.timetable});
          if(captured!==epoch)return;
          message.textContent='Next future runs: '+result.next_runs.map(value=>new Date(value*1000).toLocaleString(undefined,{timeZone:result.zone})).join('; ')+'.';
        }else message.textContent='The current timetable is preserved. Only the name or future definition changes.';
        reviewed=body;save.disabled=false;
      }catch(error){message.textContent=error.message;}finally{busy=false;preview.disabled=false;}
    });
    const save=button(box,'Save reviewed change',async()=>{
      if(!reviewed||busy)return;busy=true;save.disabled=true;preview.disabled=true;
      try{await request('/api/v1/schedules/definitions/'+encodeURIComponent(schedule.definition_id),reviewed);if(captured===epoch){box.remove();refresh();}}
      catch(error){message.textContent=error.message;reviewed=null;}
      finally{busy=false;preview.disabled=false;}
    });save.disabled=true;
    box.addEventListener('input',()=>{reviewed=null;save.disabled=true;});
    box.addEventListener('change',()=>{reviewed=null;save.disabled=true;});
    try{const options=await request('/api/v1/schedules/options');if(captured!==epoch)return;options.zones.forEach(value=>zone.append(new Option(value.replace(/_/g,' '),value)));zone.value=schedule.zone||'UTC';}
    catch(error){message.textContent=error.message;preview.disabled=true;}
    title.focus();
  }
  async function recovery(row,schedule){
    const {box,message}=panel(row,'Review future recurrence recovery');const captured=epoch;
    const parked=(schedule.history||[]).find(job=>job.recurrence_reconcile_required);
    if(!parked){message.textContent='Refresh to find the occurrence needing review.';return;}
    try{
      const review=await request('/api/v1/schedules/recovery/'+parked.id);if(captured!==epoch)return;
      message.textContent=review.consequence;
      box.append(node('p','Historical external result: unknown. '+review.problem));
      const choice=input(box,'Future recurrence','select','');choice.append(new Option('Create one next future occurrence','future'));
      review.successors.forEach(item=>choice.append(new Option('Link existing occurrence '+item.id+' · '+item.status,String(item.id))));
      const rationale=input(box,'Why is this the correct recovery?','text','');rationale.maxLength=1000;
      const stopped=input(box,'I checked the execution evidence and confirmed old workers are stopped','checkbox',false);
      const apply=button(box,'Repair future recurrence',async()=>{
        if(apply.disabled||!stopped.checked||rationale.value.trim().length<5){message.textContent='Describe the reviewed evidence and confirm old workers are stopped.';return;}
        apply.disabled=true;
        try{
          const result=await request('/api/v1/schedules/recovery/'+parked.id,{expected_attempts:review.attempts,expected_evidence:review.expected_evidence,context:review.context,
            successor_id:choice.value==='future'?null:Number(choice.value),rearm:choice.value==='future',confirm:true,rationale:rationale.value.trim()});
          if(captured!==epoch)return;
          if(result.repaired!==true||result.historical_replayed!==false)throw new Error('Recovery result was not confirmed. Refresh before continuing.');
          message.textContent='Future recurrence repaired. The historical result remains unknown and was not replayed.';refresh();
        }catch(error){message.textContent=error.message;}
      });
    }catch(error){message.textContent=error.message;}
  }
  async function history(row,schedule){
    const {box,message}=panel(row,'Schedule history');
    try{
      const data=await request('/api/v1/schedules/definitions/'+encodeURIComponent(schedule.definition_id)+'/history');
      data.versions.forEach(v=>box.append(node('p','Revision '+v.revision+' · '+v.state+' · '+new Date(v.created_at*1000).toLocaleString()+' · '+v.rationale)));
      data.schedule.history.forEach(job=>box.append(node('p','Run '+job.id+' · '+job.status+' · '+new Date(job.scheduled_at*1000).toLocaleString())));
      message.textContent=data.schedule.history_total+' retained occurrences; showing the most recent 50. Definition history is retained independently.';
      button(box,'Download audit history',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download='schedule-history.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    }catch(error){message.textContent=error.message;}
  }
  function attach(){
    list.querySelectorAll('[data-schedule]').forEach(row=>{
      if(row.dataset.managementAttached)return;row.dataset.managementAttached='true';
      const schedule=JSON.parse(row.dataset.schedule);if(!schedule.definition_id)return;
      button(row,'History',()=>history(row,schedule));
      if(schedule.state==='parked'){button(row,'Review recovery',()=>recovery(row,schedule));return;}
      if(schedule.state==='retired')return;
      button(row,'Change future runs',()=>edit(row,schedule));
      const toggle=button(row,schedule.state==='paused'?'Resume':'Pause',()=>stateChange(row,schedule,schedule.state==='paused'?'active':'paused',toggle));
      const retire=button(row,'Retire',()=>stateChange(row,schedule,'retired',retire));
    });
  }
  document.addEventListener('lightwork:schedules-rendered',attach);attach();
})();
