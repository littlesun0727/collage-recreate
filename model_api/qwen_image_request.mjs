/** One Qwen reference-image request through the local audit gateway. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {parseArgs} from 'node:util';
import {CREDENTIALS,auditBase,credential,hash,saveJSON,redact,imageMime,post} from './client.mjs';
const {values:a}=parseArgs({options:{
 image:{type:'string'},prompt:{type:'string'},output:{type:'string'},
 credentials:{type:'string',default:CREDENTIALS},model:{type:'string',default:'qwen-image-3.0-pro'},
 ratio:{type:'string',default:'1:1'},size:{type:'string'},'timeout-seconds':{type:'string',default:'600'},'dry-run':{type:'boolean',default:false}
}});
if(!a.image||!a.prompt||!a.output) throw new Error('Require image, prompt and output');
await fs.mkdir(path.dirname(path.resolve(a.output)),{recursive:true});await fs.mkdir(a.output);
const write=(name,value)=>saveJSON(path.join(a.output,name),value);
const audit={status:'preparing',provider:'yibuapi',requested_model:a.model,http_dispatches:0,automatic_retries:0,dry_run:a['dry-run'],started:new Date().toISOString()};
let key='';const start=performance.now();
try {
 if(!['qwen-image-3.0','qwen-image-3.0-pro'].includes(a.model)) throw new Error('Unsupported Qwen image model');
 const base=auditBase(),route='/v1/images/generations';
 const timeout=Number(a['timeout-seconds'])*1000;
 if(!Number.isInteger(timeout)||timeout<=0) throw new Error('Invalid timeout');
 const [rw,rh]=a.ratio.split(':').map(Number);
 if(!(rw>0&&rh>0&&rw/rh>=1/8&&rw/rh<=8)) throw new Error('Invalid ratio');
 const width=Math.round(Math.sqrt(1048576*rw/rh)/16)*16,height=Math.round(Math.sqrt(1048576*rh/rw)/16)*16;
 const size=a.size||width+'x'+height;
 if(!/^\d+x\d+$/.test(size)) throw new Error('Invalid size');
 const [w,h]=size.split('x').map(Number);
 if(w*h<512*512||w*h>2048*2048||w/h<1/8||w/h>8)throw new Error('Invalid image dimensions');
 const bytes=await fs.readFile(a.image),mime=imageMime(bytes),prompt=(await fs.readFile(a.prompt,'utf8')).replace(/^\uFEFF/,'');
 key=await credential(a.credentials);
 const payload={model:a.model,prompt,image:'data:'+mime+';base64,'+bytes.toString('base64'),n:1,size,response_format:'b64_json'};
 Object.assign(audit,{endpoint:base+route,reference_sha256:hash(bytes),prompt_sha256:hash(prompt),size});
 await write('request-body.redacted.json',{...payload,image:{data_omitted:true,bytes:bytes.length,sha256:hash(bytes),mimeType:mime}});
 if(a['dry-run']) audit.status='dry_run_verified';
 else {
  audit.status='request_in_flight';audit.http_dispatches=1;await write('call.json',audit);
  const response=await post(route,payload,key,{base,timeout});
  audit.http_status=response.status;
  const data=JSON.parse(response.text);
  audit.request_id=response.requestId||data.request_id||null;audit.usage=data.usage||null;
  audit.returned_model=data.model||null;
  await write('response.redacted.json',JSON.parse(JSON.stringify(data,(k,v)=>k==='b64_json'?{data_omitted:true}:v)));
  if(data.error)throw new Error(JSON.stringify(data.error));
  if(!Array.isArray(data.data)||data.data.length!==1)throw new Error('Expected exactly one image');
  audit.image_count=1;
  const result=data.data[0];
  if(typeof result.b64_json==='string'&&result.b64_json.length) {
   if(!/^[A-Za-z0-9+/]*={0,2}$/.test(result.b64_json))throw new Error('Invalid base64');
   const generated=Buffer.from(result.b64_json,'base64');imageMime(generated);
   await fs.writeFile(path.join(a.output,'original-image.bin'),generated);
   audit.image_sha256=hash(generated);audit.status='completed';
  } else if(typeof result.url==='string') {
   const u=new URL(result.url);
   if(u.protocol!=='https:'||u.username||u.password||u.port||!u.hostname.endsWith('.aliyuncs.com'))throw new Error('Unexpected artifact URL');
   await write('artifact.json',{url:result.url});
   audit.status='image_url_returned';
  } else throw new Error('No image bytes or URL');
 }
} catch(e) {audit.status='failed';audit.error=redact(e.message,key);if(e.status)audit.http_status=e.status;}
audit.elapsed_seconds=Number(((performance.now()-start)/1000).toFixed(3));audit.finished=new Date().toISOString();
await write('call.json',audit);
console.log(JSON.stringify({status:audit.status,output:a.output}));
process.exitCode=['completed','image_url_returned','dry_run_verified'].includes(audit.status)?0:2;

