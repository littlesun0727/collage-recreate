"use strict";
const chat={task:null,data:null,config:null,fetching:false,sending:false,asset:null,reply:null,pending:null};
const chatLabels={queued:'排队中',running:'修改中',clarify:'需要补充',unsupported:'暂不支持',failed:'未完成',conflict:'版本已更新',completed:'新版本已保存'};
const phaseLabels={understanding:'理解修改',editing:'执行修改',reviewing:'对照复核',publishing:'保存版本'};
const chatSeconds=(n)=>`${Math.floor(Math.round(n)/60)}分${Math.round(n)%60}秒`;
function chatDraft(){return 'collage-chat-draft:'+state.id;}
function rememberChat(){
  if(chat.task) localStorage.setItem('collage-chat-draft:'+chat.task,JSON.stringify({text:$('#chatInput').value,asset:chat.asset,pending:chat.pending,reply:chat.reply,focus:state.focus}));
}
function renderChat(){
  if(!state.data)return;
  const latest=state.data.versions.at(-1),view=selectedVersion();
  const old=latest?.id!==view?.id;
  $('#chatBase').textContent=view?'基于第 '+(state.data.versions.findIndex(v=>v.id===view.id)+1)+' 版':'尚无成图';
  const enabled=chat.config?.enabled;
  const active=(chat.data?.requests||[]).find(j=>j.status==='running');
  if(active){$('#execution').textContent='正在修改';$('#execution').className='badge running';}
  $('#chatHelp').textContent=!enabled?'当前为浏览模式，对话修改尚未启用。':old?'正在查看历史版本，返回最新版本后可继续修改。':'可点选右侧成图中的对象，再描述修改。';
  $('#chatSend').disabled=!enabled||!latest||old||chat.sending;
  $('#chatSend').textContent=chat.sending?'正在发送…':'发送修改 →';
  const objects=view?.objects||state.data.materials;
  setHTML($('#chatObject'),'<option value="">按描述定位</option>'+objects.map(o=>`<option value="${escapeHTML(o.id)}">${escapeHTML(o.label||o.id)}</option>`).join(''));
  $('#chatObject').value=state.focus||'';
  const selected=objects.find(o=>o.id===state.focus);
  setHTML($('#chatSelection'),selected?`<span>已选：${escapeHTML(selected.label||selected.id)}</span><button type="button" class="text-button" data-chat-clear>清除</button>`:'');
  setHTML($('#chatCatalog'),state.data.catalog.map((a,i)=>`<button type="button" data-chat-asset="${escapeHTML(a.id)}" class="${chat.asset===a.id?'selected':''}"><img src="${a.image||''}" alt="客户照片 ${i+1}" loading="lazy"><span>照片 ${i+1}</span></button>`).join(''));
  const index=state.data.catalog.findIndex(a=>a.id===chat.asset);
  $('#chatPhotoLabel').textContent=index<0?'':'· 已选照片 '+(index+1);
  const box=$('#chatMessages'),atEnd=box.scrollTop+box.clientHeight>=box.scrollHeight-35;
  setHTML(box,(chat.data?.requests||[]).map(j=>{
    const seconds=j.started_at ? (Date.parse(j.finished_at||new Date().toISOString())-Date.parse(j.started_at))/1000 : 0;
    return `<article class="chat-round"><div class="chat-customer">${escapeHTML(j.request.message)}</div><div class="chat-reply"><div class="chat-meta">${chatLabels[j.status]||j.status}${j.status==='queued'?` · 前方 ${Math.max(0,j.queue_position-1)} 条`:''}${j.started_at?' · 执行 '+chatSeconds(seconds):''}</div>${j.summary?`<p>${escapeHTML(j.summary)}</p>`:''}<p>${escapeHTML(j.message)}</p>${j.status==='running'?`<div class="chat-phases">${Object.entries(phaseLabels).map(([k,v])=>`<span class="${j.phase===k?'active':''}">${v}</span>`).join('')}</div>`:''}${j.status==='failed'?`<button type="button" class="text-button" data-chat-retry="${j.id}">重试本轮 →</button>`:''}${j.status==='clarify'?`<button type="button" class="text-button" data-chat-reply="${j.id}">补充说明 →</button>`:''}${j.status==='conflict'?`<button type="button" class="text-button" data-chat-rebase="${j.id}">查看最新版本并重发 →</button>`:''}${j.version_id?`<button type="button" class="text-button" data-chat-version="${j.version_id}">查看修改前后 →</button><small>${j.verdict==='pass'?'整图复核通过':'仍有待改问题，见复核记录'}</small>`:''}${Object.keys(j.timings||{}).length?`<details><summary>本轮耗时</summary><p>排队 ${chatSeconds(j.queue_seconds||0)}</p>${Object.entries(j.timings).map(([k,v])=>`<p>${phaseLabels[k]||k}：${v.toFixed(1)}秒</p>`).join('')}</details>`:''}</div></article>`;
  }).join('')||'<p class="subtle">成图交付后，仍可以在这里继续修改。</p>');
  if(atEnd)box.scrollTop=box.scrollHeight;
}
window.refreshChat=async function(){
  if(!state.id||!state.data)return;
  if(chat.task!==state.id){
    rememberChat();chat.task=state.id;chat.data=null;chat.asset=null;chat.reply=null;chat.pending=null;
    let saved={};try{saved=JSON.parse(localStorage.getItem(chatDraft())||'{}');}catch{}
    $('#chatInput').value=saved.text||'';chat.asset=saved.asset||null;chat.pending=saved.pending||null;chat.reply=saved.reply||null;
    if(saved.focus)state.focus=saved.focus;
    $('#chatError').textContent='';
  }
  renderChat();if(chat.fetching)return;chat.fetching=true;const id=state.id;
  try{
    if(!chat.config)chat.config=await api('/api/chat-config');
    const data=await api('/api/tasks/'+id+'/chat');
    if(id===state.id){chat.data=data;renderChat();}
  }catch{if(id===state.id)$('#chatError').textContent='对话连接暂时中断，内容已保留。';}
  finally{chat.fetching=false;}
};
async function chatPost(url,payload){
  for(let attempt=0;attempt<2;attempt++){
    if(!chat.config||attempt)chat.config=await api('/api/chat-config');
    const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Chat-Token':chat.config.token},body:JSON.stringify(payload)});
    const data=await response.json();if(response.ok)return data;
    if(response.status===403&&attempt===0)continue;
    throw Error(data.error||'发送失败');
  }
}
$('#chatInput').addEventListener('input',()=>{chat.pending=null;rememberChat();});
$('#chatObject').onchange=()=>{state.focus=$('#chatObject').value||null;highlight();renderChat();};
$('#chatForm').onsubmit=async e=>{
  e.preventDefault();if(chat.sending||$('#chatSend').disabled||!$('#chatInput').value.trim())return;
  const id=state.id;
  const payload=chat.pending||{request_id:crypto.randomUUID(),message:$('#chatInput').value.trim(),base_version_id:selectedVersion().id,selected_ids:state.focus?[state.focus]:[],asset_id:chat.asset,reply_to:chat.reply};
  chat.pending=payload;chat.sending=true;rememberChat();$('#chatError').textContent='';renderChat();
  try{
    await chatPost('/api/tasks/'+id+'/chat',payload);
    localStorage.removeItem('collage-chat-draft:'+id);
    if(id===state.id){$('#chatInput').value='';chat.pending=null;chat.reply=null;chat.asset=null;}
  }catch(err){if(id===state.id)$('#chatError').textContent=err.message;}
  finally{chat.sending=false;window.refreshChat();}
};
document.addEventListener('click',async e=>{
  const target=e.target.closest('[data-chat-asset],[data-chat-clear],[data-chat-version],[data-chat-retry],[data-chat-reply],[data-chat-rebase]');
  if(!target){if(e.target.closest('[data-focus]'))renderChat();return;}
  if(target.hasAttribute('data-chat-clear')){state.focus=null;highlight();renderChat();}
  if(target.dataset.chatAsset){chat.asset=chat.asset===target.dataset.chatAsset?null:target.dataset.chatAsset;chat.pending=null;rememberChat();renderChat();}
  if(target.dataset.chatVersion){state.version=target.dataset.chatVersion;state.stage=5;state.mode='before';render();}
  if(target.dataset.chatReply){chat.reply=target.dataset.chatReply;$('#chatInput').focus();rememberChat();}
  if(target.dataset.chatRebase){const job=chat.data.requests.find(j=>j.id===target.dataset.chatRebase);follow();$('#chatInput').value=job.request.message;chat.pending=null;chat.asset=job.request.asset_id;state.focus=job.request.selected_ids[0]||null;rememberChat();renderChat();}
  if(target.dataset.chatRetry){target.disabled=true;try{await chatPost(`/api/tasks/${state.id}/chat/${target.dataset.chatRetry}/retry`,{});window.refreshChat();}catch(err){$('#chatError').textContent=err.message;target.disabled=false;}}
});
let pick=null;
$('#canvas').addEventListener('pointerdown',e=>{const world=e.target.closest('.image-world');pick=world?{x:e.clientX,y:e.clientY,world}:null;});
$('#canvas').addEventListener('pointerup',e=>{
  if(!pick)return;const p=pick;pick=null;
  if(Math.hypot(e.clientX-p.x,e.clientY-p.y)>5||!['after','wipe'].includes(p.world.dataset.role))return;
  const r=p.world.getBoundingClientRect(),[w,h]=state.data.reference_size;
  const x=(e.clientX-r.left)/r.width*w,y=(e.clientY-r.top)/r.height*h;
  const choices=(selectedVersion()?.objects||state.data.materials).filter(o=>{const b=focusBounds(o.id,'after');return o.kind!=='background'&&b&&x>=b[0]&&x<=b[2]&&y>=b[1]&&y<=b[3];});
  choices.sort((a,b)=>(a.bbox[2]-a.bbox[0])*(a.bbox[3]-a.bbox[1])-(b.bbox[2]-b.bbox[0])*(b.bbox[3]-b.bbox[1]));
  if(choices.length){state.focus=choices[0].id;highlight();renderChat();}
});
window.refreshChat();
