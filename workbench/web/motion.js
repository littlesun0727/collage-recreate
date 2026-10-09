"use strict";
const live = {config:null, fetching:false, data:null, task:null, video:null, cover:null, sending:false, upload:false};
const motionNames = {prepare:'固定封面',decode:'准备视频帧',matte:'逐帧抠图',compose_encode:'合成与编码',validate:'验证时长与来源'};
const motionStatuses = {queued:'排队中',running:'制作中',completed:'已完成',failed:'未完成'};
const motionSeconds = n => `${Number(n||0).toFixed(1)} 秒`;
const liveElapsed = row => row?.status==='running'&&row.started_at ? Math.max(0,(Date.now()-Date.parse(row.started_at))/1000) : row?.elapsed_seconds||0;

async function liveConfig(){
  live.config = await api('/api/chat-config');
  $('#openUpload').hidden = !live.config.motion_enabled;
  return live.config;
}
async function livePost(url, payload){
  const config = await liveConfig();
  const response = await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Chat-Token':config.token},body:JSON.stringify(payload)});
  const value = await response.json();
  if(!response.ok)throw Error(value.error||'请求失败');
  return value;
}
function showMotionVideo(record){
  if(!record)return;
  live.video=record.id;
  const video=$('#motionVideo');
  if(video.getAttribute('src')!==record.url)video.src=record.url;
  $('#motionPlayer').hidden=false;
  $('#motionDownload').href=record.url+'?download=1';
  const cover=state.data.versions.findIndex(v=>v.render_id===record.base_render_id)+1;
  $('#motionCaption').textContent=`关联封面第 ${cover||'?'} 版 · ${motionSeconds(record.duration_us/1e6)} · ${record.frame_count} 帧 · 动态效果待复核${record.stale?' · 封面已修改，此视频为旧版':''}`;
}
function renderMotion(){
  const data=live.data;
  $('#motionPanel').hidden=!data?.videos.length;
  if(!data?.videos.length)return;
  const current=state.data.versions.at(-1), selected=selectedVersion();
  if(live.cover!==selected?.render_id){live.cover=selected?.render_id;live.video=null;}
  const jobs=data.jobs||[], active=jobs.find(j=>['queued','running'].includes(j.status));
  $('#motionSummary').textContent=`${data.videos.length} 段 Live 素材 · ${data.eligible_slots||0} 个动态照片槽 · 目标 ${motionSeconds(data.target_duration_us/1e6)} · 保留原视频帧率 · 短素材循环 · 静音`;
  $('#renderMotion').disabled=!current||!data.eligible_slots||selected?.id!==current.id||!!active||live.sending;
  $('#renderMotion').textContent=active?'正在制作…':'制作 / 更新动态成片';
  setHTML($('#motionProgress'),jobs.slice(-3).map(job=>{
    const stages=job.progress?.stages||{};
    return `<article class="motion-job"><strong>${motionStatuses[job.status]||job.status}</strong> <span>${motionSeconds(liveElapsed(job.progress||job))}</span>${job.error?`<p class="chat-error">${escapeHTML(job.error)}</p>`:''}<div class="motion-stages">${Object.entries(motionNames).map(([key,name])=>{
      const stage=stages[key];
      return `<div class="${stage?.status||'pending'}"><span>${name}</span><small>${stage?stage.total?`${stage.done||0} / ${stage.total} 帧`:(stage.skipped?'无需抠图':stage.status==='complete'?'完成':'处理中'):'等待'}${stage?' · '+motionSeconds(liveElapsed(stage)):''}</small></div>`;
    }).join('')}</div>${job.status==='failed'?`<button class="text-button" data-motion-retry="${escapeHTML(job.id)}">使用缓存重试 →</button>`:''}</article>`;
  }).join(''));
  setHTML($('#motionVersions'),data.versions.map(v=>`<button class="button" data-motion-version="${escapeHTML(v.id)}">${escapeHTML(v.at.slice(0,19).replace('T',' '))} · ${v.stale?'旧封面':'当前封面'} · ${motionSeconds(v.duration_us/1e6)}</button>`).join(''));
  setHTML($('#liveMaterials'),data.videos.map(v=>`<article><video controls muted playsinline preload="none" poster="${v.poster||''}" src="${v.url}"></video><p>${escapeHTML(v.name)}</p><small>${motionSeconds(v.duration_us/1e6)} · ${v.frame_count} 个原始帧</small></article>`).join(''));
  const viewed=live.video&&data.versions.find(v=>v.id===live.video);
  const match=data.versions.filter(v=>v.base_render_id===selected?.render_id).at(-1);
  if(viewed)showMotionVideo(viewed);
  else if(match)showMotionVideo(match);
  else if(data.versions.length)showMotionVideo(data.versions.at(-1));
  else $('#motionPlayer').hidden=true;
}
window.refreshMotion=async function(){
  if(!state.id||!state.data||live.fetching)return;
  live.fetching=true;const task=state.id;
  if(live.task!==task)$('#motionPanel').hidden=true;
  try{
    if(!live.config)await liveConfig();
    if(!live.config.motion_enabled)return;
    const value=await api(`/api/tasks/${task}/motion`);
    if(task!==state.id)return;
    if(live.task!==task){live.video=null;$('#motionVideo').removeAttribute('src');$('#motionVideo').load();$('#motionError').textContent='';}
    live.task=task;live.data=value;renderMotion();
  }catch(e){$('#motionError').textContent=e.message;}
  finally{live.fetching=false;}
};
async function submitMotion(identifier){
  const version=state.data.versions.at(-1);if(!version)return;
  live.sending=true;$('#motionError').textContent='';renderMotion();
  try{
    await livePost(`/api/tasks/${state.id}/motion/render`,{request_id:identifier||'motion-'+crypto.randomUUID(),base_version_id:version.id});
    await window.refreshMotion();
  }catch(e){$('#motionError').textContent=e.message;}
  finally{live.sending=false;renderMotion();}
}
$('#renderMotion').onclick=()=>submitMotion();
$('#motionPanel').onclick=e=>{
  const retry=e.target.closest('[data-motion-retry]');
  if(retry)submitMotion(retry.dataset.motionRetry);
  const selected=e.target.closest('[data-motion-version]');
  if(selected)showMotionVideo(live.data.versions.find(v=>v.id===selected.dataset.motionVersion));
};
$('#openUpload').onclick=()=>$('#uploadDialog').showModal();
$('#closeUpload').onclick=()=>$('#uploadDialog').close();
function uploadFile(batch,file,role,index,total){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();
    xhr.open('POST',`/api/uploads/${batch}/files`);
    xhr.setRequestHeader('X-Chat-Token',live.config.token);
    xhr.setRequestHeader('X-File-Name',encodeURIComponent(file.name));
    xhr.setRequestHeader('X-Media-Role',role);
    xhr.upload.onprogress=e=>{if(e.lengthComputable)$('#uploadProgress').value=100*(index+e.loaded/e.total)/total;};
    xhr.onerror=()=>reject(Error('上传连接中断，请重新提交素材'));
    xhr.onload=()=>{let value;try{value=JSON.parse(xhr.responseText);}catch{return reject(Error('上传响应无效'));}xhr.status<300?resolve(value):reject(Error(value.error||'上传失败'));};
    xhr.send(file);
  });
}
async function pollImport(identifier){
  while(true){
    const job=await api('/api/uploads/jobs/'+identifier);
    $('#uploadStatus').textContent=job.status==='completed'?'素材准备完成，等待封面制作':job.status==='failed'?job.error:'正在准备代表帧和素材联系表…';
    if(job.status==='failed')throw Error(job.error);
    if(job.status==='completed'){
      $('#uploadHandoff').hidden=false;$('#uploadHandoffText').value=job.codex_handoff;
      $('#uploadOpenTask').onclick=()=>{$('#uploadDialog').close();selectTask(job.task_id);};
      localStorage.removeItem('collage-live-import');await refresh();return;
    }
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
}
$('#uploadForm').onsubmit=async e=>{
  e.preventDefault();if(live.upload)return;live.upload=true;
  $('#uploadSubmit').disabled=true;$('#uploadHandoff').hidden=true;$('#uploadProgress').hidden=false;
  try{
    const files=[{file:$('#uploadReference').files[0],role:'reference'},...Array.from($('#uploadMaterials').files).map(file=>({file,role:'material'}))];
    if(files.some(v=>!v.file||v.file.size>128*1024*1024)||files.length>100)throw Error('请检查素材数量和单个文件大小');
    const batch=await livePost('/api/uploads',{});
    for(let index=0;index<files.length;index++){
      $('#uploadStatus').textContent=`上传 ${index+1} / ${files.length}：${files[index].file.name}`;
      await uploadFile(batch.id,files[index].file,files[index].role,index,files.length);
    }
    const job=await livePost(`/api/uploads/${batch.id}/prepare`,{width:1200,instructions:$('#uploadInstructions').value});
    localStorage.setItem('collage-live-import',job.id);await pollImport(job.id);
  }catch(error){$('#uploadStatus').textContent=error.message;}
  finally{live.upload=false;$('#uploadSubmit').disabled=false;}
};
liveConfig().then(()=>{
  const identifier=localStorage.getItem('collage-live-import');
  if(identifier&&live.config.motion_enabled)pollImport(identifier).catch(e=>{$('#uploadStatus').textContent=e.message;});
}).catch(()=>{});
