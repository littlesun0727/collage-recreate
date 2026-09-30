/** Analysis only: independent analyst and critic, both fixed to gpt-5.6-sol medium. */
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {IMAGE_TRANSPORT,originalImageInstructions,originalImageInput} from './image-input.mjs';
const HERE=path.dirname(fileURLToPath(import.meta.url)),SKILL=path.dirname(HERE);
const {values:a}=parseArgs({options:{root:{type:'string'},names:{type:'string'},concurrency:{type:'string',default:'3'},'reference-dir':{type:'string',default:'D:/datas/图片排版样图'},'max-edge':{type:'string',default:'0'},'existing-analysis':{type:'string'},'routing-probe':{type:'boolean',default:false},'timeout-minutes':{type:'string',default:'12'},resume:{type:'boolean',default:false}}});
if(!a.root)throw Error('--root required');
let sdk;try{sdk=import.meta.resolve('@openai/codex-sdk');}catch{sdk=pathToFileURL('D:/codes/collage_batch/node_modules/@openai/codex-sdk/dist/index.js').href;}
const {Codex}=await import(sdk);
const ROOT=path.resolve(a.root),PY='D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe',HELPER=path.join(HERE,'analysis-tools.py');
const MODEL='gpt-5.6-sol',EFFORT='medium';
const codex=new Codex({codexPathOverride:'C:/Users/admin/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe',env:{...process.env,CODEX_HOME:'C:/Users/admin/.codex'},config:{features:{image_generation:false,multi_agent:false}}});
const write=async(p,v)=>{await fs.mkdir(path.dirname(p),{recursive:true});await fs.writeFile(p,JSON.stringify(v,null,2)+'\n','utf8');};
const read=async p=>JSON.parse((await fs.readFile(p,'utf8')).replace(/^\uFEFF/,''));
const helper=(cmd,run,extra=[])=>{
 const p=spawnSync(PY,['-X','utf8',HELPER,cmd,'--run',run,...extra],{encoding:'utf8',windowsHide:true});
 if(p.status!==0)throw Error(p.stderr||p.stdout||'Helper failed');return JSON.parse(p.stdout);
};
const names=(await fs.readdir(a['reference-dir'])).filter(n=>/\.(jpg|jpeg|png|webp)$/i.test(n)).sort().filter(n=>!a.names||a.names.split(',').includes(n));
if(a['existing-analysis']&&(names.length!==1||Number(a['max-edge'])!==0))throw Error('Existing analysis requires exactly one original-size reference');
await fs.mkdir(ROOT,{recursive:true});
const manifestFile=path.join(ROOT,'manifest.json');
if(existsSync(manifestFile)){
 const previous=await read(manifestFile);
 if(!a.resume||previous.image_transport?.analyst!==IMAGE_TRANSPORT||previous.analysis_rules_sha256!==createHash('sha256').update(await fs.readFile(path.join(SKILL,'references/analysis.md'))).digest('hex'))throw Error('Use a new output directory; only resume a run with matching image transport and analysis rules');
}else{
 await write(manifestFile,{model:MODEL,effort:EFFORT,sdk,started_at:new Date().toISOString(),names,reference_dir:a['reference-dir'],max_edge:Number(a['max-edge']),concurrency:Number(a.concurrency),scope:'analysis-only; frozen first JSON; self-check; independent critic; no production',image_transport:{analyst:IMAGE_TRANSPORT,critic:'sdk-local-image-baseline'},analysis_rules_sha256:createHash('sha256').update(await fs.readFile(path.join(SKILL,'references/analysis.md'))).digest('hex')});
 const snapshot=path.join(ROOT,'_implementation');await fs.mkdir(snapshot,{recursive:true});
 for(const file of ['evaluation/run-analysis-sdk.mjs','evaluation/image-input.mjs','references/analysis.md'])await fs.copyFile(path.join(SKILL,file),path.join(snapshot,path.basename(file)));
}
let stopped=false;const active=new Set();const monitor=setInterval(()=>{if(existsSync(path.join(ROOT,'STOP'))){stopped=true;for(const c of active)c.abort();}},1000);
async function agent(dir,phase,prompt,images,expected){
 const control=path.join(dir,phase+'-controller');await fs.mkdir(control,{recursive:true});const sf=path.join(control,'state.json');
 const old=existsSync(sf)?await read(sf):{};
 if(old.status==='completed')return old;
 if(old.thread_id&&!a.resume)throw Error('Existing thread requires --resume');
 const state={phase,model:MODEL,effort:EFFORT,started_at:new Date().toISOString(),thread_id:old.thread_id??null,status:'running'};
 const original=phase==='analyst';
 await write(sf,state);await fs.writeFile(path.join(control,'task.md'),prompt+(original?'\n\n'+originalImageInstructions(images):''),'utf8');
 const options={model:MODEL,modelReasoningEffort:EFFORT,workingDirectory:dir,additionalDirectories:[dir],skipGitRepoCheck:true,sandboxMode:'workspace-write',approvalPolicy:'on-request'};
 const thread=state.thread_id?codex.resumeThread(state.thread_id,options):codex.startThread(options);
 const abort=new AbortController();active.add(abort);const timer=setTimeout(()=>abort.abort(),Number(a['timeout-minutes'])*60000);const started=Date.now();
 console.log('START',path.basename(dir),phase);
 try{
  for(let turn=0;turn<2;turn++){
   const input=turn===0&&!state.thread_id?(original?originalImageInput(prompt,images):[{type:'text',text:prompt},...images.map(p=>({type:'local_image',path:p}))]):`继续读取 ${control}/task.md，仅完成未完成的分析诊断产物，禁止制作或修改冻结分析。`;
   const {events}=await thread.runStreamed(input,{signal:abort.signal});
   for await(const e of events){
    const at=new Date().toISOString();
    if(e.type==='thread.started'){state.thread_id=e.thread_id;await write(sf,state);}
    if(e.type==='turn.completed')state.usage=e.usage;
    if(e.type==='turn.failed'||e.type==='error')state.last_error=e.error??e.message;
    if(e.type==='item.completed'&&e.item?.type==='agent_message')console.log(path.basename(dir),phase,e.item.text.slice(0,200).replace(/\s+/g,' '));
    if(e.item?.type!=='reasoning')await fs.appendFile(path.join(control,'events.jsonl'),JSON.stringify({at,...e})+'\n');
   }
   if(existsSync(expected))break;
  }
  state.status=existsSync(expected)?'completed':'incomplete';
 }catch(e){state.status=abort.signal.aborted?(stopped?'stopped':'timed_out'):'failed';state.error=String(e);}
 finally{clearTimeout(timer);active.delete(abort);state.elapsed_seconds=(Date.now()-started)/1000;state.finished_at=new Date().toISOString();await write(sf,state);console.log('END',path.basename(dir),phase,state.status,state.elapsed_seconds);}
 return state;
}
const constraints=`Windows exec_command 必须 tty:true，建议 login:false；中文文件用 UTF-8。仅在本图目录写文件。不要调用子代理、外部分析/生成 API、yibu、imagegen，不读取其他运行或版本，不改技能，不写制作脚本，不进行绑定/build/提取。原有制作任务暂停，本任务仅分析诊断。`;
const results=[];let next=0;
async function job(name){
 const dir=path.join(ROOT,path.parse(name).name),run=path.join(dir,'analysis');await fs.mkdir(dir,{recursive:true});
 const info=existsSync(path.join(run,'input.json'))?await read(path.join(run,'input.json')):helper('prepare',run,['--reference',path.join(a['reference-dir'],name),'--max-edge',a['max-edge']]);
 const ref=path.join(run,'prepared/reference.png');const self=path.join(dir,'self-check.json');
 if(a['routing-probe']){
  const target=path.join(dir,'routing-probe.json');
  const probe=`你只执行图片传递路径诊断，不分析图片、不输出坐标。先使用 functions.exec 调用 tools.view_image({path:${JSON.stringify(ref)},detail:"original"})，用 image(result.image_url) 展示一次；再独立调用同一 view_image，用 image(result.image_url,"original") 展示第二次。两种传递方式必须原样执行，不用其他图片工具替代，不自行缩放图片。成功后仅写 ${target} 为 {"completed":true,"default_forwarded":true,"original_forwarded":true}。我们会从实际会话图片字节头核对尺寸，不需要你猜尺寸。${constraints}`;
  const state=await agent(dir,'routing-probe',probe,[],target);results.push({name,probe:state});return;
 }
 const prompt=`你是分析主控，固定 ${MODEL} / ${EFFORT}。只分析参考图，不制作。\n请完整读取 ${SKILL}/references/analysis.md，遵循其中 analysis.json 格式，不执行绑定部分。参考已准备在 ${ref}，真实分析画布尺寸 ${JSON.stringify(info.reference_size)}。输出 ${run}/analysis.json，按分析协议的制作单元覆盖照片、设计文字、装饰与背景，填写 bbox 和层级；同层局部装饰能合并的先合并，组内点缀不必逐个建立 ID，不能遗漏独立照片或文案。不需要客户素材。所有 bbox 必须对应这张分析画布的像素。不要参考已有答案，也不要漏掉底部对象。\n写完运行：${PY} -X utf8 ${HELPER} freeze --run ${run}。此命令校验并冻结第一次分析与叠框；仅 schema 失败时允许修正 JSON 后重试，冻结后禁止修改。\n实际打开返回的 previews/boxes-first.png，逐项检查定位，然后写 ${self}，格式 {"verdict":"pass或needs_changes","coordinate_basis":"你实际用什么尺寸或方式估计坐标","issues":[{"id":"对象ID","reason":"具体观察"}],"summary":"整体判断"}。必须记录实际偏移/漏框，schema 通过不等于定位正确。当前阶段只记录问题，不修正视觉坐标。\n${constraints}`;
 let analyst;
 if(a['existing-analysis']){
  if(!existsSync(path.join(run,'analysis-first.json'))){await fs.copyFile(a['existing-analysis'],path.join(run,'analysis.json'));helper('freeze',run);}
  analyst={phase:'analyst',status:'imported',source:path.resolve(a['existing-analysis']),source_sha256:createHash('sha256').update(await fs.readFile(a['existing-analysis'])).digest('hex'),note:'Historical model output copied without editing; no new analyst inference claimed'};
  await write(path.join(dir,'analyst-controller/state.json'),analyst);
 }else analyst=await agent(dir,'analyst',prompt,[ref],self);
 if(stopped||!['completed','imported'].includes(analyst.status)||!existsSync(path.join(run,'analysis-first.json'))){results.push({name,analyst});return;}
 const ev=helper('evidence',run);const target=path.join(dir,'independent-review.json');
 const criticPrompt=`你是独立的坐标复核者，固定 ${MODEL} / ${EFFORT}。此分析由另一个同型号会话生成，你没有参与定位。你的任务只是检查，不重做制作。\n只读取 ${run}/analysis-first.json 与 ${run}/input.json，以及参考 ${ref}、叠框 ${run}/previews/boxes-first.png、带原图像素网格 ${ev.grid}、裁区联系表 ${JSON.stringify(ev.crop_sheets)}。不要读取 self-check.json 或 analyst-controller，不受原分析者自评影响。\n必须实际打开这些图片，重点对照每个对象 ID 与框内内容是否对应、目标是否完整、框是否偏移；组合装饰按 description 所列整体成员检查，不因为组内点缀未单列 ID 就判漏项；仍检查是否遗漏成员、夹带无关照片或跨越不能合并的叠放层。裁区联系表每格上方为精确框内内容、下方为带红框的邻域。照片槽允许合理重叠，旋转对象的 bbox 是未旋转制作框，轻微旋转造成的角部差异应区别对待。无需评价艺术风格，也不要把原参考照片与客户素材混淆。\n输出 ${target}，格式 {"verdict":"pass或needs_changes","checked_ids":[所有检查对象ID],"errors":[{"id":"对象ID","severity":"major或minor","observed_bbox":[l,t,r,b],"suggested_bbox":[l,t,r,b]或null,"confidence":"high或medium或low","reason":"框中实际是什么、目标哪部分在框外"}],"pattern":"是否存在整体缩放/平移、纵向累积漂移或局部定位错误","hypotheses":["假设及证据；不要把假设当作已证实原因"],"summary":"简要结论"}。建议 bbox 是你的估计，不是人工真值；不确定就 null。原始像素画布是 ${JSON.stringify(ev.reference_size)}。不要输出或修改 analysis.json。\n${constraints}`;
 const critic=await agent(dir,'critic',criticPrompt,[ref,path.join(run,'previews/boxes-first.png')],target);
 results.push({name,analyst,critic});await write(path.join(dir,'case-result.json'),results.at(-1));
}
async function worker(){while(!stopped&&next<names.length){const name=names[next++];try{await job(name);}catch(e){results.push({name,status:'harness_failed',error:String(e)});console.log('ERROR',name,String(e));}}}
try{await Promise.all(Array.from({length:Number(a.concurrency)},()=>worker()));}finally{clearInterval(monitor);await write(path.join(ROOT,'batch-result.json'),{model:MODEL,effort:EFFORT,finished_at:new Date().toISOString(),results});}
