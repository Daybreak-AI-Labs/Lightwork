(() => {
  'use strict';
  const panel=document.getElementById('workflow-versions'), content=document.getElementById('workflow-versions-content'), status=document.getElementById('workflow-versions-status');
  if(!panel)return;
  let name=new URLSearchParams(location.search).get('edit')||new URLSearchParams(location.search).get('template'), serial=0, context=null, busy=false, lost=false;
  const node=(tag,text)=>{const e=document.createElement(tag);if(text)e.textContent=text;return e;};
  const control=(label,fn)=>{const e=node('button',label);e.type='button';e.className='btn';e.addEventListener('click',fn);return e;};
  const field=(parent,label,value='',type='text')=>{const wrapper=node('label',label), input=node('input');input.className='input';input.type=type;input.value=value;wrapper.append(input);parent.append(wrapper);return input;};
  function clear(){lost=true;serial++;content.replaceChildren();status.textContent='Account or workspace changed. Reload to review this workflow.';}
  document.addEventListener('lightwork:workspace-changed',clear);
  async function request(path,body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch('/api/v1/workflows/'+encodeURIComponent(name)+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify({...body,context}):undefined,cache:'no-store',signal:controller.signal});
      const data=await response.json().catch(()=>({}));
      if(response.status===401){clear();throw new Error('Sign in before continuing.');}
      if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'The workflow review could not be completed.');
      return data;
    }finally{clearTimeout(timer);}
  }
  async function mutate(button,path,body,success){
    if(busy||lost)return;busy=true;button.disabled=true;
    try{const data=await request(path,body);if(lost)return;await success(data);}
    catch(error){status.textContent=error.name==='AbortError'?'The result is unknown. Refresh the version review before trying again.':error.message;}
    finally{busy=false;if(button.isConnected)button.disabled=false;}
  }
  async function refresh(){
    if(!name||lost||busy)return;panel.hidden=false;const attempt=++serial;status.textContent='Loading saved versions.';
    content.replaceChildren();
    try{
      const data=await request('/versions');if(attempt!==serial||lost)return;
      if(context&&context!==data.context){clear();return;}context=data.context;
      content.append(node('p',data.published===data.current?'This saved draft is published.':'This saved draft is not the published version.'));
      const samples=node('fieldset');samples.append(node('legend','Review a sample without executing the workflow'));
      const sampleName=field(samples,'Sample name','Reviewed sample'), inputs={};
      for(const key of data.params){
        const spec=(data.parameter_schema.fields||{})[key]||{},label=spec.label||key;
        if(spec.type==='enum'){
          const wrapper=node('label',label),select=node('select');select.className='input';select.append(node('option','Choose an option'));
          for(const choice of spec.choices||[]){const option=node('option',choice);option.value=choice;select.append(option);}select.value=spec.default||'';wrapper.append(select);samples.append(wrapper);inputs[key]=select;
        }else inputs[key]=field(samples,label,spec.default||'',spec.type==='date'?'date':spec.type==='number'?'number':spec.type==='email'?'email':'text');
        inputs[key].required=spec.required===true;if(spec.help)samples.append(node('p',spec.help));
      }
      function conditions(){
        for(const [key,input] of Object.entries(inputs)){
          const when=((data.parameter_schema.fields||{})[key]||{}).show_when;
          const hidden=!!when&&inputs[when.field]&&inputs[when.field].value!==when.value;
          input.disabled=hidden;input.closest('label').hidden=hidden;
        }
      }
      samples.addEventListener('change',conditions);samples.addEventListener('input',conditions);conditions();
      const expected=field(samples,'Expected phrase in the rendered instructions (optional)'), output=node('pre');output.style.whiteSpace='pre-wrap';
      const check=control('Check reviewed sample',()=>{
        if(!Object.values(inputs).every(input=>input.reportValidity()))return;
        mutate(check,'/fixtures',{expected_version:data.current,inputs:Object.fromEntries(Object.entries(inputs).map(([key,value])=>[key,value.value])),label:sampleName.value,expected_contains:expected.value},result=>{
          output.textContent=result.title+'\n\n'+result.body;status.textContent=result.passed?'Sample inputs passed. Review the instructions below. No work was executed.':'The expected phrase was not present. No work was executed.';
        });
      });samples.append(check,output);content.append(samples);
      const publication=node('fieldset');publication.append(node('legend','Publish for new schedules'),node('p',data.effect));
      if(data.schedules.length){const list=node('ul');for(const schedule of data.schedules)list.append(node('li',schedule.title+' — '+schedule.state+' — keeps its current version'));publication.append(list);}
      else publication.append(node('p','No schedules refer to this workflow.'));
      const label=field(publication,'Publication name','Reviewed version '+data.generation);
      const publish=control('Review publication',async()=>{
        if(!await mvConfirm('Publish this checked draft for new schedules? '+data.effect,{okText:'Publish version'}))return;
        mutate(publish,'/publish',{expected_version:data.current,impact_version:data.impact_version,label:label.value,confirm:true},()=>{status.textContent='Published. Existing schedules retain their versions.';});
      });publish.disabled=!data.can_publish;if(!data.can_publish)publication.append(node('p','An administrator publishes reviewed versions.'));publication.append(publish);content.append(publication);
      content.append(node('h3','Version history and comparison with the current draft'));
      for(const version of [...data.versions].reverse()){
        const box=node('details');box.append(node('summary',version.label+(version.current?' · current draft':'')+(version.published?' · published':'')));
        const diff=node('pre');diff.style.whiteSpace='pre-wrap';
        if(!version.diff.length)diff.textContent='No content changes.';
        else for(const line of version.diff){const span=node(line.startsWith('+')?'ins':line.startsWith('-')?'del':'span',line+'\n');diff.append(span);}box.append(diff);
        const copy=field(box,'Name for a new copy','');
        const clone=control('Clone as new draft',()=>mutate(clone,'/restore',{version:version.version,expected_generation:data.generation,new_name:copy.value},result=>{location.assign('/workflow-builder?edit='+encodeURIComponent(result.name));}));
        const restore=control('Restore as draft',async()=>{
          if(!await mvConfirm('Restore this content as a new draft? The published version and existing schedules stay pinned.',{okText:'Restore draft'}))return;
          mutate(restore,'/restore',{version:version.version,expected_generation:data.generation},result=>location.assign('/workflow-builder?edit='+encodeURIComponent(result.name)));
        });restore.disabled=version.current;box.append(clone,restore);content.append(box);
      }
      status.textContent='Version review loaded. '+data.fixtures.length+' recent sample checks recorded. Rendering checks do not qualify live execution.';
    }catch(error){if(attempt===serial)status.textContent=error.message;}
  }
  document.getElementById('workflow-versions-refresh').addEventListener('click',refresh);
  document.addEventListener('lightwork:workflow-saved',event=>{name=event.detail.name;refresh();});
  refresh();
})();
