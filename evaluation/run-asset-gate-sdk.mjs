/** Actual Codex Agent SDK skill exercise; no developer-authored visual decisions. */
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {createHash} from 'node:crypto';
import {originalImageInput} from './image-input.mjs';

const SOURCE=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const {values:a}=parseArgs({options:{root:{type:'string'},baseline:{type:'string'},names:{type:'string'},concurrency:{type:'string',default:'2'},resume:{type:'boolean',default:false}}});
if(!a.root||!a.baseline)throw new Error('--root and --baseline required');
const ROOT=path.resolve(a.root),BASE=path.resolve(a.baseline),SKILL=path.join(ROOT,'_skill_snapshot');
const MODEL='gpt-5.6-sol',EFFORT='medium',PY='D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe';
const sdkPath='D:/codes/collage_batch/node_modules/@openai/codex-sdk';
const {Codex}=await import(pathToFileURL(path.join(sdkPath,'dist/index.js')).href);
const read=async p=>JSON.parse((await fs.readFile(p,'utf8')).replace(/^\uFEFF/,''));
const write=async(p,v)=>{await fs.mkdir(path.dirname(p),{recursive:true});await fs.writeFile(p,JSON.stringify(v,null,2)+'\n','utf8');};
const hash=async p=>createHash('sha256').update(await fs.readFile(p)).digest('hex');
await fs.mkdir(ROOT,{recursive:true});
const files={};
for(const rel of ['scripts','references','schemas','SKILL.md','requirements.txt','requirements-repair.txt','repair-tools.local.json']){
 const collect=async p=>{const stat=await fs.stat(p);if(stat.isDirectory()){
  for(const e of await fs.readdir(p)){if(e!=='__pycache__')await collect(path.join(p,e));}
 }else files[path.relative(SOURCE,p)]=await hash(p);};
 await collect(path.join(SOURCE,rel));
}
let names=(await read(path.join(BASE,'manifest.json'))).names;
if(a.names)names=names.filter(n=>a.names.split(',').includes(n));
const priority=['拼贴3','拼贴5','拼贴6'];names.sort((x,y)=>(priority.includes(x)?priority.indexOf(x):99)-(priority.includes(y)?priority.indexOf(y):99));
const manifestFile=path.join(ROOT,'manifest.json');
if(existsSync(manifestFile)){
 const old=await read(manifestFile);
 if(!a.resume||JSON.stringify(old.skill_files)!==JSON.stringify(files))throw new Error('Resume requires unchanged implementation');
}else{
 for(const rel of Object.keys(files)){const dest=path.join(SKILL,rel);await fs.mkdir(path.dirname(dest),{recursive:true});await fs.copyFile(path.join(SOURCE,rel),dest);}
 // A read-only dependency reuse link avoids duplicating the large Python 3.14 environment.
 await fs.symlink(path.join(SOURCE,'.deps314'),path.join(SKILL,'.deps314'),'junction');
 await write(manifestFile,{model:MODEL,effort:EFFORT,sdk_version:(await read(path.join(sdkPath,'package.json'))).version,
  mode:'frozen-analysis-offline-skill-gate-recovery',baseline:BASE,skill:SKILL,skill_files:files,names,
  reference_analysis_origin:'Previously frozen SDK gpt-5.6-sol medium inputs',new_extraction_requests:0,
  developer_visual_decisions_provided:false,started_at:new Date().toISOString()});
}
const codex=new Codex({codexPathOverride:'C:/Users/admin/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe',
 env:{...process.env,CODEX_HOME:'C:/Users/admin/.codex'},config:{features:{image_generation:false,multi_agent:false}}});
const aborters=new Set();let stopped=false,next=0;const states=[];
const monitor=setInterval(()=>{if(existsSync(path.join(ROOT,'STOP'))){stopped=true;for(const c of aborters)c.abort();}},1000);

