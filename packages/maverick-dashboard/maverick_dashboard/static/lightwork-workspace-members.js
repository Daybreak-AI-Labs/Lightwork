(function(){
  "use strict";
  const root=document.getElementById("wm-root");if(!root||!root.dataset.tenant)return;
  const el=id=>document.getElementById("wm-"+id),picker=el("tenant");let busy=false,denied=false,labels={};
  try{labels=JSON.parse(root.dataset.labels||"{}")||{};}catch(_){labels={};}
  // The workspace being edited comes from the picker when the page offers one;
  // the stale-tab header stays the workspace the page was RENDERED under.
  const tenant=()=>picker&&picker.value?picker.value:root.dataset.tenant,base=()=>"/api/v1/admin/tenants/"+encodeURIComponent(tenant())+"/roles";
  const expected=()=>root.dataset.expected!==undefined?root.dataset.expected:tenant(),label=principal=>labels[principal]?labels[principal]+" ("+principal+")":principal;
  function node(tag,text){const item=document.createElement(tag);item.textContent=text;return item;}
  function lock(){denied=true;el("list").replaceChildren();el("principal").value="";el("role").selectedIndex=0;root.querySelectorAll("button,input,select").forEach(control=>control.disabled=true);}
  async function api(path,options={}){
    const headers={"Content-Type":"application/json"},exp=expected();if(exp)headers["X-Lightwork-Expected-Tenant"]=exp;
    const response=await fetch(base()+path,{...options,credentials:"same-origin",headers});
    if([401,403].includes(response.status)){lock();throw new Error("Your sign-in or administrator access changed. Sign in again and reload Users.");}
    if(response.status===204)return null;
    let data;try{data=await response.json();}catch(_){throw new Error("The workspace member service returned an unreadable response.");}
    if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"Workspace membership could not be changed.");return data;
  }
  async function load(){
    const members=await api("");el("list").replaceChildren();
    for(const [principal,role] of Object.entries(members)){
      const li=node("li",label(principal)+" · "+role+" "),edit=node("button","Change workspace role"),remove=node("button","Remove workspace assignment");edit.type=remove.type="button";edit.className=remove.className="btn";
      edit.addEventListener("click",()=>{el("principal").value=principal;el("role").value=role;el("role").focus();});
      remove.addEventListener("click",()=>action(async()=>{if(!confirm("Remove "+label(principal)+" from workspace "+tenant()+"? Project review access ends, but a broader global role may become effective."))return;await api("/"+encodeURIComponent(principal),{method:"DELETE"});await load();}));
      li.append(edit,remove);el("list").append(li);
    }
    if(!Object.keys(members).length)el("list").append(node("li","No workspace-specific member assignments yet."));
    el("status").textContent="Members of workspace "+tenant()+" loaded.";
  }
  async function action(work){if(busy||denied)return;busy=true;const controls=Array.from(root.querySelectorAll("button,input,select"));controls.forEach(value=>value.disabled=true);try{await work();}catch(error){el("status").textContent=error.message;}finally{busy=false;if(!denied)controls.forEach(value=>value.disabled=false);}}
  el("refresh").addEventListener("click",()=>action(load));
  if(picker)picker.addEventListener("change",()=>{root.dataset.tenant=picker.value;el("list").replaceChildren();el("status").textContent="Workspace "+picker.value+" chosen. Select Refresh members to load its roster.";});
  el("form").addEventListener("submit",event=>{event.preventDefault();action(async()=>{
    const principal=el("principal").value.trim(),role=el("role").value;
    if(!principal.startsWith("user:")||principal.length<=5)throw new Error("Choose a named colleague's exact user: sign-in identity.");
    if(!confirm("Assign "+label(principal)+" the "+role+" role in workspace "+tenant()+"?"))return;
    await api("/"+encodeURIComponent(principal),{method:"PUT",body:JSON.stringify({role})});await load();
  });});
})();
