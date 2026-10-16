"use strict";
const live = {config:null, fetching:false, data:null, task:null, video:null, cover:null, sending:false, upload:false};
const motionNames = {prepare:'固定封面',decode:'准备帧缓存',matte:'逐帧抠图',compose_encode:'解码、合成与编码',validate:'验证时长与来源'};
const motionStatuses = {queued:'排队中',running:'制作中',completed:'已完成',failed:'未完成'};
const motionSeconds = n => `${Number(n||0).toFixed(1)} 秒`;
const liveElapsed = row => row?.status==='running'&&row.started_at ? Math.max(0,(Date.now()-Date.parse(row.started_at))/1000) : row?.elapsed_seconds||0;

async function liveConfig(){
  live.config = await api('/api/chat-config');
  $('#openUpload').hidden = !live.config.motion_enabled;
  $('#uploadSubmit').textContent=live.config.create_enabled?'上传并开始制作':'上传并准备素材';
  $('#uploadAutoHint').hidden=!live.config.create_enabled;
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
  const coverIssues=record.cover_issues?.incomplete_objects?.length||record.cover_issues?.visual_verdict==='needs_changes';
  $('#motionCaption').textContent=`关联封面第 ${cover||'?'} 版 · ${motionSeconds(record.duration_us/1e6)} · ${record.frame_count} 帧 · 动态效果待复核${coverIssues?' · 沿用有待改问题的封面':''}${record.stale?' · 封面已修改，此视频为旧版':''}`;
}
function renderMotion(){
  const data=live.data;
  $('#motionPanel').hidden=!data?.videos.length;
  if(!data?.videos.length)return;
  const current=state.data.versions.at(-1), selected=selectedVersion();
  if(live.cover!==selected?.render_id){live.cover=selected?.render_id;live.video=null;}
  const jobs=data.jobs||[], active=jobs.find(j=>['queued','running'].includes(j.status));
  $('#motionSummary').textContent=data.render_blocker||`${data.videos.length} 段 Live 素材 · ${data.eligible_slots||0} 个动态照片槽 · 目标 ${motionSeconds(data.target_duration_us/1e6)} · 保留原视频帧率 · 短素材循环 · 静音${data.cover_warning?'。'+data.cover_warning:''}`;
  $('#renderMotion').disabled=!current||!data.eligible_slots||selected?.id!==current.id||!!active||live.sending||!!data.render_blocker;
  $('#renderMotion').textContent=data.render_blocker?'请先处理封面问题':active?.status==='queued'?'视频排队中…':active?'正在制作…':'制作 / 更新动态成片';
  setHTML($('#motionProgress'),jobs.slice(-3).map(job=>{
    const stages=job.status==='queued'?{}:job.progress?.stages||{};
    const elapsed=job.status==='queued'?`已等待 ${motionSeconds(job.waiting_seconds)}`:motionSeconds(liveElapsed(job.progress?.status?job.progress:job));
    const waiting=job.status==='queued'?`<p>视频队列第 ${job.queue_position||1} 位${job.blocked_by?` · 正在等待 ${escapeHTML(job.blocked_by.task_name)} 的视频完成`:''}；尚未开始渲染。</p>`:'';
    return `<article class="motion-job"><strong>${motionStatuses[job.status]||job.status}</strong> <span>${elapsed}</span>${waiting}${job.error?`<p class="chat-error">${escapeHTML(job.error)}</p>`:''}<div class="motion-stages">${Object.entries(motionNames).map(([key,name])=>{
      const stage=stages[key];
      return `<div class="${stage?.status||'pending'}"><span>${name}</span><small>${stage?.status==='failed'?'失败':stage?.status==='pending'?'等待':stage?stage.total?`${stage.done||0} / ${stage.total} 帧`:(stage.skipped?(key==='decode'?'直接解码合成，无需缓存':'无需抠图'):stage.status==='complete'?'完成':'处理中'):'等待'}${stage?' · '+motionSeconds(liveElapsed(stage)):''}</small></div>`;
    }).join('')}</div>${job.status==='failed'&&!data.render_blocker?`<button class="text-button" data-motion-retry="${escapeHTML(job.id)}">使用缓存重试 →</button>`:''}</article>`;
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
  if(live.task!==task){$('#motionPanel').hidden=true;$('#taskImportNotes').hidden=true;}
  try{
    if(!live.config)await liveConfig();
    if(!live.config.motion_enabled)return;
    if(live.config.create_enabled){
      const creation=await api(`/api/tasks/${task}/creation`);
      if(task!==state.id)return;
      const job=creation.job;
      $('#creationPanel').hidden=!job;
      if(job){
        const label=job.status==='completed'?(job.visual_verdict==='pass'?'首版已完成 · 复核通过':'首版已完成 · 有待改问题'):
          job.status==='paused'?'已暂停制作':job.status==='queued'?`首版排队中 · 队列第 ${job.queue_position} 位`:job.status==='running'?'正在自动制作首版':'制作中断 · 已保留完成的步骤';
        $('#creationStatus').textContent=label;
        $('#creationDetail').textContent=job.status==='paused'?'已保留上传素材，当前优先制作拼贴类。':job.error||`gpt-5.6-sol / medium · ${motionSeconds(liveElapsed(job))} · ${job.status==='completed'?'成图、对照和复核结论见下方。':'可收起页面，后台会继续制作。'}`;
        $('#creationRetry').hidden=job.status!=='failed';
      }
    }else $('#creationPanel').hidden=true;
    const value=await api(`/api/tasks/${task}/motion`);
    if(task!==state.id)return;
    $('#taskImportNotes').textContent=(value.import_notes||[]).join('\n');
    $('#taskImportNotes').hidden=!value.import_notes?.length;
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
$('#creationRetry').onclick=async()=>{
  const task=state.id;$('#creationRetry').disabled=true;
  try{await livePost(`/api/tasks/${task}/creation/retry`,{});await window.refreshMotion();}
  catch(e){$('#creationDetail').textContent=e.message;}
  finally{$('#creationRetry').disabled=false;}
};
$('#motionPanel').onclick=e=>{
  const retry=e.target.closest('[data-motion-retry]');
  if(retry)submitMotion(retry.dataset.motionRetry);
  const selected=e.target.closest('[data-motion-version]');
  if(selected)showMotionVideo(live.data.versions.find(v=>v.id===selected.dataset.motionVersion));
};
$('#openUpload').onclick=()=>$('#uploadDialog').showModal();
$('#closeUpload').onclick=()=>$('#uploadDialog').close();
const uploadImages=['png','jpg','jpeg','webp','bmp','heic','heif','hif','avif','tif','tiff','gif'];
const uploadVideoExtensions=['mp4','mov','m4v','webm'];
const selectedUploadMaterials=[];
const uploadExtension=file=>file.name.split('.').pop().toLowerCase();
const uploadIdentity=file=>JSON.stringify([file.name,file.size,file.lastModified]);
function renderUploadSelection(){
  const total=selectedUploadMaterials.reduce((sum,file)=>sum+file.size,0);
  $('#uploadSelectionCount').textContent=selectedUploadMaterials.length?`已选 ${selectedUploadMaterials.length} / 99 个素材 · ${(total/1024/1024).toFixed(1)} MB`:'尚未添加素材';
  $('#clearUploadMaterials').disabled=live.upload||!selectedUploadMaterials.length;
  $('#uploadMaterialPicker').disabled=live.upload;
  $('#uploadReferencePicker').disabled=live.upload;
  setHTML($('#uploadSelection'),selectedUploadMaterials.map((file,index)=>{
    const ext=uploadExtension(file),type=uploadVideoExtensions.includes(ext)?'视频':ext==='livp'?'Live 包':'图片';
    return `<li><span><small>${type}</small> ${escapeHTML(file.name)}</span><button type="button" data-remove-upload="${index}" aria-label="移除 ${escapeHTML(file.name)}">移除</button></li>`;
  }).join(''));
}
function addUploadMaterials(files){
  if(live.upload)return;
  const errors=[],seen=new Set(selectedUploadMaterials.map(uploadIdentity));
  let added=0,duplicates=0,total=selectedUploadMaterials.reduce((sum,file)=>sum+file.size,0)+($('#uploadReference').files[0]?.size||0);
  for(const file of files){
    if(seen.has(uploadIdentity(file))){duplicates++;continue;}
    let error='';
    if(![...uploadImages,...uploadVideoExtensions,'livp'].includes(uploadExtension(file)))error='暂不支持此格式';
    else if(!file.size||file.size>128*1024*1024)error='文件需要在 1 字节至 128 MB 之间';
    else if(selectedUploadMaterials.length>=99)error='本批最多添加 99 个客户素材';
    else if(total+file.size>1024**3)error='本批文件合计不能超过 1 GB';
    if(error){errors.push(`${file.name}：${error}`);continue;}
    selectedUploadMaterials.push(file);seen.add(uploadIdentity(file));total+=file.size;added++;
  }
  $('#uploadSelectionMessage').textContent=[added?`已添加 ${added} 个，可继续添加图片或视频。`:'',duplicates?`已跳过 ${duplicates} 个重复选择。`:'',...errors.slice(0,3),errors.length>3?`另有 ${errors.length-3} 个文件未添加。`:''].filter(Boolean).join('\n');
  renderUploadSelection();
}
$('#uploadMaterials').onchange=()=>{addUploadMaterials(Array.from($('#uploadMaterials').files));$('#uploadMaterials').value='';};
$('#uploadReference').onchange=()=>{
  const file=$('#uploadReference').files[0];
  if(!file){$('#uploadReferenceName').textContent='选择一张希望仿照的图片';return;}
  if(!uploadImages.includes(uploadExtension(file))||!file.size||file.size>128*1024*1024){
    $('#uploadReferenceName').textContent=`${file.name}：请选择不超过 128 MB 的图片作为参考图`;
    $('#uploadReference').value='';return;
  }
  $('#uploadReferenceName').textContent=file.name;
};
$('#clearUploadMaterials').onclick=()=>{if(live.upload)return;selectedUploadMaterials.length=0;$('#uploadSelectionMessage').textContent='';renderUploadSelection();};
$('#uploadSelection').onclick=e=>{
  const remove=e.target.closest('[data-remove-upload]');
  if(!remove||live.upload)return;
  selectedUploadMaterials.splice(Number(remove.dataset.removeUpload),1);$('#uploadSelectionMessage').textContent='';renderUploadSelection();
};
function uploadFile(batch,file,role,index,total){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();
    xhr.open('POST',`/api/uploads/${batch}/files`);
    xhr.timeout=300000;
    xhr.setRequestHeader('X-Chat-Token',live.config.token);
    xhr.setRequestHeader('X-File-Name',encodeURIComponent(file.name));
    xhr.setRequestHeader('X-Media-Role',role);
    const fail=message=>reject(Error(`${file.name}：${message}`));
    xhr.upload.onprogress=e=>{if(e.lengthComputable){
      $('#uploadProgress').value=100*(index+e.loaded/e.total)/total;
      $('#uploadStatus').textContent=`上传 ${index+1} / ${total}：${file.name} · ${Math.round(100*e.loaded/e.total)}%${e.loaded===e.total?' · 等待服务器确认':''}`;
    }};
    xhr.onerror=()=>fail('上传连接中断，请检查网络后重试');
    xhr.ontimeout=()=>fail('上传超过 5 分钟，请保持手机页面在前台后重试');
    xhr.onabort=()=>fail('上传已取消');
    xhr.onload=()=>{let value;try{value=JSON.parse(xhr.responseText);}catch{return fail(xhr.status===413?'文件超过入口大小限制，请缩小后上传':'服务器响应异常，请刷新后重试');}xhr.status>=200&&xhr.status<300?resolve(value):fail(value.error||'上传失败');};
    xhr.send(file);
  });
}
async function pollImport(identifier){
  while(true){
    const job=await api('/api/uploads/jobs/'+identifier);
    $('#uploadStatus').textContent=importStatus(job);
    $('#uploadProgress').hidden=job.status!=='running';
    if(job.status==='running'){
      const p=job.progress||{};
      if(p.stage==='video'&&p.total)$('#uploadProgress').value=100*p.done/p.total;
      else $('#uploadProgress').removeAttribute('value');
    }
    if(job.status==='failed')throw Error(job.error);
    if(job.status==='completed'){
      $('#uploadNotes').textContent=(job.notes||[]).join('\n');
      if(job.auto_create){
        localStorage.removeItem('collage-live-import');
        await refresh();selectTask(job.task_id);
        if(!job.notes?.length)$('#uploadDialog').close();
        return;
      }
      $('#uploadHandoff').hidden=false;$('#uploadHandoffText').value=job.codex_handoff;
      $('#uploadOpenTask').onclick=()=>{$('#uploadDialog').close();selectTask(job.task_id);};
      localStorage.removeItem('collage-live-import');await refresh();return;
    }
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
}
function importStatus(job){
  if(job.status==='completed')return job.auto_create?'素材准备完成，已进入自动制作队列':'素材准备完成，等待封面制作';
  if(job.status==='failed')return job.error;
  if(job.status==='queued')return `文件已上传，等待素材预处理${job.queue_position?` · 排队第 ${job.queue_position} 位`:''}${job.waiting_seconds?` · 已等待 ${motionSeconds(job.waiting_seconds)}`:''}${job.blocked_by?` · 前一批素材正在处理`:''}。无需重复上传。`;
  const p=job.progress||{},elapsed=p.elapsed_seconds?` · 已处理 ${motionSeconds(p.elapsed_seconds)}`:'';
  if(p.stage==='video')return `正在读取视频 ${p.done+1} / ${p.total}：${p.current||''}${p.frames_decoded?` · 已读 ${p.frames_decoded}${p.frames_total?' / '+p.frames_total:''} 帧`:''}${elapsed}`;
  if(p.stage==='normalize')return `正在读取和转换素材：${p.current||''}${elapsed}`;
  if(p.stage==='contact_sheets')return '正在生成照片缩略图和素材联系表…'+elapsed;
  if(p.stage==='complete')return '素材准备完成，正在登记任务…';
  return '已开始素材预处理，正在扫描文件…';
}
$('#uploadForm').onsubmit=async e=>{
  e.preventDefault();if(live.upload)return;live.upload=true;
  renderUploadSelection();
  $('#uploadSubmit').disabled=true;$('#uploadHandoff').hidden=true;$('#uploadProgress').hidden=false;$('#uploadProgress').value=0;$('#uploadNotes').textContent='';
  try{
    if(!selectedUploadMaterials.length)throw Error('请先添加至少一个客户素材，可以分别添加图片和视频');
    const files=[{file:$('#uploadReference').files[0],role:'reference'},...selectedUploadMaterials.map(file=>({file,role:'material'}))];
    if(files.some(v=>!v.file||!v.file.size||v.file.size>128*1024*1024)||files.length>100)throw Error('请检查素材数量和单个文件大小');
    const images=uploadImages;
    for(const {file,role} of files){
      const allowed=role==='reference'?images:[...images,'mp4','mov','m4v','webm','livp'];
      if(!allowed.includes(file.name.split('.').pop().toLowerCase()))throw Error(`${file.name}：${role==='reference'?'参考图需要图片格式':'暂不支持此格式，请选择下方列出的图片、视频或 LIVP'}`);
    }
    if(files.reduce((sum,v)=>sum+v.file.size,0)>1024**3)throw Error('一批素材合计不能超过 1 GB');
    const batch=await livePost('/api/uploads',{});
    for(let index=0;index<files.length;index++){
      $('#uploadStatus').textContent=`上传 ${index+1} / ${files.length}：${files[index].file.name}`;
      await uploadFile(batch.id,files[index].file,files[index].role,index,files.length);
    }
    const job=await livePost(`/api/uploads/${batch.id}/prepare`,{width:1200,instructions:$('#uploadInstructions').value});
    localStorage.setItem('collage-live-import',job.id);await pollImport(job.id);
  }catch(error){$('#uploadStatus').textContent=error.message;$('#uploadProgress').hidden=true;}
  finally{live.upload=false;$('#uploadSubmit').disabled=false;renderUploadSelection();}
};
liveConfig().then(()=>{
  const identifier=localStorage.getItem('collage-live-import');
  if(identifier&&live.config.motion_enabled){
    live.upload=true;$('#uploadSubmit').disabled=true;
    pollImport(identifier).catch(e=>{$('#uploadStatus').textContent=e.message;})
      .finally(()=>{live.upload=false;$('#uploadSubmit').disabled=false;});
  }
}).catch(()=>{});
