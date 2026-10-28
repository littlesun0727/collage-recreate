import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const app = readFileSync(new URL('../workbench/web/app.js', import.meta.url), 'utf8');
const code = app.slice(app.indexOf('let catalogueRequest ='), app.indexOf('\ndocument.addEventListener("click"'));

function setup() {
  const calls = [], pending = new Map(), rendered = [];
  const context = vm.createContext({
    state: {id: 'a', data: null}, document: {hidden: false},
    api(url) {
      calls.push(url);
      return new Promise(resolve => pending.set(url, resolve));
    },
    $: () => ({style: {}}), taskList() {}, availableTasks: () => [{id:'a'}, {id:'b'}],
    render() { rendered.push(context.state.data.id); }, renderPage() {}, selectTask() {},
  });
  vm.runInContext(code, context);
  return {context, calls, pending, rendered};
}

test('click loads selected detail while catalogue is still pending', async () => {
  const {context, calls, pending, rendered} = setup();
  const first = context.refresh();
  assert.ok(calls.includes('/api/tasks/a'));
  context.state.id = 'b';
  const second = context.refresh();
  assert.ok(calls.includes('/api/tasks/b'));
  pending.get('/api/tasks/b')({id:'b'});
  await second;
  pending.get('/api/tasks/a')({id:'a'});
  await first;
  assert.deepEqual(rendered, ['b']);
  assert.equal(calls.filter(url => url === '/api/tasks').length, 1);
});

test('hidden tab stops polling and duplicate task requests are coalesced', async () => {
  const {context, calls, pending} = setup();
  context.document.hidden = true;
  await context.refresh();
  assert.equal(calls.length, 0);
  context.document.hidden = false;
  const first = context.refresh();
  await context.refresh();
  assert.equal(calls.filter(url => url === '/api/tasks/a').length, 1);
  pending.get('/api/tasks/a')({id:'a'});
  await first;
});
