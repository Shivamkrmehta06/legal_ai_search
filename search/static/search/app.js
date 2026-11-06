const providerSelect=document.querySelector('#provider');
const modelSelect=document.querySelector('#model');
const form=document.querySelector('#search-form');
const submitButton=document.querySelector('#submit');
const message=document.querySelector('#message');
const modelNote=document.querySelector('#model-note');
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

async function loadProviders(){
  try{
    const response=await fetch('/api/providers/');
    const data=await response.json();
    if(!response.ok) throw new Error(data.detail||'Could not load AI providers.');
    const configured=(data.providers||[]).filter(p=>p.configured);
    if(!configured.length){providerSelect.innerHTML='<option value="">No AI provider set up</option>';modelNote.textContent='Basic local query analysis will be used.';return;}
    for(const provider of configured){const option=document.createElement('option');option.value=provider.id;option.textContent=provider.label;providerSelect.append(option);}
    await loadModels();
  }catch(error){providerSelect.innerHTML='<option value="">Default</option>';modelNote.textContent=error.message;}
}

async function loadModels(){
  const provider=providerSelect.value;
  modelSelect.innerHTML='<option value="">Use configured default</option>';
  modelNote.textContent='Loading models available to this backend…';
  try{
    const response=await fetch(`/api/models/?provider=${encodeURIComponent(provider)}`);
    const data=await response.json();
    if(!response.ok) throw new Error(data.detail||'Could not load models.');
    const models=data.models||[];
    if(!models.length){modelNote.textContent=`No ${provider} models were returned. The configured default will be used when available.`;return;}
    for(const model of models){const option=document.createElement('option');option.value=model.id;option.textContent=model.label||model.id;modelSelect.append(option);}
    modelNote.textContent=`${models.length} ${provider} model${models.length===1?'':'s'} available. If one can't answer, another model takes over.`;
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
  if(!results.length){list.innerHTML=`<div class="record"><p>${data.retrieval&&data.retrieval.unavailable?'Records could not be loaded right now.':'No matching records were found.'}</p></div>`;}
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
    const payload={query:document.querySelector('#query').value};
    const provider=providerSelect.value;
    const model=modelSelect.value;
    if(provider)payload.provider=provider;
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
    const data=await response.json().catch(()=>({detail:'The server could not finish this search. Please try again.'}));
    if(!response.ok)throw new Error(data.detail||JSON.stringify(data));
    renderAnalysis(data);renderResults(data);
    const retrieval=data.retrieval||{};
    const notices=[];
    if(data.query_analysis_provider==='heuristic_fallback')notices.push('The selected AI provider was unavailable. Basic local query analysis was used; review filters carefully.');
    else if(model?data.query_analysis_provider!==`${provider}:${model}`:provider&&!data.query_analysis_provider.startsWith(`${provider}:`))notices.push(`Your selected model was unavailable, so ${data.query_analysis_provider} answered instead.`);
    if(retrieval.is_demo||retrieval.unavailable)notices.push(retrieval.message||'Showing clearly labelled local demo records.');
    if(notices.length){message.textContent=notices.join(' ');message.hidden=false;}
  }catch(error){message.textContent=error instanceof TypeError?'The server could not be reached. Check your connection and try again.':error.message;message.hidden=false;}
  finally{submitButton.disabled=false;submitButton.querySelector('span').textContent='Search legal sources';}
});
loadProviders();
