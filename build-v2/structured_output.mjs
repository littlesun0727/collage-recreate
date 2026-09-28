/** Independent v2 wire contract: intent is open, geometry is not prescribed. */
import {createHash} from 'node:crypto';
const str={type:'string'}, list=items=>({type:'array',items});
const obj=properties=>({type:'object',properties,required:Object.keys(properties),additionalProperties:false});
export function schemaFor(task){
  if(task==='plan-units')return obj({schema_version:{type:'string',enum:['build-unit-decision-v2']},units:list(obj({
    member_keys:list(str),brief:str,method:{type:'string',enum:['draw','text','generate','compose','feather','blocked']}}))});
  if(task==='plan')return obj({schema_version:{type:'string',enum:['build-plan-v2']},items:list(obj({
    key:str,method:{type:'string',enum:['draw','text','generate','feather','blocked']},reason:str,
    intent:{type:'object',additionalProperties:true},content_scope:obj({keep:str,exclude_keys:list(str)}),
    approximations:list(str),related_keys:list(str)}))});
  if(task==='review')return obj({reviews:list(obj({key:str,status:{type:'string',enum:['passed','needs_changes']},
    issues:list(str),observed_text:{type:['string','null']},unexpected_content:list(str)})),combination_issues:list(str)});
  throw new Error('Unknown build-v2 task');
}
export function outputContract(task,mode='light'){
  if(!['light','off'].includes(mode))throw new Error('Invalid structured output mode');
  const schema=schemaFor(task), serialized=JSON.stringify(schema), format={type:'json_schema',json_schema:{name:'collage_'+task.replaceAll('-','_'),strict:false,schema}};
  return {audit:{mode,task,version:'build-v2-light-1',schema_bytes:mode==='light'?Buffer.byteLength(serialized):0,
    schema_sha256:mode==='light'?createHash('sha256').update(serialized).digest('hex'):null},
    apply(p){if(mode==='light')p.response_format=structuredClone(format);return p;},
    verify(p){if(mode==='light'&&JSON.stringify(p.response_format)!==JSON.stringify(format))throw new Error('Wire schema changed');
      if(mode==='off'&&p.response_format)throw new Error('Unexpected wire schema');}};
}
