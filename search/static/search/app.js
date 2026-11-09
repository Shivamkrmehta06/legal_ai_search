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
  const values=[['Case',a.case_name],['Asked for',(data.requested_info||[]).join(', ').replaceAll('_',' ')],['Domain',a.domain],['Court',a.court],['Act',a.act],['Section',a.section],['Language',a.language],['Issues',(a.legal_issues||[]).join(', ')],['Keywords',(a.keywords||[]).join(', ')]];
  box.innerHTML=values.filter(([,value])=>value!==null&&value!==undefined&&value!=='').map(([label,value])=>`<span class="chip"><strong>${esc(label)}:</strong> ${esc(value)}</span>`).join('');
  document.querySelector('#provider-badge').textContent=data.query_analysis_provider||'analysis';
  document.querySelector('#analysis').hidden=false;
}

const listItems=rows=>`<ul>${rows.map(row=>`<li>${esc(row)}</li>`).join('')}</ul>`;
const present=(...values)=>values.filter(value=>value!==null&&value!==undefined&&value!=='').join(' · ');
const section=(title,rows)=>rows.length?`<section><h4>${esc(title)}</h4>${listItems(rows)}</section>`:'';
const textSection=(title,text)=>text?`<section><h4>${esc(title)}</h4><p class="long-text">${esc(text)}</p></section>`:'';
const benchNames=bench=>bench&&typeof bench==='object'?(bench.names||bench.judges||[]):[];
const pairs=rows=>rows.filter(([,value])=>value).map(([label,value])=>`${label}: ${value}`);
const SECTIONS={
  case_details:j=>section('Case details',pairs([['Court',j.court_name],['Case number',j.case_number],['Judgment date',j.judgment_date],['Citation',j.neutral_citation||j.primary_citation],['Outcome',j.disposition],['Status',present(j.publication_status,j.verification_status)]])),
  court:j=>section('Court',j.court_name?[j.court_name]:[]),
  judgment_date:j=>section('Judgment date',j.judgment_date?[j.judgment_date]:[]),
  case_number:j=>section('Case number',j.case_number?[j.case_number]:[]),
  citation:j=>section('Citation',[j.neutral_citation,j.primary_citation].filter(Boolean)),
  outcome:j=>section('Outcome',j.disposition?[j.disposition]:[]),
  bench:j=>section('Bench',benchNames(j.bench_details)),
  judges:j=>{const judges=(j.judges||[]).map(judge=>present(judge.judge_name,judge.judge_role));return section('Judges',judges.length?judges:benchNames(j.bench_details));},
  parties:j=>section('Parties',(j.parties||[]).map(party=>present(party.party_name,party.party_role))),
  statutes:j=>section('Acts and sections',(j.statutes||[]).map(link=>present(link.provision_label||link.provision_code,link.discussion_type,link.verification_status))),
  issues:j=>section('Legal issues',(j.issues||[]).map(issue=>present(issue.issue_name,issue.verification_status))),
  domains:j=>section('Legal domains',(j.domains||[]).map(domain=>domain.domain_name)),
  summary:j=>textSection('Summary',j.summary),
  propositions:j=>section('Legal propositions',(j.propositions||[]).map(item=>present(item.proposition_text,item.proposition_type,item.verification_status))),
  citation_aliases:j=>section('Other citations',(j.citation_aliases||[]).map(item=>present(item.alias,item.citation_type))),
  cases_cited:j=>section('Cases this judgment cites',(j.cases_cited||[]).map(item=>present(item.cited_case_title,item.relationship_type))),
  cited_by:j=>section('Cited by',(j.cited_by||[]).map(item=>present(item.citing_case_title,item.relationship_type))),
  passages:j=>section('Passages',(j.passages||[]).map(item=>present(item.paragraph_text,item.language_name,item.page_number?`Page ${item.page_number}`:''))),
  case_documents:j=>section('Documents',(j.case_documents||[]).map(item=>present(item.legal_document_title,item.language_name,item.version_type_display))),
  legal_documents:j=>section('Legal documents',(j.legal_documents||[]).map(item=>present(item.canonical_title_en,item.document_type))),
  versions:j=>section('Versions',(j.versions||[]).map(item=>present(item.version_number,item.change_summary))),
  metadata_versions:j=>section('Metadata versions',(j.metadata_versions||[]).map(item=>present(item.title,item.language_name,item.version_type))),
};
const FULL_ORDER=['case_details','judges','parties','statutes','issues','domains','summary','propositions','citation_aliases','cases_cited','cited_by','passages','case_documents'];
const unavailableNote=j=>(j.unavailable_sections||[]).length?`<p class="hint">Some details could not be loaded: ${esc(j.unavailable_sections.join(', ').replaceAll('_',' '))}.</p>`:'';

function requestedAnswer(judgment){
  const requested=judgment.requested||[];
  if(!requested.length)return '';
  const parts=requested.map(item=>SECTIONS[item]?SECTIONS[item](judgment):'');
  const body=parts.join('')||'<p class="hint">KMS has no record of this for this case.</p>';
  return `<div class="judgment-answer">${body}${unavailableNote(judgment)}</div>`;
}

