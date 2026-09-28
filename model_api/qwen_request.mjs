/** Shared CLI implementation; each stage supplies its existing JSON contract. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {parseArgs} from 'node:util';
import {BASE_URL,CREDENTIALS,auditBase,credential,hash,readJSON,saveJSON,redact,imageMime,safePayload,post,chatResult} from './client.mjs';
export async function main({task:defaultTask,contract:outputContract,reasoning:defaultReasoning='high'}) {
  const {values:a}=parseArgs({options:{
    image:{type:'string'},images:{type:'string'},prompt:{type:'string'},output:{type:'string'},
    credentials:{type:'string'},config:{type:'string'},'openclaw-root':{type:'string'},
    'base-url':{type:'string'},provider:{type:'string',default:'yibu'},
    model:{type:'string',default:'gpt-5.6-sol'},task:{type:'string',default:defaultTask},
    reasoning:{type:'string',default:defaultReasoning},'timeout-seconds':{type:'string',default:'600'},
    'output-tokens':{type:'string',default:'32768'},'structured-output':{type:'string',default:'light'},
    'dry-run':{type:'boolean',default:false}
  }});
  if(!a.output || !a.prompt || (!a.image && !a.images)) throw new Error('Require --prompt, --output and --image or --images');
  await fs.mkdir(path.dirname(path.resolve(a.output)),{recursive:true});
  await fs.mkdir(a.output);
  const write=(name,value)=>saveJSON(path.join(a.output,name),value);
  const start=performance.now();
  const audit={status:'preparing',provider:'yibuapi',requested_model:a.model,reasoning:a.reasoning,
    http_dispatches:0,automatic_retries:0,tools:0,dry_run:a['dry-run'],started:new Date().toISOString()};
  let key='';
  try {
    const base=auditBase(a['base-url']);
    if(a.provider!=='yibu' && a.provider!=='yibuapi') throw new Error('Only yibu is supported for internal calls');
    if(!['off','low','high'].includes(a.reasoning)) throw new Error('Reasoning must be off, low or high');
    const timeout=Number(a['timeout-seconds'])*1000,tokens=Number(a['output-tokens']);
    if(!Number.isInteger(timeout) || timeout<=0 || !Number.isInteger(tokens) || tokens<1 || tokens>32768) throw new Error('Invalid request limits');
    key=await credential(a.credentials || a.config || CREDENTIALS);
    const prompt=(await fs.readFile(a.prompt,'utf8')).replace(/^\uFEFF/,'');
    const inputs=a.image?[{path:a.image,label:'完整参考图；按任务规范返回 JSON。'}]:await readJSON(a.images);
    if(!Array.isArray(inputs) || !inputs.length) throw new Error('No image inputs');
    const content=[];
    const images=[];
    for(const input of inputs) {
      const bytes=await fs.readFile(input.path),mime=imageMime(bytes);
      content.push({type:'text',text:input.label || '参考图'});
      content.push({type:'image_url',image_url:{url:'data:'+mime+';base64,'+bytes.toString('base64')}});
      images.push({sha256:hash(bytes),bytes:bytes.length,mime});
    }
    const payload={model:a.model,messages:[{role:'system',content:prompt},{role:'user',content}],
      stream:true,stream_options:{include_usage:true},max_completion_tokens:tokens,enable_thinking:a.reasoning!=='off'};
    if(a.reasoning!=='off') payload.reasoning_effort=a.reasoning;
    if(a.model.startsWith('gpt-')) {
      delete payload.enable_thinking;
      payload.reasoning_effort=a.reasoning==='off'?'none':a.reasoning;
    }
    if(a.model==='kimi-k3') {
      if(a.reasoning!=='high') throw new Error('This workflow requires Kimi K3 reasoning_effort=high');
      delete payload.enable_thinking;
      delete payload.max_completion_tokens;
      payload.max_tokens=tokens;
    }
    const contract=outputContract(a.task,a['structured-output']);
    contract.apply(payload);
    contract.verify(payload);
    audit.structured_output=contract.audit;
    Object.assign(audit,{api:'openai-chat',endpoint:base+'/v1/chat/completions',images,
      prompt_sha256:hash(prompt),effective_thinking:{enable_thinking:a.reasoning!=='off',reasoning_effort:payload.reasoning_effort || null},
      effective_max_completion_tokens:tokens});
    await write('request-body.redacted.json',safePayload(payload));
    if(a['dry-run']) audit.status='dry_run_verified';
    else {
      audit.http_dispatches=1;audit.status='request_in_flight';await write('call.json',audit);
      const response=await post('/v1/chat/completions',payload,key,{base,timeout});
      audit.http_status=response.status;audit.request_id=response.requestId;
      const result=chatResult(response.text);
      audit.request_id=response.requestId || result.id;
      await fs.writeFile(path.join(a.output,'raw-response.txt'),redact(result.content,key),'utf8');
      Object.assign(audit,{stop_reason:result.finish,returned_model:result.model,usage:result.usage,
        response_sha256:hash(result.content),status:result.finish==='stop' && result.content.length?'completed':'model_error_or_incomplete'});
      if(audit.status==='completed') JSON.parse(result.content);
    }
  } catch(error) {
    audit.status='failed';audit.error=redact(error.message,key);
    if(error.status) audit.http_status=error.status;
  }
  audit.elapsed_seconds=Number(((performance.now()-start)/1000).toFixed(3));audit.finished=new Date().toISOString();
  await write('call.json',audit);
  console.log(JSON.stringify({status:audit.status,http_dispatches:audit.http_dispatches,elapsed_seconds:audit.elapsed_seconds,output:a.output}));
  process.exitCode=['completed','dry_run_verified'].includes(audit.status)?0:2;
}
