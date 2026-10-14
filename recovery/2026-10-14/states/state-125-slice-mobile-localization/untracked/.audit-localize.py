from pathlib import Path
import re

root=Path('packages/maverick-dashboard/maverick_dashboard')
p=root/'static/lightwork-task-file-recovery.js'
s=p.read_text(encoding='utf8')
s=re.sub(r"new Error\(('(?:[^'\\]|\\.)*')\)",r'problem(\1)',s)
s=s.replace("throw new Error(plain(data.detail,'File recovery was refused. Review the changes again.'));", "const detail=plain(data.detail,null);throw detail?new Error(detail):problem('File recovery was refused. Review the changes again.');")
s=s.replace('say(error.message);',"if(error.productSource)say(error.productSource);else user(el('status'),error.message||'');")
s=re.sub(r"el\('(operation|restore)'\)\.textContent=([^;]+);",r"product(el('\1'),\2);",s)
s=s.replace("{okText:'Turn off'}","{okText:'Turn off',productCopy:true}").replace("{okText:'Restore files'}","{okText:'Restore files',productCopy:true}")
p.write_text(s,encoding='utf8',newline='\n')

p=root/'static/lightwork-project-reviews.js'
s=p.read_text(encoding='utf8')
needle='  // People are stored by sign-in name'
helpers='''  // Translate only authored product copy. Stored content is always opaque.
  function product(value,source,values={}){if(window.LightworkI18n)window.LightworkI18n.set(value,source,values);else{value.textContent=source.replace(/\\{(\\w+)\\}/g,(_,key)=>String(values[key]??""));value.lang="en";value.dir="ltr";}return value;}
  function text(source,values={}){return window.LightworkI18n?window.LightworkI18n.text(source,values):source.replace(/\\{(\\w+)\\}/g,(_,key)=>String(values[key]??""));}
  function user(value,content){value.textContent=content;value.lang="und";value.dir="auto";value.dataset.userContent="";return value;}
  function userNode(tag,content){return user(document.createElement(tag),content);}
  function problem(source){return Object.assign(new Error(source),{productSource:source});}
  function status(source,values){product(el("status"),source,values);}
  function date(value){return window.LightworkI18n?window.LightworkI18n.formatDate(value):new Date(value*1000).toLocaleString();}
  const labels={pending:"Pending",approved:"Approved",changes_requested:"Changes requested",cancelled:"Cancelled",sent:"Sent",approve:"Approve",request_changes:"Request changes",cancel:"Cancel",comment:"Comment",done:"Done",failed:"Failed",stopped:"Stopped"};
  function labelNode(value){return labels[value]?node("span",labels[value]):userNode("span",words(value));}
'''
s=s.replace(needle,helpers+needle)
s=s.replace('||"someone"','||text("someone")')
s=s.replace('async function ask(message,okText){return typeof window.mvConfirm==="function"?!!(await window.mvConfirm(message,{okText})):false;}','async function ask(message,okText,values={}){return typeof window.mvConfirm==="function"?!!(await window.mvConfirm(message,{okText,productCopy:true,values})):false;}')
s=s.replace('function node(tag,text){const value=document.createElement(tag);value.textContent=text;return value;}','function node(tag,source,values){return product(document.createElement(tag),source,values);}')
s=s.replace('const option=node("option",row.label);','const option=userNode("option",row.label);')
s=re.sub(r'new Error\(("(?:[^"\\]|\\.)*")\)',r'problem(\1)',s)
s=s.replace('const error=new Error(failure(', 'const error=problem(failure(')
s=s.replace('el("status").textContent=error.message||"The action could not be completed. Your input has been kept.";', 'if(error.productSource)status(error.productSource);else if(error.message)user(el("status"),error.message);else status("The action could not be completed. Your input has been kept.");')
s=re.sub(r'el\("status"\)\.textContent=([^;]*(?:"(?:[^"\\]|\\.)*"[^;]*)?);',lambda m:m.group(0),s) # handled line-by-line below without splitting quoted semicolons
literal=r'"(?:[^"\\]|\\.)*"'
s=re.sub(r'el\("status"\)\.textContent=('+literal+r');',r'status(\1);',s)
s=s.replace('el("status").textContent=setup.eligible_members.length?','status(setup.eligible_members.length?').replace('on the People page.";','on the People page.");')
start=s.index('    for(const row of model.items)')
end=s.index('\n',start)
s=s[:start]+'''    for(const row of model.items){const item=document.createElement("p"),button=document.createElement("button");button.append(userNode("bdi",row.title)," · ",labelNode(row.status));if(row.stale)button.append(" · ",node("span","source changed"));if(row.overdue)button.append(" · ",node("span","overdue"));button.type="button";button.className="btn";button.addEventListener("click",()=>action(()=>openPacket(row.id)));item.append(button);el("inbox").append(item);}'''+s[end:]
s=s.replace('label:row.title+" · "+words(row.status)','label:row.title+" · "+(labels[row.status]?text(labels[row.status]):words(row.status))')
s=s.replace('const item=node("li",who(member)+" "),button=node("button","Remove reviewer");','const item=document.createElement("li"),button=node("button","Remove reviewer");item.append(userNode("bdi",who(member))," ");')
s=s.replace('button.setAttribute("aria-label","Remove "+who(member)+" as a reviewer")','button.setAttribute("aria-label",text("Remove {name} as a reviewer",{name:who(member)}))')
s=s.replace('ask("Remove "+who(member)+" from this project\'s reviewers? They lose access to its reviews straight away.","Remove reviewer")','ask("Remove {name} from this project\'s reviewers? They lose access to its reviews straight away.","Remove reviewer",{name:who(member)})')
s=s.replace('el("packet-title").textContent=packet.snapshot.title;', 'user(el("packet-title"),packet.snapshot.title);')
start=s.index('    el("packet-summary").textContent=')
end=s.index('\n',start)
s=s[:start]+'''    el("packet-summary").replaceChildren(userNode("bdi",packet.project_name)," · ",labelNode(packet.status)," · ",node("span","Reviewer: {name}",{name:who(packet.reviewer)})," · ",node("span","Due: {date}",{date:date(packet.due_at)}));'''+s[end:]
s=s.replace('el("stale").textContent=packet.stale?', 'product(el("stale"),packet.stale?').replace('shown below.";', 'shown below.");')
s=s.replace('el("packet-note").textContent=packet.note;el("result").textContent=packet.snapshot.result;', 'user(el("packet-note"),packet.note);user(el("result"),packet.snapshot.result);')
start=s.index('    packet.history.forEach(')
end=s.index('\n',start)
s=s[:start]+'''    packet.history.forEach(row=>{const item=document.createElement("li");item.append(userNode("bdi",who(row.actor))," · ",labelNode(row.action)," · ",userNode("bdi",date(row.at))," - ",userNode("bdi",row.note));el("history").append(item);});'''+s[end:]
s=s.replace('node("span","Changed since the previous captured result: "+packet.change_summary+" ")','node("span","Changed since the previous captured result: {changes}",{changes:packet.change_summary})')
start=s.index('    el("outcome-timeline").append(')
end=s.index('\n',start)
s=s[:start]+'''    const outcome=node("li",decision);if(packet.stale)outcome.append("; ",node("span","the source has changed since capture"));
    el("outcome-timeline").append(outcome,node("li","Next owner: {name}",{name:who(packet.status==="pending"?packet.reviewer:packet.owner)}),node("li","Task execution and deliverable sign-off: separate decisions; this packet grants neither."),node("li","External delivery and verification: not established by this review packet."));'''+s[end:]
