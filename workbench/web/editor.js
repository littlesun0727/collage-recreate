"use strict";
const editor={task:null,data:null,objects:[],selected:null,undo:[],redo:[],masks:new Map(),images:new Map(),busy:false,zoom:1,pan:[0,0],space:false,pending:null};
const copyEdit=x=>JSON.parse(JSON.stringify(x));
const editDraftKey=()=> 'collage-editor:'+editor.task;
const editStatus=message=>{$('#editorStatus').textContent=message;};
const editTransform=o=>({x:0,y:0,rotation:0,scale:1,...o.transform});
const editCenter=o=>[(o.bbox[0]+o.bbox[2])/2,(o.bbox[1]+o.bbox[3])/2];
const editMembers=o=>$('#editorTransformScope').value==='group'?o.capabilities.group:[o.id];
const editImageKey=o=>JSON.stringify([o.id,o.text,o.source_crop||[0,0,1,1]]);
function editMatrix(o){const v=editTransform(o),[cx,cy]=editCenter(o),a=v.rotation*Math.PI/180,c=Math.cos(a)*v.scale,s=Math.sin(a)*v.scale;return new DOMMatrix([c,s,-s,c,cx+v.x-c*cx+s*cy,cy+v.y-s*cx-c*cy]);}
function editBounds(o){const v=editTransform(o),[cx,cy]=editCenter(o),a=((o.rotation||0)+v.rotation)*Math.PI/180,c=Math.abs(Math.cos(a))*v.scale,s=Math.abs(Math.sin(a))*v.scale,w=o.bbox[2]-o.bbox[0],h=o.bbox[3]-o.bbox[1];return [cx+v.x-(w*c+h*s)/2,cy+v.y-(w*s+h*c)/2,cx+v.x+(w*c+h*s)/2,cy+v.y+(w*s+h*c)/2];}
function editGroupBox(o){const boxes=editor.objects.filter(n=>!n.removed&&editMembers(o).includes(n.id)).map(editBounds);return [Math.min(...boxes.map(b=>b[0])),Math.min(...boxes.map(b=>b[1])),Math.max(...boxes.map(b=>b[2])),Math.max(...boxes.map(b=>b[3]))];}
function editChanges(){
  if(!editor.data)return [];
  const changes=[];
  for(const o of editor.objects){
    const base=editor.data.objects.find(b=>b.id===o.id),change={id:o.id};
    if(o.removed){changes.push({id:o.id,remove:true});continue;}
    if(o.capabilities.move&&JSON.stringify(editTransform(o))!==JSON.stringify(editTransform(base))){
      change.transform=o.transform;change.scope='object';
    }
    if(o.capabilities.text&&o.text!==base.text)change.text=o.text;
    if(o.capabilities.crop&&JSON.stringify(o.source_crop)!==JSON.stringify(base.source_crop))change.source_crop=o.source_crop;
    if(o.capabilities.crop&&JSON.stringify(o.window_crop)!==JSON.stringify(base.window_crop))change.window_crop=o.window_crop;
    if(Object.keys(change).length>1)changes.push(change);
  }
  return changes;
}
function editOrder(){
  if(!editor.data)return null;
  const alive=editor.objects.filter(o=>!o.removed),ids=new Set(alive.map(o=>o.id));
  if(JSON.stringify(alive.map(o=>o.id))===JSON.stringify(editor.data.objects.filter(o=>ids.has(o.id)).map(o=>o.id)))return null;
  return alive.flatMap(o=>o.capabilities.remove_ids||[o.id]);
}
function rememberEdit(){
  if(!editor.task||!editor.data)return;
  try{localStorage.setItem(editDraftKey(),JSON.stringify({base:editor.data.base_version_id,objects:editor.objects.map(o=>({id:o.id,transform:o.transform,text:o.text,source_crop:o.source_crop,window_crop:o.window_crop,removed:!!o.removed})),pending:editor.pending}));}
  catch{editStatus('浏览器未能保存草稿，请保持页面打开并保存新版本。');}
  editControls();
}
function editControls(){
  const dirty=editChanges().length||editOrder();
  $('#editorDirty').textContent=dirty?'有未保存修改':'与当前版本一致';
  $('#editorUndo').disabled=editor.busy||!editor.undo.length;$('#editorRedo').disabled=editor.busy||!editor.redo.length;
  $('#editorSave').disabled=editor.busy||!dirty;$('#editorPreview').disabled=editor.busy||!dirty;
  $('#editorReload').disabled=editor.busy;$('#editorText').disabled=editor.busy;$('#editorObjects').disabled=editor.busy;
  const o=editor.objects.find(o=>o.id===editor.selected&&!o.removed);
  $('#editorTransformScope').disabled=editor.busy||!o?.capabilities.move;
  $('#editorCrop').disabled=editor.busy||!o?.capabilities.crop||!o?.crop_image;
  $('#editorWindowCrop').disabled=editor.busy||!o?.capabilities.crop;
  $('#editorWindowReset').disabled=editor.busy||!o?.capabilities.crop;
  for(const id of ['editorAngle','editorScale','editorSmaller','editorLarger'])$('#'+id).disabled=editor.busy||!o?.capabilities.move;
  $('#editorDelete').disabled=editor.busy||!o?.capabilities.remove;
  $('#editorDeleteGroup').disabled=editor.busy||!o?.capabilities.remove||o.capabilities.group.length<2;
  const alive=editor.objects.filter(n=>!n.removed),members=alive.map((n,i)=>o?.capabilities.group.includes(n.id)?i:-1).filter(i=>i>=0);
  for(const id of ['editorLayerUp','editorLayerTop'])$('#'+id).disabled=editor.busy||!o||Math.max(...members)===alive.length-1;
  for(const id of ['editorLayerDown','editorLayerBottom'])$('#'+id).disabled=editor.busy||!o||Math.min(...members)===0;
}
function checkpointEdit(){editor.undo.push(copyEdit(editor.objects));editor.redo=[];editor.pending=null;}
function editSelection(){
  const o=editor.objects.find(o=>o.id===editor.selected&&!o.removed);
  $('#editorObjects').value=o?.id||'';
  $('#editorSelection').textContent=o?o.label+(o.capabilities.group.length>1?(editMembers(o).length>1?' · 照片与关联素材一起变换':' · 仅变换当前对象'):'')+(o.capabilities.remove_ids?.length>1?' · 此素材内的文字或图案已合并，随素材一起移动':''):'';
  if(document.activeElement!==$('#editorAngle'))$('#editorAngle').value=o?Math.round(editTransform(o).rotation*10)/10:0;
  if(document.activeElement!==$('#editorScale'))$('#editorScale').value=o?Math.round(editTransform(o).scale*1000)/10:100;
  $('#editorTextLabel').hidden=!o?.capabilities.text;
  if(document.activeElement!==$('#editorText'))$('#editorText').value=o?.text||'';
  $('#editorSvg').querySelectorAll('.editor-selection,.editor-handle').forEach(n=>n.remove());
  editControls();
  if(!o)return;
  if(typeof windowCropMode!=='undefined'&&windowCropMode&&o.capabilities.crop){drawWindowCrop(o);return;}
  const box=document.createElementNS('http://www.w3.org/2000/svg','rect');box.classList.add('editor-selection');
  const [l,t,r,b]=editGroupBox(o);
  for(const [k,v] of Object.entries({x:l,y:t,width:r-l,height:b-t,fill:'none',stroke:'#16886c','stroke-width':2,'vector-effect':'non-scaling-stroke','pointer-events':'none'}))box.setAttribute(k,v);
  $('#editorSvg').append(box);
  if(o.capabilities.move){
    const unit=1/Math.max(.05,$('#editorSvg').getScreenCTM()?.a||1);
    for(const [kind,x,y] of [['rotate',(l+r)/2,t-24*unit],['scale',r,b]]){
      const handle=document.createElementNS('http://www.w3.org/2000/svg','circle');handle.classList.add('editor-handle');handle.dataset.handle=kind;
      for(const [key,value] of Object.entries({cx:x,cy:y,r:7*unit,fill:'white',stroke:'#16886c','stroke-width':2,'vector-effect':'non-scaling-stroke'}))handle.setAttribute(key,value);
      handle.style.cursor=kind==='rotate'?'grab':'nwse-resize';$('#editorSvg').append(handle);
    }
  }
}
function positionEdit(){
  const nodes=new Map(Array.from($('#editorSvg').querySelectorAll('image')).map(n=>[n.dataset.object,n]));
  for(const o of editor.objects){const node=nodes.get(o.id);if(node)node.parentNode.append(node);}
  for(const image of $('#editorSvg').querySelectorAll('image')){
    const o=editor.objects.find(o=>o.id===image.dataset.object);image.style.display=o.removed?'none':'';image.setAttribute('transform',editMatrix(o).toString());
    if(o.capabilities.text||o.capabilities.crop){const cached=editor.images.get(editImageKey(o));if(cached){image.setAttribute('href',cached.image);editor.masks.set(o.id,cached.mask);}}
  }
  if(typeof updateWindowClips==='function')updateWindowClips();
  $('#editorObjects').replaceChildren(new Option('点击画面或选择对象',''),...editor.objects.filter(o=>!o.removed&&(o.capabilities.move||o.capabilities.text||o.capabilities.remove)).map(o=>new Option(o.label,o.id)));
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
  const ns='http://www.w3.org/2000/svg',defs=document.createElementNS(ns,'defs'),clip=document.createElementNS(ns,'clipPath'),rect=document.createElementNS(ns,'rect'),layerGroup=document.createElementNS(ns,'g');
  clip.id='editorCanvasClip';rect.setAttribute('width',w);rect.setAttribute('height',h);clip.append(rect);defs.append(clip);svg.append(defs);layerGroup.setAttribute('clip-path','url(#editorCanvasClip)');svg.append(layerGroup);
  const layers=editor.data.objects.map(base=>data.objects.find(o=>o.id===base.id)||base);
  await Promise.all(layers.map(async o=>{
    const node=document.createElementNS('http://www.w3.org/2000/svg','image');node.dataset.object=o.id;
    node.setAttribute('href',o.image);node.setAttribute('width',w);node.setAttribute('height',h);node.setAttribute('pointer-events','none');layerGroup.append(node);
    const image=new Image();image.src=o.image;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=Math.min(512,w);canvas.height=Math.round(canvas.width*h/w);
    const context=canvas.getContext('2d',{willReadFrequently:true});context.drawImage(image,0,0,canvas.width,canvas.height);
    const mask={width:canvas.width,height:canvas.height,rgba:context.getImageData(0,0,canvas.width,canvas.height).data};
    editor.masks.set(o.id,mask);editor.images.set(editImageKey(o),{image:o.image,mask});
  }));
  $('#editorObjects').replaceChildren(new Option('点击画面或选择对象',''),...editor.objects.filter(o=>o.capabilities.move||o.capabilities.text).map(o=>new Option(o.label,o.id)));
  zoomEdit();positionEdit();
}
function hitEdit(point){
  const [w,h]=editor.data.reference_size;
  for(const o of [...editor.objects].reverse()){
    if(o.removed||(!o.capabilities.move&&!o.capabilities.text))continue;
    const m=editor.masks.get(o.id);if(!m)continue;
    const local=new DOMPoint(point.x,point.y).matrixTransform(editMatrix(o).inverse());
    if(typeof insideWindow==='function'&&!insideWindow(o,local))continue;
    const x=Math.floor(local.x/w*m.width),y=Math.floor(local.y/h*m.height);
    if(x>=0&&y>=0&&x<m.width&&y<m.height&&m.rgba[(y*m.width+x)*4+3]>20)return o;
  }
  return null;
}
function pointEdit(e){return new DOMPoint(e.clientX,e.clientY).matrixTransform($('#editorSvg').getScreenCTM().inverse());}
async function loadEdit(restore=true){
  let restoreCrop=false;
  editor.busy=true;editControls();editStatus('正在准备可编辑图层…');
  try{
    const started=performance.now(),data=await api('/api/tasks/'+editor.task+'/editor');editor.data=data;editor.objects=copyEdit(data.objects);editor.undo=[];editor.redo=[];editor.selected=null;editor.pending=null;editor.images.clear();
    editStatus(`正在加载 ${data.objects.length} 个素材并准备点选…`);
    let saved=null;try{saved=JSON.parse(localStorage.getItem(editDraftKey()));}catch{}
    let stale=false;
    if(restore&&saved?.base===data.base_version_id){
      editor.undo.push(copyEdit(editor.objects));
      for(const o of editor.objects){const draft=saved.objects.find(n=>n.id===o.id);if(draft){o.transform=draft.transform;o.text=draft.text;o.source_crop=draft.source_crop||o.source_crop;o.window_crop=draft.window_crop||o.window_crop;o.removed=!!draft.removed;}}
      const rank=new Map(saved.objects.map((o,i)=>[o.id,i]));editor.objects.sort((a,b)=>(rank.get(a.id)??Infinity)-(rank.get(b.id)??Infinity));
      editor.pending=saved.pending||null;
      restoreCrop=editor.objects.some(o=>o.capabilities.crop&&JSON.stringify(o.source_crop)!==JSON.stringify(data.objects.find(n=>n.id===o.id).source_crop));
    }else if(restore&&saved){stale=true;localStorage.setItem(editDraftKey()+':previous',JSON.stringify(saved));}
    editor.zoom=1;editor.pan=[0,0];await drawEdit(data);$('#editorResult').hidden=true;
    editStatus(stale?'已有旧版本草稿，已另存于本机；当前显示最新版本，请重新核对后编辑。':`画布已就绪 · ${((performance.now()-started)/1000).toFixed(1)}秒 · 上方圆点旋转、右下圆点缩放。Delete 删除，Ctrl+Z 撤销。`);
  }catch(error){editStatus('加载失败：'+error.message);}
  finally{editor.busy=false;editControls();}
  if(restoreCrop)await submitEdit('preview');
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
$('#editorTransformScope').onchange=()=>{endEditDrag();editSelection();};
let editTextTransaction=null;
$('#editorText').addEventListener('blur',()=>{editTextTransaction=null;});
$('#editorText').addEventListener('input',()=>{const o=editor.objects.find(o=>o.id===editor.selected);if(!editor.busy&&o?.capabilities.text){if(editTextTransaction!==o.id){checkpointEdit();editTextTransaction=o.id;}o.text=$('#editorText').value;editor.pending=null;rememberEdit();editStatus('文字草稿已更新，点击预览查看实际排版。');}});
$('#editorUndo').onclick=()=>{if(!editor.undo.length||editor.busy)return;editor.redo.push(copyEdit(editor.objects));editor.objects=editor.undo.pop();editor.pending=null;positionEdit();rememberEdit();};
$('#editorRedo').onclick=()=>{if(!editor.redo.length||editor.busy)return;editor.undo.push(copyEdit(editor.objects));editor.objects=editor.redo.pop();editor.pending=null;positionEdit();rememberEdit();};
$('#editorFit').onclick=()=>{editor.zoom=1;editor.pan=[0,0];zoomEdit();};
function changeEditGeometry(originals,group,pivot,rotation,ratio){
  const a=rotation*Math.PI/180,c=Math.cos(a)*ratio,s=Math.sin(a)*ratio;
  for(const o of editor.objects)if(!o.removed&&group.includes(o.id)){
    const original=originals.find(n=>n.id===o.id),v=editTransform(original),[cx,cy]=editCenter(o),dx=cx+v.x-pivot[0],dy=cy+v.y-pivot[1];
    o.transform={x:Math.round((pivot[0]+c*dx-s*dy-cx)*1000)/1000,y:Math.round((pivot[1]+s*dx+c*dy-cy)*1000)/1000,rotation:Math.round((v.rotation+rotation)*1e6)/1e6,scale:Math.round(v.scale*ratio*1e6)/1e6};
  }
}
function adjustEditGeometry(angle,size){
  const o=editor.objects.find(o=>o.id===editor.selected&&!o.removed);if(editor.busy||!o?.capabilities.move)return;
  const v=editTransform(o),ratio=size/v.scale,group=editor.objects.filter(n=>!n.removed&&editMembers(o).includes(n.id));
  if(!Number.isFinite(angle)||!Number.isFinite(size)||Math.abs(angle)>36000||group.some(n=>editTransform(n).scale*ratio<.05||editTransform(n).scale*ratio>10)){editStatus('缩放范围为5%至1000%，请输入有效角度。');return;}
  const [l,t,r,b]=editGroupBox(o);checkpointEdit();changeEditGeometry(copyEdit(editor.objects),editMembers(o),[(l+r)/2,(t+b)/2],angle-v.rotation,ratio);positionEdit();rememberEdit();
}
$('#editorAngle').onchange=()=>{const o=editor.objects.find(o=>o.id===editor.selected);if(o)adjustEditGeometry(Number($('#editorAngle').value),editTransform(o).scale);};
$('#editorScale').onchange=()=>{const o=editor.objects.find(o=>o.id===editor.selected);if(o)adjustEditGeometry(editTransform(o).rotation,Number($('#editorScale').value)/100);};
for(const [id,factor] of [['editorSmaller',.9],['editorLarger',1.1]])$('#'+id).onclick=()=>{const o=editor.objects.find(o=>o.id===editor.selected);if(o)adjustEditGeometry(editTransform(o).rotation,editTransform(o).scale*factor);};
function deleteEdit(group=false){
  const o=editor.objects.find(o=>o.id===editor.selected&&!o.removed);if(editor.busy||!o?.capabilities.remove)return;
  const ids=group?o.capabilities.group:[o.id];
  if(editor.objects.filter(n=>!n.removed&&!ids.includes(n.id)).length===0){editStatus('至少保留一个素材。');return;}
  checkpointEdit();for(const n of editor.objects)if(ids.includes(n.id))n.removed=true;
  editor.selected=null;positionEdit();rememberEdit();editStatus('素材已从草稿删除，可撤销。保存后生成新版本。');
}
$('#editorDelete').onclick=()=>deleteEdit();$('#editorDeleteGroup').onclick=()=>deleteEdit(true);
function reorderEdit(direction){
  const o=editor.objects.find(o=>o.id===editor.selected&&!o.removed);if(editor.busy||!o)return;
  const alive=editor.objects.filter(n=>!n.removed),ids=new Set(o.capabilities.group),group=alive.filter(n=>ids.has(n.id)),rest=alive.filter(n=>!ids.has(n.id));
  const first=alive.indexOf(group[0]),last=alive.indexOf(group.at(-1));
  if((['up','top'].includes(direction)&&last===alive.length-1)||(['down','bottom'].includes(direction)&&first===0))return;
  let index=direction==='top'?rest.length:0;
  if(direction==='up')index=rest.indexOf(alive[last+1])+1;
  if(direction==='down')index=rest.indexOf(alive[first-1]);
  checkpointEdit();rest.splice(index,0,...group);editor.objects=[...rest,...editor.objects.filter(n=>n.removed)];
  positionEdit();rememberEdit();editStatus('图层顺序已更新，可撤销。保存后生成新版本。');
}
for(const [id,direction] of [['editorLayerUp','up'],['editorLayerDown','down'],['editorLayerTop','top'],['editorLayerBottom','bottom']])$('#'+id).onclick=()=>reorderEdit(direction);
let editDrag=null;
$('#editorViewport').addEventListener('pointerdown',e=>{
  if(editor.busy||!editor.data||e.button!==0)return;
  if(typeof windowCropMode!=='undefined'&&windowCropMode&&e.target.dataset.windowCrop){startWindowCrop(e);return;}
  if(e.target.dataset.handle){
    const o=editor.objects.find(o=>o.id===editor.selected),point=pointEdit(e),[l,t,r,b]=editGroupBox(o);
    editDrag={kind:e.target.dataset.handle,point,pivot:[(l+r)/2,(t+b)/2],objects:copyEdit(editor.objects),group:editMembers(o),moved:false};
  }else if(editor.space){editDrag={pan:true,start:[e.clientX,e.clientY],value:[...editor.pan]};}
  else{
    const point=pointEdit(e),o=hitEdit(point);editor.selected=o?.id||null;editSelection();
    if(!o?.capabilities.move)return;
    editDrag={point,objects:copyEdit(editor.objects),moved:false,group:editMembers(o)};
  }
  e.preventDefault();$('#editorViewport').setPointerCapture(e.pointerId);
});
$('#editorViewport').addEventListener('pointermove',e=>{
  if(typeof windowCropDrag!=='undefined'&&windowCropDrag){moveWindowCrop(e);return;}
  if(!editDrag)return;
  if(editDrag.pan){editor.pan=[editDrag.value[0]+e.clientX-editDrag.start[0],editDrag.value[1]+e.clientY-editDrag.start[1]];zoomEdit();return;}
  const point=pointEdit(e),dx=point.x-editDrag.point.x,dy=point.y-editDrag.point.y;
  if(!editDrag.moved&&Math.hypot(dx,dy)<2)return;
  if(!editDrag.moved){checkpointEdit();editDrag.moved=true;}
  if(editDrag.kind){
    const [cx,cy]=editDrag.pivot,start=editDrag.point;
    let angle=0,ratio=1;
    if(editDrag.kind==='rotate')angle=(Math.atan2(point.y-cy,point.x-cx)-Math.atan2(start.y-cy,start.x-cx))*180/Math.PI;
    else {ratio=Math.hypot(point.x-cx,point.y-cy)/Math.max(1,Math.hypot(start.x-cx,start.y-cy));const values=editDrag.objects.filter(o=>editDrag.group.includes(o.id)&&!o.removed).map(o=>editTransform(o).scale);ratio=Math.min(10/Math.max(...values),Math.max(.05/Math.min(...values),ratio));}
    changeEditGeometry(editDrag.objects,editDrag.group,editDrag.pivot,angle,ratio);
  }else for(const o of editor.objects)if(editDrag.group.includes(o.id)&&!o.removed){
    const original=editDrag.objects.find(n=>n.id===o.id);o.transform={...editTransform(original),x:Math.round((original.transform.x+dx)*1000)/1000,y:Math.round((original.transform.y+dy)*1000)/1000};
  }
  positionEdit();
});
let lastEditTap=null;
function endEditDrag(event){
  if(typeof windowCropDrag!=='undefined'&&windowCropDrag){windowCropDrag=null;rememberEdit();return;}
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
  if(!$('#editorDialog').open||$('#editorCropDialog').open||e.target.matches('input,textarea,select'))return;
  if(e.code==='Space'){e.preventDefault();editor.space=true;}
  if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();deleteEdit();}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();$(e.shiftKey?'#editorRedo':'#editorUndo').click();}
});
document.addEventListener('keyup',e=>{if(e.code==='Space')editor.space=false;});window.addEventListener('blur',()=>{editor.space=false;endEditDrag();});
new ResizeObserver(zoomEdit).observe($('#editorViewport'));
async function submitEdit(action){
  if(editor.busy||(!editChanges().length&&!editOrder()))return;
  editor.busy=true;editControls();editStatus(action==='save'?'正在保存并渲染新版本…':'正在生成实际文字与成图预览…');
  const task=editor.task;
  const order=editOrder();
  const payload=editor.pending?.action===action?editor.pending.payload:{request_id:'edit-'+crypto.randomUUID(),base_version_id:editor.data.base_version_id,changes:editChanges(),...(order?{layer_order:order}:{})};
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
