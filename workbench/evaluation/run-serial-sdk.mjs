/** Run independent Codex agents strictly one at a time; never supply design answers. */
import fs from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { createHash, randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";

const { values: args } = parseArgs({
  options: {
    root: { type: "string" },
    sdk: { type: "string" },
    "reference-dir": {
      type: "string",
      default: "D:/datas/图片排版样图_去水印",
    },
    "timeout-minutes": { type: "string", default: "45" },
    resume: { type: "boolean", default: false },
  },
});
if (!args.root) throw new Error("--root is required");
const REPO = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const SKILL = path.join(REPO, "skill");
const ROOT = path.resolve(args.root);
const MODEL = "gpt-5.6-sol";
const EFFORT = "medium";
const CLI = "C:/Users/admin/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe";
const PY = "D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe";
const MATERIALS = ["D:/视频素材/人像素材3.0", "D:/视频素材/风景照片"];
const sdkUrl = args.sdk
  ? pathToFileURL(path.resolve(args.sdk)).href
  : import.meta.resolve("@openai/codex-sdk");
const { Codex } = await import(sdkUrl);
const read = async (file) =>
  JSON.parse((await fs.readFile(file, "utf8")).replace(/^\uFEFF/, ""));
async function write(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const tmp = file + "." + randomUUID() + ".tmp";
  const text = JSON.stringify(value, null, 2) + "\n";
  await fs.writeFile(tmp, text, "utf8");
  for (let attempt = 0; attempt < 20; attempt++) {
    try {
      await fs.rename(tmp, file);
      return;
    } catch (error) {
      if (!["EPERM", "EACCES", "EBUSY"].includes(error.code)) throw error;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  // Windows readers/security scanners can retain a delete-sharing lock.
  // This is controller telemetry, not an immutable production version.
  await fs.writeFile(file, text, "utf8");
  await fs.unlink(tmp).catch(() => {});
}
async function hashes(dir) {
  const result = {};
  for (const entry of (await fs.readdir(dir, { withFileTypes: true })).sort(
    (a, b) => a.name.localeCompare(b.name),
  )) {
    if (entry.name.startsWith(".") || entry.name === "__pycache__") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) Object.assign(result, await hashes(full));
    else
      result[path.relative(SKILL, full)] = createHash("sha256")
        .update(await fs.readFile(full))
        .digest("hex");
  }
  return result;
}
await fs.mkdir(ROOT, { recursive: true });
const lock = await fs.open(path.join(ROOT, "runner.lock"), "wx");
await lock.writeFile(String(process.pid));
const skillHashes = await hashes(SKILL);
const names = (await fs.readdir(args["reference-dir"]))
  .filter((n) => /\.(png|jpe?g|webp)$/i.test(n))
  .sort();
const samples = names.map((name, i) => ({
  index: i + 1,
  name,
  task: `${String(i + 1).padStart(2, "0")}-${path.parse(name).name}`,
}));
const manifestFile = path.join(ROOT, "manifest.json");
const manifest = {
  schema_version: "collage-sdk-batch/1",
  created_at: new Date().toISOString(),
  model: MODEL,
  effort: EFFORT,
  sdk: sdkUrl,
  concurrency: 1,
  reference_dir: args["reference-dir"],
  materials: MATERIALS,
  samples,
  skill: SKILL,
  skill_hashes: skillHashes,
  timeout_minutes: Number(args["timeout-minutes"]),
  timing:
    "wall time from SDK turn start through final response; includes startup, tools and remote waits",
};
const results = [];
let stopped = false;
const codex = new Codex({
  codexPathOverride: CLI,
  config: { features: { image_generation: false, multi_agent: false } },
});

function taskPrompt(sample, run, control) {
  return `请独立执行 ${SKILL}/SKILL.md，完成一个样片的完整制作与实际看图复核。模型固定 ${MODEL}，推理档位 ${EFFORT}。先完整读 SKILL.md，按需读它引用的文档。
参考图：${path.join(args["reference-dir"], sample.name)}
客户素材目录：${MATERIALS.join(" 和 ")}
唯一任务目录 RUN=${run}，prepare 前不存在。解释器 ${PY}，输出宽度 1200；人物抠图模型按 SKILL.md 使用本机已有模型。
这是用户要求的端到端串行测试。你独立完成读图、analysis/bindings、校验、实际查看叠框、构建首版、必要时一次集中 screen、整图与局部看图、必要时一次 apply、review 登记、stage 6 交付。不要只做分析；不需要等待人工确认。遵守 skill 首版范围，不进行无限精修；每个阶段使用 references/workbench.md 的 progress 记录真实操作和结论。
有复杂装饰时使用固定 workflow build --reveal 真实提取；纯照片或明确的规则几何才可 --no-reveal。用户授权本次样片制作所需的现有提取服务。不读取历史任务答案、analysis、bindings、提取缓存或其他样片结果。不要修改 skill 或 workbench 源码，不安装依赖，不写替代制作脚本。允许在 RUN 内写分析、绑定、筛选方案、调整和复核 JSON。scene/result 与来源证明只能由既有脚本生成。
客户人物和照片只能来自给定 catalog；不可生成替代人像，不把参考整图当背景回填。提取不完整、裁切、遮挡、边框、抠图缺口都要实际看图并如实记录；有关键缺口不能判 pass。必须看整图与 result 指定的局部图。所有看图用 view_image({path, detail:"original"})；经 functions.exec 转发时必须 image(result.image_url,"original")，两处都显式 original。
Windows 每个 exec_command 必须 tty:true、login:false；中文文本读写 UTF-8。只在 RUN 和 ${control} 写文件。不要打开或输出凭据，固定 workflow 自行使用已有配置；不修改安全设置。如果网络或工具报错，只做有证据的恢复，不重复提交结果未知的提取请求，不另建任务绕过提交上限。不能调用子 agent 或并行执行其他样片。
交付后在 ${path.join(control, "evaluation.json")} 保存 {"model":"${MODEL}","effort":"${EFFORT}","visual_verdict":"pass或needs_changes或blocked","strengths":[实际优点],"issues":[实际问题],"skill_friction":[实际操作障碍]}。最后回复成图和对照路径及真实结论。耗时由外部 SDK 控制器计时，不要自己猜测。
不要把本提示提到的路径当已存在；首先 prepare 后再查看其 prepared/reference.png 与客户联系表。`;
}

async function runSample(sample) {
  const run = path.join(ROOT, "tasks", sample.task);
  const control = path.join(ROOT, "controller", sample.task);
  const stateFile = path.join(control, "state.json");
  if (existsSync(stateFile)) {
    const old = await read(stateFile);
    if (args.resume && ["completed", "incomplete"].includes(old.status)) {
      results.push(old);
      return;
    }
    throw new Error(
      `Sample already started: ${sample.task}; inspect its evidence before any retry`,
    );
  }
  await fs.mkdir(control, { recursive: true });
  const prompt = taskPrompt(sample, run, control);
  await fs.writeFile(path.join(control, "task.md"), prompt, "utf8");
  const state = {
    ...sample,
    model: MODEL,
    effort: EFFORT,
    status: "running",
    started_at: new Date().toISOString(),
    run,
  };
  const start = performance.now();
  await write(stateFile, state);
  console.log("START", sample.index, sample.name, state.started_at);
  const thread = codex.startThread({
    model: MODEL,
    modelReasoningEffort: EFFORT,
    workingDirectory: control,
    skipGitRepoCheck: true,
    sandboxMode: "workspace-write",
    approvalPolicy: "on-request",
    additionalDirectories: [path.join(ROOT, "tasks")],
  });
  const abort = new AbortController();
  const timer = setTimeout(
    () => abort.abort(),
    Number(args["timeout-minutes"]) * 60000,
  );
  const sync = async () => {
    state.elapsed_seconds = Math.round((performance.now() - start) / 100) / 10;
    await write(stateFile, state);
    if (existsSync(path.join(run, "input.json"))) {
      await write(path.join(run, "sdk-timing.json"), {
        model: MODEL,
        effort: EFFORT,
        started_at: state.started_at,
        finished_at: state.finished_at ?? null,
        elapsed_seconds: state.elapsed_seconds,
        status: state.status,
        thread_id: state.thread_id ?? null,
      });
    }
  };
  let completed = false;
  try {
    const { events } = await thread.runStreamed(prompt, {
      signal: abort.signal,
    });
    for await (const event of events) {
      const at = new Date().toISOString();
      if (event.type === "thread.started") state.thread_id = event.thread_id;
      if (event.type === "turn.completed") {
        state.usage = event.usage;
        completed = true;
      }
      if (event.type === "turn.failed" || event.type === "error")
        state.error = event.error ?? event.message;
      if (
        event.type === "item.completed" &&
        event.item?.type === "agent_message"
      ) {
        await fs.writeFile(
          path.join(control, "last-response.txt"),
          event.item.text,
          "utf8",
        );
        console.log(
          "MESSAGE",
          sample.index,
          event.item.text.slice(0, 180).replace(/\s+/g, " "),
        );
      }
      if (event.item?.type !== "reasoning")
        await fs.appendFile(
          path.join(control, "events.jsonl"),
          JSON.stringify({ at, ...event }) + "\n",
        );
      await sync();
    }
    const result = existsSync(path.join(run, "result.json"))
      ? await read(path.join(run, "result.json"))
      : {};
    const taskEvents = existsSync(path.join(run, "events.jsonl"))
      ? await fs.readFile(path.join(run, "events.jsonl"), "utf8")
      : "";
    const delivered = taskEvents.split("\n").some((line) => {
      try {
        const e = JSON.parse(line);
        return (
          e.event === "agent_stage" && e.stage === 6 && e.status === "complete"
        );
      } catch {
        return false;
      }
    });
    state.status =
      completed && result.exported && result.visual_review && delivered
        ? "completed"
        : "incomplete";
    state.visual_verdict = result.visual_review?.verdict ?? "blocked";
    state.customer_photos_verified = result.customer_photos_verified ?? false;
    state.render_id = result.render_id ?? null;
    state.versions = existsSync(path.join(run, "observability/versions"))
      ? (await fs.readdir(path.join(run, "observability/versions"))).filter(
          (n) => !n.startsWith("."),
        ).length
      : 0;
    if (!completed) stopped = true; // Never overlap a failed/unknown agent with the next case.
  } catch (error) {
    state.status = abort.signal.aborted ? "timed_out" : "failed";
    state.error = String(error);
    stopped = true;
  } finally {
    clearTimeout(timer);
    state.finished_at = new Date().toISOString();
    await sync();
    results.push(state);
    await write(path.join(ROOT, "batch-result.json"), {
      model: MODEL,
      effort: EFFORT,
      concurrency: 1,
      finished: false,
      results,
    });
    console.log(
      "END",
      sample.index,
      sample.name,
      state.status,
      state.elapsed_seconds,
      state.visual_verdict,
    );
  }
}

try {
  if (existsSync(manifestFile)) {
    const old = await read(manifestFile);
    if (
      !args.resume ||
      JSON.stringify(old.skill_hashes) !== JSON.stringify(skillHashes)
    )
      throw new Error("Existing batch requires --resume and unchanged skill");
  } else {
    await write(manifestFile, manifest);
  }
  await fs.mkdir(path.join(ROOT, "tasks"), { recursive: true });
  for (const sample of samples) {
    if (stopped || existsSync(path.join(ROOT, "STOP"))) break;
    if (JSON.stringify(await hashes(SKILL)) !== JSON.stringify(skillHashes))
      throw new Error(
        "Skill changed during batch; stop to preserve comparable timings",
      );
    await runSample(sample); // Intentionally no worker pool or Promise.all.
  }
  await write(path.join(ROOT, "batch-result.json"), {
    model: MODEL,
    effort: EFFORT,
    concurrency: 1,
    finished: results.length === samples.length,
    finished_at: new Date().toISOString(),
    results,
  });
} finally {
  await lock.close();
  await fs.unlink(path.join(ROOT, "runner.lock"));
}
