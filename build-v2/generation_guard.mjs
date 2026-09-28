/** Audit fetches inside bl; reject a repeated model submission. */
import fs from 'node:fs';
import {createHash} from 'node:crypto';
const log=process.env.COLLAGE_GENERATION_AUDIT;
const nativeFetch=globalThis.fetch;
const rows=[];let submissions=0;
const save=()=>{if(log)fs.writeFileSync(log,JSON.stringify({model_submissions:submissions,requests:rows},null,2)+'\n');};
save();
globalThis.fetch=async(input,init)=>{
  const url=new URL(typeof input==='string'||input instanceof URL?input:input.url);
  const method=(init?.method??(input instanceof Request?input.method:'GET')).toUpperCase();
  const isModel=method==='POST'&&(/\/services\/(aigc|aigeneration)\//.test(url.pathname)||/\/images\/(generations|edits)$/.test(url.pathname));
  const row={origin:url.origin,path:url.pathname,method,model_submission:isModel};
  if(isModel){
    if(submissions>=1){row.status='blocked_duplicate';rows.push(row);save();throw new Error('Repeated model submission disabled by build');}
    submissions++;
    const body=typeof init?.body==='string'?init.body:input instanceof Request?await input.clone().text():null;
    if(body){row.body_sha256=createHash('sha256').update(body).digest('hex');try{const p=JSON.parse(body);row.model=p.model;row.parameters=p.parameters;}catch{}}
  }
  rows.push(row);save();
  const started=performance.now();
  try{
    const response=await nativeFetch(input,init);row.http_status=response.status;
    if(isModel&&typeof response.clone==='function'){
      try{const result=await response.clone().json();row.request_id=result.request_id;row.usage=result.usage;
        row.api_code=result.code;row.returned_model=result.model;
        row.output_images=(result.output?.choices??[]).flatMap(c=>c.message?.content??[]).filter(c=>c.image).length;
      }catch{/* Preserve the original response if it isn't JSON. */}
    }
    row.elapsed_seconds=Number(((performance.now()-started)/1000).toFixed(3));save();return response;
  }
  catch(error){row.status='network_error';save();throw error;}
};
