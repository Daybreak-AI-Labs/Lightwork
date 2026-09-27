(function(){
  "use strict";
  // Users > Workspace members: who holds a role inside one workspace.
  // People are chosen from a list and shown by name; the exact sign-in
  // identity only ever travels in the request, never on screen.
  const root=document.getElementById("wm-root");if(!root||!root.dataset.tenant)return;
  const el=id=>document.getElementById("wm-"+id),picker=el("tenant");let busy=false,denied=false,labels={},roleNames={};
  try{labels=JSON.parse(root.dataset.labels||"{}")||{};}catch(_){labels={};}
  try{roleNames=JSON.parse(root.dataset.roleLabels||"{}")||{};}catch(_){roleNames={};}
  // The workspace being edited comes from the picker when the page offers one;
  // the stale-tab header stays the workspace the page was RENDERED under.
  const tenant=()=>picker&&picker.value?picker.value:root.dataset.tenant,base=()=>"/api/v1/admin/tenants/"+encodeURIComponent(tenant())+"/roles";
  const expected=()=>root.dataset.expected!==undefined?root.dataset.expected:tenant();
  const label=principal=>labels[principal]||"A colleague";
  const roleLabel=role=>roleNames[role]||(window.mvHuman?window.mvHuman(role):String(role));
  const workspaceName=()=>{if(picker&&picker.selectedIndex>=0&&picker.options[picker.selectedIndex])return picker.options[picker.selectedIndex].textContent.trim();return root.dataset.tenantName||"this workspace";};
  const ask=(message,okText)=>window.mvConfirm?window.mvConfirm(message,{okText}):Promise.resolve(false);
  const access=window.LightworkAccessState.create(()=>tenant()+":"+expected(),lock);
  function node(tag,text,cls){const item=document.createElement(tag);item.textContent=text;if(cls)item.className=cls;return item;}
  function lock(){denied=true;el("list").replaceChildren();el("principal").value="";el("role").selectedIndex=0;root.querySelectorAll("button,input,select").forEach(control=>control.disabled=true);}
  function problem(response,action){return window.mvErrorText?window.mvErrorText(response,action):"Could not "+action+". Please try again.";}
  async function api(path,options={},action="load the workspace members"){
    const token=access.capture();
    const headers={"Content-Type":"application/json"},exp=expected();if(exp)headers["X-Lightwork-Expected-Tenant"]=exp;
    const response=await fetch(base()+path,{...options,credentials:"same-origin",headers});
    access.assertCurrent(token);
    if(["session","scope"].includes(access.observe(response,null,{forbiddenScope:true}).kind)){throw new Error("Your sign-in or administrator access changed. Sign in again and reload Users.");}
    if(response.status===204)return null;
    let data;try{data=await response.json();}catch(_){throw new Error(problem(response.ok?{status:500}:response,action));}
    access.assertCurrent(token);
    if(access.observe(response,data,{forbiddenScope:true,contextMessage:/active workspace changed/i}).kind==="context")throw new Error("The workspace changed. Reload Users before continuing.");
    if(!response.ok)throw new Error(problem(response,action));return data;
  }
  async function load(){
    const members=await api("");el("list").replaceChildren();
    for(const [principal,role] of Object.entries(members)){
      const li=node("li","",""),name=node("span",label(principal),"wm-name"),badge=node("span",roleLabel(role),"badge");
      const edit=node("button","Change role","btn"),remove=node("button","Remove","btn btn--danger");edit.type=remove.type="button";
      edit.setAttribute("aria-label","Change "+label(principal)+"'s role");remove.setAttribute("aria-label","Remove "+label(principal)+" from "+workspaceName());
      edit.addEventListener("click",()=>{if(Array.from(el("principal").options||[]).some(o=>o.value===principal))el("principal").value=principal;el("role").value=role;el("role").focus();});
      remove.addEventListener("click",()=>action(async()=>{if(!await ask("Remove "+label(principal)+" from "+workspaceName()+"? They go back to their normal role. To limit them instead, give them the Viewer role.","Remove"))return;await api("/"+encodeURIComponent(principal),{method:"DELETE"},"remove this person");await load();}));
      li.append(name," ",badge," ",edit," ",remove);el("list").append(li);
    }
    if(!Object.keys(members).length)el("list").append(node("li","No one has a role just for this workspace yet. Choose a colleague below to add one.","muted"));
    el("status").textContent="Showing members of "+workspaceName()+".";
  }
  async function action(work){if(busy||denied)return;busy=true;const controls=Array.from(root.querySelectorAll("button,input,select"));controls.forEach(value=>value.disabled=true);try{await work();}catch(error){el("status").textContent=error&&error.message?error.message:problem(error,"update the workspace members");}finally{busy=false;if(!denied)controls.forEach(value=>value.disabled=false);}}
  el("refresh").addEventListener("click",()=>action(load));
  if(picker)picker.addEventListener("change",()=>{access.advance();root.dataset.tenant=picker.value;el("list").replaceChildren();el("status").textContent="Loading members of "+workspaceName()+"…";action(load);});
  el("form").addEventListener("submit",event=>{event.preventDefault();action(async()=>{
    const principal=el("principal").value.trim(),role=el("role").value;
    if(!principal.startsWith("user:")||principal.length<=5)throw new Error("Choose a colleague from the list.");
    if(!await ask("Give "+label(principal)+" the "+roleLabel(role)+" role in "+workspaceName()+"?","Save"))return;
    await api("/"+encodeURIComponent(principal),{method:"PUT",body:JSON.stringify({role})},"save this workspace member");await load();
  });});
  if(root.dataset.autoload==="true")action(load);
})();
