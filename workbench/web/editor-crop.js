"use strict";
let windowCropMode=false,windowCropDrag=null;
function windowLocalMatrix(o){const [cx,cy]=editCenter(o);return new DOMMatrix().translate(cx,cy).rotate(o.rotation||0).translate(-cx,-cy);}
function windowBox(o){const [l,t,r,b]=o.bbox,[a,c,d,e]=o.window_crop||[0,0,1,1];return [l+(r-l)*a,t+(b-t)*c,l+(r-l)*d,t+(b-t)*e];}
function insideWindow(o,point){if(!o.window_crop||JSON.stringify(o.window_crop)==='[0,0,1,1]')return true;const p=point.matrixTransform(windowLocalMatrix(o).inverse()),[l,t,r,b]=windowBox(o);return p.x>=l&&p.x<=r&&p.y>=t&&p.y<=b;}
function updateWindowClips(){
  const svg=$('#editorSvg'),defs=svg.querySelector('defs');if(!defs)return;
  defs.querySelectorAll('.editor-window-clip').forEach(n=>n.remove());
  for(const [index,o] of editor.objects.entries()){
    const node=Array.from(svg.querySelectorAll('image')).find(n=>n.dataset.object===o.id);if(!node)continue;
    if(!o.window_crop||JSON.stringify(o.window_crop)==='[0,0,1,1]'){node.removeAttribute('clip-path');continue;}
    const id='window-clip-'+index,[l,t,r,b]=windowBox(o),clip=cropNode('clipPath',{id,clipPathUnits:'userSpaceOnUse',class:'editor-window-clip'});
    clip.append(cropNode('rect',{x:l,y:t,width:r-l,height:b-t,transform:windowLocalMatrix(o).toString()}));defs.append(clip);node.setAttribute('clip-path',`url(#${id})`);
  }
}
function drawWindowCrop(o){
  const [l,t,r,b]=windowBox(o),matrix=editMatrix(o).multiply(windowLocalMatrix(o)),group=cropNode('g',{class:'editor-selection',transform:matrix.toString()});
  const unit=1/Math.max(.01,($('#editorSvg').getScreenCTM()?.a||1)*editTransform(o).scale);
  group.append(cropNode('rect',{x:l,y:t,width:r-l,height:b-t,fill:'none',stroke:'#e88920','stroke-width':2,'vector-effect':'non-scaling-stroke','pointer-events':'none'}));
  for(const [name,x,y] of [['nw',l,t],['n',(l+r)/2,t],['ne',r,t],['e',r,(t+b)/2],['se',r,b],['s',(l+r)/2,b],['sw',l,b],['w',l,(t+b)/2]]){
    const n=cropNode('rect',{x:x-5*unit,y:y-5*unit,width:10*unit,height:10*unit,fill:'white',stroke:'#e88920','stroke-width':2,'vector-effect':'non-scaling-stroke','data-window-crop':name});n.style.cursor=['n','s'].includes(name)?'ns-resize':['w','e'].includes(name)?'ew-resize':'nwse-resize';group.append(n);
  }
  $('#editorSvg').append(group);
}
function startWindowCrop(e){
  const o=editor.objects.find(n=>n.id===editor.selected);if(!o?.capabilities.crop)return;
  checkpointEdit();windowCropDrag={object:o,edge:e.target.dataset.windowCrop};e.preventDefault();$('#editorViewport').setPointerCapture(e.pointerId);
}
function moveWindowCrop(e){
  const {object:o,edge}=windowCropDrag,p=pointEdit(e).matrixTransform(editMatrix(o).multiply(windowLocalMatrix(o)).inverse());
  const [l,t,r,b]=o.bbox,x=Math.max(0,Math.min(1,(p.x-l)/(r-l))),y=Math.max(0,Math.min(1,(p.y-t)/(b-t)));let [a,c,d,f]=o.window_crop||[0,0,1,1];
  if(edge.includes('w'))a=Math.min(d-.01,x);if(edge.includes('e'))d=Math.max(a+.01,x);
  if(edge.includes('n'))c=Math.min(f-.01,y);if(edge.includes('s'))f=Math.max(c+.01,y);
  o.window_crop=[a,c,d,f].map(v=>Math.round(v*1e6)/1e6);positionEdit();
}
$('#editorWindowCrop').onclick=()=>{windowCropMode=!windowCropMode;$('#editorWindowCrop').textContent=windowCropMode?'完成窗口裁剪':'裁剪显示窗口';editSelection();editStatus(windowCropMode?'拖动橙色边框的四边或四角裁掉多余部分；照片大小、位置和相框保持不变。':'窗口裁剪已保留在草稿中，点击保存新版本。');};
$('#editorWindowReset').onclick=()=>{const o=editor.objects.find(n=>n.id===editor.selected);if(!o?.capabilities.crop||editor.busy)return;checkpointEdit();o.window_crop=[0,0,1,1];positionEdit();rememberEdit();};
const photoCrop={object:null,rect:[0,0,1,1],size:[1,1],drag:null};
function cropNode(name,attributes){const n=document.createElementNS('http://www.w3.org/2000/svg',name);for(const [k,v] of Object.entries(attributes))n.setAttribute(k,v);return n;}
function drawPhotoCrop(){
  const svg=$('#editorCropSvg'),[w,h]=photoCrop.size,[l,t,r,b]=photoCrop.rect;
  svg.querySelectorAll('.crop-mark').forEach(n=>n.remove());
  svg.append(cropNode('path',{d:`M0 0H${w}V${h}H0Z M${l*w} ${t*h}V${b*h}H${r*w}V${t*h}Z`,fill:'#0009','fill-rule':'evenodd','pointer-events':'none',class:'crop-mark'}));
  svg.append(cropNode('rect',{x:l*w,y:t*h,width:(r-l)*w,height:(b-t)*h,fill:'transparent',stroke:'#fff','stroke-width':2,'vector-effect':'non-scaling-stroke',class:'crop-mark','data-crop':'move'}));
  const unit=1/Math.max(.01,svg.getScreenCTM()?.a||1);
  for(const [name,x,y] of [['nw',l,t],['ne',r,t],['se',r,b],['sw',l,b]])svg.append(cropNode('circle',{cx:x*w,cy:y*h,r:7*unit,fill:'#fff',stroke:'#16886c','stroke-width':2,'vector-effect':'non-scaling-stroke','data-crop':name,class:'crop-mark'}));
  $('#editorCropStatus').textContent=`保留宽度 ${Math.round((r-l)*100)}% · 高度 ${Math.round((b-t)*100)}%`;
}
$('#editorCrop').onclick=async()=>{
  const o=editor.objects.find(o=>o.id===editor.selected&&!o.removed);if(editor.busy||!o?.capabilities.crop||!o.crop_image)return;
  photoCrop.object=o;photoCrop.rect=[...(o.source_crop||[0,0,1,1])];photoCrop.drag=null;
  $('#editorCropApply').disabled=true;$('#editorCropStatus').textContent='正在加载照片…';$('#editorCropSvg').replaceChildren();$('#editorCropDialog').showModal();
  try{
    const im=new Image();im.src=o.crop_image;await im.decode();if(!$('#editorCropDialog').open||photoCrop.object!==o)return;
    photoCrop.size=[im.naturalWidth,im.naturalHeight];const [w,h]=photoCrop.size;
    $('#editorCropSvg').setAttribute('viewBox',`0 0 ${w} ${h}`);$('#editorCropSvg').style.aspectRatio=`${w}/${h}`;
    $('#editorCropSvg').append(cropNode('image',{href:o.crop_image,width:w,height:h,'pointer-events':'none'}));
    drawPhotoCrop();$('#editorCropApply').disabled=false;
  }catch(e){$('#editorCropStatus').textContent='照片加载失败：'+e.message;}
};
function cropPoint(e){const p=new DOMPoint(e.clientX,e.clientY).matrixTransform($('#editorCropSvg').getScreenCTM().inverse());return [Math.max(0,Math.min(1,p.x/photoCrop.size[0])),Math.max(0,Math.min(1,p.y/photoCrop.size[1]))];}
$('#editorCropSvg').addEventListener('pointerdown',e=>{
  if(e.button!==0||$('#editorCropApply').disabled)return;
  photoCrop.drag={point:cropPoint(e),rect:[...photoCrop.rect],kind:e.target.dataset.crop||'new'};
  e.preventDefault();$('#editorCropSvg').setPointerCapture(e.pointerId);
});
$('#editorCropSvg').addEventListener('pointermove',e=>{
  const d=photoCrop.drag;if(!d)return;const [x,y]=cropPoint(e),[px,py]=d.point;let [l,t,r,b]=d.rect;
  if(d.kind==='move'){
    const dx=Math.max(-l,Math.min(1-r,x-px)),dy=Math.max(-t,Math.min(1-b,y-py));l+=dx;r+=dx;t+=dy;b+=dy;
  }else if(d.kind==='new'){
    l=Math.min(x,px);t=Math.min(y,py);r=Math.max(x,px);b=Math.max(y,py);
    if(r-l<.01||b-t<.01)return;
  }else{
    if(d.kind.includes('w'))l=Math.min(r-.01,x);if(d.kind.includes('e'))r=Math.max(l+.01,x);
    if(d.kind.includes('n'))t=Math.min(b-.01,y);if(d.kind.includes('s'))b=Math.max(t+.01,y);
  }
  photoCrop.rect=[l,t,r,b];drawPhotoCrop();
});
for(const event of ['pointerup','pointercancel'])$('#editorCropSvg').addEventListener(event,()=>{photoCrop.drag=null;});
$('#editorCropCancel').onclick=()=>$('#editorCropDialog').close();
$('#editorCropDialog').addEventListener('close',()=>{photoCrop.drag=null;});
$('#editorCropReset').onclick=()=>{photoCrop.rect=[0,0,1,1];drawPhotoCrop();};
$('#editorCropApply').onclick=async()=>{
  const o=photoCrop.object;if(editor.busy||!o||!editor.objects.includes(o))return;
  const value=photoCrop.rect.map(v=>Math.round(v*1e6)/1e6);
  if(JSON.stringify(value)!==JSON.stringify(o.source_crop)){checkpointEdit();o.source_crop=value;rememberEdit();}
  $('#editorCropDialog').close();await submitEdit('preview');
};
new ResizeObserver(()=>{if($('#editorCropDialog').open&&!$('#editorCropApply').disabled)drawPhotoCrop();}).observe($('#editorCropSvg'));
