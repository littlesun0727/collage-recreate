/** Independent skill exercise. Harness starts agents and records evidence; never authors design JSON. */
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {createHash} from 'node:crypto';
import {IMAGE_TRANSPORT,originalImageInstructions,originalImageInput} from './image-input.mjs';
const HERE=path.dirname(fileURLToPath(import.meta.url)),SOURCE=path.dirname(HERE);
const {values:a}=parseArgs({options:{root:{type:'string'},names:{type:'string'},priority:{type:'string'},limit:{type:'string'},concurrency:{type:'string',default:'2'},'reference-dir':{type:'string',default:'D:/datas/图片排版样图'},'timeout-minutes':{type:'string',default:'20'},resume:{type:'boolean',default:false}}});
if(!a.root)throw new Error('--root required');
let sdk;
try{sdk=import.meta.resolve('@openai/codex-sdk');}catch{sdk=pathToFileURL('D:/codes/collage_batch/node_modules/@openai/codex-sdk/dist/index.js').href;}
const {Codex}=await import(sdk);
const MODEL='gpt-5.6-sol',EFFORT='medium',ROOT=path.resolve(a.root),SKILL=path.join(ROOT,'_skill_snapshot');
const CLI='C:/Users/admin/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe';
const PY='D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe';
const MATERIALS=['D:/视频素材/人像素材3.0','D:/视频素材/风景照片'];
const names=(await fs.readdir(a['reference-dir'])).filter(n=>/\.(jpg|jpeg|png|webp)$/i.test(n)).sort().filter(n=>!a.names||a.names.split(',').includes(n));
if(a.priority){const first=a.priority.split(',');names.sort((x,y)=>(first.includes(x)?first.indexOf(x):-1+names.length)-(first.includes(y)?first.indexOf(y):-1+names.length));}
const queue=a.limit?names.slice(0,Number(a.limit)):names;
const write=async(p,v)=>{await fs.mkdir(path.dirname(p),{recursive:true});await fs.writeFile(p,JSON.stringify(v,null,2)+'\n','utf8');};
const read=async p=>JSON.parse((await fs.readFile(p,'utf8')).replace(/^\uFEFF/,''));
await fs.mkdir(ROOT,{recursive:true});
async function fileHashes(dir){
 const output={};
 for(const entry of await fs.readdir(dir,{withFileTypes:true})){
  if(entry.name.startsWith('.')||['__pycache__','node_modules','evaluation','tests','plans','PLAN.md'].includes(entry.name))continue;
  const full=path.join(dir,entry.name);
  if(entry.isDirectory())Object.assign(output,await fileHashes(full));
  else if(/\.(py|mjs|json|md)$/.test(entry.name))output[path.relative(SOURCE,full)]=createHash('sha256').update(await fs.readFile(full)).digest('hex');
 }
 return output;
}
const skillFiles=await fileHashes(SOURCE);
for(const file of ['evaluation/run-grouped-light-sdk.mjs','evaluation/image-input.mjs'])skillFiles[file]=createHash('sha256').update(await fs.readFile(path.join(SOURCE,file))).digest('hex');
const manifestFile=path.join(ROOT,'manifest.json');
if(existsSync(manifestFile)){
 const oldManifest=await read(manifestFile);
 if(!a.resume)throw new Error('Use a new batch directory or --resume');
 for(const [file,digest] of Object.entries(oldManifest.skill_files)){
  if(createHash('sha256').update(await fs.readFile(path.join(SKILL,file))).digest('hex')!==digest)throw new Error('Frozen skill changed: '+file);
 }
}else{
 await write(manifestFile,{started_at:new Date().toISOString(),model:MODEL,effort:EFFORT,skill:SKILL,skill_files:skillFiles,reference_dir:a['reference-dir'],materials:MATERIALS,names:queue,concurrency:Number(a.concurrency),mode:'fresh-analysis-live-grouped-light-screen-end-to-end',reveal_padding:.1,remote_reveal_authorized:true,image_transport:IMAGE_TRANSPORT,initial_runs_per_reference:1,sdk});
 for(const file of Object.keys(skillFiles)){const target=path.join(ROOT,'_skill_snapshot',file);await fs.mkdir(path.dirname(target),{recursive:true});await fs.copyFile(path.join(SOURCE,file),target);}
 await fs.symlink(path.join(SOURCE,'.deps314'),path.join(SKILL,'.deps314'),'junction');
}
const codex=new Codex({codexPathOverride:CLI,env:{...process.env},config:{features:{image_generation:false,multi_agent:false}}});
let next=0;const results=[];const aborters=new Set();let stopped=false;
const monitor=setInterval(()=>{if(existsSync(path.join(ROOT,'STOP'))){stopped=true;for(const c of aborters)c.abort();}},1000);
async function job(name){
 const dir=path.join(ROOT,path.parse(name).name),control=path.join(dir,'controller'),run=path.join(dir,'run');await fs.mkdir(control,{recursive:true});
 const stateFile=path.join(control,'state.json');
 if(existsSync(stateFile)&&!a.resume)throw new Error('Existing case requires --resume: '+name);
 const old=existsSync(stateFile)?await read(stateFile):{};
 if(old.status==='completed'){results.push(old);return;}
 const started=Date.now();const state={name,model:MODEL,effort:EFFORT,status:'running',started_at:old.started_at??new Date().toISOString(),attempt_started_at:new Date().toISOString(),previous_elapsed_seconds:old.elapsed_seconds??0,thread_id:old.thread_id??null};await write(stateFile,state);
 const prompt=`请独立使用 ${SKILL}/SKILL.md，从头端到端制作这张拼贴首版。你是唯一分析、绑定、筛选与视觉评估者，实际模型固定 ${MODEL} / ${EFFORT}。
参考：${path.join(a['reference-dir'],name)}
客户素材：${MATERIALS.join(' 和 ')}
任务目录 RUN=${run}（尚未prepare）；唯一可写案例目录 ${dir}；解释器 ${PY}。
读取SKILL后按需读取analysis/drawing/asset-gate/review参考。独立prepare，看原尺寸参考和客户联系表，重新写analysis.json与bindings.json。严禁读取旧运行的analysis、bindings、提取缓存或修复结论。不得访问其他样本产物、修改skill源码、调用其他模型/子agent或写临时图像制作脚本。
用户已授权这些图片与坐标用于360提取：实际运行 workflow.py build --run RUN --reveal --reveal-layout grouped --brief。最多3组由程序决定，不改成整图模式，不跳过复杂装饰提取。默认简单几何用local；不能为了少请求而丢弃关键设计。提取异常保留证据，未知任务不能重复提交。
首版完成后集中看完整对照、非空提取表与门禁问题。必要时一次screen：写RUN/screen-plan.json，运行 workflow.py screen --run RUN --file RUN/screen-plan.json --brief。不使用recover、OCR、LaMa、像素擦除、yibu或其他生图；不可用复杂素材隔离，简单形状可补画。筛选JSON字段报错只修正格式，不多轮试画。无问题就跳过screen。
查看最终完整图，写review.json并运行 workflow.py review --run RUN --file RUN/review.json --brief。如实保留缺口，不因为照片可见就pass。不重复进行例行审计/读取源码/打印整份schema；必要查询批量完成，命令成功后继续下一步，主要精力用于原图分析和一次集中判断。
在 ${dir}/evaluation.json 写 {"model":"${MODEL}","effort":"${EFFORT}","visual_verdict":"pass或needs_changes或blocked","strengths":[],"issues":[],"clarity_observations":[],"screening_observations":[],"skill_friction":[]}。观察须对应实际对象ID，不预设分组一定更清晰；缺层和近似绘制仍如实记录。
Windows所有exec_command使用tty:true、login:false；中文UTF-8；view_image使用detail original并用image转发original。凭证只由固定workflow读取D:/codes/.env发送至360，不自行输出/复制密钥。网络受限时对同一build申请权限，用户已授权实际提取。无需向用户再次询问本次已授权的数据上传。只读输入，只写本案例目录。
完成必要步骤即交付，不反复读取已经确认的文件。`;
 const visualPrompt=prompt+'\n先完成 prepare，再打开其产出的 prepared/reference.png；该路径在 prepare 前尚不存在。';
 const referenceImages=[path.join(run,'prepared/reference.png')];
 await fs.writeFile(path.join(control,'task.md'),visualPrompt+'\n\n'+originalImageInstructions(referenceImages),'utf8');
 const opts={model:MODEL,modelReasoningEffort:EFFORT,workingDirectory:dir,skipGitRepoCheck:true,sandboxMode:'workspace-write',approvalPolicy:'on-request',additionalDirectories:[dir]};
 const thread=state.thread_id?codex.resumeThread(state.thread_id,opts):codex.startThread(opts);
 const abort=new AbortController();aborters.add(abort);const timeout=setTimeout(()=>abort.abort(),Number(a['timeout-minutes'])*60000);
 console.log('START',name);
 try{
  for(let turn=0;turn<1;turn++){
   const input=turn===0&&!state.thread_id?originalImageInput(visualPrompt,referenceImages):`继续controller/task.md中未完成的步骤。保留本次已完成的新分析、绑定、提取和首版，不重做成功步骤，不读取旧运行；若本轮已有screen收据，不得再次screen；只完成缺少的review登记、evaluation.json与交付。已提交远端任务只可续查原任务，不能再次提交。`;
   state.sdk_run_calls=(old.sdk_run_calls??0)+1;await write(stateFile,state);
   const {events}=await thread.runStreamed(input,{signal:abort.signal});
   for await(const e of events){
    const at=new Date().toISOString();
    if(e.type==='thread.started'){state.thread_id=e.thread_id;await write(stateFile,state);}
    if(e.type==='turn.completed')state.usage=e.usage;
    if(e.type==='turn.failed'||e.type==='error')state.last_error=e.error??e.message;
    if(e.type==='item.completed'&&e.item?.type==='command_execution'){state.last_command_at=at;state.command_count=(state.command_count??0)+1;await write(stateFile,state);}
    if(e.type==='item.completed'&&e.item?.type==='agent_message'){console.log(name+': '+e.item.text.slice(0,180).replace(/\s+/g,' '));}
    if(e.item?.type!=='reasoning')await fs.appendFile(path.join(control,'events.jsonl'),JSON.stringify({at,...e})+'\n','utf8');
   }
   if(existsSync(path.join(dir,'evaluation.json')))break;
  }
  state.status=existsSync(path.join(dir,'evaluation.json'))&&existsSync(path.join(run,'review.json'))?'completed':'incomplete';
  if(existsSync(path.join(run,'result.json')))state.render_result=await read(path.join(run,'result.json'));
 }catch(e){state.status=abort.signal.aborted?(stopped?'stopped':'timed_out'):'failed';state.error=String(e);}
 finally{clearTimeout(timeout);aborters.delete(abort);state.finished_at=new Date().toISOString();state.attempt_elapsed_seconds=(Date.now()-started)/1000;state.elapsed_seconds=(old.elapsed_seconds??0)+state.attempt_elapsed_seconds;await write(stateFile,state);results.push(state);console.log('END',name,state.status,state.elapsed_seconds);}
}
async function worker(){while(!stopped&&next<queue.length){const name=queue[next++];try{await job(name);}catch(e){results.push({name,status:'harness_failed',error:String(e)});console.log('ERROR',name,String(e));}}}
try{await Promise.all(Array.from({length:Number(a.concurrency)},()=>worker()));}finally{clearInterval(monitor);await write(path.join(ROOT,'batch-result.json'),{finished_at:new Date().toISOString(),model:MODEL,effort:EFFORT,results});}
