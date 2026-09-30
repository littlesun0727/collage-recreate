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
for(const file of ['evaluation/run-ownership-analysis-sdk.mjs','evaluation/image-input.mjs','evaluation/freeze-analysis.py'])skillFiles[file]=createHash('sha256').update(await fs.readFile(path.join(SKILL,file))).digest('hex');
const manifestFile=path.join(ROOT,'manifest.json');
if(existsSync(manifestFile)){
 const oldManifest=await read(manifestFile);
 if(!a.resume||JSON.stringify(oldManifest.skill_files)!==JSON.stringify(skillFiles))throw new Error('Use a new batch directory; resume requires the same skill implementation');
}else{
 await write(manifestFile,{started_at:new Date().toISOString(),model:MODEL,effort:EFFORT,skill:SKILL,skill_files:skillFiles,reference_dir:a['reference-dir'],materials:MATERIALS,names,concurrency:Number(a.concurrency),mode:'ownership-analysis-only',remote_reveal_authorized:false,production_allowed:false,image_transport:IMAGE_TRANSPORT,initial_runs_per_reference:1,sdk});
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
 const prompt=`请独立使用 ${SKILL}/SKILL.md 完成本参考图的联合解析。模型固定 ${MODEL} / ${EFFORT}，你是唯一分析主控。
参考图：${path.join(a['reference-dir'],name)}
客户素材：${MATERIALS.join(' 和 ')}
任务 RUN=${run}（prepare前不存在），当前目录 ${dir}，解释器 ${PY}。
本次用户只要看解析效果，不制作。读取SKILL及分析规范，通过固定workflow prepare准备原尺寸参考图和客户联系表；集中看原尺寸参考与客户素材，按当前skill写严格analysis.json、bindings.json。不要读取其他任务的JSON、旧图、旧评价或其他版本，全部从当前输入独立分析。不要只因为本轮是分析测试而省略照片绑定。
写好两个JSON后运行 ${PY} -X utf8 ${SKILL}/evaluation/freeze-analysis.py --run ${run}。它仅校验JSON、来源和生成叠框，绝不调用提取或制作。schema错误允许局部修正后重试；成功后首份JSON已冻结，不再修改。
冻结后以original打开返回的previews/boxes-first.png，一次集中自检框内内容、组合完整性、内容归属、文字关系和照片窗口覆盖。组合内部成员无需单独ID；不以对象数越少越好，不声称schema通过等于视觉正确。输出 ${dir}/analysis-review.json，格式 {"model":"${MODEL}","effort":"${EFFORT}","verdict":"pass或needs_changes","checked_ids":[所有检查对象ID],"strengths":[实际观察],"issues":[{"id":"对象ID或null表示漏项","category":"ownership或fragmentation或coverage或bbox或text或layering或production_limit","reason":"具体问题"}],"summary":"实际判断"}。这是同一主控自检，不是独立第三方验收；不编写额外逐元素审核表，不多轮改稿。
禁止build、apply、generate、提取、回填、抠图或成品渲染，禁止访问360/yibu/imagegen/其他分析API，不读取API密钥。不能用已有结果假装新分析，不调用子代理，不写临时制作脚本，不修改技能源码。只可写本任务目录。Windows exec_command必须tty:true，建议login:false，中文读写UTF-8。交付analysis/bindings、框选图和自检记录路径后结束。`;
 const visualPrompt=prompt+'\n先完成 prepare，再打开其产出的 prepared/reference.png；该路径在 prepare 前尚不存在。';
 const referenceImages=[path.join(run,'prepared/reference.png')];
 await fs.writeFile(path.join(control,'task.md'),visualPrompt+'\n\n'+originalImageInstructions(referenceImages),'utf8');
 const opts={model:MODEL,modelReasoningEffort:EFFORT,workingDirectory:dir,skipGitRepoCheck:true,sandboxMode:'workspace-write',approvalPolicy:'on-request',additionalDirectories:[dir]};
 const thread=state.thread_id?codex.resumeThread(state.thread_id,opts):codex.startThread(opts);
 const abort=new AbortController();aborters.add(abort);const timeout=setTimeout(()=>abort.abort(),Number(a['timeout-minutes'])*60000);
 console.log('START',name);
 try{
  for(let turn=0;turn<1;turn++){
   const input=turn===0&&!state.thread_id?originalImageInput(visualPrompt,referenceImages):`继续本任务的controller/task.md，只完成解析、冻结与自检。禁止制作或改动已冻结JSON。`;
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
   if(existsSync(path.join(dir,'analysis-review.json')))break;
  }
  state.status=existsSync(path.join(dir,'analysis-review.json'))&&existsSync(path.join(run,'analysis-freeze.json'))?'completed':'incomplete';
  if(existsSync(path.join(run,'analysis-freeze.json')))state.analysis_result=await read(path.join(run,'analysis-freeze.json'));
 }catch(e){state.status=abort.signal.aborted?(stopped?'stopped':'timed_out'):'failed';state.error=String(e);}
 finally{clearTimeout(timeout);aborters.delete(abort);state.finished_at=new Date().toISOString();state.elapsed_seconds=(Date.now()-started)/1000;await write(stateFile,state);results.push(state);console.log('END',name,state.status,state.elapsed_seconds);}
}
async function worker(){while(!stopped&&next<queue.length){const name=queue[next++];try{await job(name);}catch(e){results.push({name,status:'harness_failed',error:String(e)});console.log('ERROR',name,String(e));}}}
try{await Promise.all(Array.from({length:Number(a.concurrency)},()=>worker()));}finally{clearInterval(monitor);await write(path.join(ROOT,'batch-result.json'),{finished_at:new Date().toISOString(),model:MODEL,effort:EFFORT,results});}
