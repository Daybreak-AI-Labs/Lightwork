(function () {
  "use strict";
  const root=document.getElementById("pr-root"); if(!root)return;
  const el=id=>document.getElementById("pr-"+id),base="/api/v1/project-reviews";
  let model=null,setup=null,preview=null,packet=null,busy=false,offset=0;
  const refs={};
  function reference(name){if(!refs[name])refs[name]=crypto.randomUUID();return refs[name];}
  function resetReference(name){delete refs[name];}
  function node(tag,text){const value=document.createElement(tag);value.textContent=text;return value;}
  function choices(select,rows,placeholder){select.replaceChildren(node("option",placeholder));select.firstChild.value="";rows.forEach(row=>{const option=node("option",row.label);option.value=String(row.value);select.append(option);});}
  async function api(path,options={}){
    const response=await fetch(base+path,{...options,credentials:"same-origin",cache:"no-store",headers:{"Content-Type":"application/json","X-Lightwork-Review-Context":root.dataset.context}});
    const raw=await response.text();if(new TextEncoder().encode(raw).byteLength>2*1024*1024)throw new Error("Review response is too large. Narrow the workspace inventory.");
    let data;try{data=JSON.parse(raw);}catch(_){throw new Error("The review service returned an unreadable response.");}
    if(!response.ok){const error=new Error(typeof data.detail==="string"?data.detail:"The review could not be saved. Check the required fields and reload if the version changed.");error.status=response.status;throw error;}
    return data;
  }
  function permissions(){
    el("previous").disabled=busy||!offset;el("next").disabled=busy||!model?.has_more;
    el("response-button").disabled=busy||!packet?.can_respond;
    el("send-button").disabled=busy||!preview||!setup;
    const canDecide=packet&&packet.reviewer===model?.principal&&packet.status==="pending"&&!packet.stale;
    const canCancel=packet&&packet.owner===model?.principal&&packet.status==="pending";
    for(const option of el("action").options)option.disabled=option.value==="cancel"?!canCancel:option.value!=="comment"&&!canDecide;
    if(el("action").selectedOptions[0]?.disabled)el("action").value="comment";
  }
  async function action(work){
    if(busy)return;busy=true;
    const controls=Array.from(root.querySelectorAll("button,input,textarea,select"));const previous=controls.map(item=>item.disabled);controls.forEach(item=>item.disabled=true);
    try{await work();}catch(error){el("status").textContent=error.message||"The action could not be completed. Your input has been kept.";}
    finally{controls.forEach((item,index)=>item.disabled=previous[index]);busy=false;permissions();}
  }
  function renderInbox(){
    el("page").textContent=model.total?"Showing "+(model.offset+1)+"–"+Math.min(model.offset+model.limit,model.total)+" of "+model.total:"No matching reviews";
    el("counts").textContent=model.counts.total+" packets · "+model.counts.pending+" pending · "+model.counts.overdue+" overdue";
    el("inbox").replaceChildren();if(!model.items.length)el("inbox").append(node("p","No review packets are currently shared with you. Project owners can select a result and assign a reviewer below."));
    for(const row of model.items){const item=node("p",""),button=node("button",row.title+" · "+row.status.replace(/_/g," ")+(row.stale?" · source changed":"")+(row.overdue?" · overdue":""));button.type="button";button.className="btn";button.addEventListener("click",()=>action(()=>openPacket(row.id)));item.append(button);el("inbox").append(item);}
  }
  async function refresh(){
    let next;try{next=await api("?"+new URLSearchParams({offset:String(offset),limit:"50",q:el("search").value}));}catch(error){if([401,403,404,409].includes(error.status))invalidateView();throw error;}
    if(next.context!==root.dataset.context){invalidateView();throw new Error("Your account or workspace changed. Reload this page.");}
    model=next;renderInbox();const selected=el("project").value;choices(el("project"),model.projects.map(row=>({value:row.id,label:row.name})),"Choose your project");
    el("project").value=model.projects.some(row=>String(row.id)===selected)?selected:"";el("owner").hidden=!model.projects.length;
    el("status").textContent="Review list refreshed. Only packets you can currently access are shown.";
  }
  function invalidateView(){model=null;packet=null;setup=null;preview=null;el("inbox").replaceChildren();el("members").replaceChildren();el("counts").textContent="";el("page").textContent="";el("result").textContent="";el("preview-text").textContent="";el("response-note").value="";el("note").value="";el("packet").hidden=true;el("owner").hidden=true;el("setup").hidden=true;el("preview-panel").hidden=true;Object.keys(refs).forEach(key=>delete refs[key]);}
  function clearPreview(){preview=null;el("preview-panel").hidden=true;el("preview-text").textContent="";resetReference("send");permissions();}
  async function loadProject(){
    setup=null;clearPreview();el("setup").hidden=true;if(!el("project").value)return;
    setup=await api("/projects/"+encodeURIComponent(el("project").value));
    choices(el("member"),setup.eligible_members.map(value=>({value,label:value})),"Choose a current workspace member");
    choices(el("reviewer"),setup.team.members.filter(value=>setup.eligible_members.includes(value)).map(value=>({value,label:value})),"Choose an active project reviewer");
    choices(el("goal"),setup.goals.map(row=>({value:row.id,label:row.title+" · "+row.status})),"Choose a result");
    el("members").replaceChildren();if(!setup.team.members.length)el("members").append(node("li","No project reviewers yet. Grant access to a workspace colleague above."));
    for(const member of setup.team.members){const item=node("li",member+" "),button=node("button","Remove review access");button.type="button";button.className="btn";button.addEventListener("click",()=>action(async()=>{if(!window.confirm("Remove "+member+" from this project's review team? Their packet access ends immediately."))return;await saveMember(member,false);await loadProject();await refresh();}));item.append(button);el("members").append(item);}
    resetReference("membership");el("setup").hidden=false;el("status").textContent=setup.eligible_members.length?"Choose a task and preview the result before sharing it.":"No eligible workspace colleagues are available. An administrator can add a named workspace member with permission to operate.";
  }
  async function saveMember(member,allowed){
    if(!member)throw new Error("Choose a workspace colleague.");
    await api("/projects/"+el("project").value+"/members",{method:"PUT",body:JSON.stringify({member,allowed,expected_revision:setup.team.revision,request_id:reference("membership")})});resetReference("membership");
  }
  function renderPacket(){
    el("packet").hidden=false;el("packet-title").textContent=packet.snapshot.title;
    el("packet-summary").textContent=packet.project_name+" · "+packet.status.replace(/_/g," ")+" · reviewer "+packet.reviewer+" · due "+new Date(packet.due_at*1000).toLocaleString();
    el("stale").textContent=packet.stale?"The source task changed after this result was captured. Ask the owner for a fresh packet before making a decision.":"You are reviewing the captured result shown below.";
    el("packet-note").textContent=packet.note;el("result").textContent=packet.snapshot.result;el("history").replaceChildren();
    packet.history.forEach(row=>el("history").append(node("li",row.actor+" · "+row.action.replace(/_/g," ")+" · "+new Date(row.at*1000).toLocaleString()+" — "+row.note)));
    el("owner-task").replaceChildren();if(packet.owner===model.principal){const link=node("a","Open your task to decide its next step");link.href="/chat/goal/"+encodeURIComponent(packet.snapshot.goal_id);el("owner-task").append(link);}
    permissions();
  }
  async function openPacket(id){
    const previous=packet;
    // Clear prior content before reauthorization so revoked data does not
    // remain presented as the newly selected packet after a failed read.
    packet=null;el("packet").hidden=true;el("result").textContent="";
    packet=await api("/packets/"+encodeURIComponent(id));renderPacket();
    if(!previous||previous.id!==packet.id||previous.revision!==packet.revision||previous.source_version!==packet.source_version){el("response-note").value="";el("action").value="comment";resetReference("response");}
    el("packet-title").focus();
  }
  el("refresh").addEventListener("click",()=>action(async()=>{await refresh();if(packet)await openPacket(packet.id);}));
  el("search-button").addEventListener("click",()=>action(async()=>{offset=0;await refresh();}));
  el("previous").addEventListener("click",()=>action(async()=>{offset=Math.max(0,offset-50);await refresh();}));
  el("next").addEventListener("click",()=>action(async()=>{if(model?.has_more){offset+=50;await refresh();}}));
  el("project").addEventListener("change",()=>action(loadProject));el("goal").addEventListener("change",clearPreview);
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
    if(!payload.reviewer||!payload.note.trim()||!Number.isFinite(payload.due_at))throw new Error("Choose a reviewer, future due date and review note.");
    if(!window.confirm("Share exactly the captured result shown above with "+payload.reviewer+"?"))return;
    const saved=await api("/projects/"+el("project").value+"/packets",{method:"POST",body:JSON.stringify(payload)});resetReference("send");await refresh();packet=saved;renderPacket();el("status").textContent="Review packet sent. No task was run or signed off.";
  });});
  el("respond").addEventListener("submit",event=>{event.preventDefault();action(async()=>{
    if(!packet)throw new Error("Open a review packet first.");const kind=el("action").value,note=el("response-note").value;
    if(!note.trim())throw new Error("Add a review comment before saving.");
    if(kind!=="comment"&&!window.confirm("Save this "+kind.replace(/_/g," ")+" decision for the captured result? The live task will remain unchanged."))return;
    const saved=await api("/packets/"+encodeURIComponent(packet.id)+"/responses",{method:"POST",body:JSON.stringify({action:kind,note,expected_revision:packet.revision,source_version:packet.source_version,request_id:reference("response")})});
    packet=saved;resetReference("response");renderPacket();el("response-note").value="";await refresh();el("status").textContent="Review response saved. The owner decides the live task's next step.";
  });});
  action(async()=>{await refresh();const parameters=new URLSearchParams(location.hash.slice(1)),initial=parameters.get("project");if(initial&&/^\d{1,16}$/.test(initial)&&model.projects.some(row=>String(row.id)===initial)){el("project").value=initial;await loadProject();}const selected=parameters.get("packet");if(selected&&/^PRP-[0-9a-f]{32}$/.test(selected))await openPacket(selected);});
})();
