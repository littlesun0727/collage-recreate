"use strict";
const $ = (s) => document.querySelector(s);
const escapeHTML = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const statusText = {
  pending: "未开始",
  running: "执行中",
  waiting: "等待结果",
  complete: "已完成",
  skipped: "不适用",
  failed: "执行失败",
  blocked: "需要处理",
  saved: "已有产物",
  unresolved: "未解决",
};
const state = {
  id: new URLSearchParams(location.search).get("task"),
  page: new URLSearchParams(location.search).get("view") === "technical" ? "technical" : "workbench",
  techSlide: new URLSearchParams(location.search).get("slide") === "motion" ? "motion" : "overview",
  techArtifact: "analysis",
  techObject: null,
  revealObject: null,
  data: null,
  stage: null,
  version: null,
  mode: "side",
  zoom: 1,
  pan: [0, 0],
  focus: null,
  busy: false,
  tasks: [],
  batches: [],
  batch: new URLSearchParams(location.search).get("batch") || "",
  expandedBatches: new Map(),
  canvasKey: "",
  materialKeys: new Map(),
};
const shortTime = (x) =>
  x
    ? new Date(x).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "";
const duration = (x) => {
  const n = Math.max(0, Math.floor((Date.now() - Date.parse(x)) / 1000));
  return Number.isFinite(n) ? `${Math.floor(n / 60)}分${n % 60}秒` : "—";
};
const badge = (s) =>
  `<span class="badge ${escapeHTML(s)}">${escapeHTML(statusText[s] || s)}</span>`;
