/* Current, authority-scoped metadata results for the existing keyboard finder. */
(function (global) {
  "use strict";
  function errText(failure,action){return global.mvErrorText?global.mvErrorText(failure,action):'Could not '+action+'. Please try again.';}
  function plainError(text){var error=new Error(text);error.plain=true;return error;}
  function init() {
    var doc=global.document, meta=doc.querySelector('meta[name="lightwork-search-context"]');
    var dialog=doc.getElementById('navfinder-dialog'), kind=doc.getElementById('navfinder-kind');
    if(!meta||!dialog||!dialog.lightworkFinder||!kind||dialog.dataset.recordSearchReady)return;
    dialog.dataset.recordSearchReady='true';
    var context=meta.content, finder=dialog.lightworkFinder, input=doc.getElementById('navfinder-input');
    var status=doc.getElementById('navfinder-record-status'), more=doc.getElementById('navfinder-more'), coverage=doc.getElementById('navfinder-coverage');
    var timer=null, generation=0, next=null, snapshot='', rows=[], activeQuery='', activeKind='';
    function clear(){generation++;clearTimeout(timer);rows=[];next=null;snapshot='';more.hidden=true;coverage.replaceChildren();finder.setRecords([]);}
    function validRow(row){
      if(!row||!['vendor','evidence','assessment','decision','task','report'].includes(row.kind)||typeof row.title!=='string'||typeof row.id!=='string'||typeof row.revision!=='string'||typeof row.state!=='string'||typeof row.href!=='string')return false;
      // Only approved app review destinations; a compromised source cannot
      // smuggle a script, remote URL, or protocol-relative result into the UI.
      return /^\/(?:chat\/goal\/\d+|privacy\/vendors\/[A-Za-z0-9%_.-]+|third-party\/vendor-onboarding\?case=[A-Za-z0-9%_.-]+|(?:privacy|finance|security)\?assessment=[A-Za-z0-9%_.-]+|administration\/document-vault\?document=[A-Za-z0-9%_.-]+|third-party\/executive-reports\?report=[A-Za-z0-9%_.:-]+|project-reviews#packet=PRP-[0-9a-f]{32})$/.test(row.href);
    }
    async function search(append){
      if(kind.value==='pages'){clear();status.textContent='Showing navigation pages only.';return;}
      var query=input.value.trim();
      if(Array.from(query).length>200){clear();status.textContent='Use 200 characters or fewer to search records.';return;}
      if(!query){clear();status.textContent='Type a name or reference to search records.';return;}
      if(!append)clear();
      var current=++generation, offset=append?next:0, selectedKind=kind.value;
      if(append&&(next===null||query!==activeQuery||selectedKind!==activeKind))return;
      more.disabled=true; status.textContent='Searching accessible records…';
      try{
        var response=await global.fetch('/api/v1/records/search',{method:'POST',credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'Content-Type':'application/json','X-Lightwork-Search-Context':context},body:JSON.stringify({query:query,kind:selectedKind,offset:offset,limit:20,snapshot:append?snapshot:''})});
        if(!response.ok){if(current!==generation)return;throw plainError(errText(response,'search records'));}
        var value=await response.json();
        if(current!==generation)return;
        if(value.context!==context)throw plainError('The account or workspace changed. Reload before searching records.');
        if(!Array.isArray(value.items)||!value.items.every(validRow)||!Array.isArray(value.coverage)||!Number.isSafeInteger(value.total)||typeof value.snapshot!=='string'||!(/^[a-f0-9]{64}$/).test(value.snapshot)||!(value.next_offset===null||Number.isSafeInteger(value.next_offset)&&value.next_offset>offset))throw plainError('Search results could not be verified. Try again.');
        rows=append?rows.concat(value.items):value.items;next=value.next_offset;snapshot=value.snapshot;activeQuery=query;activeKind=selectedKind;
        finder.setRecords(rows);more.hidden=next===null;
        status.textContent=rows.length+' of '+value.total+' matching records in the searched sources. '+(value.complete?'All selected sources are current.':'Some sources are partial or unavailable. Check Search coverage.');
        coverage.replaceChildren();value.coverage.forEach(function(source){var li=doc.createElement('li');var human=global.mvHuman||String;li.textContent=human(source.source)+': '+human(source.state).toLowerCase()+(Number.isSafeInteger(source.indexed)?' ('+source.indexed+' accessible records indexed)':'');coverage.appendChild(li);});
      }catch(error){if(current===generation){rows=[];next=null;snapshot='';more.hidden=true;finder.setRecords([]);status.textContent=error&&error.plain?error.message:errText(error,'search records');}}
      finally{if(current===generation)more.disabled=false;}
    }
    dialog.addEventListener('lightwork:finder-query',function(event){clear();if(event.detail.reason==='closed')return;status.textContent='Type a name or reference to search records.';timer=setTimeout(function(){search(false);},300);});
    kind.addEventListener('change',function(){search(false);});
    doc.getElementById('navfinder-search').addEventListener('click',function(){search(false);});
    more.addEventListener('click',function(){search(true);});
  }
  if(global.document.readyState==='loading')global.document.addEventListener('DOMContentLoaded',init);else init();
})(window);
