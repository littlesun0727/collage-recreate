import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const app = readFileSync(new URL('../workbench/web/app.js', import.meta.url), 'utf8');
const first = {id:'first', image:'/first-no-stroke.png', label:'首版'};
const latest = {id:'latest', parent_id:'first', image:'/latest-stroke.png', label:'成图更新'};
const state = {stage:null, version:null, mode:'side', data:{versions:[first, latest], current_stage:5,
  current_image:latest.image, first_image:first.image, reference:'/reference.png'}};
const element = {querySelectorAll:()=>[], classList:{toggle(){}}, checked:false};
const context = vm.createContext({state, $:()=>element, document:{querySelectorAll:()=>[]}, shortTime:()=>''});
vm.runInContext(app.slice(app.indexOf('function selectedVersion('), app.indexOf('function buildTaskGroups(')), context);
// Exercise image selection before the unrelated canvas DOM construction.
const canvas = app.slice(app.indexOf('function renderCanvas('), app.indexOf('  const key = JSON.stringify([', app.indexOf('function renderCanvas(')));
vm.runInContext(canvas + 'return {right, rl};\n}', context);

test('opening a task and clicking compose both show the latest image', () => {
  state.version = null;
  for (const stage of [null, 5, 3, 5]) {
    state.stage = stage;
    assert.equal(context.selectedVersion().id, 'latest');
    if (stage !== 3) assert.equal(context.renderCanvas().right, latest.image);
  }
});

test('explicit historical selection shows that version, clearing it returns to latest', () => {
  state.stage = 5;
  state.version = 'first';
  assert.equal(context.renderCanvas().right, first.image);
  state.version = null;
  assert.equal(context.renderCanvas().right, latest.image);
});

test('a task without recorded versions shows its current image instead of stale first preview', () => {
  state.data.versions = [];
  assert.equal(context.selectedVersion(), null);
  assert.equal(context.renderCanvas().right, latest.image);
});
