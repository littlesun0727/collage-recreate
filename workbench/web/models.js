"use strict";
const modelSettings = {policy:null, catalogs:new Map(), loading:null};
const modelModeLabels = {default:'模型默认',off:'关闭 thinking',on:'开启 thinking（默认预算）',budget:'开启 thinking，限制 token',effort:'按推理档位'};
const modelProviderName = p => p==='yibu'?'yibuapi':'OpenRouter';

function modelSummary(){
  const el=$('#uploadModelSummary');
  el.textContent=modelSettings.policy?modelPolicyLabel(modelSettings.policy):'模型配置读取失败，请打开设置重试';
}
async function loadModelSettings(){
  const data=await api('/api/model-config');
  modelSettings.policy=data.policy;
  modelSummary();
  return data;
}
window.snapshotModelPolicy=async function(){
  if(!modelSettings.policy)await loadModelSettings();
  return structuredClone(modelSettings.policy);
};
function stageMode(entry){
  if(entry.reasoning.max_tokens!=null)return 'budget';
  if(entry.reasoning.effort)return 'effort';
  return entry.reasoning.enabled==null?'default':entry.reasoning.enabled?'on':'off';
}
function field(card,name){return card.querySelector(`[name="${name}"]`);}
function showReasoningFields(card){
  const mode=field(card,'mode').value;
  for(const name of ['budget','effort']){
    field(card,name).closest('label').hidden=mode!==name;
    field(card,name).disabled=mode!==name;
  }
}
async function modelCapabilities(card,preferredMode=null){
  const provider=field(card,'provider').value, model=field(card,'model').value.trim();
  const signature=provider+'\n'+model;
  card.dataset.capabilityRequest=signature;
  const current=preferredMode||field(card,'mode').value||'default';
  const cached=modelSettings.catalogs.get(provider)?.models.find(m=>m.id===model)?.capabilities;
  const caps=cached||await api(`/api/model-capabilities?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`);
  if(card.dataset.capabilityRequest!==signature)return;
  const mode=field(card,'mode');
  mode.replaceChildren(...caps.modes.map(value=>new Option(modelModeLabels[value],value)));
  mode.value=caps.modes.includes(current)?current:'default';
  const effort=field(card,'effort'), previous=effort.value||'medium';
  effort.replaceChildren(...caps.efforts.map(value=>new Option(value,value)));
  effort.value=caps.efforts.includes(previous)?previous:'medium';
  card.querySelector('.model-capability-note').textContent=caps.note;
  showReasoningFields(card);
}
async function loadModelCatalog(card){
  const provider=field(card,'provider').value, status=card.querySelector('.model-catalog-status');
  status.textContent='正在读取模型列表…';
  try{
    let catalog=modelSettings.catalogs.get(provider);
    if(!catalog){catalog=await api('/api/models?provider='+encodeURIComponent(provider));modelSettings.catalogs.set(provider,catalog);}
    if(field(card,'provider').value!==provider)return;
    const list=card.querySelector('datalist');
    list.replaceChildren(...catalog.models.map(m=>new Option('',m.id)));
    status.textContent=`${catalog.models.length} 个候选模型，可输入名称搜索或手动填写 ID。`;
    await modelCapabilities(card);
  }catch(e){
    if(field(card,'provider').value===provider)status.textContent='列表暂不可用，可手动填写模型 ID。';
  }
}
function stageCard(name,entry){
  const card=document.createElement('fieldset');card.dataset.stage=name;
  const id='model-'+name;
  card.innerHTML=`<legend>${name==='analyze'?'首次分析 · analysis.json':'后续 VLM · 窗口、框选、素材与修改'}</legend>
    <label>供应商<select name="provider"><option value="openrouter">OpenRouter</option><option value="yibu">yibuapi</option></select></label>
    <label>模型 ID<input name="model" list="${id}-list" required maxlength="200" autocomplete="off" spellcheck="false"><datalist id="${id}-list"></datalist></label>
    <small class="model-catalog-status"></small>
    <label>推理设置<select name="mode"></select></label>
    <label hidden>thinking token 上限<input name="budget" type="number" min="1" max="131071" step="1" value="4096"></label>
    <label hidden>推理档位<select name="effort"></select></label>
    <label>输出 token 上限<input name="max_tokens" type="number" min="128" max="131072" step="1" required></label>
    <small>部分模型将思考计入输出上限，请为最终 JSON 留出空间。</small>
    <details><summary>坐标与 JSON 输出</summary>
      <label>bbox 坐标<select name="coordinate_mode"><option value="normalized_1000">0～1000 normalized（推荐）</option><option value="pixel">原图像素</option><option value="auto">按模型自动识别</option></select></label>
      <label>JSON 输出方式<select name="response_format"><option value="json_schema">JSON Schema</option><option value="json_object">JSON 对象</option><option value="prompt">提示词约束 JSON</option></select></label>
      <small>三种方式均在本地严格校验。yibu GPT 的 Schema 会转换为兼容结构，可选字段的 null 在本地还原；不支持 Schema 的接口也可手动选择 JSON 对象。</small>
    </details><small class="model-capability-note"></small>`;
  field(card,'provider').value=entry.provider||'openrouter';
  field(card,'model').value=entry.model;
  field(card,'max_tokens').value=entry.max_tokens;
  field(card,'coordinate_mode').value=entry.coordinate_mode;
  field(card,'response_format').value=entry.response_format||'json_schema';
  field(card,'budget').value=entry.reasoning.max_tokens||4096;
  field(card,'effort').append(new Option(entry.reasoning.effort||'medium',entry.reasoning.effort||'medium'));
  field(card,'mode').append(new Option(modelModeLabels[stageMode(entry)],stageMode(entry)));
  field(card,'mode').onchange=()=>showReasoningFields(card);
  let timer;
  field(card,'model').oninput=()=>{
    clearTimeout(timer);
    timer=setTimeout(()=>modelCapabilities(card).catch(e=>{$('#modelStatus').textContent=e.message;}),250);
  };
  field(card,'provider').onchange=()=>{
    let model=field(card,'model').value;
    if(field(card,'provider').value==='yibu')model=model.replace(/^(qwen|google|openai|anthropic)\//,'');
    else if(!model.includes('/')){
      const prefix=model.startsWith('qwen')?'qwen':model.startsWith('gemini')?'google':model.startsWith('claude')?'anthropic':/^gpt-|^o[134]/.test(model)?'openai':'';
      if(prefix)model=prefix+'/'+model;
    }
    field(card,'model').value=model;
    card.querySelector('datalist').replaceChildren();
    modelCapabilities(card).catch(e=>{$('#modelStatus').textContent=e.message;});
    loadModelCatalog(card);
  };
  showReasoningFields(card);
  return card;
}
async function openModelSettings(){
  $('#modelDialog').showModal();$('#modelStatus').textContent='正在读取配置…';
  $('#saveModelSettings').disabled=true;
  try{
    const data=await loadModelSettings();
    $('#modelCredentials').textContent=Object.entries(data.credentials).map(([p,ready])=>`${modelProviderName(p)}：${ready?'凭证已配置':'凭证未配置'}`).join(' · ');
    const cards=Object.entries(data.policy).map(([name,entry])=>stageCard(name,entry));
    $('#modelStages').replaceChildren(...cards);
    await Promise.all(cards.map(card=>modelCapabilities(card)));
    cards.forEach(card=>loadModelCatalog(card));
    $('#modelStatus').textContent='';
    $('#saveModelSettings').disabled=false;
  }catch(e){$('#modelStatus').textContent=e.message;}
}
$('#openModelSettings').onclick=openModelSettings;
$('#uploadModelSettings').onclick=openModelSettings;
$('#closeModelSettings').onclick=()=>$('#modelDialog').close();
$('#modelForm').onsubmit=async event=>{
  event.preventDefault();$('#saveModelSettings').disabled=true;$('#modelStatus').textContent='正在保存…';
  try{
    const policy={};
    for(const card of $('#modelStages').children){
      const mode=field(card,'mode').value, reasoning={enabled:mode==='default'?null:mode!=='off'};
      if(mode==='budget')reasoning.max_tokens=Number(field(card,'budget').value);
      if(mode==='effort')reasoning.effort=field(card,'effort').value;
      policy[card.dataset.stage]={provider:field(card,'provider').value,model:field(card,'model').value.trim(),reasoning,
        max_tokens:Number(field(card,'max_tokens').value),coordinate_mode:field(card,'coordinate_mode').value,response_format:field(card,'response_format').value};
    }
    const result=await livePost('/api/model-config',{policy});
    modelSettings.policy=result.policy;modelSummary();
    $('#modelStatus').textContent='已保存，新任务将使用此配置。';
  }catch(e){$('#modelStatus').textContent=e.message;}
  finally{$('#saveModelSettings').disabled=false;}
};
loadModelSettings().catch(()=>modelSummary());
