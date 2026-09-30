(function () {
  "use strict";
  const root=document.getElementById("pr-root"); if(!root)return;
  const el=id=>document.getElementById("pr-"+id),base="/api/v1/project-reviews";
  let model=null,setup=null,preview=null,packet=null,busy=false,offset=0,expired=false,packetUnavailable=false;
  let savedRequestNote="",savedResponseNote="",resubmission=null;
  const responseDirty=()=>el("response-note").value!==savedResponseNote;
  const hasDraft=()=>responseDirty()||el("note").value!==savedRequestNote||!!el("change-summary").value;
  window.addEventListener("beforeunload",event=>{if(!expired&&hasDraft()){event.preventDefault();event.returnValue="";}});
  const refs={};
  function reference(name){if(!refs[name])refs[name]=crypto.randomUUID();return refs[name];}
  function resetReference(name){delete refs[name];}
  // People are stored by sign-in name ("user:dana"); show the name part.
  function who(value){return String(value==null?"":value).replace(/^[a-z_]+:/i,"")||"someone";}
  function words(value){return String(value==null?"":value).replace(/_/g," ");}
  // Every destructive or sharing step still asks first (shared dialog).
  async function ask(message,okText){return typeof window.mvConfirm==="function"?!!(await window.mvConfirm(message,{okText})):false;}
  function failure(status,action){
    if(typeof window.mvErrorText==="function")return window.mvErrorText({status},action);
    const what="Could not "+action+". ";
    if(status===404)return what+"That item no longer exists or you no longer have access. Refresh the page to see the latest.";
    if(status===409)return what+"This was changed since you opened it. Refresh the page and try again.";
    if(status===400||status===422)return what+"Some of the details need fixing. Check what you entered and try again.";
    if(status===429)return what+"Too many requests right now. Wait a moment and try again.";
    if(status>=500)return what+"Something went wrong on our side. Please try again in a minute.";
    return what+"Please try again.";
  }
  function node(tag,text){const value=document.createElement(tag);value.textContent=text;return value;}
  function choices(select,rows,placeholder){select.replaceChildren(node("option",placeholder));select.firstChild.value="";rows.forEach(row=>{const option=node("option",row.label);option.value=String(row.value);select.append(option);});}
  async function api(path,options={}){
    const response=await fetch(base+path,{...options,credentials:"same-origin",cache:"no-store",headers:{"Content-Type":"application/json","X-Lightwork-Review-Context":root.dataset.context}});
    if([401,403].includes(response.status)){expired=true;invalidateView();throw new Error("Your sign-in or access changed. Sign in again and reload project reviews.");}
    const raw=await response.text();if(new TextEncoder().encode(raw).byteLength>2*1024*1024)throw new Error("There is too much to show at once. Search for a task or project to narrow the list.");
    if(!response.ok){const error=new Error(failure(response.status,options.method&&options.method!=="GET"?"save that":"load the review"));error.status=response.status;throw error;}
    let data;try{data=JSON.parse(raw);}catch(_){throw new Error("Could not read the reply from Lightwork. Refresh the page and try again.");}
    return data;
  }
  function permissions(){
    if(expired){root.querySelectorAll("button,input,textarea,select").forEach(item=>item.disabled=true);return;}
    el("previous").disabled=busy||!offset;el("next").disabled=busy||!model?.has_more;
    el("response-button").disabled=busy||packetUnavailable||!packet?.can_respond;
    el("send-button").disabled=busy||!preview||!setup;
    const canDecide=packet&&packet.reviewer===model?.principal&&packet.status==="pending"&&!packet.stale;
    const canCancel=packet&&packet.owner===model?.principal&&packet.status==="pending";
    for(const option of el("action").options)option.disabled=option.value==="cancel"?!canCancel:option.value!=="comment"&&!canDecide;
    if(el("action").selectedOptions[0]?.disabled)el("action").value="comment";
  }
  async function action(work){
    if(busy||expired)return;busy=true;
    const controls=Array.from(root.querySelectorAll("button,input,textarea,select"));const previous=controls.map(item=>item.disabled);controls.forEach(item=>item.disabled=true);
    try{await work();}catch(error){el("status").textContent=error.message||"The action could not be completed. Your input has been kept.";}
    finally{controls.forEach((item,index)=>item.disabled=previous[index]);busy=false;permissions();}
  }
  function renderInbox(){
    if(window.LightworkI18n){window.LightworkI18n.set(el("page"),model.total?"Showing {start}–{end} of {total}":"No matching reviews",{start:window.LightworkI18n.number(model.offset+1),end:window.LightworkI18n.number(Math.min(model.offset+model.limit,model.total)),total:window.LightworkI18n.number(model.total)});}else el("page").textContent=model.total?"Showing "+(model.offset+1)+"–"+Math.min(model.offset+model.limit,model.total)+" of "+model.total:"No matching reviews";
    if(window.LightworkI18n){el("counts").textContent=window.LightworkI18n.plural("{count} review","{count} reviews",model.counts.total)+" · "+window.LightworkI18n.text("{waiting} waiting · {overdue} overdue",{waiting:window.LightworkI18n.number(model.counts.pending),overdue:window.LightworkI18n.number(model.counts.overdue)});el("counts").lang=window.LightworkI18n.languageFor("{count} reviews");}else el("counts").textContent=model.counts.total+(model.counts.total===1?" review":" reviews")+" · "+model.counts.pending+" waiting · "+model.counts.overdue+" overdue";
    el("inbox").replaceChildren();if(!model.items.length)el("inbox").append(node("p","No reviews are shared with you right now. Project owners can pick a result and choose a reviewer below."));
    for(const row of model.items){const item=node("p",""),button=node("button",row.title+" · "+row.status.replace(/_/g," ")+(row.stale?" · source changed":"")+(row.overdue?" · overdue":""));button.type="button";button.className="btn";button.addEventListener("click",()=>action(()=>openPacket(row.id)));item.append(button);el("inbox").append(item);}
  }
  async function refresh(){
    let next;try{next=await api("?"+new URLSearchParams({offset:String(offset),limit:"50",q:el("search").value}));}catch(error){if([401,403,404,409].includes(error.status))invalidateView();throw error;}
    if(next.context!==root.dataset.context){expired=true;invalidateView();throw new Error("Your account or workspace changed. Reload this page.");}
    model=next;renderInbox();const selected=el("project").value;choices(el("project"),model.projects.map(row=>({value:row.id,label:row.name})),"Choose your project");
    el("project").value=model.projects.some(row=>String(row.id)===selected)?selected:"";el("owner").hidden=!model.projects.length;
    el("status").textContent="Review list is up to date. Only reviews you can open are shown.";
  }
  function invalidateView(){model=null;packet=null;setup=null;preview=null;resubmission=null;el("change-summary").value="";el("resubmit").hidden=true;el("review-provenance").replaceChildren();el("outcome-timeline").replaceChildren();
    for(const id of ["inbox","members","counts","page","result","preview-text","preview-title","packet-title","packet-summary","packet-note","stale","history","owner-task","project","member","reviewer","goal"])el(id).replaceChildren();
    el("response-note").value="";el("note").value="";el("due").value="";savedRequestNote="";savedResponseNote="";
    el("packet").hidden=true;el("owner").hidden=true;el("setup").hidden=true;el("preview-panel").hidden=true;Object.keys(refs).forEach(key=>delete refs[key]);permissions();}
  function clearPreview(){preview=null;el("preview-panel").hidden=true;el("preview-text").textContent="";resetReference("send");permissions();}
  async function loadProject(){
    setup=null;clearPreview();el("setup").hidden=true;if(!el("project").value)return;
    setup=await api("/projects/"+encodeURIComponent(el("project").value));
    choices(el("member"),setup.eligible_members.map(value=>({value,label:who(value)})),"Choose a colleague");
    choices(el("reviewer"),setup.team.members.filter(value=>setup.eligible_members.includes(value)).map(value=>({value,label:who(value)})),"Choose a reviewer on this project");
    choices(el("goal"),setup.goals.map(row=>({value:row.id,label:row.title+" · "+words(row.status)})),"Choose a result");
    el("members").replaceChildren();if(!setup.team.members.length)el("members").append(node("li","No reviewers on this project yet. Add a colleague above."));
    for(const member of setup.team.members){const item=node("li",who(member)+" "),button=node("button","Remove reviewer");button.type="button";button.className="btn btn--ghost btn--sm";button.setAttribute("aria-label","Remove "+who(member)+" as a reviewer");button.addEventListener("click",()=>action(async()=>{if(!(await ask("Remove "+who(member)+" from this project's reviewers? They lose access to its reviews straight away.","Remove reviewer")))return;await saveMember(member,false);await loadProject();await refresh();}));item.append(button);el("members").append(item);}
    resetReference("membership");el("setup").hidden=false;el("status").textContent=setup.eligible_members.length?"Choose a task and preview the result before sharing it.":"No colleagues can review yet. An administrator can add people who are allowed to run tasks on the People page.";
  }
  async function saveMember(member,allowed){
    if(!member)throw new Error("Choose a colleague first.");
    await api("/projects/"+el("project").value+"/members",{method:"PUT",body:JSON.stringify({member,allowed,expected_revision:setup.team.revision,request_id:reference("membership")})});resetReference("membership");
  }
  function renderPacket(){
    packetUnavailable=false;
    el("packet").hidden=false;el("packet-title").textContent=packet.snapshot.title;
    el("packet-summary").textContent=packet.project_name+" · "+words(packet.status)+" · reviewer "+who(packet.reviewer)+" · due "+(window.LightworkI18n ? window.LightworkI18n.formatDate(packet.due_at) : new Date(packet.due_at*1000).toLocaleString());
    el("stale").textContent=packet.stale?"The source task changed after this result was captured. Ask the owner to share it again before making a decision.":"You are reviewing the captured result shown below.";
    el("packet-note").textContent=packet.note;el("result").textContent=packet.snapshot.result;el("history").replaceChildren();
    packet.history.forEach(row=>el("history").append(node("li",who(row.actor)+" · "+words(row.action)+" · "+(window.LightworkI18n ? window.LightworkI18n.formatDate(row.at) : new Date(row.at*1000).toLocaleString())+" — "+row.note)));
    el("review-provenance").replaceChildren();
    if(packet.previous_packet_id){
      el("review-provenance").append(node("span","Changed since the previous captured result: "+packet.change_summary+" "));
      const previous=node("button","Open previous review");previous.type="button";previous.className="btn btn--ghost";
      previous.addEventListener("click",()=>action(()=>openPacket(packet.previous_packet_id)));el("review-provenance").append(previous);
    }
    el("outcome-timeline").replaceChildren();
    const decision=packet.status==="approved"?"Review accepted for this captured version":packet.status==="changes_requested"?"Changes requested from the owner":packet.status==="cancelled"?"Review withdrawn":"Review decision pending";
    el("outcome-timeline").append(node("li",decision+(packet.stale?"; the source has changed since capture":"")),node("li","Next owner: "+who(packet.status==="pending"?packet.reviewer:packet.owner)),node("li","Task execution and deliverable sign-off: separate decisions; this packet grants neither."),node("li","External delivery and verification: not established by this review packet."));
    el("owner-task").replaceChildren();if(packet.owner===model.principal){
      const link=node("a","Open your task to decide its next step");link.href="/chat/goal/"+encodeURIComponent(packet.snapshot.goal_id);el("owner-task").append(link);
      if(packet.status==="changes_requested"||packet.stale){
        const revise=node("button","Address this review in a private draft");revise.type="button";revise.className="btn";
        revise.addEventListener("click",()=>action(async()=>{
          const saved=await api("/packets/"+encodeURIComponent(packet.id)+"/revision-draft",{method:"POST",body:JSON.stringify({expected_revision:packet.revision,source_version:packet.source_version,request_id:reference("revision-"+packet.id)})});
          const draft=node("a","Open prepared revision and review its spending limit");draft.href=saved.href;el("owner-task").append(node("p","Private revision prepared. Nothing has run; no prior approval applies."),draft);el("status").textContent="Open the revision draft when ready. Starting it is a separate action.";
        }));el("owner-task").append(revise);
      }
      if(packet.status!=="pending"){
        const again=node("button","Resubmit a changed result for fresh review");again.type="button";again.className="btn";
        again.addEventListener("click",()=>action(async()=>{const prior=packet;el("project").value=String(prior.project_id);await loadProject();resubmission={id:prior.id,project_id:prior.project_id};el("previous-label").textContent="Previous review: "+prior.snapshot.title+" ("+prior.id+"). Its decision will not carry forward.";el("resubmit").hidden=false;el("goal").focus();el("status").textContent="Choose and preview a changed result, then explain the changes and send a fresh review packet.";}));el("owner-task").append(again);
      }
    }
    permissions();
  }
  async function openPacket(id){
    const previous=packet;
    if(previous&&previous.id!==id&&responseDirty()&&!(await ask("Discard your unfinished review comment and open another review?","Discard and open")))return;
    // Clear prior content before reauthorization so revoked data does not
    // remain presented as the newly selected packet after a failed read.
    packetUnavailable=true;el("packet").hidden=true;el("result").textContent="";
    packet=await api("/packets/"+encodeURIComponent(id));renderPacket();
    if(!previous||previous.id!==packet.id){el("response-note").value="";savedResponseNote="";el("action").value="comment";resetReference("response");}
    else if(previous.revision!==packet.revision||previous.source_version!==packet.source_version){el("action").value="comment";resetReference("response");el("status").textContent="This review changed. Your unfinished comment is kept; check it against the current result before saving. The response is reset to Add comment.";}
    el("packet-title").focus();
  }
  el("refresh").addEventListener("click",()=>action(async()=>{await refresh();if(packet)await openPacket(packet.id);}));
  el("search-button").addEventListener("click",()=>action(async()=>{offset=0;await refresh();}));
  el("previous").addEventListener("click",()=>action(async()=>{offset=Math.max(0,offset-50);await refresh();}));
  el("next").addEventListener("click",()=>action(async()=>{if(model?.has_more){offset+=50;await refresh();}}));
  el("project").addEventListener("change",()=>action(async()=>{resubmission=null;el("resubmit").hidden=true;await loadProject();}));
  el("unlink").addEventListener("click",()=>{resubmission=null;el("change-summary").value="";el("resubmit").hidden=true;resetReference("send");});el("goal").addEventListener("change",clearPreview);
  el("member").addEventListener("change",()=>resetReference("membership"));
  el("grant").addEventListener("click",()=>action(async()=>{await saveMember(el("member").value,true);await loadProject();}));
  el("preview").addEventListener("click",()=>action(async()=>{
    clearPreview();if(!el("goal").value)throw new Error("Choose a stopped task with a result.");
    preview=await api("/projects/"+el("project").value+"/goals/"+el("goal").value+"/preview");el("preview-title").textContent=preview.snapshot.title;el("preview-text").textContent=preview.snapshot.result;el("preview-panel").hidden=false;el("status").textContent="Review the captured content, select a reviewer and due date, then send.";
  }));
  el("send").addEventListener("input",()=>resetReference("send"));el("respond").addEventListener("input",()=>resetReference("response"));
  el("send").addEventListener("submit",event=>{event.preventDefault();action(async()=>{
    if(!setup||!preview)throw new Error("Preview the selected result before sending.");
    const payload={goal_id:preview.snapshot.goal_id,reviewer:el("reviewer").value,note:el("note").value,due_at:new Date(el("due").value).getTime()/1000,source_version:preview.source_version,expected_team_revision:setup.team.revision,request_id:reference("send")};
    if(resubmission){if(Number(el("project").value)!==resubmission.project_id)throw new Error("Choose the original project for this resubmission.");payload.previous_packet_id=resubmission.id;payload.change_summary=el("change-summary").value;if(!payload.change_summary.trim())throw new Error("Explain what changed before resubmitting.");}
    if(!payload.reviewer||!payload.note.trim()||!Number.isFinite(payload.due_at))throw new Error("Choose a reviewer, future due date and review note.");
    if(!(await ask("Share exactly the captured result shown above with "+who(payload.reviewer)+"?","Share for review")))return;
    const saved=await api("/projects/"+el("project").value+"/packets",{method:"POST",body:JSON.stringify(payload)});savedRequestNote=el("note").value;resubmission=null;el("change-summary").value="";el("resubmit").hidden=true;resetReference("send");await refresh();packet=saved;renderPacket();el("status").textContent="Sent for review. No task was run or signed off.";
  });});
  el("respond").addEventListener("submit",event=>{event.preventDefault();action(async()=>{
    if(!packet||packetUnavailable)throw new Error("Open a review first.");const kind=el("action").value,note=el("response-note").value;
    if(!note.trim())throw new Error("Add a review comment before saving.");
    if(kind!=="comment"&&!(await ask("Save this "+words(kind)+" decision for the captured result? The live task will remain unchanged.","Save decision")))return;
    const saved=await api("/packets/"+encodeURIComponent(packet.id)+"/responses",{method:"POST",body:JSON.stringify({action:kind,note,expected_revision:packet.revision,source_version:packet.source_version,request_id:reference("response")})});
    packet=saved;resetReference("response");renderPacket();el("response-note").value="";savedResponseNote="";await refresh();el("status").textContent="Review response saved. The owner decides the live task's next step.";
  });});
  action(async()=>{await refresh();const parameters=new URLSearchParams(location.hash.slice(1)),initial=parameters.get("project");if(initial&&/^\d{1,16}$/.test(initial)&&model.projects.some(row=>String(row.id)===initial)){el("project").value=initial;await loadProject();}const selected=parameters.get("packet");if(selected&&/^PRP-[0-9a-f]{32}$/.test(selected))await openPacket(selected);});
})();