s=s.replace('el("previous-label").textContent="Previous review: "+prior.snapshot.title+" ("+prior.id+"). Its decision will not carry forward.";', 'product(el("previous-label"),"Previous review: {title} ({id}). Its decision will not carry forward.",{title:prior.snapshot.title,id:prior.id});')
s=s.replace('el("preview-title").textContent=preview.snapshot.title;el("preview-text").textContent=preview.snapshot.result;', 'user(el("preview-title"),preview.snapshot.title);user(el("preview-text"),preview.snapshot.result);')
s=s.replace('ask("Share exactly the captured result shown above with "+who(payload.reviewer)+"?","Share for review")', 'ask("Share exactly the captured result shown above with {name}?","Share for review",{name:who(payload.reviewer)})')
s=s.replace('ask("Save this "+words(kind)+" decision for the captured result? The live task will remain unchanged.","Save decision")','ask("Save this {decision} decision for the captured result? The live task will remain unchanged.","Save decision",{decision:labels[kind]?text(labels[kind]):words(kind)})')
p.write_text(s,encoding='utf8',newline='\n')

for name,title in [('task_file_recovery.html','Task files'),('project_reviews.html','Project team reviews')]:
    p=root/'templates'/name;s=p.read_text(encoding='utf8')
    s=s.replace(f'{{% block title %}}{title} · Lightwork',f'{{% block title %}}{{{{ ut("{title}") }}}} · Lightwork')
    if name=='task_file_recovery.html':s=s.replace('<p class="page-subtitle">{{ goal.title }}</p>','<p class="page-subtitle" lang="und" dir="auto" data-user-content>{{ goal.title }}</p>')
    else:
        for identifier in ('pr-preview-title','pr-packet-title','pr-packet-note'):
            s=s.replace(f'id="{identifier}"',f'id="{identifier}" lang="und" dir="auto" data-user-content')
    p.write_text(s,encoding='utf8',newline='\n')
