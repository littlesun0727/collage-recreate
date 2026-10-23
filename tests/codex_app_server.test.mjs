import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough, Writable} from 'node:stream';
import {mkdtempSync, writeFileSync, rmSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {OriginalImageCodex, originalInputs} from '../collage/vlm/codex/codex_app_server.mjs';
import {infer} from '../collage/vlm/codex/codex_bridge.mjs';

function fixture(t, notifications, stall = false) {
  const folder = mkdtempSync(path.join(os.tmpdir(), 'collage-app-server-test-'));
  t.after(() => rmSync(folder, {recursive: true, force: true}));
  const instructions = path.join(folder, 'instructions.txt');
  writeFileSync(instructions, 'Return JSON only.');
  const image = path.join(folder, 'large.png');
  writeFileSync(image, Buffer.from([137, 80, 78, 71]));
  const requests = [], child = new EventEmitter();
  child.stdout = new PassThrough(); child.exitCode = 0;
  child.stderr = new PassThrough();
  child.unref = () => {};
  const emit = value => child.stdout.write(JSON.stringify(value)+'\n');
  child.stdin = new Writable({write(buffer, encoding, done) {
    const message = JSON.parse(buffer.toString()); requests.push(message);
    if (!stall && message.id !== undefined && message.method) {
      const result = message.method === 'config/read' ? {config: {mcp_servers: {personal: {enabled: true}}}} :
        message.method === 'thread/start' ? {thread: {id: 'test-thread'}, model: 'gpt-6-luna'} : {};
      queueMicrotask(() => {
        emit({id: message.id, result});
        if (message.method === 'turn/start') notifications.forEach(emit);
      });
    }
    done();
  }});
  let launch;
  class Fake extends OriginalImageCodex {
    constructor(options) {
      super({...options, codexPathOverride: 'fake-codex'}, (file, args, settings) => {
        launch = {file, args, settings}; return child;
      });
    }
  }
  const request = {model: 'gpt-6-luna', effort: 'medium', timeout_ms: 500,
    instructions_file: instructions, working_directory: folder,
    input: [{type: 'text', text: 'reference'}, {type: 'local_image', path: image}]};
  return {request, Fake, requests, child, launch: () => launch};
}

const success = [
  {method: 'item/started', params: {item: {type: 'userMessage'}}},
  {method: 'item/completed', params: {item: {type: 'reasoning', text: 'PRIVATE'}}},
  {method: 'item/completed', params: {item: {type: 'agentMessage', phase: 'final_answer', text: '{"ok":true}'}}},
  {method: 'thread/tokenUsage/updated', params: {tokenUsage: {total: {inputTokens: 100, outputTokens: 10, cachedInputTokens: 30}}}},
  {method: 'turn/completed', params: {turn: {status: 'completed'}}},
];

test('App Server preserves original detail, native auth, schema-off, usage and safe settings', async t => {
  const f = fixture(t, success);
  const result = await infer(f.request, f.Fake);
  assert.equal(result.choices[0].message.content, '{"ok":true}');
  assert.deepEqual(result.usage, {prompt_tokens: 100, completion_tokens: 10, prompt_tokens_details: {cached_tokens: 30}});
  assert.equal(JSON.stringify(result).includes('PRIVATE'), false);
  const thread = f.requests.find(x => x.method === 'thread/start').params;
  assert.equal(thread.ephemeral, true);
  assert.equal(thread.baseInstructions, 'Return JSON only.');
  assert.equal(thread.sandbox, 'read-only');
  assert.equal(thread.config.mcp_servers.personal.enabled, false);
  const turn = f.requests.find(x => x.method === 'turn/start').params;
  assert.equal(turn.effort, 'medium');
  assert.equal(turn.model, 'gpt-6-luna');
  assert.equal(Object.hasOwn(turn, 'outputSchema'), false);
  assert.deepEqual(turn.input, [{type: 'text', text: 'reference'},
    {type: 'image', url: 'data:image/png;base64,iVBORw==', detail: 'original'}]);
  assert.deepEqual(turn.sandboxPolicy, {type: 'readOnly', networkAccess: false});
  assert.equal(f.launch().settings.windowsHide, true);
  assert.equal(f.launch().settings.env, process.env);
});

test('Schema-on still reaches turn/start', async t => {
  const f = fixture(t, success);
  await infer({...f.request, schema: {type: 'object'}}, f.Fake);
  assert.deepEqual(f.requests.find(x => x.method === 'turn/start').params.outputSchema, {type: 'object'});
});

test('Abort covers initialization, before a turn has started', async t => {
  const f = fixture(t, [], true);
  assert.deepEqual(await infer({...f.request, timeout_ms: 20}, f.Fake), {error: 'timeout'});
});

test('Startup failure reports unavailable home without exposing stderr', async t => {
  const f = fixture(t, [], true);
  queueMicrotask(() => {
    f.child.stderr.write('PRIVATE credential\nError: Could not find home directory\n');
    f.child.emit('close', 1);
  });
  assert.deepEqual(await infer(f.request, f.Fake), {error: 'home_unavailable'});
  assert.deepEqual(f.requests.map(x => x.method), ['initialize']);
});

for (const notification of [
  {method: 'item/started', params: {item: {type: 'commandExecution'}}},
  {id: 900, method: 'item/commandExecution/requestApproval', params: {}},
]) test('Tool notifications or approval requests fail closed', async t => {
  const f = fixture(t, [notification]);
  assert.deepEqual(await infer(f.request, f.Fake), {error: 'tools'});
});

test('Failed turns and model reroutes cannot become successful responses', async t => {
  const f = fixture(t, [{method: 'turn/completed', params: {turn: {status: 'failed', error: {message: '401 private-token'}}}}]);
  assert.deepEqual(await infer(f.request, f.Fake), {error: 'auth'});
  const other = fixture(t, [{method: 'model/rerouted', params: {toModel: 'other'}}]);
  assert.deepEqual(await infer(other.request, other.Fake), {error: 'model'});
});

test('Native image inputs preserve bytes and captions without file or attachment labels', t => {
  const folder = mkdtempSync(path.join(os.tmpdir(), 'collage-inline-image-test-'));
  t.after(() => rmSync(folder, {recursive: true, force: true}));
  const input = [], expected = [];
  for (const [i, ext] of ['png', 'jpeg', 'webp'].entries()) {
    const file = path.join(folder, `image-${i}.${ext}`);
    const bytes = Buffer.from([0, 127, 128, 255, i]);
    writeFileSync(file, bytes);
    const caption = {type: 'text', text: `material_${i}`};
    input.push(caption, {type: 'local_image', path: file, detail: 'high'});
    expected.push(caption, {type: 'image', detail: 'original',
      url: `data:image/${ext};base64,${bytes.toString('base64')}`});
  }
  assert.deepEqual(originalInputs(input), expected);
  assert.throws(() => originalInputs([{type: 'image_url', url: 'https://example.com'}]));
  assert.throws(() => originalInputs([{type: 'local_image', path: 'image.svg'}]));
});
