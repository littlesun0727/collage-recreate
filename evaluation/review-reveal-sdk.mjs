/** One independent gpt-5.6-sol/medium visual review per frozen first render. */
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {createHash} from 'node:crypto';
import {originalImageInput} from './image-input.mjs';
const SKILL=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const {values:a}=parseArgs({options:{root:{type:'string'},names:{type:'string'},concurrency:{type:'string',default:'3'},'build-mode':{type:'string',default:'cached'},'wait-build':{type:'boolean',default:false}}});
if(!a.root)throw new Error('--root required');
if(!['cached','live-frozen'].includes(a['build-mode']))throw new Error('Unknown build mode');
const ROOT=path.resolve(a.root),MODEL='gpt-5.6-sol',EFFORT='medium';
const PY='D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe';
const {Codex}=await import(pathToFileURL('D:/codes/collage_batch/node_modules/@openai/codex-sdk/dist/index.js').href);
const read=async p=>JSON.parse((await fs.readFile(p,'utf8')).replace(/^\uFEFF/,''));
const write=async(p,v)=>{await fs.mkdir(path.dirname(p),{recursive:true});await fs.writeFile(p,JSON.stringify(v,null,2)+'\n','utf8');};
const hash=async p=>createHash('sha256').update(await fs.readFile(p)).digest('hex');
const files={};
async function snapshot(dir){for(const e of await fs.readdir(dir,{withFileTypes:true})){
 if(e.name.startsWith('.')||['evaluation','tests','__pycache__','node_modules','PLAN.md'].includes(e.name))continue;
 const p=path.join(dir,e.name);if(e.isDirectory())await snapshot(p);else if(/\.(py|json|md)$/.test(e.name))files[path.relative(SKILL,p)]=await hash(p);
}}
await snapshot(SKILL);
const manifest=path.join(ROOT,'review-manifest.json');
if(existsSync(manifest)){if(JSON.stringify((await read(manifest)).skill_files)!==JSON.stringify(files))throw new Error('Skill changed since review snapshot');}
else{
 await write(manifest,{started_at:new Date().toISOString(),model:MODEL,effort:EFFORT,mode:'frozen-analysis-production-build-visual-review',build_mode:a['build-mode'],skill_files:files,review_remote_extraction_submissions:0});
 for(const file of Object.keys(files)){const dest=path.join(ROOT,'_skill_snapshot',file);await fs.mkdir(path.dirname(dest),{recursive:true});await fs.copyFile(path.join(SKILL,file),dest);}
}
const queue=[];for(const n of await fs.readdir(ROOT))if(existsSync(path.join(ROOT,n,'build-evidence.json'))&&(!a.names||a.names.split(',').includes(n)))queue.push(n);
const codex=new Codex({codexPathOverride:'C:/Users/admin/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe',env:{...process.env,CODEX_HOME:'C:/Users/admin/.codex'},config:{features:{image_generation:false,multi_agent:false}}});
let next=0;const states=[];
async function job(name){
 const dir=path.join(ROOT,name),run=path.join(dir,'run'),control=path.join(dir,'review-controller'),sf=path.join(control,'state.json');
 if(existsSync(sf)){states.push(await read(sf));console.log('EXISTS',name);return;}
 const result=await read(path.join(run,'result.json'));
 const before={};for(const file of ['analysis.json','bindings.json','scene.json','final.png','previews/first.png'])before[file]=await hash(path.join(run,file));
 const state={case:name,model:MODEL,effort:EFFORT,status:'running',started_at:new Date().toISOString(),sdk_run_calls:1,input_hashes:before};await write(sf,state);
 const prompt=`请作为唯一视觉复核者，按 ${SKILL}/SKILL.md 和 references/review.md 评估现成首版。模型为 ${MODEL} / ${EFFORT}。
RUN=${run}；Python=${PY}；仅允许写本案例目录 ${dir}。
${a['build-mode']==='live-frozen'?'这是沿用本轮已冻结analysis/bindings、实际请求360并回填客户素材的第一次成图。没有重新分析，也没有先修图。':'这是固定旧analysis/bindings、使用客户素材与现有Reveal提取缓存后的生产build测试，不是重新分析测试。'}先读skill和review说明，再读RUN/result.json、analysis.json、assets/reveal/index.json；只在需要识别对象ID时读scene。集中看完整对照图和result.extraction_sheets所有图片，必要时补看细节。原参考和新客户人物不同是正常替换，不要求相貌一致。
重点判断客户照片完整、裁切正常，提取贴纸是否残留参考人像/背景，相框原照片是否清净、回填窗口是否漏边，文字是否叠印/缺失，版式和关键装饰是否可作为可接受首版。不要因为文件存在或JSON正确就pass；result.incomplete_objects非空不可pass。可以接受细微字体/纹理差异，不强求最终精修。
本轮只复核：不改analysis/bindings/scene/图片，不重新build，不apply，不生成，不访问凭证或请求360/yibu，不运行其他代理。写RUN/review.json（真实render_id，checked_entire_composition=true，列实际问题ID，verdict pass或needs_changes），执行 PY SKILL/scripts/workflow.py review --run RUN --file RUN/review.json 登记。
在 ${dir}/evaluation.json 写 {"model":"${MODEL}","effort":"${EFFORT}","visual_verdict":"pass或needs_changes或blocked","strengths":[实际观察],"issues":[实际问题],"skill_friction":[操作障碍],"generation_candidates":[对象ID]}。如果工具登记失败，记录原因，不伪造result。
Windows所有exec_command使用tty:true，中文UTF-8。所有看图使用view_image detail original并image(...,"original")。读图后如实完成一次复核，不循环修图。`;
 await fs.writeFile(path.join(control,'task.md'),prompt,'utf8');
 const thread=codex.startThread({model:MODEL,modelReasoningEffort:EFFORT,workingDirectory:dir,skipGitRepoCheck:true,sandboxMode:'workspace-write',approvalPolicy:'on-request',additionalDirectories:[dir]});
 const started=Date.now(),abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),12*60000);
 console.log('START',name);
 try{
  const {events}=await thread.runStreamed(originalImageInput(prompt,[path.join(run,'previews/comparison.png')]),{signal:abort.signal});
  for await(const e of events){
   if(e.type==='thread.started'){state.thread_id=e.thread_id;await write(sf,state);}
   if(e.type==='turn.completed')state.usage=e.usage;
   if(e.type==='turn.failed'||e.type==='error')state.error=e.error??e.message;
   if(e.item?.type!=='reasoning')await fs.appendFile(path.join(control,'events.jsonl'),JSON.stringify({at:new Date().toISOString(),...e})+'\n','utf8');
  }
  state.inputs_unchanged=true;for(const [f,h] of Object.entries(before))if(await hash(path.join(run,f))!==h)state.inputs_unchanged=false;
  const r=await read(path.join(run,'result.json'));
  state.status=existsSync(path.join(dir,'evaluation.json'))&&r.visual_review&&state.inputs_unchanged?'completed':'incomplete';
  state.verdict=r.status;
 }catch(e){state.status=abort.signal.aborted?'timed_out':'failed';state.error=String(e);}
 finally{clearTimeout(timeout);state.finished_at=new Date().toISOString();state.elapsed_seconds=(Date.now()-started)/1000;await write(sf,state);states.push(state);console.log('END',name,state.status,state.verdict,state.elapsed_seconds);}
}
const queued=new Set(queue),waitDeadline=Date.now()+45*60000;
let refreshing=null;
async function refresh(){
 if(refreshing)return refreshing;
 refreshing=(async()=>{for(const n of await fs.readdir(ROOT))if(!queued.has(n)&&existsSync(path.join(ROOT,n,'build-evidence.json'))&&(!a.names||a.names.split(',').includes(n))){queued.add(n);queue.push(n);}})();
 try{await refreshing;}finally{refreshing=null;}
}
async function worker(){while(true){
 if(next<queue.length){await job(queue[next++]);continue;}
 if(!a['wait-build'])return;
 await refresh();if(next<queue.length)continue;
 if(existsSync(path.join(ROOT,'build-batch.json'))||Date.now()>waitDeadline)return;
 await new Promise(resolve=>setTimeout(resolve,2000));
}}
await Promise.all(Array.from({length:Number(a.concurrency)},()=>worker()));
const recorded=[];for(const n of await fs.readdir(ROOT)){const sf=path.join(ROOT,n,'review-controller/state.json');if(existsSync(sf))recorded.push(await read(sf));}
await write(path.join(ROOT,'review-batch.json'),{model:MODEL,effort:EFFORT,states:recorded});
