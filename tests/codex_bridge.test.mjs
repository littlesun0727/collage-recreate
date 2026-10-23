import test from 'node:test';
import assert from 'node:assert/strict';
import {infer, errorCode, invocationOptions} from '../collage/vlm/codex/codex_bridge.mjs';

const request = {model:'gpt-6.1-sol', effort:'medium', schema:{type:'object'}, input:[{type:'text',text:'test'}],
  instructions_file:'instructions.txt', working_directory:'scratch', timeout_ms:1000};

function fake(events, record={}) {
  return class {
    constructor(options) { record.sdk=options; }
    startThread(options) {
      record.threads=(record.threads||0)+1;
      record.thread=options;
      return {id:'test-thread', async runStreamed(input, turn) {
        record.input=input; record.turn=turn;
        return {events:(async function*(){for(const event of events)yield event;})()};
      }};
    }
  };
}

test('fresh medium threads, schema and usage; never return reasoning',async()=>{
  const record={};
  const Fake=fake([
    {type:'item.completed',item:{type:'reasoning',text:'PRIVATE'}},
    {type:'item.completed',item:{type:'agent_message',text:'{"ok":true}'}},
    {type:'turn.completed',usage:{input_tokens:20,output_tokens:10,cached_input_tokens:5}},
  ],record);
  const result=await infer(request,Fake);
  await infer(request,Fake);
  assert.equal(record.threads,2);
  assert.equal(record.thread.model,'gpt-6.1-sol');
  assert.equal(record.thread.modelReasoningEffort,'medium');
  assert.equal(record.thread.sandboxMode,'read-only');
  assert.equal(record.thread.approvalPolicy,'never');
  assert.equal(record.thread.webSearchMode,'disabled');
  assert.equal(record.sdk.config.features.shell_tool,false);
  assert.equal(record.sdk.config.model_instructions_file,'instructions.txt');
  assert.deepEqual(record.turn.outputSchema,request.schema);
  assert.deepEqual(record.input,request.input);
  assert.equal(result.choices[0].message.content,'{"ok":true}');
  assert.deepEqual(result.usage,{prompt_tokens:20,completion_tokens:10,prompt_tokens_details:{cached_tokens:5}});
  assert.equal(JSON.stringify(result).includes('PRIVATE'),false);
});

test('schema-off omits SDK outputSchema while preserving Luna medium and JSON input',async()=>{
  const record={};
  const {schema,...withoutSchema}=request;
  const result=await infer({...withoutSchema,model:'gpt-6-luna'},fake([
    {type:'item.completed',item:{type:'agent_message',text:'{"ok":true}'}},
    {type:'turn.completed',usage:{input_tokens:20,output_tokens:10}},
  ],record));
  assert.equal(Object.hasOwn(record.turn,'outputSchema'),false);
  assert.equal(record.thread.model,'gpt-6-luna');
  assert.equal(record.thread.modelReasoningEffort,'medium');
  assert.deepEqual(record.input,request.input);
  assert.equal(result.choices[0].message.content,'{"ok":true}');
});

for(const type of ['command_execution','file_change','mcp_tool_call','web_search']) {
  test(`reject ${type}`,async()=>{
    assert.deepEqual(await infer(request,fake([{type:'item.started',item:{type}}])),{error:'tools'});
  });
}

test('incomplete streams cannot become successful replies',async()=>{
  assert.deepEqual(await infer(request,fake([{type:'item.completed',item:{type:'agent_message',text:'{}'}}])),{error:'invalid'});
});

test('errors are classified without exposing raw SDK messages',async()=>{
  assert.deepEqual(await infer(request,fake([{type:'turn.failed',error:{message:'401 secret-token'}}])),{error:'auth'});
  assert.equal(errorCode(new Error('429 secret')), 'quota');
  assert.equal(errorCode(new Error('model not supported secret')), 'model');
});

test('deadline aborts the active SDK request',async()=>{
  class Slow {
    startThread(){return {async runStreamed(input,{signal}){
      return {events:(async function*(){
        await new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true}));
      })()};
    }};}
  }
  assert.deepEqual(await infer({...request,timeout_ms:20},Slow),{error:'timeout'});
});

test('worker shim preserves sandbox and signal while adding session/window flags',()=>{
  const signal=new AbortController().signal;
  const result=invocationOptions(['exec','--sandbox','read-only'],{signal,env:{TEST:'1'}});
  assert.deepEqual(result.args,['exec','--ephemeral','--ignore-user-config','--sandbox','read-only']);
  assert.equal(result.options.windowsHide,true);
  assert.equal(result.options.signal,signal);
  assert.deepEqual(invocationOptions(['login','status']).args,['login','status']);
});
