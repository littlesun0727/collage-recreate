/** Independent skill exercise. Harness starts agents and records evidence; never authors design JSON. */
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {createHash} from 'node:crypto';
import {IMAGE_TRANSPORT,originalImageInstructions,originalImageInput} from './image-input.mjs';
const HERE=path.dirname(fileURLToPath(import.meta.url)),SKILL=path.dirname(HERE);
const {values:a}=parseArgs({options:{root:{type:'string'},names:{type:'string'},priority:{type:'string'},limit:{type:'string'},concurrency:{type:'string',default:'2'},'reference-dir':{type:'string',default:'D:/datas/图片排版样图'},'timeout-minutes':{type:'string',default:'20'},resume:{type:'boolean',default:false}}});
if(!a.root)throw new Error('--root required');
let sdk;
try{sdk=import.meta.resolve('@openai/codex-sdk');}catch{sdk=pathToFileURL('D:/codes/collage_batch/node_modules/@openai/codex-sdk/dist/index.js').href;}
const {Codex}=await import(sdk);
const MODEL='gpt-5.6-sol',EFFORT='medium',ROOT=path.resolve(a.root);
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
  if(entry.name.startsWith('.')||['__pycache__','node_modules','evaluation','tests','PLAN.md'].includes(entry.name))continue;
  const full=path.join(dir,entry.name);
  if(entry.isDirectory())Object.assign(output,await fileHashes(full));
  else if(/\.(py|mjs|json|md)$/.test(entry.name))output[path.relative(SKILL,full)]=createHash('sha256').update(await fs.readFile(full)).digest('hex');
 }
 return output;
}
const skillFiles=await fileHashes(SKILL);
for(const file of ['evaluation/run-e2e-reveal-sdk.mjs','evaluation/image-input.mjs'])skillFiles[file]=createHash('sha256').update(await fs.readFile(path.join(SKILL,file))).digest('hex');
const manifestFile=path.join(ROOT,'manifest.json');
if(existsSync(manifestFile)){
 const oldManifest=await read(manifestFile);
 if(!a.resume||JSON.stringify(oldManifest.skill_files)!==JSON.stringify(skillFiles))throw new Error('Use a new batch directory; resume requires the same skill implementation');
}else{
 await write(manifestFile,{started_at:new Date().toISOString(),model:MODEL,effort:EFFORT,skill:SKILL,skill_files:skillFiles,reference_dir:a['reference-dir'],materials:MATERIALS,names,concurrency:Number(a.concurrency),mode:'live-reveal-pad10-end-to-end-first-preview',reveal_padding:.1,remote_reveal_authorized:true,image_transport:IMAGE_TRANSPORT,initial_runs_per_reference:1,sdk});
 for(const file of Object.keys(skillFiles)){const target=path.join(ROOT,'_skill_snapshot',file);await fs.mkdir(path.dirname(target),{recursive:true});await fs.copyFile(path.join(SKILL,file),target);}
}
const codex=new Codex({codexPathOverride:CLI,env:{...process.env,CODEX_HOME:'C:/Users/admin/.codex'},config:{features:{image_generation:false,multi_agent:false}}});
let next=0;const results=[];const aborters=new Set();let stopped=false;
const monitor=setInterval(()=>{if(existsSync(path.join(ROOT,'STOP'))){stopped=true;for(const c of aborters)c.abort();}},1000);
async function job(name){
 const dir=path.join(ROOT,path.parse(name).name),control=path.join(dir,'controller'),run=path.join(dir,'run');await fs.mkdir(control,{recursive:true});
 const stateFile=path.join(control,'state.json');
 if(existsSync(stateFile)&&!a.resume)throw new Error('Existing case requires --resume: '+name);
 const old=existsSync(stateFile)?await read(stateFile):{};
 if(old.status==='completed'){results.push(old);return;}
 const started=Date.now();const state={name,model:MODEL,effort:EFFORT,status:'running',started_at:new Date().toISOString(),thread_id:old.thread_id??null};await write(stateFile,state);
 const prompt=`请独立执行 ${SKILL}/SKILL.md，验收这个新技能的首版制作能力。你是唯一制作/视觉验收主控，模型固定 ${MODEL} / ${EFFORT}。
参考图：${path.join(a['reference-dir'],name)}
客户素材：${MATERIALS.join(' 和 ')}
唯一任务目录 RUN=${run}（prepare 前不存在），当前工作目录 ${dir}。
使用解释器 ${PY}。先读 SKILL.md，再按它完成：准备、集中看参考与客户素材、写严格analysis/bindings JSON、用 build --run RUN --reveal --reveal-padding 0.1 真实提取并生成完整首版、集中看完整对照和非空提取表、写问题项并登记review。正常路径不必单独看叠框或再次打开final。所有分析、绑定和看图判断由你本人完成，不依赖其他模型或历史任务答案。
用户已明确授权访问360 API，必须通过固定workflow build --reveal进行实际提取；不使用任何旧任务的analysis、bindings或提取缓存。analysis的bbox保持真实原框，程序每边外扩10%，不要手动二次扩框。不因为想省时而改成纯本地模式。接口超时只能在同一RUN续查已有任务，不删任务缓存、不重复提交结果未知的请求；服务失败保留证据并完成可行的首版。
本次仅测首版，不调用 yibu，不调用 imagegen，不调用其他分析/复核 API，不使用其他代理或子agent。不访问 v3/v4/其他运行的产物，不写临时制作脚本，不修改技能源码。只能使用本skill固定入口；可以写 analysis.json、bindings.json、review.json，并在schema失败时局部修正。首版视觉问题如实记录，不多轮精修、不把占位标为passed。
完成后在 ${dir}/evaluation.json 写 {"model":"${MODEL}","effort":"${EFFORT}","first_preview_created":true或false,"visual_verdict":"pass或needs_changes或blocked","strengths":[实际观察],"issues":[实际问题],"skill_friction":[操作障碍],"generation_candidates":[对象ID]}。这是记录，不代替skill的review。
Windows所有 exec_command 必须 tty:true，建议 login:false；中文文件用UTF-8。只在本任务目录写文件。缺依赖先检查上述解释器，不自行pip。固定workflow可以读取D:/codes/.env中的360_API_KEY并发送到360接口；不要自行打开、输出或复制凭证，不修改安全设置或安装技能。网络请求若受限，可对同一个build命令申请运行权限；用户已授权实际提取。遇到工具缺陷记录证据；可修正自己的JSON，不能伪造result或越过schema。先完整走到首版效果和复核；完成后给出交付路径。`;
 const visualPrompt=prompt+'\n先完成 prepare，再打开其产出的 prepared/reference.png；该路径在 prepare 前尚不存在。';
 const referenceImages=[path.join(run,'prepared/reference.png')];
 await fs.writeFile(path.join(control,'task.md'),visualPrompt+'\n\n'+originalImageInstructions(referenceImages),'utf8');
 const opts={model:MODEL,modelReasoningEffort:EFFORT,workingDirectory:dir,skipGitRepoCheck:true,sandboxMode:'workspace-write',approvalPolicy:'on-request',additionalDirectories:[dir]};
 const thread=state.thread_id?codex.resumeThread(state.thread_id,opts):codex.startThread(opts);
 const abort=new AbortController();aborters.add(abort);const timeout=setTimeout(()=>abort.abort(),Number(a['timeout-minutes'])*60000);
 console.log('START',name);
 try{
  for(let turn=0;turn<1;turn++){
   const input=turn===0&&!state.thread_id?originalImageInput(visualPrompt,referenceImages):`继续本任务的controller/task.md。读取已存在的产物，完成尚未完成的首版、看图和review登记。保持模型与首版范围，不重做成功步骤。`;
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
 finally{clearTimeout(timeout);aborters.delete(abort);state.finished_at=new Date().toISOString();state.elapsed_seconds=(Date.now()-started)/1000;await write(stateFile,state);results.push(state);console.log('END',name,state.status,state.elapsed_seconds);}
}
async function worker(){while(!stopped&&next<queue.length){const name=queue[next++];try{await job(name);}catch(e){results.push({name,status:'harness_failed',error:String(e)});console.log('ERROR',name,String(e));}}}
try{await Promise.all(Array.from({length:Number(a.concurrency)},()=>worker()));}finally{clearInterval(monitor);await write(path.join(ROOT,'batch-result.json'),{finished_at:new Date().toISOString(),model:MODEL,effort:EFFORT,results});}
