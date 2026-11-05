const providerSelect=document.querySelector('#provider');
const modelSelect=document.querySelector('#model');
const form=document.querySelector('#search-form');
const submitButton=document.querySelector('#submit');
const message=document.querySelector('#message');
const modelNote=document.querySelector('#model-note');
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

async function loadModels(){
  const provider=providerSelect.value;
  modelSelect.innerHTML='<option value="">Use configured default</option>';
  modelNote.textContent='Loading models available to this backend…';
  try{
    const response=await fetch(`/api/models/?provider=${encodeURIComponent(provider)}`);
    const data=await response.json();
    if(!response.ok) throw new Error(data.detail||'Could not load models.');
    const models=data.models||[];
    const usable=provider==='gemini'?models.filter(m=>m.is_legal_search_compatible):provider==='openrouter'?models.filter(m=>m.is_legal_search_compatible):models;
    if(!usable.length){modelNote.textContent=`No compatible ${provider} models were returned. The configured default will be used when available.`;return;}
    for(const model of usable){const option=document.createElement('option');option.value=model.id;option.textContent=model.label||model.id;modelSelect.append(option);}
    modelNote.textContent=`${usable.length} compatible ${provider} model${usable.length===1?'':'s'} available.`;
  }catch(error){modelNote.textContent=`${error.message} The configured default will be used when available.`;}
}

function renderAnalysis(data){
  const box=document.querySelector('#analysis-content');
  const a=data.query_analysis||{};
  const values=[['Intent',a.intent],['Domain',a.domain],['Court',a.court],['Act',a.act],['Section',a.section],['Language',a.language],['Issues',(a.legal_issues||[]).join(', ')],['Keywords',(a.keywords||[]).join(', ')]];
  box.innerHTML=values.filter(([,value])=>value!==null&&value!==undefined&&value!=='').map(([label,value])=>`<span class="chip"><strong>${esc(label)}:</strong> ${esc(value)}</span>`).join('');
  document.querySelector('#provider-badge').textContent=data.query_analysis_provider||'analysis';
  document.querySelector('#analysis').hidden=false;
}

function renderResults(data){
  const results=data.results||[];
  document.querySelector('#result-count').textContent=`${results.length} record${results.length===1?'':'s'}`;
  const list=document.querySelector('#result-list');
  if(!results.length){list.innerHTML='<div class="record"><p>No matching records were found.</p></div>';}
  else list.innerHTML=results.map((record,index)=>{
    if(typeof record==='string')return `<article class="record"><p>${esc(record)}</p></article>`;
    if(record&&typeof record==='object'){
      const title=record.case_title||record.title||record.name||record.case_name||record.citation||`Source record ${index+1}`;
      const description=record.summary||record.snippet||record.description||'';
      const metadata=[record.language_name||record.language_code,record.version_type_display||record.version_type,record.is_official===true?'Official source':record.is_official===false?'Non-official source':'',record.paragraph_number?`Paragraph ${record.paragraph_number}`:'',record.page_number!==null&&record.page_number!==undefined?`Page ${record.page_number}`:'',record.source_label].filter(Boolean);
      return `<article class="record"><h3>${esc(title)}</h3>${metadata.length?`<div class="record-meta">${metadata.map(value=>`<span>${esc(value)}</span>`).join('')}</div>`:''}${description?`<p>${esc(description)}</p>`:`<pre>${esc(JSON.stringify(record,null,2))}</pre>`}</article>`;
    }
    return `<article class="record"><p>${esc(record)}</p></article>`;
  }).join('');
  document.querySelector('#results').hidden=false;
}

providerSelect.addEventListener('change',loadModels);
form.addEventListener('submit',async event=>{
  event.preventDefault();message.hidden=true;document.querySelector('#analysis').hidden=true;document.querySelector('#results').hidden=true;
  submitButton.disabled=true;submitButton.querySelector('span').textContent='Searching…';
  try{
    const payload={query:document.querySelector('#query').value,provider:providerSelect.value};
    const model=modelSelect.value;
    const language=document.querySelector('#language').value.trim();
    const versionType=document.querySelector('#version-type').value;
    const official=document.querySelector('#official').value;
    const limit=document.querySelector('#limit').value;
    if(model)payload.model=model;
    if(language)payload.language=language;
    if(versionType)payload.version_type=versionType;
    if(official)payload.is_official=official==='true';
    if(limit)payload.limit=Number(limit);
    const response=await fetch('/api/search/',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail||JSON.stringify(data));
    renderAnalysis(data);renderResults(data);
    const retrieval=data.retrieval||{};
    if(retrieval.is_demo){message.textContent=retrieval.message||'Showing clearly labelled local demo records.';message.hidden=false;}
    else if(data.query_analysis_provider==='heuristic_fallback'){message.textContent='The selected AI provider was unavailable. Basic local query analysis was used; review filters carefully.';message.hidden=false;}
  }catch(error){message.textContent=error.message;message.hidden=false;}
  finally{submitButton.disabled=false;submitButton.querySelector('span').textContent='Search legal sources';}
});
loadModels();