const setHTML = (el, html) => {
  if (el._html !== html) {
    el.innerHTML = html;
    el._html = html;
  }
};
function modelPolicyLabel(policy, fallback) {
  if (!policy?.analyze || !policy?.subsequent) return fallback || 'qwen/qwen3.8-flash';
  const name = (entry) => {
    const r=entry.reasoning||{}, mode=r.max_tokens?`thinking ≤ ${r.max_tokens}`:r.effort?`thinking ${r.effort}`:r.enabled===true?'thinking':r.enabled===false?'thinking 关闭':'推理默认';
    return `${({yibu:'yibuapi',openrouter:'OpenRouter',codex:'Codex SDK'})[entry.provider]||'OpenRouter'} / ${entry.model}（${mode}）`;
  };
  if (JSON.stringify(policy.analyze) === JSON.stringify(policy.subsequent)) return name(policy.subsequent);
  return `分析 ${name(policy.analyze)} · 后续 ${name(policy.subsequent)}`;
}
function renderTiming() {
  const timing = state.data?.sdk_timing;
  const el = $("#sdkTiming");
  el.hidden = !timing?.started_at;
  if (el.hidden) return;
  const seconds = timing.finished_at
    ? timing.elapsed_seconds
    : (Date.now() - Date.parse(timing.started_at)) / 1000;
  const n = Math.max(0, Math.floor(seconds));
  el.textContent = `自动制作 · ${modelPolicyLabel(timing.model_policy, timing.model)}${timing.effort ? ' / '+timing.effort : ''} · ${timing.finished_at ? "总耗时" : "已用时"} ${Math.floor(n / 60)}分${n % 60}秒`;
}
async function api(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) {
    let detail;
    try { detail = await r.json(); } catch (_) { /* A proxy may return HTML. */ }
    const message = typeof detail?.error === "string" ? `：${detail.error}` : "";
    const log = typeof detail?.log === "string" ? `；任务目录下的日志：${detail.log}` : "";
    throw Error(`HTTP ${r.status}${message}${log}`);
  }
  return r.json();
}
function selectedVersion() {
  const d = state.data;
  if (!d) return null;
  if (state.version)
    return d.versions.find((v) => v.id === state.version) || null;
  if (state.stage === 5) return d.versions[0] || null;
  return d.versions.at(-1) || null;
}
function taskList() {
  const term = $("#search").value.toLowerCase().trim();
  const statuses = {queued:"排队中",running:"执行中",completed:"已完成",failed:"未完成"};
  const byId = new Map(state.tasks.map(t => [t.id, t]));
  const groups = state.batches.map(batch => ({...batch, tasks:batch.items.map(item => ({
    ...(byId.get(item.task_id) || {}), id:item.task_id, name:item.name, index:item.index,
    status:item.status, phase:item.phase, error:item.error,
  }))}));
  // Offline runs and imported tasks carry batch metadata without a web queue record.
  // Keep those tasks visible, including tasks missing from a partial queue listing.
  const grouped = new Set(groups.flatMap(g => g.tasks.map(t => t.id)).filter(Boolean));
  for (const task of state.tasks) {
    if (!task.batch?.id || grouped.has(task.id)) continue;
    let group = groups.find(g => g.id === task.batch.id);
    if (!group) {
      group = {id:task.batch.id, name:task.batch.name || task.batch.id,
        created_at:task.batch.created_at, model_policy:task.batch.model_policy,
        tasks:[], total:task.batch.total || 0};
      groups.push(group);
    }
    group.tasks.push({...task, index:task.batch.index});
    group.total = Math.max(group.total, group.tasks.length);
    grouped.add(task.id);
  }
  for (const group of groups) group.tasks.sort((a,b) => (a.index || 0) - (b.index || 0));
  const ungrouped = state.tasks.filter(t => !t.batch?.id);
  groups.push({id:"ungrouped",name:"单独任务 / 历史任务",tasks:ungrouped,total:ungrouped.length});
  setHTML($("#batchFilter"), '<option value="">全部批次</option>'+groups.map(g =>
    `<option value="${escapeHTML(g.id)}">${escapeHTML(g.name)} (${g.total})</option>`).join(''));
  // A deep link can arrive while its first input is still being prepared.
  if (state.batch && !groups.some(g => g.id === state.batch)) {
    $("#batchFilter").insertAdjacentHTML('beforeend', `<option value="${escapeHTML(state.batch)}">批次暂不可用</option>`);
  }
  $("#batchFilter").value = state.batch;
  const selected = groups.find(g => g.id === state.batch);
  const summary = $("#batchSummary");
  summary.hidden = !selected?.model_policy;
  if (selected?.model_policy) {
    const c = selected.counts;
    const progress = c ? `完成 ${c.completed}/${selected.total} · 未完成 ${c.failed} · 执行中 ${c.running} · 排队 ${c.queued}` : `已载入 ${selected.tasks.length}/${selected.total} 个样本`;
    summary.textContent = `${modelPolicyLabel(selected.model_policy)} · ${progress}` +
      (selected.materials ? ` · 固定客户素材 ${selected.materials.length} 个` : '');
    summary.title = (selected.dataset_sha256 ? `输入指纹 ${selected.dataset_sha256}\n` : '') +
      new Date(selected.created_at).toLocaleString("zh-CN");
  }
  let count = 0;
  const html = groups.filter(g => !state.batch || state.batch === g.id).map((g, position) => {
    const matchGroup = `${g.name} ${g.model_policy ? modelPolicyLabel(g.model_policy) : ''}`.toLowerCase().includes(term);
    const tasks = g.tasks.filter(t => matchGroup || t.name.toLowerCase().includes(term));
    if (!tasks.length && (term || !g.total)) return '';
    count += tasks.length;
    const open = term || state.batch || (state.expandedBatches.has(g.id) ? state.expandedBatches.get(g.id) :
      position === 0 || tasks.some(t => t.id && t.id === state.id));
    const progress = g.counts ? `完成 ${g.counts.completed}/${g.total} · 未完成 ${g.counts.failed}` : `已载入 ${g.tasks.length}/${g.total} 个样本`;
    const cards = tasks.map(t => `<button class="task ${t.id && t.id === state.id ? 'active' : ''}" ${t.id ? `data-task="${escapeHTML(t.id)}"` : 'disabled'} ${t.error ? `title="${escapeHTML(t.error)}"` : ''}>
      ${t.thumbnail ? `<img src="${escapeHTML(t.thumbnail)}" alt="" loading="lazy">` : '<span class="task-placeholder">▧</span>'}
      <span><strong>${t.index ? String(t.index).padStart(2,'0')+' · ' : ''}${escapeHTML(t.name)}</strong>
      <small class="batch-task-${escapeHTML(t.status || 'saved')}">${escapeHTML(statuses[t.status] || (t.created_at ? new Date(t.created_at).toLocaleDateString('zh-CN') : ''))}${t.phase === 'import' ? ' · 准备素材' : ''}</small></span></button>`).join('');
    const description = g.model_policy ? `${modelPolicyLabel(g.model_policy)} · ${new Date(g.created_at).toLocaleString('zh-CN')}` : '';
    return `<details class="task-group" data-batch-group="${escapeHTML(g.id)}" ${open ? 'open' : ''}>
      <summary title="${escapeHTML(description)}"><strong>${escapeHTML(g.name)}</strong>${!state.batch && description ? `<small>${escapeHTML(description)}</small>` : ''}<small>${escapeHTML(progress)}</small></summary>${cards}</details>`;
  }).join('');
  setHTML($("#tasks"), html || '<p class="batch-empty">当前分组没有匹配的任务</p>');
  $("#taskCount").textContent = count;
}
function availableTasks() {
  const tasks = state.tasks.filter(t => !state.batch || (t.batch?.id || 'ungrouped') === state.batch);
  return state.batch && state.batch !== 'ungrouped' ? tasks.sort((a,b) => a.batch.index-b.batch.index) : tasks;
}
$("#batchFilter").onchange = () => {
  state.batch = $("#batchFilter").value;
  const u = new URL(location.href);
  if (state.batch) u.searchParams.set('batch', state.batch);
  else u.searchParams.delete('batch');
  history.replaceState({}, '', u);
  taskList();
  refresh();
};
$("#tasks").addEventListener('toggle', e => {
  if (e.target.matches('details[data-batch-group]')) {
    state.expandedBatches.set(e.target.dataset.batchGroup, e.target.open);
  }
}, true);
function selectTask(id) {
  const selected = state.tasks.find(t => t.id === id);
  if (state.batch && selected && (selected.batch?.id || 'ungrouped') !== state.batch) {
    state.batch = selected.batch?.id || 'ungrouped';
  }
  state.page = "workbench";
  state.techObject = null;
  state.revealObject = null;
  state.id = id;
  state.stage = null;
  state.version = null;
  state.data = null;
  state.focus = null;
  state.canvasKey = "";
  state.materialKeys.clear();
  state.zoom = 1;
  state.pan = [0, 0];
  const u = new URL(location.href);
  if (state.batch) u.searchParams.set('batch', state.batch);
  else u.searchParams.delete('batch');
  u.searchParams.set("task", id);
  u.searchParams.delete("view");
  u.searchParams.delete("slide");
  history.replaceState({}, "", u);
  renderPage();
  taskList();
  refresh();
}
// The presentation shares task selection, but polling must never leave its page.
function renderPage() {
  const technical = state.page === "technical";
  $("#technical").hidden = !technical;
  $("#workspace").hidden = technical || !state.data;
  $("#empty").hidden = technical || !!state.data;
  $("#openTechnical").classList.toggle("active", technical);
  $("#openTechnical").setAttribute("aria-pressed", String(technical));
  $("#techShowCase").textContent = state.id ? "查看当前案例 →" : "查看制作效果 →";
  document.title = technical ? "技术方案 · 拼贴制作工作台" : "拼贴制作工作台";
  if (!technical) setPresentation(false);
  for (const name of ["overview", "motion"]) {
    const selected = state.techSlide === name;
    const tab = $(`[role=tab][data-tech-slide="${name}"]`);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    $(name === "overview" ? "#techOverview" : "#techMotion").hidden = !selected;
  }
  if (technical && $("#techJson").open) renderTechVisual();
  if (technical && $("#techReveal").open) renderRevealExample();
}
function renderRevealExample() {
  const d = state.data;
  if (!d || state.page !== "technical" || !$("#techReveal").open) return;
  const candidates = (d.reference_objects || []).filter(o => o.method === "extract");
  $("#revealExample").hidden = !candidates.length;
  if (!candidates.length) return;
  if (!candidates.some(o => o.id === state.revealObject)) {
    state.revealObject = (candidates.find(o => d.materials.some(m => m.id === o.id && m.status === "complete")) || candidates[0]).id;
  }
  const object = candidates.find(o => o.id === state.revealObject);
  const material = d.materials.find(m => m.id === object.id);
  const version = d.versions.at(-1);
  const current = version?.objects?.find(o => o.id === object.id);
  setHTML($("#revealObject"), candidates.map(o => `<option value="${escapeHTML(o.id)}">${escapeHTML(o.label || o.id)} · ${escapeHTML(o.id)}</option>`).join(""));
  $("#revealObject").value = object.id;
  $("#revealExampleStatus").textContent = `${d.name} · ${statusText[material?.status] || material?.status || "等待提取"}`;
  setHTML($("#revealReference"), techObjectMap(d.reference, [object], object.id));
  const image = material?.status === "complete" ? (material.images.processed || material.images.final) : null;
  setHTML($("#revealForeground"), image ? `<img src="${escapeHTML(image)}" alt="${escapeHTML(object.label || object.id)}的已处理提取图层" loading="lazy">` : '<div class="tech-visual-empty">当前对象没有可用提取结果<br>保留缺口，等待处理</div>');
  setHTML($("#revealComposition"), techObjectMap(version?.image || d.current_image, current ? [current] : [], object.id));
}
function techObjectMap(url, objects, selected, clickable = false) {
  if (!url) return '<div class="tech-visual-empty">尚未生成画面</div>';
  const [width, height] = state.data.reference_size;
  const rect = (obj) => {
    if (!obj.bbox) return "";
    const [l, t, r, b] = obj.bbox;
    const transform = {x:0, y:0, scale:1, rotation:0, ...obj.editor_transform};
    const cx = (l+r)/2, cy = (t+b)/2;
    const active = obj.id === selected;
    return `<rect x="${l}" y="${t}" width="${r-l}" height="${b-t}" transform="translate(${transform.x} ${transform.y}) rotate(${(obj.rotation || 0)+transform.rotation} ${cx} ${cy}) translate(${cx} ${cy}) scale(${transform.scale}) translate(${-cx} ${-cy})" vector-effect="non-scaling-stroke" class="${active ? "selected" : ""}" ${clickable ? `data-tech-object="${escapeHTML(obj.id)}" role="button" tabindex="0" aria-label="${escapeHTML(obj.label || obj.id)}"` : ''}><title>${escapeHTML(obj.label || obj.id)}</title></rect>`;
  };
  const ordered = [...objects.filter(o => o.id !== selected), ...objects.filter(o => o.id === selected)];
  return `<svg viewBox="0 0 ${width} ${height}" aria-label="${clickable ? '参考图与可选择对象框' : '当前成图与对象位置'}"><image href="${escapeHTML(url)}" width="${width}" height="${height}" />${ordered.map(rect).join("")}</svg>`;
}
function renderTechVisual() {
  const d = state.data;
  if (!d || state.page !== "technical" || !$("#techJson").open) return;
  const originals = d.reference_objects || [];
  if (!originals.length) {
    $("#techVisual").hidden = true;
    $("#techJsonHint").textContent = "当前任务尚无分析对象，分析完成后可查看图与真实对象字段的对照。";
    document.querySelectorAll(".tech-json-content pre code").forEach(code => { code.textContent = "{}"; });
    return;
  }
  if (!originals.some(o => o.id === state.techObject)) {
    state.techObject = (originals.find(o => o.kind === "photo") || originals[0]).id;
  }
  const original = originals.find(o => o.id === state.techObject);
  const version = d.versions.at(-1);
  const current = version?.objects?.find(o => o.id === original.id);
  const material = d.materials.find(m => m.id === original.id);
  const binding = current?.binding;
  const customer = d.catalog.find(a => a.id === binding?.asset_id);
  const photo = original.kind === "photo";
  $("#techVisual").hidden = false;
  $("#techJsonHint").textContent = `当前案例：${d.name}。选择对象后，三张图与下方 JSON 同步更新。JSON 为公开记录中的当前对象片段，省略完整层级、路径与哈希；不是完整运行文件。`;
  setHTML($("#techObject"), originals.map(o => `<option value="${escapeHTML(o.id)}">${escapeHTML(o.label || o.id)} · ${escapeHTML(o.id)}</option>`).join(""));
  $("#techObject").value = original.id;
  $("#techObjectRelation").textContent = binding ? `${original.id} → ${binding.asset_id}` : `${original.id} · ${original.method || original.kind}`;
  setHTML($("#techReferenceMap"), techObjectMap(d.reference, originals, original.id, true));
  setHTML($("#techResultMap"), techObjectMap(version?.image || d.current_image, current ? [current] : [], original.id));
  $("#techBoxCaption").textContent = `bbox = [${original.bbox.join(", ")}] · 参考图 ${d.reference_size.join(" × ")} px`;
  $("#techAssetLabel").textContent = photo ? "绑定的客户素材" : "制作后的独立图层";
  const assetImage = photo ? (customer?.image || material?.images.customer) : material?.status === "complete" ? (material?.images.processed || material?.images.final) : null;
  setHTML($("#techAssetPreview"), assetImage ? `<img src="${escapeHTML(assetImage)}" alt="${escapeHTML(photo ? '绑定的客户素材代表图' : '制作后的独立素材图层')}" loading="lazy">` : '<div class="tech-visual-empty">尚无可用素材<br>缺失或未采用的结果不会作为成品展示</div>');
  $("#techAssetCaption").textContent = photo ? (binding?.asset_id || "尚无已编译的素材绑定") : `${material?.method || original.method || "本地绘制"} · ${statusText[material?.status] || material?.status || "待制作"}`;
  $("#techResultCaption").textContent = current ? `render_id: ${version.render_id} · 高亮为布局框，描边与人物可超出框外` : "当前成图中没有该对象（可能已删除或尚未构建）";
  const explanations = {
    analysis: `左图高亮的是“${original.label || original.id}”。下方 bbox 决定它的位置，kind 决定对象类型，method / mode 决定制作方式。点击其它框可查看对应对象。`,
    bindings: binding ? `左边的 ${binding.slot_id} 窗口，绑定到中间这张客户素材 ${binding.asset_id}，裁剪后进入右边成图。Live 素材在这里展示其代表帧。` : photo ? "这个照片槽尚无已编译的绑定记录。完成分析与构建后可查看它对应的客户素材。" : "当前选中的是装饰或背景，不需要客户照片绑定。切换到照片对象，即可看到 slot_id 与 asset_id 的图像对应关系。",
    scene: `右图展示当前成图，下方是该对象当前编译记录。${binding ? "binding 把照片窗口与客户素材连起来；渲染器沿用对象的布局和样式。" : "装饰的制作方式、素材检查结果和已提取资源会随对象一起交给渲染器。"}`,
  };
  $("#techVisualExplanation").textContent = explanations[state.techArtifact];
  const snippets = {
    analysis: {schema_version:"collage-analysis-v1", reference_size:d.reference_size, objects:[original]},
    bindings: {schema_version:"collage-bindings-v1", photos:binding ? [binding] : []},
    scene: {schema_version:"collage-scene-v1", reference_size:d.reference_size, objects:current ? [current] : []},
  };
  for (const [name, snippet] of Object.entries(snippets)) {
    const code = $(`[data-tech-json-panel="${name}"] pre code`);
    code.parentElement.setAttribute("aria-label", `${name}.json 当前对象片段`);
    const text = JSON.stringify(snippet, null, 2);
    if (code.textContent !== text) code.textContent = text;
  }
}
function setPresentation(enabled) {
  document.body.classList.toggle("tech-presenting", enabled);
  $("#techPresent").setAttribute("aria-pressed", String(enabled));
  $("#techPresent").textContent = enabled ? "退出演示 · Esc" : "演示模式";
}
function showPage(page, slide = state.techSlide) {
  state.page = page;
  state.techSlide = slide === "motion" ? "motion" : "overview";
  const url = new URL(location.href);
  if (state.id) url.searchParams.set("task", state.id);
  if (page === "technical") {
    url.searchParams.set("view", "technical");
    url.searchParams.set("slide", state.techSlide);
    document.querySelectorAll("#workspace video").forEach(video => video.pause());
  } else {
    url.searchParams.delete("view");
    url.searchParams.delete("slide");
  }
  history.replaceState({}, "", url);
  renderPage();
  if (page === "workbench" && state.data) render();
  window.scrollTo(0, 0);
  requestAnimationFrame(fitWorlds);
}
function follow() {
  state.stage = null;
  state.version = null;
  state.focus = null;
  state.canvasKey = "";
  render();
}
function openImage(url, title = "原图") {
  if (!url) return;
  $("#originalImage").src = url;
  $("#imageTitle").textContent = title;
  $("#originalDownload").href = url + "?download=1";
  $("#imageDialog").showModal();
}
function thumb(url, label) {
  return `<figure class="thumb">${url ? `<img src="${url}" data-enlarge="${url}" data-title="${escapeHTML(label)}" alt="${escapeHTML(label)}" loading="lazy">` : '<div class="placeholder">尚无可用结果</div>'}<figcaption>${escapeHTML(label)}</figcaption></figure>`;
}
function renderProducts(stage) {
  setHTML($('#stageProducts'), stage.artifacts.map(a => a.url
    ? `<button class="product-link" ${a.kind === 'json' ? `data-document="${escapeHTML(a.url)}"` : `data-enlarge="${escapeHTML(a.url)}"`} data-title="${escapeHTML(a.name)}"><span>${a.kind === 'json' ? '{ }' : '▧'}</span> ${escapeHTML(a.name)} ↗</button>`
    : `<span class="product-link missing">${escapeHTML(a.name)} · 未生成</span>`).join(''));
}
async function openDocument(url, name) {
  const dialog = $('#documentDialog');
  $('#documentTitle').textContent = name;
  $('#documentContent').textContent = '正在读取…';
  $('#documentDownload').href = url+'?download=1';
  dialog.dataset.url = url;
  dialog.showModal();
  try {
    const value = await api(url);
    if (dialog.dataset.url === url) $('#documentContent').textContent = JSON.stringify(value, null, 2);
  } catch (error) {
    if (dialog.dataset.url === url) $('#documentContent').textContent = error.message;
  }
}
function transform() {
  document
    .querySelectorAll(".image-world")
    .forEach(
      (e) =>
        (e.style.transform = `translate(${state.pan[0]}px,${state.pan[1]}px) scale(${state.zoom})`),
    );
  $("#zoomLabel").textContent = `${Math.round(state.zoom * 100)}%`;
}
function highlight() {
  document.querySelectorAll(".highlight-layer").forEach((e) => e.remove());
  if (!state.focus || !state.data.reference_size) return;
  const [w, h] = state.data.reference_size;
  document.querySelectorAll(".image-world").forEach((e) => {
    const roles =
      e.dataset.role === "wipe" ? ["reference", "after"] : [e.dataset.role];
    roles.forEach((role) => {
      const b = focusBounds(state.focus, role);
      if (!b) return;
      const layer = document.createElement("div");
      layer.className = "highlight-layer";
      if (e.dataset.role === "wipe") {
        const value = $(".wipe-slider")?.value || 50;
        layer.style.clipPath =
          role === "after"
            ? `inset(0 ${100 - value}% 0 0)`
            : `inset(0 0 0 ${value}%)`;
      }
      const box = document.createElement("span");
      box.className = "highlight";
      Object.assign(box.style, {
        left: `${(b[0] / w) * 100}%`,
        top: `${(b[1] / h) * 100}%`,
        width: `${((b[2] - b[0]) / w) * 100}%`,
        height: `${((b[3] - b[1]) / h) * 100}%`,
      });
      layer.append(box);
      e.append(layer);
    });
  });
}
function focusBounds(id, role) {
  const version = selectedVersion();
  const previous = state.data.versions.find((v) => v.id === version?.parent_id);
  const objects =
    role === "before"
      ? previous?.objects
      : role === "reference"
        ? state.data.reference_objects
        : version?.objects || state.data.materials;
  const obj = objects?.find((o) => o.id === id);
  if (!obj?.bbox) return null;
  // Scene bbox already includes the translation; extracted_offset moves the full RGBA layer.
  const t={x:0,y:0,rotation:0,scale:1,...obj.editor_transform},[l,top,r,b]=obj.bbox,cx=(l+r)/2+t.x,cy=(top+b)/2+t.y;
  const angle=((obj.rotation||0)+t.rotation)*Math.PI/180,c=Math.abs(Math.cos(angle))*t.scale,s=Math.abs(Math.sin(angle))*t.scale,w=(r-l)*c+(b-top)*s,h=(r-l)*s+(b-top)*c;
  return [cx-w/2,cy-h/2,cx+w/2,cy+h/2];
}
function fitWorlds() {
  if (!state.data?.reference_size) return;
  const [w, h] = state.data.reference_size;
  document.querySelectorAll(".image-world").forEach((e) => {
    const viewport = e.parentElement;
    const width = Math.min(
      viewport.clientWidth,
      (viewport.clientHeight * w) / h,
    );
    e.style.width = width + "px";
    e.style.height = (width * h) / w + "px";
  });
}
function focusObject(id) {
  if (!focusBounds(id, "after") && !focusBounds(id, "reference")) return;
  state.focus = id;
  state.zoom = 1;
  state.pan = [0, 0];
  transform();
  highlight();
  $("#canvas").scrollIntoView({ behavior: "smooth", block: "center" });
}
function pane(url, label, aspect, role) {
  return `<div class="image-pane"><div class="pane-label"><span>${escapeHTML(label)}</span><span>${url ? "点击「原图」查看细节" : ""}</span></div><div class="viewport">${url ? `<div class="image-world" data-role="${role}" style="aspect-ratio:${aspect}"><img src="${url}" alt="${escapeHTML(label)}" draggable="false"></div>` : '<div class="no-image">这一阶段还没有成图<br>制作完成后会自动显示</div>'}</div></div>`;
}
function renderCanvas() {
  $("#modes").querySelectorAll("button").forEach(b=>b.classList.toggle("active",b.dataset.mode===state.mode));
  const d = state.data,
    v = selectedVersion(),
    step = state.stage || d.current_stage;
  const before = v ? d.versions.find((x) => x.id === v.parent_id) : null;
  let left = d.reference,
    right = v?.image || d.current_image,
    ll = "原始参考",
    rl = v?.label || "当前成图";
  if (step === 2) {
    right = $("#boxes").checked ? d.boxes : d.reference;
    rl = $("#boxes").checked ? "分析叠框" : "参考原图";
  }
  if (step === 3) {
    right = d.windows;
    rl = 'photo-windows.png · 窗口蒙版';
  }
  if (step === 5 && !state.version && state.stage === 5) {
    right = d.first_image || v?.image;
    rl = '首版成图';
  }
  if (state.mode === "before" && step === 5) {
    left = before?.image || null;
    ll = "修改前";
    rl = "修改后";
  }
  $("#boxesControl").hidden = step !== 2;
  $('#modes').hidden = step === 1 || step === 4;
  document.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('active', b.dataset.mode === state.mode));
  $('[data-mode="before"]').hidden = step !== 5;
  $('.zoom-tools').hidden = step === 1 || step === 4;
  $("#canvasTitle").textContent =
    ['参考与客户素材', '分析素材', '判断窗口蒙版', '照片绑定', '首版成图'][step-1];
  $("#versionBadge").textContent =
    v && step === 5 ? `${v.label} · ${shortTime(v.at)}` : "";
  $('#canvasHint').textContent = step === 1 || step === 4 ? '点击缩略图查看原图' :
    step === 3 ? '预览展示窗口位置与透明变化；主体轮廓在绑定客户照片后生成。' : '滚轮缩放 · 拖动画面同步移动 · 点击素材定位';
  $("#dimensions").textContent = d.reference_size
    ? d.reference_size.join(" × ") + " px 参考"
    : "";
  const key = JSON.stringify([
    d.id,
    step,
    state.mode,
    left,
    right,
    $("#boxes").checked,
    step === 1 ? d.catalog : null,
    step === 4 ? d.materials : null,
  ]);
  if (key === state.canvasKey) {
    highlight();
    return;
  }
  state.canvasKey = key;
  const c = $("#canvas");
  c.className = "canvas";
  if (step === 1) {
    c.innerHTML = `<div class="catalog-grid"><p class="subtle">参考图与 ${d.catalog.length} 张客户照片</p>${thumb(d.reference, "原始参考")}${d.catalog.map((a, i) => thumb(a.image, `客户照片 ${i + 1}`)).join("")}</div>`;
    return;
  }
  if (step === 4) {
    const photos = d.materials.filter(m => m.kind === 'photo');
    c.innerHTML = `<div class="binding-grid">${photos.length ? photos.map(m =>
      `<article><h3>${escapeHTML(m.id)} → ${escapeHTML(m.binding?.asset_id || '尚未绑定')}</h3><div class="material-pair">${thumb(m.images.reference, '参考位置')}${thumb(m.images.customer, '绑定的客户素材')}</div></article>`
    ).join('') : '<p class="subtle">当前没有需要绑定的照片对象。</p>'}</div>`;
    return;
  }
  const aspect = (d.reference_size || [3, 4]).join("/");
  if (state.mode === "wipe" && left && right) {
    c.classList.add("wipe");
    c.innerHTML = `<div class="pane-label"><span>${ll} / ${rl}</span><span>拖动滑块对照</span></div><div class="wipe-container viewport"><div class="image-world" data-role="${step <= 3 ? "reference" : "wipe"}" style="aspect-ratio:${aspect}"><img src="${left}" alt="${ll}"><div class="wipe-top"><img src="${right}" alt="${rl}"></div><div class="wipe-line"></div></div></div><input class="wipe-slider" type="range" min="0" max="100" value="50" aria-label="对比位置">`;
  } else {
    c.innerHTML =
      pane(
        left,
        ll,
        aspect,
        state.mode === "before" && step === 5 ? "before" : "reference",
      ) + pane(right, rl, aspect, step <= 3 ? "reference" : "after");
  }
  if (step === 5 && state.mode === "before" && !before)
    $("#canvasHint").textContent =
      "此版本没有已留存的上一版，不能还原修改前图片";
  transform();
  highlight();
}
function materialCard(m, extraction = false) {
  if (m.method === "程序相框" && m.decision?.status) {
    m = { ...m, decision: { ...m.decision, status: "原提取未采用，已用程序相框补位" } };
  }
  return `<div class="material-title"><button data-focus="${escapeHTML(m.id)}">${escapeHTML(m.label)}</button>${badge(m.status)}</div><div class="material-pair">${thumb(m.images.reference, "参考局部")}${thumb(extraction ? m.images.processed || m.images.raw || m.images.final : m.images.final, extraction ? '360 提取结果' : m.method === "客户照片" ? "照片处理结果" : "采用素材")}</div><details><summary>${escapeHTML(m.method)}${m.cache_hit ? " · 缓存复用" : ""} · 查看过程</summary>${m.images.customer ? thumb(m.images.customer, "客户原照片缩略图") : ""}${m.images.raw ? thumb(m.images.raw, "原始返回") : ""}${m.images.processed ? thumb(m.images.processed, "透明处理结果") : ""}<p>${escapeHTML(m.note || "可对照参考图检查处理结果。")}</p></details>`;
}
function renderMaterials() {
  const d = state.data,
    v = selectedVersion();
  const step = state.stage || d.current_stage;
  const historical = step === 5 && (state.version !== null || state.stage !== null);
  const available =
    historical && v
      ? v.materials
      : d.materials;
  const cards = step === 4 ? available.filter(m => m.method === '参考提取') : available;
  const completed = cards.filter((m) => m.status === "complete").length,
    unresolved = cards.filter((m) => m.status === "unresolved").length,
    failed = cards.filter((m) => m.status === "failed").length;
  $("#materialHeading").textContent =
    step === 4 ? '360 提取结果' : historical && v ? "该版本的素材" : "素材制作过程";
  $("#materialCount").textContent = `${cards.length} 件`;
  setHTML(
    $("#materialProgress"),
    `<div class="material-progress">可用 ${completed} 件 · 失败 ${failed} 件 · 未解决 ${unresolved} 件 · 处理中或待制作 ${cards.length - completed - unresolved - failed} 件${cards.length ? `<div class="progress-track"><span style="width:${((completed + unresolved + failed) / cards.length) * 100}%"></span></div>` : ""}</div>`,
  );
  setHTML(
    $("#groups"),
    !historical && d.groups.length
      ? `<div class="groups">提取请求 ${d.groups.length} 组 · 已返回 ${d.groups.filter((g) => g.status === "complete").length} 组 · 等待 ${d.groups.filter((g) => g.status === "waiting").length} 组${d.groups.filter((g) => g.status === "failed").map((g) => `<p>提取或下载异常：${escapeHTML(g.error || "未获得完整结果")} · 已恢复 ${g.available_ids?.length || 0} 件，缺失 ${g.missing_ids?.length || 0} 件</p>`).join("")}</div>`
      : "",
  );
  if (step === 4 && !cards.length) setHTML($('#groups'), '<p class="subtle">当前没有需要单独提取的固定装饰。</p>');
  const grid = $("#materials"),
    seen = new Set();
  for (const m of cards) {
    seen.add(m.id);
    let el = [...grid.children].find((n) => n.dataset.id === m.id);
    if (!el) {
      el = document.createElement("article");
      el.className = "material";
      el.dataset.id = m.id;
      grid.append(el);
    }
    const sig = JSON.stringify([m, step]);
    if (el._sig !== sig) {
      const opened = el.querySelector("details")?.open;
      el.innerHTML = materialCard(m, step === 4);
      el.querySelector("details").open = !!opened;
      el._sig = sig;
    }
  }
  [...grid.children]
    .filter((el) => !seen.has(el.dataset.id))
    .forEach((el) => el.remove());
}
function render() {
  renderPage();
  const d = state.data;
  if (!d) return;
  renderTiming();
  $("#empty").hidden = true;
  $("#workspace").hidden = state.page === "technical";
  const step = state.stage || d.current_stage,
    s = d.stages[step - 1],
    v = selectedVersion();
  $('#workspace').dataset.stage = step;
  $('#openEditor').disabled = !d.current_image;
  $('#openEditor').title = d.current_image ? '编辑当前成图' : '生成首版后可编辑';
  renderProducts(s);
  $("#title").textContent = d.name;
  $("#taskKind").textContent = d.historical ? "历史导入" : "实时任务";
  $("#subtitle").textContent =
    `${d.catalog.length} 张客户照片 · ${d.materials.length} 个画面对象 · ${d.versions.length} 个成图版本`;
  $("#execution").textContent = d.execution;
  $("#execution").className = "badge " + (d.live ? "running" : "pending");
  $("#download").hidden = !d.current_image;
  $("#download").href = (d.current_image || "") + "?download=1";
  $("#notice").hidden = !d.recording_incomplete && !d.historical;
  $("#notice").textContent = d.recording_incomplete
    ? "部分过程记录不完整，已保存产物仍可查看。"
    : d.historical
      ? "历史任务：仅展示已留存产物，未记录的过程和中间版本无法补全。"
      : "";
  $("#historyBanner").hidden = state.stage === null && state.version === null;
  $("#viewing").textContent =
    `正在查看：${s.label}${state.version && v ? " / " + v.label : ""}`;
  setHTML(
    $("#stages"),
    d.stages
      .map(
        (x) =>
          `<button class="stage ${x.status} ${x.id === step ? "selected" : ""}" data-stage="${x.id}" aria-current="${x.id === step ? 'step' : 'false'}"><span class="number">${x.status === "complete" ? "✓" : String(x.id).padStart(2, "0")}</span><span><strong>${x.label}</strong><small>${escapeHTML(x.description)}</small><small>${statusText[x.status] || x.status}</small></span></button>`,
      )
      .join(""),
  );
  $("#stepTitle").textContent = `${String(step).padStart(2, "0")} ${s.label}`;
  $("#stepStatus").textContent = statusText[s.status] || s.status;
  $("#stepStatus").className = "badge " + s.status;
  $("#stepSummary").textContent = s.summary || "此步骤尚未开始。";
  setHTML(
    $("#stepFacts"),
    `<div class="fact"><span>最近记录</span><span>${shortTime(s.at) || "—"}</span></div>${s.status === "running" && s.started_at ? `<div class="fact"><span>距开始记录</span><span>${duration(s.started_at)}</span></div>` : ""}`,
  );
  setHTML(
    $("#versions"),
    d.versions.length
      ? d.versions
          .map(
            (x, i) =>
              `<button class="version ${v?.id === x.id ? "active" : ""}" data-version="${x.id}"><img src="${x.image}" alt="${x.label}"><strong>${String(i + 1).padStart(2, "0")} ${x.label}</strong><small>${shortTime(x.at)}</small></button>`,
          )
          .join("")
      : '<p class="subtle">尚无完整版本快照。已有历史成图可在上方查看。</p>',
  );
  const changes = v?.changes || [];
  $("#changeCount").textContent = changes.length ? `${changes.length} 项` : "";
  const reasons = new Map(
    (v?.intent?.items || v?.intent?.decisions || []).map((x) => [
      x.id,
      x.reason,
    ]),
  );
  const fields = {
    removed: "已删除",
    kind: "对象类型",
    bbox: "位置或尺寸",
    rotation: "角度",
    style: "外观",
    binding: "照片或裁切",
    method: "制作方式",
    gate: "素材采用",
    layer_order: "前后顺序",
    extracted_offset: "装饰平移",
    editor_transform: "手动移动位置",
    text: "文字",
  };
  setHTML(
    $("#changes"),
    changes.length
      ? changes
          .map(
            (c) =>
              `<button class="change" data-focus="${escapeHTML(c.id)}"><strong>${escapeHTML(c.label)}</strong><small>${escapeHTML(
                reasons.get(c.id) || (c.fields.removed ? "已删除" :
                  Object.keys(c.fields)
                    .map((k) => fields[k] || k)
                    .join("、") + "发生变化"),
              )}</small></button>`,
          )
          .join("")
      : '<p class="review-summary">' +
          (v?.parent_id
            ? "本次没有记录到对象参数变化。"
            : "首版记录，尚无前后修改。") +
          "</p>",
  );
  setHTML(
    $("#timeline"),
    [...d.history]
      .reverse()
      .slice(0, 25)
      .map(
        (h) =>
          `<li><time>${shortTime(h.at)}</time>${escapeHTML(h.summary)}</li>`,
      )
      .join("") || '<li class="subtle">未留存阶段执行记录，可查看已保存产物。</li>',
  );
  $("#updated").textContent =
    "最近同步 " + new Date().toLocaleTimeString("zh-CN");
  renderCanvas();
  renderMaterials();
  window.refreshChat?.();
  window.refreshMotion?.();
}
async function refresh() {
  if (state.busy) return;
  state.busy = true;
  const id = state.id;
  try {
    [state.tasks, state.batches] = await Promise.all([api("/api/tasks"), api("/api/batches").catch(error => {
      // Static files can update while a previous server is still finishing jobs.
      if (error.message.startsWith('HTTP 404')) return [];
      throw error;
    })]);
    const available = availableTasks();
    if (!available.some((task) => task.id === state.id)) {
      state.id = available[0]?.id || null;
      state.data = null;
      state.stage = null;
      state.version = null;
      state.focus = null;
      state.techObject = null;
      state.revealObject = null;
      state.canvasKey = "";
      state.materialKeys.clear();
      state.zoom = 1;
      state.pan = [0, 0];
      const u = new URL(location.href);
      if (state.id) u.searchParams.set("task", state.id);
      else u.searchParams.delete("task");
      history.replaceState({}, "", u);
      renderPage();
    }
    taskList();
    if (state.id) {
      const requested = state.id;
      const data = await api("/api/tasks/" + requested);
      if (requested === state.id) {
        state.data = data;
        render();
      }
    }
    $("#connection").textContent = "工作台已连接";
    $("#connectionDot").style.background = "var(--green)";
  } catch (e) {
    $("#connection").textContent = "连接中断，稍后重试";
    $("#connectionDot").style.background = "var(--amber)";
  } finally {
    state.busy = false;
    if (id && id !== state.id) queueMicrotask(refresh);
  }
}
document.addEventListener("click", (e) => {
  const revealTopic = e.target.closest("[data-reveal-topic]");
  if (revealTopic) return selectRevealTopic(revealTopic.dataset.revealTopic);
  const revealZoom = e.target.closest("[data-reveal-zoom]");
  if (revealZoom) {
    const original = document.getElementById(revealZoom.dataset.revealZoom);
    $("#revealFigureLarge").src = original.src;
    $("#revealFigureLarge").alt = original.alt;
    $("#revealFigureTitle").textContent = original.dataset.revealAsset === "architecture" ? "Figure 3 · RevealLayer 模型架构" : "Figure 4 · 训练数据构建";
    setRevealFigureZoom(false);
    $("#revealFigureDialog").showModal();
    $("#revealFigureViewport").scrollTo(0, 0);
    return;
  }
  const visualObject = e.target.closest("[data-tech-object]");
  if (visualObject) {
    state.techObject = visualObject.dataset.techObject;
    renderTechVisual();
    return;
  }
  const artifact = e.target.closest("[data-tech-artifact]");
  if (artifact) {
    const name = artifact.dataset.techArtifact;
    state.techArtifact = name;
    $("#techJson").open = true;
    document.querySelectorAll("[data-tech-json-panel]").forEach(panel => {
      panel.hidden = panel.dataset.techJsonPanel !== name;
    });
    document.querySelectorAll("#techJson .tech-json-switch button").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.techArtifact === name));
    });
    renderTechVisual();
    if (artifact.closest(".tech-contract")) $("#techJson").scrollIntoView({ block: "start" });
    return;
  }
  const slide = e.target.closest("[data-tech-slide]");
  if (slide) {
    showPage("technical", slide.dataset.techSlide);
    $(`[role=tab][data-tech-slide="${state.techSlide}"]`).focus({ preventScroll: true });
    return;
  }
  const task = e.target.closest("[data-task]");
  if (task) return selectTask(task.dataset.task);
  const step = e.target.closest("button[data-stage]");
  if (step) {
    state.stage = Number(step.dataset.stage);
    state.version = null;
    state.focus = null;
    if (state.mode === 'before') state.mode = 'side';
    return render();
  }
  const version = e.target.closest("[data-version]");
  if (version) {
    state.version = version.dataset.version;
    state.stage = 5;
    state.focus = null;
    return render();
  }
  const mode = e.target.closest("[data-mode]");
  if (mode) {
    state.mode = mode.dataset.mode;
    $("#modes")
      .querySelectorAll("button")
      .forEach((b) => b.classList.toggle("active", b === mode));
    return renderCanvas();
  }
  const focus = e.target.closest("[data-focus]");
  if (focus) return focusObject(focus.dataset.focus);
  const enlarge = e.target.closest("[data-enlarge]");
  if (enlarge) return openImage(enlarge.dataset.enlarge, enlarge.dataset.title);
  const doc = e.target.closest('[data-document]');
  if (doc) return openDocument(doc.dataset.document, doc.dataset.title);
});
$('#closeDocument').onclick = () => $('#documentDialog').close();
$("#openTechnical").onclick = () => showPage("technical");
function selectRevealTopic(topic) {
  document.querySelectorAll("[data-reveal-topic]").forEach(button => {
    const active = button.dataset.revealTopic === topic;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    document.getElementById(button.getAttribute("aria-controls")).hidden = !active;
  });
  $("#techReveal .reveal-scroll").scrollTop = 0;
  if (topic === "integration") renderRevealExample();
}
function loadRevealFigures() {
  document.querySelectorAll("img[data-reveal-asset]").forEach(img => {
    if (img.hasAttribute("src")) return;
    const source = document.getElementById(`revealAsset-${img.dataset.revealAsset}`);
    const bytes = Uint8Array.from(atob(source.content.textContent.trim()), char => char.charCodeAt(0));
    img.src = URL.createObjectURL(new Blob([bytes], {type:"image/png"}));
  });
}
$("#openRevealDetail").onclick = () => {
  loadRevealFigures();
  selectRevealTopic("architecture");
  $("#techReveal").showModal();
  document.body.classList.add("reveal-open");
  $("#openRevealDetail").setAttribute("aria-expanded", "true");
  renderRevealExample();
};
$("#openRevealDetail").addEventListener("keydown", event => {
  if (!["Enter", " "].includes(event.key)) return;
  event.preventDefault();
  $("#openRevealDetail").click();
});
$("#closeReveal").onclick = () => $("#techReveal").close();
$("#techReveal").addEventListener("close", () => {
  document.body.classList.remove("reveal-open");
  $("#openRevealDetail").setAttribute("aria-expanded", "false");
  $("#openRevealDetail").focus({preventScroll:true});
});
$("#techReveal").addEventListener("keydown", event => {
  const tab = event.target.closest("[data-reveal-topic]");
  if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  event.stopPropagation();
  const tabs = [...document.querySelectorAll("[data-reveal-topic]")];
  const index = event.key === "Home" ? 0 : event.key === "End" ? tabs.length-1 :
    (tabs.indexOf(tab) + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  selectRevealTopic(tabs[index].dataset.revealTopic);
  tabs[index].focus();
});
function setRevealFigureZoom(zoomed) {
  $("#revealFigureViewport").classList.toggle("zoomed", zoomed);
  $("#revealFigureZoom").setAttribute("aria-pressed", String(zoomed));
}
$("#revealFigureFit").onclick = () => setRevealFigureZoom(false);
$("#revealFigureZoom").onclick = () => setRevealFigureZoom(true);
$("#closeRevealFigure").onclick = () => $("#revealFigureDialog").close();
$("#revealFigureDialog").addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  // Close only the enlarged figure, keeping the paper walkthrough underneath.
  event.preventDefault();
  event.stopPropagation();
  $("#revealFigureDialog").close();
});
$("#revealObject").onchange = (event) => {
  state.revealObject = event.target.value;
  renderRevealExample();
};
$("#techObject").onchange = (event) => {
  state.techObject = event.target.value;
  renderTechVisual();
};
$("#techJson").addEventListener("toggle", () => { if ($("#techJson").open) renderTechVisual(); });
$("#techShowCase").onclick = $("#techFinish").onclick = () => showPage("workbench");
$("#techPresent").onclick = () => setPresentation(!document.body.classList.contains("tech-presenting"));
document.addEventListener("keydown", (e) => {
  if (state.page === "technical" && e.target.matches("[data-tech-object]") && ["Enter", " "].includes(e.key)) {
    e.preventDefault();
    state.techObject = e.target.dataset.techObject;
    renderTechVisual();
    return;
  }
  if (state.page !== "technical" || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey ||
      e.target.closest("input, textarea, select, [contenteditable], dialog")) return;
  if (e.key === "Escape") return setPresentation(false);
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
  // Arrow navigation is scoped to tabs or presentation mode, preserving normal scrolling.
  if (!e.target.closest("[role=tab]") && !document.body.classList.contains("tech-presenting")) return;
  e.preventDefault();
  showPage("technical", ["ArrowRight", "End"].includes(e.key) ? "motion" : "overview");
  $(`[role=tab][data-tech-slide="${state.techSlide}"]`).focus({ preventScroll: true });
});
$("#search").oninput = taskList;
$("#follow").onclick = follow;
$("#boxes").onchange = renderCanvas;
$("#zoomIn").onclick = () => {
  state.zoom = Math.min(5, state.zoom + 0.25);
  transform();
};
$("#zoomOut").onclick = () => {
  state.zoom = Math.max(0.5, state.zoom - 0.25);
  transform();
};
$("#resetZoom").onclick = () => {
  state.zoom = 1;
  state.pan = [0, 0];
  state.focus = null;
  transform();
  highlight();
};
$("#original").onclick = () => {
  const d = state.data, step = state.stage || d.current_stage;
  const url = step === 2 ? ($('#boxes').checked ? d.boxes : d.reference) : step === 3 ? d.windows : step === 5 ? selectedVersion()?.image || d.first_image || d.current_image : d.reference;
  if (url) openImage(url, d.stages[step-1].label);
};
$("#closeImage").onclick = () => $("#imageDialog").close();
$("#canvas").addEventListener("input", (e) => {
  if (e.target.matches(".wipe-slider")) {
    $(".wipe-top").style.clipPath = `inset(0 ${100 - e.target.value}% 0 0)`;
    $(".wipe-line").style.left = e.target.value + "%";
    highlight();
  }
});
$("#canvas").addEventListener(
  "wheel",
  (e) => {
    if (!e.target.closest(".viewport")) return;
    e.preventDefault();
    state.zoom = Math.max(
      0.5,
      Math.min(5, state.zoom + (e.deltaY < 0 ? 0.1 : -0.1)),
    );
    transform();
  },
  { passive: false },
);
let drag = null;
$("#canvas").addEventListener("pointerdown", (e) => {
  if (!e.target.closest(".viewport")) return;
  drag = [e.clientX, e.clientY, ...state.pan];
  $("#canvas").setPointerCapture(e.pointerId);
});
$("#canvas").addEventListener("pointermove", (e) => {
  if (!drag) return;
  state.pan = [drag[2] + e.clientX - drag[0], drag[3] + e.clientY - drag[1]];
  transform();
});
$("#canvas").addEventListener("pointerup", () => (drag = null));
$("#canvas").addEventListener("pointercancel", () => (drag = null));
new ResizeObserver(fitWorlds).observe($("#canvas"));
new MutationObserver(() => requestAnimationFrame(fitWorlds)).observe(
  $("#canvas"),
  { childList: true },
);
renderPage();
refresh();
setInterval(refresh, 2000);
setInterval(renderTiming, 1000);
