"use strict";
const editor={task:null,data:null,objects:[],selected:null,undo:[],redo:[],masks:new Map(),images:new Map(),busy:false,zoom:1,pan:[0,0],space:false,pending:null};
const copyEdit=x=>JSON.parse(JSON.stringify(x));
const editDraftKey=()=> 'collage-editor:'+editor.task;
const editStatus=message=>{$('#editorStatus').textContent=message;};
function editChanges(){
  if(!editor.data)return [];
  const used=new Set(),changes=[];
  for(const o of editor.objects){
    const base=editor.data.objects.find(b=>b.id===o.id),change={id:o.id};
    if(o.capabilities.move&&!used.has(o.id)&&(o.transform.x!==base.transform.x||o.transform.y!==base.transform.y)){
      change.transform=o.transform;o.capabilities.group.forEach(id=>used.add(id));
    }
    if(o.capabilities.text&&o.text!==base.text)change.text=o.text;
    if(Object.keys(change).length>1)changes.push(change);
  }
  return changes;
}
function rememberEdit(){
  if(!editor.task||!editor.data)return;
  try{localStorage.setItem(editDraftKey(),JSON.stringify({base:editor.data.base_version_id,objects:editor.objects.map(o=>({id:o.id,transform:o.transform,text:o.text})),pending:editor.pending}));}
  catch{editStatus('浏览器未能保存草稿，请保持页面打开并保存新版本。');}
  editControls();
}
function editControls(){
  const dirty=editChanges().length;
  $('#editorDirty').textContent=dirty?'有未保存修改':'与当前版本一致';
  $('#editorUndo').disabled=editor.busy||!editor.undo.length;$('#editorRedo').disabled=editor.busy||!editor.redo.length;
  $('#editorSave').disabled=editor.busy||!dirty;$('#editorPreview').disabled=editor.busy||!dirty;
  $('#editorReload').disabled=editor.busy;$('#editorText').disabled=editor.busy;$('#editorObjects').disabled=editor.busy;
}
function checkpointEdit(){editor.undo.push(copyEdit(editor.objects));editor.redo=[];editor.pending=null;}
function editSelection(){
  const o=editor.objects.find(o=>o.id===editor.selected);
  $('#editorObjects').value=o?.id||'';
  $('#editorSelection').textContent=o?o.label+(o.capabilities.group.length>1?' · 照片与关联素材整组移动':''):'';
  $('#editorTextLabel').hidden=!o?.capabilities.text;
  if(document.activeElement!==$('#editorText'))$('#editorText').value=o?.text||'';
  let box=$('#editorSvg').querySelector('.editor-selection');if(box)box.remove();
  if(!o)return;
  box=document.createElementNS('http://www.w3.org/2000/svg','rect');box.classList.add('editor-selection');
  const group=editor.objects.filter(n=>o.capabilities.group.includes(n.id));
  const l=Math.min(...group.map(n=>n.bbox[0]+n.transform.x)),t=Math.min(...group.map(n=>n.bbox[1]+n.transform.y));
  const r=Math.max(...group.map(n=>n.bbox[2]+n.transform.x)),b=Math.max(...group.map(n=>n.bbox[3]+n.transform.y));
  for(const [k,v] of Object.entries({x:l,y:t,width:r-l,height:b-t,fill:'none',stroke:'#16886c','stroke-width':2,'vector-effect':'non-scaling-stroke','pointer-events':'none'}))box.setAttribute(k,v);
  $('#editorSvg').append(box);
}
function positionEdit(){
  for(const image of $('#editorSvg').querySelectorAll('image')){
    const o=editor.objects.find(o=>o.id===image.dataset.object);image.setAttribute('transform',`translate(${o.transform.x} ${o.transform.y})`);
    if(o.capabilities.text){const cached=editor.images.get(JSON.stringify([o.id,o.text]));if(cached){image.setAttribute('href',cached.image);editor.masks.set(o.id,cached.mask);}}
  }
  editSelection();editControls();
}
function zoomEdit(){
  if(!editor.data)return;
  const [w,h]=editor.data.reference_size,v=$('#editorViewport');
  const fit=Math.min((v.clientWidth-40)/w,(v.clientHeight-40)/h);
  Object.assign($('#editorSvg').style,{width:w*fit*editor.zoom+'px',height:h*fit*editor.zoom+'px',transform:`translate(${editor.pan[0]}px,${editor.pan[1]}px)`});
}
async function drawEdit(data){
  const svg=$('#editorSvg');svg.replaceChildren();editor.masks.clear();
  const [w,h]=data.reference_size;svg.setAttribute('viewBox',`0 0 ${w} ${h}`);
  await Promise.all(data.objects.map(async o=>{
    const node=document.createElementNS('http://www.w3.org/2000/svg','image');node.dataset.object=o.id;
    node.setAttribute('href',o.image);node.setAttribute('width',w);node.setAttribute('height',h);node.setAttribute('pointer-events','none');svg.append(node);
    const image=new Image();image.src=o.image;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=Math.min(512,w);canvas.height=Math.round(canvas.width*h/w);
    const context=canvas.getContext('2d',{willReadFrequently:true});context.drawImage(image,0,0,canvas.width,canvas.height);
    const mask={width:canvas.width,height:canvas.height,rgba:context.getImageData(0,0,canvas.width,canvas.height).data};
    editor.masks.set(o.id,mask);editor.images.set(JSON.stringify([o.id,o.text]),{image:o.image,mask});
  }));
  $('#editorObjects').replaceChildren(new Option('点击画面或选择对象',''),...editor.objects.filter(o=>o.capabilities.move||o.capabilities.text).map(o=>new Option(o.label,o.id)));
  zoomEdit();positionEdit();
}
function hitEdit(point){
  const [w,h]=editor.data.reference_size;
  for(const o of [...editor.objects].reverse()){
    if(!o.capabilities.move&&!o.capabilities.text)continue;
    const m=editor.masks.get(o.id);if(!m)continue;
    const x=Math.floor((point.x-o.transform.x)/w*m.width),y=Math.floor((point.y-o.transform.y)/h*m.height);
    if(x>=0&&y>=0&&x<m.width&&y<m.height&&m.rgba[(y*m.width+x)*4+3]>20)return o;
  }
  return null;
}
function pointEdit(e){return new DOMPoint(e.clientX,e.clientY).matrixTransform($('#editorSvg').getScreenCTM().inverse());}
async function loadEdit(restore=true){
  editor.busy=true;editControls();editStatus('正在准备可编辑图层…');
  try{
    const data=await api('/api/tasks/'+editor.task+'/editor');editor.data=data;editor.objects=copyEdit(data.objects);editor.undo=[];editor.redo=[];editor.selected=null;editor.pending=null;editor.images.clear();
    let saved=null;try{saved=JSON.parse(localStorage.getItem(editDraftKey()));}catch{}
    let stale=false;
    if(restore&&saved?.base===data.base_version_id){
      editor.undo.push(copyEdit(editor.objects));
      for(const o of editor.objects){const draft=saved.objects.find(n=>n.id===o.id);if(draft){o.transform=draft.transform;o.text=draft.text;}}
      editor.pending=saved.pending||null;
    }else if(restore&&saved){stale=true;localStorage.setItem(editDraftKey()+':previous',JSON.stringify(saved));}
    editor.zoom=1;editor.pan=[0,0];await drawEdit(data);$('#editorResult').hidden=true;
    editStatus(stale?'已有旧版本草稿，已另存于本机；当前显示最新版本，请重新核对后编辑。':'可拖动照片或贴纸。独立文字支持双击编辑。');
  }catch(error){editStatus('加载失败：'+error.message);}
  finally{editor.busy=false;editControls();}
}
$('#openEditor').onclick=async()=>{
  if(editor.busy){$('#editorDialog').showModal();return;}
  if(editor.task!==state.id){editor.data=null;editor.objects=[];editor.selected=null;$('#editorSvg').replaceChildren();}
  editor.task=state.id;$('#editorDialog').showModal();await loadEdit();
};
$('#closeEditor').onclick=()=>{rememberEdit();$('#editorDialog').close();};
$('#editorDialog').addEventListener('cancel',()=>rememberEdit());
$('#editorReload').onclick=async()=>{
  const previous=localStorage.getItem(editDraftKey());if(previous)localStorage.setItem(editDraftKey()+':previous',previous);
  await loadEdit(false);rememberEdit();
};
$('#editorObjects').onchange=()=>{editor.selected=$('#editorObjects').value;editSelection();};
let editTextTransaction=null;
$('#editorText').addEventListener('blur',()=>{editTextTransaction=null;});
$('#editorText').addEventListener('input',()=>{const o=editor.objects.find(o=>o.id===editor.selected);if(!editor.busy&&o?.capabilities.text){if(editTextTransaction!==o.id){checkpointEdit();editTextTransaction=o.id;}o.text=$('#editorText').value;editor.pending=null;rememberEdit();editStatus('文字草稿已更新，点击预览查看实际排版。');}});
$('#editorUndo').onclick=()=>{if(!editor.undo.length||editor.busy)return;editor.redo.push(copyEdit(editor.objects));editor.objects=editor.undo.pop();editor.pending=null;positionEdit();rememberEdit();};
$('#editorRedo').onclick=()=>{if(!editor.redo.length||editor.busy)return;editor.undo.push(copyEdit(editor.objects));editor.objects=editor.redo.pop();editor.pending=null;positionEdit();rememberEdit();};
$('#editorFit').onclick=()=>{editor.zoom=1;editor.pan=[0,0];zoomEdit();};
let editDrag=null;
$('#editorViewport').addEventListener('pointerdown',e=>{
  if(editor.busy||!editor.data||e.button!==0)return;
  if(editor.space){editDrag={pan:true,start:[e.clientX,e.clientY],value:[...editor.pan]};}
  else{
    const point=pointEdit(e),o=hitEdit(point);editor.selected=o?.id||null;editSelection();
    if(!o?.capabilities.move)return;
    editDrag={point,objects:copyEdit(editor.objects),moved:false,group:o.capabilities.group};
  }
  e.preventDefault();$('#editorViewport').setPointerCapture(e.pointerId);
});
$('#editorViewport').addEventListener('pointermove',e=>{
  if(!editDrag)return;
  if(editDrag.pan){editor.pan=[editDrag.value[0]+e.clientX-editDrag.start[0],editDrag.value[1]+e.clientY-editDrag.start[1]];zoomEdit();return;}
  const point=pointEdit(e),dx=point.x-editDrag.point.x,dy=point.y-editDrag.point.y;
  if(!editDrag.moved&&Math.hypot(dx,dy)<2)return;
  if(!editDrag.moved){checkpointEdit();editDrag.moved=true;}
  for(const o of editor.objects)if(editDrag.group.includes(o.id)){
    const original=editDrag.objects.find(n=>n.id===o.id);o.transform={x:Math.round((original.transform.x+dx)*1000)/1000,y:Math.round((original.transform.y+dy)*1000)/1000};
  }
  positionEdit();
});
let lastEditTap=null;
function endEditDrag(event){
  if(editDrag?.moved)rememberEdit();
  else if(editDrag&&!editDrag.pan&&event?.type==='pointerup'){
    const o=editor.objects.find(o=>o.id===editor.selected),at=performance.now();
    if(o?.capabilities.text&&lastEditTap?.id===o.id&&at-lastEditTap.at<450){$('#editorText').focus();$('#editorText').select();lastEditTap=null;}
    else lastEditTap={id:o?.id,at};
  }
  editDrag=null;
}
$('#editorViewport').addEventListener('pointerup',endEditDrag);$('#editorViewport').addEventListener('pointercancel',endEditDrag);
$('#editorViewport').addEventListener('dblclick',e=>{if(editor.busy)return;const o=hitEdit(pointEdit(e));if(o?.capabilities.text){editor.selected=o.id;editSelection();$('#editorText').focus();$('#editorText').select();}});
$('#editorViewport').addEventListener('wheel',e=>{if(!editor.data)return;e.preventDefault();editor.zoom=Math.min(5,Math.max(.4,editor.zoom*(e.deltaY<0?1.1:.9)));zoomEdit();},{passive:false});
document.addEventListener('keydown',e=>{
  if(!$('#editorDialog').open||e.target.matches('input,textarea,select'))return;
  if(e.code==='Space'){e.preventDefault();editor.space=true;}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();$(e.shiftKey?'#editorRedo':'#editorUndo').click();}
});
document.addEventListener('keyup',e=>{if(e.code==='Space')editor.space=false;});window.addEventListener('blur',()=>{editor.space=false;endEditDrag();});
new ResizeObserver(zoomEdit).observe($('#editorViewport'));
async function submitEdit(action){
  if(editor.busy||!editChanges().length)return;
  editor.busy=true;editControls();editStatus(action==='save'?'正在保存并渲染新版本…':'正在生成实际文字与成图预览…');
  const task=editor.task;
  const payload=editor.pending?.action===action?editor.pending.payload:{request_id:'edit-'+crypto.randomUUID(),base_version_id:editor.data.base_version_id,changes:editChanges()};
  editor.pending={action,payload};rememberEdit();
  try{
    await chatPost(`/api/tasks/${task}/editor/${action}`,payload);
    let job;
    do{await new Promise(resolve=>setTimeout(resolve,650));job=await api(`/api/tasks/${task}/editor/jobs/${payload.request_id}`);}while(['queued','running'].includes(job.status));
    if(job.status!=='completed')throw Error(job.error||'本轮未完成，可以重试');
    editor.pending=null;
    if(action==='preview'){
      await drawEdit(job.preview);$('#editorResult').src=job.preview.image;$('#editorResult').hidden=false;
      editStatus(`预览已更新 · ${job.elapsed_seconds.toFixed(1)}秒 · 尚未保存版本`);rememberEdit();
    }else{
      localStorage.removeItem(editDraftKey());await loadEdit(false);rememberEdit();
      editStatus(`已保存新版本 · ${job.elapsed_seconds.toFixed(1)}秒 · 手动修改，未重新复核`);
      if(state.id===task){state.version=null;state.stage=null;state.canvasKey='';await refresh();}
    }
  }catch(error){editStatus('未完成：'+error.message+'。草稿已保留，可重试或重新载入最新版本。');}
  finally{editor.busy=false;editControls();}
}
$('#editorPreview').onclick=()=>submitEdit('preview');$('#editorSave').onclick=()=>submitEdit('save');
api('/api/chat-config').then(config=>{$('#openEditor').hidden=!config.editor_enabled;}).catch(()=>{});
