/* Tenant-scoped record selection. Choosing a record only fills an unsaved form. */
(function (root) {
  'use strict';
  var doc=root.document, API='/api/v1/consent/publishing';
  function element(tag,text){var node=doc.createElement(tag);if(text!==undefined)node.textContent=text;return node;}
  function differences(before,after,prefix,out){
    if(JSON.stringify(before)===JSON.stringify(after)||out.length>=31)return;
    if(before&&after&&typeof before==='object'&&typeof after==='object'){
      Array.from(new Set(Object.keys(before).concat(Object.keys(after)))).forEach(function(key){differences(before[key],after[key],prefix?prefix+'.'+key:key,out);});
    }else out.push(prefix.replace(/_/g,' '));
  }
  async function call(path){var meta=doc.querySelector('meta[name="lightwork-workspace-tenant"]'),headers={};if(meta)headers['X-Lightwork-Expected-Tenant']=meta.content;var response=await root.fetch(API+path,{cache:'no-store',headers:headers}),data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Could not load saved consent records.');return data;}
  function picker(host,options){
    var details=element('details');details.className='cpp-picker';details.append(element('summary',options.title||'Choose a saved record'));
    var search=element('input');search.type='search';search.className='input';search.placeholder='Search saved names';search.setAttribute('aria-label','Search saved consent records');
    var find=element('button','Search');find.type='button';find.className='btn';
    var results=element('select');results.className='input';results.size=4;results.setAttribute('aria-label','Matching saved records');
    var versions=element('select');versions.className='input';versions.setAttribute('aria-label','Saved version');versions.hidden=true;
    var status=element('p');status.setAttribute('role','status');
    var preview=element('details');preview.append(element('summary','Review selected record'));var content=element('pre');preview.append(content);preview.hidden=true;
    var apply=element('button',options.applyLabel||'Use selected version');apply.type='button';apply.className='btn';apply.disabled=true;
    var more=element('button','More results');more.type='button';more.className='btn';more.hidden=true;
    var older=element('button','More versions');older.type='button';older.className='btn';older.hidden=true;
    details.append(search,find,status,results,more,versions,older,preview,apply);host.append(details);
    var generation=0,selected=null,items=[],versionItems=[],next=null,baseline=null,currentKind=null,versionNext=null,versionResource=null;
    function kind(){return typeof options.kind==='function'?options.kind():options.kind;}
    function snapshot(){return options.snapshot?options.snapshot():'';}
    function clearSelection(){selected=null;apply.disabled=true;preview.hidden=true;versions.hidden=true;older.hidden=true;}
    async function load(offset){
      var request=++generation;clearSelection();currentKind=kind();var term=search.value.trim();
      if(!offset){items=[];results.textContent='';baseline=snapshot();}
      status.textContent='Loading saved records…';find.disabled=true;more.disabled=true;
      try{
        var data=await call('/catalog/'+encodeURIComponent(currentKind)+'?q='+encodeURIComponent(term)+'&offset='+(offset||0));
        if(request!==generation||kind()!==currentKind)return;
        if(!data||!Array.isArray(data.items))throw new Error('The saved record list could not be verified.');
        data.items.forEach(function(item){var option=element('option',(item.property_id||item.resource_id)+(item.locale?' · '+item.locale:'')+(item.version?' · version '+item.version:''));option.value=String(items.length);results.append(option);items.push(item);});
        results.selectedIndex=-1;next=data.next_offset;more.hidden=next===null;status.textContent=data.total?('Showing '+items.length+' of '+data.total+' matching records. Select one to review.'):'No matching saved records. You can still enter a new name in the form.';
      }catch(error){if(request===generation)status.textContent=error.message;}
      finally{if(request===generation){find.disabled=false;more.disabled=false;}}
    }
    async function loadVersion(record){
      var request=++generation;selected=null;apply.disabled=true;preview.hidden=true;status.textContent='Loading the exact version…';
      try{
        var data=await call('/catalog/'+encodeURIComponent(currentKind)+'/'+encodeURIComponent(record.resource_id)+'/versions/'+record.version+'?locale='+encodeURIComponent(record.locale||''));
        if(request!==generation||kind()!==currentKind)return;
        if(!data||data.resource_id!==record.resource_id||data.version!==record.version||data.locale!==(record.locale||'')||!data.payload)throw new Error('The selected resource version could not be verified.');
        var comparison='This is the first saved version.';
        if(record.version>1){
          var previous=await call('/catalog/'+encodeURIComponent(currentKind)+'/'+encodeURIComponent(record.resource_id)+'/versions/'+(record.version-1)+'?locale='+encodeURIComponent(record.locale||''));
          if(request!==generation||kind()!==currentKind)return;
          if(!previous||previous.resource_id!==record.resource_id||previous.version!==record.version-1||previous.locale!==(record.locale||''))throw new Error('The prior version comparison could not be verified.');
          var changedFields=[];differences(previous.payload,data.payload,'',changedFields);
          comparison='Compared with version '+previous.version+': '+(changedFields.length?'Changed '+changedFields.slice(0,30).join(', ')+(changedFields.length>30?' (additional changes appear in the full record).':'.'):'content is unchanged.');
        }
        selected=Object.assign({},data,{current_version:record.current_version});content.textContent=JSON.stringify(selected,null,2);preview.hidden=false;preview.open=true;apply.disabled=false;status.textContent=comparison+' Review this exact saved version before using it. Nothing will be published.';
      }catch(error){if(request===generation)status.textContent=error.message;}
    }
    async function loadVersions(offset){
      var request=++generation;selected=null;apply.disabled=true;older.disabled=true;preview.hidden=true;
      try{
        var data=await call('/catalog/'+encodeURIComponent(currentKind)+'?resource_id='+encodeURIComponent(versionResource)+'&limit=50&offset='+(offset||0));
        if(request!==generation||kind()!==currentKind)return;
        if(!data||!Array.isArray(data.items))throw new Error('Saved versions could not be read.');
        if(!offset){versionItems=[];versions.textContent='';}
        data.items.filter(function(value){return value.resource_id===versionResource;}).forEach(function(record){
          var option=element('option','Version '+record.version+(record.locale?' · '+record.locale:'')+(record.current_version===record.version?' · latest':''));option.value=String(versionItems.length);versions.append(option);versionItems.push(record);
        });
        versions.hidden=false;versions.selectedIndex=-1;versionNext=data.next_offset;older.hidden=versionNext===null;
        status.textContent='Choose a version to preview. Showing '+versionItems.length+' of '+data.total+' saved versions.';
      }catch(error){if(request===generation)status.textContent=error.message;}
      finally{if(request===generation)older.disabled=false;}
    }
    results.addEventListener('change',function(){
      var item=items[Number(results.value)];if(!item)return;++generation;clearSelection();
      if(currentKind==='properties'){selected=item;content.textContent=JSON.stringify(item,null,2);preview.hidden=false;preview.open=true;apply.disabled=false;status.textContent='Review the property and its current versions before filling the form.';return;}
      versionResource=item.resource_id;loadVersions(0);
    });
    older.addEventListener('click',function(){loadVersions(versionNext);});
    versions.addEventListener('change',function(){var record=versionItems[Number(versions.value)];if(record)loadVersion(record);});
    apply.addEventListener('click',function(){
      if(!selected||kind()!==currentKind)return;
      if(snapshot()!==baseline){status.textContent='Your form changed while choosing this record. Search again to review it against your current draft.';return;}
      try{options.apply(selected);baseline=snapshot();details.open=false;status.textContent='Selected record filled in for review. Nothing was saved or published.';}catch(error){status.textContent=error.message;}
    });
    find.addEventListener('click',function(){load(0);});more.addEventListener('click',function(){load(next);});
    search.addEventListener('keydown',function(event){if(event.key==='Enter'){event.preventDefault();load(0);}});
    details.addEventListener('toggle',function(){if(details.open&&!items.length)load(0);});
    return {reload:function(){items=[];generation++;clearSelection();results.textContent='';versions.textContent='';if(details.open)load(0);}};
  }
  function changed(field){field.dispatchEvent(new Event('change',{bubbles:true}));field.dispatchEvent(new Event('input',{bubbles:true}));}
  function formSnapshot(form){return JSON.stringify(Array.from(form.elements).filter(function(field){return field.name;}).map(function(field){return [field.name,field.value,field.checked];}));}
  function set(form,name,value){if(form.elements[name]){form.elements[name].value=value==null?'':value;changed(form.elements[name]);}}
  function attachProperty(form){
    var host=element('div');host.className='wide';form.prepend(host);
    picker(host,{kind:'properties',title:'Find a saved website or app',applyLabel:'Use selected property',snapshot:function(){return formSnapshot(form);},apply:function(record){
      set(form,'property_id',record.property_id);set(form,'expected_revision',record.revision);set(form,'expected_property_revision',record.revision);set(form,'expected_publication_revision',record.publication_revision);
      if(form.id==='cpp-property'){
        ['kind','consent_group_id','platform'].forEach(function(key){set(form,key,record[key]);});
        ['web_domains','origin_allowlist','mobile_bundle_ids'].forEach(function(key){set(form,key,(record[key]||[]).join(', '));});
      }
    }});
  }
  function attachResource(form){
    var host=element('div');host.className='wide';form.prepend(host);
    var control=picker(host,{kind:function(){return form.elements.kind.value;},title:'Find and revise saved content',applyLabel:'Prepare next version from this copy',snapshot:function(){return formSnapshot(form);},apply:function(record){
      var payload=JSON.parse(JSON.stringify(record.payload));
      if(record.resource_kind==='topic'){payload.purpose_id=payload.purpose.id;payload.purpose_version=payload.purpose.version;delete payload.purpose;}
      payload.expected_current_version=record.current_version;payload.version=record.current_version+1;
      payload.idempotency_key=root.crypto.randomUUID();
      set(form,'resource_id',record.resource_id);set(form,'locale',record.locale);set(form,'payload',JSON.stringify(payload));
    }});
    form.elements.kind.addEventListener('change',function(){control.reload();});
  }
  function attachPromotion(form){
    var host=element('div');host.className='wide';form.prepend(host);
    picker(host,{kind:'rule-groups',title:'Choose a saved rule group and version',snapshot:function(){return formSnapshot(form);},apply:function(record){set(form,'rule_group_id',record.resource_id);set(form,'rule_group_version',record.version);}});
  }
  root.mvConsentPicker={attach:picker};
  ['cpp-property','cpp-promotion','cpp-rollback','cpp-withdraw'].forEach(function(id){var form=doc.getElementById(id);if(form)attachProperty(form);});
  var resource=doc.getElementById('cpp-resource');if(resource)attachResource(resource);
  var promotion=doc.getElementById('cpp-promotion');if(promotion)attachPromotion(promotion);
})(typeof window==='undefined'?globalThis:window);