async function job(name){
 const dir=path.join(ROOT,name),run=path.join(dir,'run'),control=path.join(dir,'controller'),sf=path.join(control,'state.json');
 await fs.mkdir(control,{recursive:true});
 const previous=existsSync(sf)?await read(sf):{};
 if(previous.status==='completed'){states.push(previous);return;}
 if(!existsSync(path.join(run,'input.json'))){
  const old=path.join(BASE,name,'run');await fs.mkdir(run,{recursive:true});await fs.cp(path.join(old,'prepared'),path.join(run,'prepared'),{recursive:true});
  const inputs=await read(path.join(old,'input.json'));inputs.reference.file=path.join(run,'prepared/reference.png');await write(path.join(run,'input.json'),inputs);
  for(const f of ['analysis.json','bindings.json'])await fs.copyFile(path.join(old,f),path.join(run,f));
  const config=await read(path.join(old,'reveal-config.json'));config.remote=false;delete config.key_file;await write(path.join(run,'reveal-config.json'),config);
  await fs.copyFile(path.join(old,'final.png'),path.join(dir,'baseline.png'));
 }
 const inputHashes={};for(const f of ['analysis.json','bindings.json'])inputHashes[f]=await hash(path.join(run,f));
 const prompt=`请独立使用 ${SKILL}/SKILL.md 测试素材门禁与离线修复。你是本案例唯一制作决策和视觉评估者，模型 ${MODEL} / ${EFFORT}。开发者没有提供任何逐图修复答案。
任务目录 RUN=${run}；唯一可写案例目录 ${dir}；解释器 ${PY}。
已有input、prepared、analysis、bindings、reveal-config。分析与绑定来自之前冻结的指定模型会话。本次测试制作端，不重做分析或绑定；不要修改这两个JSON。仅使用已有提取缓存，禁止--reveal、请求360/yibu、生图或其他模型/子agent。
先阅读skill及需要的参考，运行固定 workflow.py build --run RUN（配置已经指向原始缓存且remote=false），由你实际触发编译渲染。查看result、完整对照图、非空素材表和gate表。若有可修复问题，按skill写一份明确的recovery计划并离线recover；最多一轮实质像素修复，必要时再提交一次对修复结果的accepted/rejected决定以完成准入，不反复试擦或扩大选区。清理区域、成员归属、是否可用必须由你对照参考实际看图判断；input_key从当前门禁报告复制，不猜值。必要的OCR/LaMa通过现成edits操作即可，已配置本地工具。不得修改skill源码、安装依赖或写临时图像处理脚本。
只进行证据支持的修复；复杂缺失、模糊边界或工具不支持时如实保留未解决。程序接受不证明语义完整，疑难隔离也不证明图层确实有错。对照 ${path.join(dir,'baseline.png')} 评估门禁/修复是否改善、有无误拦、有什么退化；基线同样不是标准答案。
完成后查看当前完整成图，写 RUN/review.json 并用 workflow.py review 登记。不能因照片恢复可见就把剩余缺层、旧照片残留或关键装饰缺失标为通过。previews/first.png由工具保留，不覆盖。
在 ${dir}/evaluation.json 记录 {"model":"${MODEL}","effort":"${EFFORT}","visual_verdict":"pass或needs_changes或blocked","comparison":"improved或mixed或unchanged或regressed","strengths":[],"issues":[],"gate_false_positives":[],"gate_missed_issues":[],"repairs_performed":[],"skill_friction":[]}，用实际对象ID和观察说明。工具失败也写明证据，不伪造完成或review。
Windows exec_command始终tty:true，中文UTF-8；view_image用detail original并image(...,"original")。只在本案例目录写文件，不读取开发者回归结论或其他案例的修复决定。`;
 await fs.writeFile(path.join(control,'task.md'),prompt,'utf8');
 const options={model:MODEL,modelReasoningEffort:EFFORT,workingDirectory:dir,skipGitRepoCheck:true,
  sandboxMode:'workspace-write',approvalPolicy:'on-request',additionalDirectories:[dir]};
 const thread=previous.thread_id?codex.resumeThread(previous.thread_id,options):codex.startThread(options);
 const state={name,model:MODEL,effort:EFFORT,status:'running',thread_id:previous.thread_id??null,
  sdk_run_calls:(previous.sdk_run_calls??0)+1,started_at:new Date().toISOString(),input_hashes:inputHashes};await write(sf,state);
 const abort=new AbortController();aborters.add(abort);const timer=setTimeout(()=>abort.abort(),20*60000);const started=Date.now();
 console.log('START',name);
 try{
  const {events}=await thread.runStreamed(originalImageInput(prompt,[path.join(run,'prepared/reference.png')]),{signal:abort.signal});
  for await(const event of events){
   if(event.type==='thread.started'){state.thread_id=event.thread_id;await write(sf,state);}
   if(event.type==='turn.completed')state.usage=event.usage;
   if(event.type==='turn.failed'||event.type==='error')state.error=event.error??event.message;
   if(event.item?.type!=='reasoning')await fs.appendFile(path.join(control,'events.jsonl'),JSON.stringify({at:new Date().toISOString(),...event})+'\n','utf8');
   if(event.type==='item.completed'&&event.item?.type==='agent_message')console.log(name+': '+event.item.text.replace(/\s+/g,' ').slice(0,180));
  }
  state.inputs_unchanged=true;for(const [f,h] of Object.entries(inputHashes))if(await hash(path.join(run,f))!==h)state.inputs_unchanged=false;
  state.status=existsSync(path.join(dir,'evaluation.json'))&&existsSync(path.join(run,'review.json'))&&state.inputs_unchanged?'completed':'incomplete';
 }catch(error){state.status=abort.signal.aborted?'timed_out':'failed';state.error=String(error);}
 finally{clearTimeout(timer);aborters.delete(abort);state.elapsed_seconds=(Date.now()-started)/1000;state.finished_at=new Date().toISOString();await write(sf,state);states.push(state);await write(path.join(ROOT,'progress.json'),{states});console.log('END',name,state.status,state.elapsed_seconds);}
}
async function worker(){while(!stopped&&next<names.length)await job(names[next++]);}
try{await Promise.all(Array.from({length:Number(a.concurrency)},worker));}
finally{clearInterval(monitor);await write(path.join(ROOT,'batch-result.json'),{model:MODEL,effort:EFFORT,states});}
