# Codex SDK 串行样片测试

此目录只负责启动独立制作 agent、计时和读取证据，不预写 analysis、bindings 或视觉复核答案。每张样片使用一个新的 Codex thread，固定模型 `gpt-5.6-sol`、推理档位 `medium`；按参考文件名排序，必须等上一张 turn 结束后才开始下一张。制作仍遵循 `skill/SKILL.md`。

在仓库根目录运行本机已有 SDK：

```powershell
node workbench/evaluation/run-serial-sdk.mjs --root D:/codes/collage_outputs/sdk-serial-20261008-r1 --sdk D:/codes/collage_batch/node_modules/@openai/codex-sdk/dist/index.js
```

若在其他环境使用，先在本目录安装 package.json 中固定的 `@openai/codex-sdk`，省略 `--sdk`。当前运行器中的 CLI、Python、素材及模型路径遵循此项目的 Windows 本机环境。登录使用现有 Codex 配置，不输出或复制凭据。

数据保存在源码仓库外：

- `manifest.json`：固定模型、推理档位、单并发、输入清单和 skill 源文件哈希。
- `controller/<样本>/state.json`：开始、结束、真实墙钟耗时、thread ID、usage 和完成情况。
- `controller/<样本>/events.jsonl`：SDK 执行事件，不额外保存 reasoning 项。
- `tasks/<样本>/`：原始制作产物、事件和版本；`sdk-timing.json` 供工作台显示计时。
- `batch-result.json`：每完成一张就保存批次进度。

总耗时包含 SDK 启动、看图分析、工具运行、网络等待及复核回复。界面运行中计时持续增长，结束后冻结；它不是单次 render 的耗时。测试采用全新 RUN，不复用以前任务的分析或提取缓存。

默认每样本超时 45 分钟。超时或 SDK 基础设施失败会停止队列，避免上一任务是否退出仍不明确就执行下一张；正常结束但未交付的样本保留 incomplete，再继续下一张。根目录创建 `STOP` 文件会在当前样本结束后停止。`--resume` 只跳过已完成或明确 incomplete 的样本，并验证 skill 未变更；对正在执行或失败样本必须先检查真实进程与已有证据，不能盲目重复提取。

生成或更新可阅读的耗时表与证据审计：

```powershell
& 'D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe' -B workbench/evaluation/report.py --root D:/codes/collage_outputs/sdk-serial-20261008-r1 --contact-sheets
```

输出 `REPORT.md`、`timings.csv`、`audit.json` 和参考/成图联系表。审计从真实 session 的 turn_context 验证模型与档位，检查样本时间是否重叠、客户照片来源校验以及当前成图和历史快照哈希。素材提取门禁与 agent 视觉复核分别记录，完成执行不代表视觉通过。

2026-10-08 首次预检目录 `sdk-serial-20261008` 因 Windows 状态文件占用在分析阶段结束，尚未提取。修复记录器后使用独立正式批次 `sdk-serial-20261008-r1`，不把预检中断耗时混入正式样本。
