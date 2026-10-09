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
  data: null,
  stage: null,
  version: null,
  mode: "side",
  zoom: 1,
  pan: [0, 0],
  focus: null,
  busy: false,
  tasks: [],
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
function renderTiming() {
  const timing = state.data?.sdk_timing;
  const el = $("#sdkTiming");
  el.hidden = !timing?.started_at;
  if (el.hidden) return;
  const seconds = timing.finished_at
    ? timing.elapsed_seconds
    : (Date.now() - Date.parse(timing.started_at)) / 1000;
  const n = Math.max(0, Math.floor(seconds));
  el.textContent = `SDK 串行测试 · ${timing.model} / ${timing.effort} · ${timing.finished_at ? "总耗时" : "已用时"} ${Math.floor(n / 60)}分${n % 60}秒 · 包含分析、制作与复核`;
}
async function api(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw Error(`HTTP ${r.status}`);
  return r.json();
}
function selectedVersion() {
  const d = state.data;
  if (!d) return null;
  if (state.version)
    return d.versions.find((v) => v.id === state.version) || null;
  if (state.stage === 3) return d.versions[0] || null;
  if (state.stage === 4)
    return (
      [...d.versions].reverse().find((v) => v.command === "screen") ||
      d.versions[0] ||
      null
    );
  return d.versions.at(-1) || null;
}
function taskList() {
  const term = $("#search").value.toLowerCase();
  setHTML(
    $("#tasks"),
    state.tasks
      .filter((t) => t.name.toLowerCase().includes(term))
      .map(
        (t) =>
          `<button class="task ${t.id === state.id ? "active" : ""}" data-task="${t.id}">${t.thumbnail ? `<img src="${t.thumbnail}" alt="">` : ""}<span><strong>${escapeHTML(t.name)}</strong><small>${t.created_at ? new Date(t.created_at).toLocaleDateString("zh-CN") : ""}</small></span></button>`,
      )
      .join(""),
  );
  $("#taskCount").textContent = state.tasks.length;
}
function selectTask(id) {
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
  u.searchParams.set("task", id);
  history.replaceState({}, "", u);
  taskList();
  refresh();
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
  return obj.bbox;
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
  if (state.mode === "before" && step > 2) {
    left = before?.image || null;
    ll = "修改前";
    rl = "修改后";
  }
  $("#boxesControl").hidden = step !== 2;
  $("#canvasTitle").textContent =
    step === 1 ? "参考与客户素材" : step === 2 ? "布局分析" : "成图对照";
  $("#versionBadge").textContent =
    v && step > 2 ? `${v.label} · ${shortTime(v.at)}` : "";
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
  const aspect = (d.reference_size || [3, 4]).join("/");
  if (state.mode === "wipe" && left && right) {
    c.classList.add("wipe");
    c.innerHTML = `<div class="pane-label"><span>${ll} / ${rl}</span><span>拖动滑块对照</span></div><div class="wipe-container viewport"><div class="image-world" data-role="${step === 2 ? "reference" : "wipe"}" style="aspect-ratio:${aspect}"><img src="${left}" alt="${ll}"><div class="wipe-top"><img src="${right}" alt="${rl}"></div><div class="wipe-line"></div></div></div><input class="wipe-slider" type="range" min="0" max="100" value="50" aria-label="对比位置">`;
  } else {
    c.innerHTML =
      pane(
        left,
        ll,
        aspect,
        state.mode === "before" && step > 2 ? "before" : "reference",
      ) + pane(right, rl, aspect, step === 2 ? "reference" : "after");
  }
  if (state.mode === "before" && !before)
    $("#canvasHint").textContent =
      "此版本没有已留存的上一版，不能还原修改前图片";
  else
    $("#canvasHint").textContent = "滚轮缩放 · 拖动画面同步移动 · 点击素材定位";
  transform();
  highlight();
}
function materialCard(m) {
  return `<div class="material-title"><button data-focus="${escapeHTML(m.id)}">${escapeHTML(m.label)}</button>${badge(m.status)}</div><div class="material-pair">${thumb(m.images.reference, "参考局部")}${thumb(m.images.final, m.method === "客户照片" ? "照片处理结果" : "采用素材")}</div><details><summary>${escapeHTML(m.method)}${m.cache_hit ? " · 缓存复用" : ""} · 查看过程</summary>${m.images.customer ? thumb(m.images.customer, "客户原照片缩略图") : ""}${m.images.raw ? thumb(m.images.raw, "原始返回") : ""}${m.images.processed ? thumb(m.images.processed, "透明处理结果") : ""}<p>${escapeHTML(m.note || "处理结果需结合整图复核。")}</p>${m.decision?.status ? `<p>素材检查：${escapeHTML({ accepted: "采用", rejected: "隔离", pending: "待筛选" }[m.decision.status] || m.decision.status)}</p>` : ""}</details>`;
}
function renderMaterials() {
  const d = state.data,
    v = selectedVersion();
  const historical = state.version !== null || state.stage !== null;
  const cards =
    historical && v && state.stage !== 1 && state.stage !== 2
      ? v.materials
      : d.materials;
  const completed = cards.filter((m) => m.status === "complete").length,
    unresolved = cards.filter((m) => m.status === "unresolved").length,
    failed = cards.filter((m) => m.status === "failed").length;
  $("#materialHeading").textContent =
    historical && v ? "该版本的素材" : "素材制作过程";
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
    const sig = JSON.stringify(m);
    if (el._sig !== sig) {
      const opened = el.querySelector("details")?.open;
      el.innerHTML = materialCard(m);
      el.querySelector("details").open = !!opened;
      el._sig = sig;
    }
  }
  [...grid.children]
    .filter((el) => !seen.has(el.dataset.id))
    .forEach((el) => el.remove());
}
function render() {
  const d = state.data;
  if (!d) return;
  renderTiming();
  $("#empty").hidden = true;
  $("#workspace").hidden = false;
  const step = state.stage || d.current_stage,
    s = d.stages[step - 1],
    v = selectedVersion();
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
    `正在回看：${s.label}${state.version && v ? " / " + v.label : ""} · 后台执行继续`;
  setHTML(
    $("#stages"),
    d.stages
      .map(
        (x) =>
          `<button class="stage ${x.status} ${x.id === step ? "selected" : ""}" data-stage="${x.id}"><span class="number">${x.status === "complete" ? "✓" : String(x.id).padStart(2, "0")}</span><span><strong>${x.label}</strong><small>${statusText[x.status] || x.status}</small></span></button>`,
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
  const review = v?.review || (!v ? d.review : null);
  setHTML(
    $("#quality"),
    `<div class="quality ${review?.verdict === "pass" ? "" : "warn"}">${review?.verdict === "pass" ? "✓ 当前查看版本已通过复核" : review ? "当前查看版本仍有待改问题" : "当前查看版本尚未登记复核"}</div>`,
  );
  setHTML(
    $("#versions"),
    d.versions.length
      ? d.versions
          .map(
            (x, i) =>
              `<button class="version ${v?.id === x.id ? "active" : ""}" data-version="${x.id}"><img src="${x.image}" alt="${x.label}"><strong>${String(i + 1).padStart(2, "0")} ${x.label}</strong><small>${shortTime(x.at)} · ${x.review?.verdict === "pass" ? "复核通过" : x.review ? "仍有问题" : "待复核"}</small></button>`,
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
    bbox: "位置或尺寸",
    rotation: "角度",
    style: "外观",
    binding: "照片或裁切",
    method: "制作方式",
    gate: "素材采用",
    layer_order: "前后顺序",
    extracted_offset: "装饰平移",
    text: "文字",
  };
  setHTML(
    $("#changes"),
    changes.length
      ? changes
          .map(
            (c) =>
              `<button class="change" data-focus="${escapeHTML(c.id)}"><strong>${escapeHTML(c.label)}</strong><small>${escapeHTML(
                reasons.get(c.id) ||
                  Object.keys(c.fields)
                    .map((k) => fields[k] || k)
                    .join("、") + "发生变化",
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
    $("#review"),
    review
      ? `<p class="review-summary">${escapeHTML(review.summary)}</p>${(review.items || []).map((i) => `<div class="issue">${escapeHTML(i.reason)}${i.id ? ` <button class="text-button" data-focus="${escapeHTML(i.id)}">定位 ↗</button>` : ""}</div>`).join("")}`
      : '<p class="review-summary">等待对当前成图进行实际看图复核。导出成功不代表视觉通过。</p>',
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
      .join(""),
  );
  $("#updated").textContent =
    "最近同步 " + new Date().toLocaleTimeString("zh-CN");
  renderCanvas();
  renderMaterials();
  window.refreshChat?.();
}
async function refresh() {
  if (state.busy) return;
  state.busy = true;
  const id = state.id;
  try {
    state.tasks = await api("/api/tasks");
    if (!state.id && state.tasks.length) {
      state.id = state.tasks[0].id;
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
  const task = e.target.closest("[data-task]");
  if (task) return selectTask(task.dataset.task);
  const step = e.target.closest("[data-stage]");
  if (step) {
    state.stage = Number(step.dataset.stage);
    state.version = null;
    state.focus = null;
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
$("#original").onclick = () =>
  openImage(
    (state.stage === 2 ? state.data.boxes : selectedVersion()?.image) ||
      state.data?.current_image ||
      state.data?.reference,
  );
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
refresh();
setInterval(refresh, 2000);
setInterval(renderTiming, 1000);
