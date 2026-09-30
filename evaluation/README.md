# 独立首版验收

验收主控固定为 **Codex Agent SDK 0.158.0 + gpt-5.6-sol / medium**。开发主控只实现工具、启动验收、汇总日志，不代写各图的分析、绑定、视觉 review。每张图独立会话，不提供历史答案。子会话仍使用 workspace-write 沙箱及 on-request 审批。

启动器将参考图直接作为 local_image 传入，让模型读 SKILL.md 后完整操作。模型自己完成严格 JSON、叠框检查、选图、build、完整看图、review 登记。首版测试禁用生成，不进行 apply 或多轮精修。报告中的 `completed` 只表示测试完成，视觉质量以 `visual_verdict` 和 `renders_verified` 为准。

## 本机重跑

```powershell
node D:\codes\collage-recreate-v5\evaluation\run-sdk.mjs --root D:\codes\collage_outputs\v5-eval-new --concurrency 2
```

默认读取 `D:\datas\图片排版样图` 全部 JPG/PNG/WebP。客户素材使用 `D:\视频素材\人像素材3.0` 和 `D:\视频素材\风景照片`。用 `--names '图片1.jpg,图片2.png'` 选择子集，`--reference-dir` 指定其他参考目录。使用新 root，避免覆盖已冻结首版。每张图默认超时 20 分钟，最多两个会话回合；中断后可在同一 root 追加 `--resume`。在 root 创建 `STOP` 文件可停止本批；不会停止其他 Codex 任务。

SDK 优先读本目录 node_modules，未安装时读本机 `D:\codes\collage_batch\node_modules\@openai\codex-sdk`。其他机器先在本目录安装 package.json 依赖，并调整 run-sdk.mjs 中 CLI、Python、素材路径。本机 PowerShell 的 npm.ps1 执行策略受限时使用 npm.cmd。

Windows 调用 exec_command 时保持 tty:true。当前机器的 SDK 子会话存在沙箱 PTY 初始化失败，需要正常审批后重试；不要关闭沙箱或更改安全配置。启动器需要读真实 Codex HOME 的登录/会话状态，因此外层沙箱可能需要审批。

## 证据与汇总

- manifest.json：模型、effort、参考目录、素材目录及技能文件指纹。
- 每图 controller/task.md：实际验收任务；state.json：thread_id、耗时与状态；events.jsonl：逐事件时间戳、工具及模型输出，不保存推理正文。
- 每图 run/：分析、绑定、图像、严格 review、确定性来源校验与本地阶段耗时。
- 每图 evaluation.json：指定模型自己的视觉优缺点和操作障碍。

```powershell
D:\codes\visual-recreate-validation\clean-env\Scripts\python.exe D:\codes\collage-recreate-v5\evaluation\summarize.py D:\codes\collage_outputs\v5-eval-new --output D:\codes\collage-recreate-v5\evaluation\report
```

汇总程序只读上述证据，并从实际会话 turn_context 核对 model/effort，不执行视觉判分。首版时间从会话开始到 build_finished；总时间包括看图、review 与交付。工具时间来自 events.jsonl。扣除本地工具后的剩余时间包含模型输出、工具调度、会话初始化及审批等待，不能全部称为“模型思考时间”。

纯程序回归测试使用生产解释器：

```powershell
D:\codes\visual-recreate-validation\clean-env\Scripts\python.exe -m pytest D:\codes\collage-recreate-v5\tests -q --basetemp D:\codes\collage-recreate-v5-test-fresh
```

Yibu 测试目前覆盖 dry-run 零 HTTP 和模拟成功响应后的组合/来源保留；首版测试不验证远端服务延迟与生成质量。
