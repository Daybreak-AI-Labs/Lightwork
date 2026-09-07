(function(){
  "use strict";
  const root=document.getElementById("wm-root");if(!root||!root.dataset.tenant)return;
  const el=id=>document.getElementById("wm-"+id),base="/api/v1/admin/tenants/"+encodeURIComponent(root.dataset.tenant)+"/roles";let busy=false;
  function node(tag,text){const item=document.createElement(tag);item.textContent=text;return item;}
  async function api(path,options={}){
    const response=await fetch(base+path,{...options,credentials:"same-origin",headers:{"Content-Type":"application/json","X-Lightwork-Expected-Tenant":root.dataset.tenant}});
    if(response.status===204)return null;
    let data;try{data=await response.json();}catch(_){throw new Error("The workspace member service returned an unreadable response.");}
    if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"Workspace membership could not be changed.");return data;
  }
  async function load(){
    const members=await api("");el("list").replaceChildren();
    for(const [principal,role] of Object.entries(members)){
      const li=node("li",principal+" · "+role+" "),edit=node("button","Change workspace role"),remove=node("button","Remove workspace assignment");edit.type=remove.type="button";edit.className=remove.className="btn";
      edit.addEventListener("click",()=>{el("principal").value=principal;el("role").value=role;el("role").focus();});
      remove.addEventListener("click",()=>action(async()=>{if(!confirm("Remove "+principal+" from this workspace roster? Project review access ends, but a broader global role may become effective."))return;await api("/"+encodeURIComponent(principal),{method:"DELETE"});await load();}));
      li.append(edit,remove);el("list").append(li);
    }
    if(!Object.keys(members).length)el("list").append(node("li","No workspace-specific member assignments yet."));
    el("status").textContent="Current workspace membership loaded.";
  }
  async function action(work){if(busy)return;busy=true;const controls=Array.from(root.querySelectorAll("button,input,select"));controls.forEach(value=>value.disabled=true);try{await work();}catch(error){el("status").textContent=error.message;}finally{busy=false;controls.forEach(value=>value.disabled=false);}}
  el("refresh").addEventListener("click",()=>action(load));
  el("form").addEventListener("submit",event=>{event.preventDefault();action(async()=>{
    const principal=el("principal").value.trim(),role=el("role").value;
    if(!principal.startsWith("user:")||principal.length<=5)throw new Error("Choose a named colleague's exact user: sign-in identity.");
    if(!confirm("Assign "+principal+" the "+role+" role in workspace "+root.dataset.tenant+"?"))return;
    await api("/"+encodeURIComponent(principal),{method:"PUT",body:JSON.stringify({role})});await load();
  });});
})();