function renderJudgment(judgment){
  return `<article class="judgment-card"><details class="judgment" data-case-id="${esc(judgment.id)}"><summary><span class="judgment-title">${esc(judgment.title)}</span><span class="judgment-more"></span></summary><div class="judgment-body"></div></details>${requestedAnswer(judgment)}</article>`;
}

async function loadJudgment(details){
  const body=details.querySelector('.judgment-body');
  if(!details.open||details.dataset.loaded)return;
  details.dataset.loaded='true';
  body.innerHTML='<p class="hint">Loading details…</p>';
  try{
    const response=await fetch(`/api/judgments/${encodeURIComponent(details.dataset.caseId)}/`);
    const data=await response.json().catch(()=>({detail:'This judgment could not be loaded.'}));
    if(!response.ok)throw new Error(data.detail||'This judgment could not be loaded.');
    const j=data.judgment;
    body.innerHTML=FULL_ORDER.map(item=>SECTIONS[item](j)).join('')+unavailableNote(j);
  }catch(error){
    delete details.dataset.loaded;
    body.innerHTML=`<p class="hint">${esc(error instanceof TypeError?'The server could not be reached. Close and open this again to retry.':error.message)}</p>`;
  }
}

function renderResults(data){
  const retrievalInfo=data.retrieval||{};
  const title=document.querySelector('#results-title');
  if(!retrievalInfo.is_demo){
    const count=data.judgment_count||0;
    title.textContent=data.results_title||'';
    document.querySelector('#result-count').textContent=`${count} judgment${count===1?'':'s'}`;
    document.querySelector('#results .panel-head').hidden=!data.results_title;
    const unavailable=retrievalInfo.unavailable?`<p class="response-note">${esc(retrievalInfo.message||'Judgments could not be loaded right now.')}</p>`:'';
    const note=data.response_message?`<p class="response-note">${esc(data.response_message)}</p>`:'';
    const list=document.querySelector('#result-list');
    list.innerHTML=unavailable+note+(data.judgments||[]).map(renderJudgment).join('');
    list.querySelectorAll('.judgment').forEach(details=>details.addEventListener('toggle',()=>loadJudgment(details)));
    document.querySelector('#results').hidden=false;
    return;
  }
  document.querySelector('#results .panel-head').hidden=false;
  title.textContent='Search results';
  const results=data.results||[];
  document.querySelector('#result-count').textContent=`${results.length} record${results.length===1?'':'s'}`;
  const list=document.querySelector('#result-list');
  if(!results.length){list.innerHTML=`<div class="record"><p>${data.retrieval&&data.retrieval.unavailable?'Records could not be loaded right now.':'No matching records were found.'}</p></div>`;}
  else list.innerHTML=results.map((record,index)=>{
    if(typeof record==='string')return `<article class="record"><p>${esc(record)}</p></article>`;
    if(record&&typeof record==='object'){
      const title=record.display_title||record.case_title||record.title||record.name||record.case_name||record.citation||`Source record ${index+1}`;
      const description=record.display_text||record.summary||record.snippet||record.description||'';
      const metadata=[record.source_key&&record.source_key!=='passages'?record.source_label:'',...(record.display_details||[]),record.language_name||record.language_code,record.version_type_display||record.version_type,record.is_official===true?'Official source':record.is_official===false?'Non-official source':'',record.paragraph_number?`Paragraph ${record.paragraph_number}`:'',record.page_number!==null&&record.page_number!==undefined?`Page ${record.page_number}`:'',record.source_key==='passages'?record.source_label:(record.source_key?'':record.source_label)].filter(Boolean);
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
    if(model)payload.model=model;
    const response=await fetch('/api/search/',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json().catch(()=>({detail:'The server could not finish this search. Please try again.'}));
    if(!response.ok)throw new Error(data.detail||JSON.stringify(data));
    renderAnalysis(data);renderResults(data);
    const retrieval=data.retrieval||{};
    const notices=[];
    if(data.query_analysis_provider==='heuristic_fallback')notices.push('The selected AI provider was unavailable. Basic local query analysis was used; review filters carefully.');
    else if(model?data.query_analysis_provider!==`${provider}:${model}`:provider&&!data.query_analysis_provider.startsWith(`${provider}:`))notices.push(`Your selected model was unavailable, so ${data.query_analysis_provider} answered instead.`);
    if(retrieval.is_demo)notices.push(retrieval.message||'Showing clearly labelled local demo records.');
    if(notices.length){message.textContent=notices.join(' ');message.hidden=false;}
  }catch(error){message.textContent=error instanceof TypeError?'The server could not be reached. Check your connection and try again.':error.message;message.hidden=false;}
  finally{submitButton.disabled=false;submitButton.querySelector('span').textContent='Search legal sources';}
});
loadProviders();
