import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Exercise the exact browser grouping functions without starting its polling/DOM code.
const app = readFileSync(new URL('../workbench/web/app.js', import.meta.url), 'utf8');
const context = vm.createContext({ state: {} });
vm.runInContext(app.slice(app.indexOf('function buildTaskGroups('), app.indexOf('function taskList(')), context);
const group = context.buildTaskGroups;
const policy = model => ({ analyze: { provider: 'codex', model }, subsequent: { provider: 'codex', model } });
const batch = (id, model, index, count = 2) => ({
  id, name: `${model} | ${index}`, created_at: `2026-10-23T${String(index).padStart(2, '0')}:00:00Z`,
  model_policy: policy(model), total: count, counts: { completed: count - 1, failed: 1, running: 0, queued: 0 },
  items: Array.from({ length: count }, (_, i) => ({ index: i + 1, task_id: `${id}-${i}`, name: `${id}-${i}.png`, status: i ? 'completed' : 'failed' })),
});

test('research cases form three separate model groups with Sol first and preserve legacy links', () => {
  const full = '5def21db9044e70268387af0e2aea5bb7f25e0103a645b4f5291878db15eff3e';
  const check = '63650c39ac72058638219568e04fa8185005a2da0824505184a14b74eb35512f';
  const rest = 'a75cd4390b710ee7f2e3712234c1fc994cce9d2771f86fdfbf1ca08834910646';
  const research = [
    {...batch('qwen-case', 'qwen3.8-flash', 2, 22), dataset_sha256:full},
    {...batch('luna-case', 'gpt-6-luna', 2, 22), dataset_sha256:full},
    {...batch('sol-check', 'gpt-6.1-sol', 2, 1), dataset_sha256:check},
    {...batch('sol-rest', 'gpt-6.1-sol', 3, 21), dataset_sha256:rest},
  ];
  const groups = group([], [...research, batch('history', 'gpt-6.1-sol', 1)]);
  assert.equal(groups.length, 4);
  assert.match(groups[0].name, /预研部case · Sol/);
  for (const g of groups.slice(0, 3)) {
    assert.equal(g.total, 22);
    assert.equal(g.tasks.length, 22);
    assert.equal(g.researchCase, true);
  }
  assert.equal(groups[3].total, 2);
  assert.equal(new Set(groups.flatMap(g => g.tasks.map(t => t.id))).size, 68);
  context.state.batch = 'sol-rest';
  assert.equal(context.selectedTaskGroup(groups).id, groups[0].id);
  const offline = group([{id:'offline-case', name:'case.jpg', batch:{...research[0], total:1, index:1}}], []);
  assert.equal(offline[0].researchCase, true);
});

test('21 execution chunks become three models with 116 cases and continuous numbering each', () => {
  const batches = ['qwen3.8-flash', 'gpt-6-luna', 'gpt-6.1-sol'].flatMap(model =>
    [16, 17, 18, 19, 20, 11, 15].map((n, i) => batch(`${model}-${i}`, model, i + 1, n)));
  const groups = group([], batches.reverse());
  assert.equal(groups.length, 3);
  for (const g of groups) {
    assert.equal(g.total, 116);
    assert.equal(g.tasks.length, 116);
    assert.equal(g.batchIds.length, 7);
    assert.equal(g.counts.completed, 109);
    assert.equal(g.counts.failed, 7);
    assert.deepEqual(Array.from(g.tasks, t => t.index), Array.from({ length: 116 }, (_, i) => i + 1));
    assert.match(g.tasks[0].id, /-0-0$/);
  }
});

test('old batch links resolve to the entire model, keeping their selected task', () => {
  const groups = group([], [batch('first', 'sol', 1), batch('second', 'sol', 2)]);
  context.state.batch = 'second';
  const selected = context.selectedTaskGroup(groups);
  assert.equal(selected.id, groups[0].id);
  assert.equal(selected.tasks.length, 4);
  assert.ok(selected.tasks.some(t => t.id === 'second-1'));
});

test('partial queue listings preserve offline tasks and thumbnails without duplicating queue tasks', () => {
  const b = batch('first', 'sol', 1);
  const tasks = [{ id: 'first-0', thumbnail: '/image.png', name: 'first.png', batch: b },
    { id: 'offline', name: 'offline.png', batch: { ...b, id: 'offline-batch', index: 1, total: 1 } },
    { id: 'single', name: 'single.png' }];
  const groups = group(tasks, [b]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].total, 3);
  assert.equal(groups[0].tasks.length, 3);
  assert.equal(groups[0].tasks[0].thumbnail, '/image.png');
  assert.equal(groups[1].id, 'ungrouped');
  assert.equal(groups[1].tasks.length, 1);
});

test('policy key order does not split a model; distinct model configurations stay distinguishable', () => {
  const first = batch('first', 'sol', 1), second = batch('second', 'sol', 2), third = batch('third', 'sol', 3);
  second.model_policy = { subsequent: { model: 'sol', provider: 'codex' }, analyze: { model: 'sol', provider: 'codex' } };
  third.model_policy.analyze.model = 'luna';
  const groups = group([], [first, second, third]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].total, 4);
  assert.notEqual(groups[0].id, groups[1].id);
  assert.equal(group([], [second])[0].id, groups[0].id);
});

test('pending imports remain visible and empty legacy group is omitted', () => {
  const b = batch('pending', 'sol', 1, 1);
  b.items[0].task_id = null;
  b.items[0].status = 'queued';
  b.counts = { completed: 0, failed: 0, running: 0, queued: 1 };
  const groups = group([], [b]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].tasks[0].id, null);
  assert.equal(groups[0].counts.queued, 1);
});

test('merged batches with different customer inputs do not claim one fixed material set', () => {
  const first = batch('first', 'sol', 1), second = batch('second', 'sol', 2);
  first.materials = [{ name: 'first.jpg', sha256: 'first' }];
  second.materials = [{ name: 'second.jpg', sha256: 'second' }];
  assert.equal(group([], [first, second])[0].materials, undefined);
  second.materials = first.materials;
  assert.equal(group([], [first, second])[0].materials.length, 1);
});
