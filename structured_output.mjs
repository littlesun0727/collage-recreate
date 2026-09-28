/** Lightweight wire-format contracts. Business/visual validation stays in Python. */
import {createHash} from 'node:crypto';

const str = {type:'string'};
const nullableString = {type:['string','null']};
const array = items => ({type:'array',items});
const choice = values => ({type:'string',enum:values});
const object = (properties, required=Object.keys(properties)) =>
  ({type:'object',properties,required,additionalProperties:false});
// Intentionally open: do not duplicate the executor's recipe/geometry language.
const parameters = {type:'object',additionalProperties:true};
const bbox = {type:'array',items:{type:'integer'},minItems:4,maxItems:4};
const base = {id:str,label:str,source_bbox_1000:bbox,review_notes:str};
const requiredBase = ['id','label','source_bbox_1000'];

export function schemaFor(task) {
  switch(task) {
    case 'analysis': return object({
      background:object({mode:choice(['fixed','slot']),kind:choice(['solid','texture','photo','unknown']),
        background_brief:str,slot_id:str,preserve_reason:str,review_notes:str},['mode','kind']),
      slots:array(object({...base,mode:choice(['photo','photo_feather','cutout','unknown']),
        display_brief:str,source_slot_id:str},[...requiredBase,'mode'])),
      texts:array(object({...base,default_text:nullableString,style_brief:str},[...requiredBase,'default_text'])),
      overlays:array(object({...base,action:choice(['basic_shape','reference_generate']),shape:parameters,
        generation_brief:str,attachment:{type:['object','null'],additionalProperties:true},
        requires_exact_content:{type:'boolean'},text_content:nullableString},[...requiredBase,'action'])),
      layer_order:array(object({type:choice(['background','slot','text','overlay']),id:str},['type'])),
      questions:array(str)
    });
    case 'describe': return object({descriptions:array(object({asset_id:str,description:str,
      subject_type:choice(['portrait','landscape','object','mixed','unknown']),
      shot:choice(['close_up','half_body','full_body','wide','unknown'])}))});
    case 'match': return object({bindings:array(object({slot_id:str,asset_id:nullableString,reason:str}))});
    case 'text': return object({text_bindings:array(object({key:str,text:nullableString,
      status:choice(['generated','unresolved']),reason:str}))});
    case 'plan': return object({schema_version:choice(['build-plan-v1']),items:array(object({
      key:str,method:choice(['draw','text','generate','cutout','feather','blocked']),reason:str,
      recipe:{type:['object','null'],additionalProperties:true},approximations:array(str),
      content_scope:object({keep:str,exclude_keys:array(str)})
    }))});
    case 'review': return object({reviews:array(object({key:str,status:choice(['passed','needs_changes']),
      issues:array(str),observed_text:nullableString,unexpected_content:array(str)},['key','status','issues']))});
    default: throw new Error(`Unknown structured-output task: ${task}`);
  }
}

export function outputContract(task, mode='light') {
  if(!['light','off'].includes(mode)) throw new Error('Structured output must be light or off');
  const schema = schemaFor(task);
  const serialized = JSON.stringify(schema);
  const format = {type:'json_schema',json_schema:{name:'collage_'+task.replaceAll('-','_'),strict:false,schema}};
  return {
    audit:{mode,task,version:'light-v1',schema_bytes:mode==='light'?Buffer.byteLength(serialized):0,
      schema_sha256:mode==='light'?createHash('sha256').update(serialized).digest('hex'):null},
    apply(payload) {
      if(mode==='light') payload.response_format = structuredClone(format);
      return payload;
    },
    verify(payload) {
      if(mode==='light' && JSON.stringify(payload.response_format)!==JSON.stringify(format))
        throw new Error('Structured output missing or altered on the wire');
      if(mode==='off' && payload.response_format)
        throw new Error('Baseline must not contain a structured-output format');
    }
  };
}
