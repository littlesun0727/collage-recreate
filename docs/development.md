# 运行与开发

## 安装与启动

Python 依赖在 `requirements.txt`，已验证版本在 `requirements-lock.txt`。本机可复用已有解释器：

```powershell
$collagePython = 'D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe'
& $collagePython -B -m collage check
& $collagePython -B launch.py
```

其他机器可以创建虚拟环境并安装 `requirements.txt`。`start-local.ps1` 支持指定 Python、端口、凭证文件、抠图模型和产物目录；`start.vbs` 提供隐藏窗口启动入口。默认端口是 8809，默认数据目录是工程旁的 `collage-recreate-v9-output/runtime/`。

新建单任务和批跑任务统一使用 `tasks/task-20261023-001/` 这样的“本地日期 + 当日序号”目录。序号在入队前持久化预留，重启、并发提交和删除旧任务后都不会复用已分配编号；提交中断可留下跳号。批次名称、原参考图名和完整请求 ID 仍保存在记录中。直接使用 CLI 的 `--run` 时沿用指定路径。

Reveal 提取使用 `assets/reveal/chain/round_01/`、`round_02/`；分组和旧版提取模式使用 `batch_01/`。这些是请求目录编号，缓存是否可复用仍按目录内的完整请求记录校验。已有长名称目录保持原位并支持续跑，避免破坏历史文件引用。

## 配置

`.env` 只保存本机凭证，已被 Git 忽略；字段见 `.env.example`。OpenRouter、yibu、素材提取服务分别读取自己的字段。`COLLAGE_OUTPUT_ROOT` 可指定产物根目录，`launch.py --data` 可单独指定工作台数据目录。

工作台中可分别设置首次分析与后续步骤的供应商、模型、推理和坐标模式。新任务保存 `workflow-models.json` 与 `workflow-options.json`，续跑和编辑使用任务快照。

Codex 后端使用本机登录，依赖版本由 `package-lock.json` 锁定：

```powershell
npm.cmd ci
npx.cmd --no-install codex login
& $collagePython -B -m collage check --model-config examples/codex.example.json
```

`examples/codex.example.json` 与 `examples/codex-luna.example.json` 分别使用 Sol 和 Luna，均默认 `json_object`，输出约束通过提示词给出。首次分析使用字段表和示例，其他阶段使用任务特定 Schema，详见 [提示词说明](prompts.md)。需要接口 Schema 时显式设置 `response_format: "json_schema"`。`check` 只检查本地配置和依赖；`probe --out <目录>` 会发起一次真实图片请求并使用模型额度。

## CLI 与批跑

```powershell
& $collagePython -B -m collage create --run D:/my-tasks/demo --reference D:/images/reference.jpg --materials D:/images/customer --no-reveal
& $collagePython -B -m collage run --run D:/my-tasks/demo
& $collagePython -B batch_run.py --config examples/batch.example.json --dry-run
```

批跑前复制样例并填写实际参考图与客户素材。`--dry-run` 只检查配置和文件，不提交制作。批跑支持 `--provider`、`--model`、`--reasoning`、`--json-schema` / `--no-json-schema`、`--bbox-refinement` / `--no-bbox-refinement`、`--screen` / `--no-screen`；使用 `--help` 查看全部参数。

相框审核默认关闭。关闭时构建阶段直接使用成功提取的素材，框内像素占比等启发式检查仅记录告警，不再把相框挂为待审核或替换成简化相框；空图、读取失败和明确要求删除的素材仍按失败处理。批跑配置可写 `"screen": true` 开启，命令行开关优先于配置；`python -B -m collage create/run` 也支持 `--screen` / `--no-screen`。单任务 `POST /api/uploads/{id}/prepare` 和批跑 `POST /api/batches` 均接受布尔字段 `screen`，省略时为 `false`。开关保存在 `workflow-options.json` 中，续跑沿用任务快照；修改开关需创建新任务。

抠图需要本地模型。启动器会检查 `COLLAGE_CUTOUT_MODEL` 指定的路径；本机默认路径沿用已有模型安装位置。应用源码不依赖原工程目录。

## 开发验证

```powershell
& $collagePython -B -m pytest tests -q
npm.cmd test
```

测试使用模型响应替身，不自动发起付费调用。集成测试会实际执行本地图像准备、校验、渲染及续跑。新请求的记录应包含完整 `system-*.txt` 和 `request-*.json`，可用来对照改动前后的模型输入。

## 模块职责

| 位置 | 应该放什么 |
| --- | --- |
| `workflow/pipeline.py` | 阶段顺序、条件分支、任务身份与续跑边界 |
| `workflow/stages/` | 对应阶段的数据准备、Schema、模型调用与业务校验 |
| `workflow/context.py` | 各阶段共用的任务状态、文件与本地命令接口 |
| `workflow/support/` | 确定性的几何处理和本地降级逻辑 |
| `prompts/` | VLM 正文及唯一请求组装器 |
| `vlm/` | 预算、缓存、传输、坐标数据转换、供应商 Schema 适配 |
| `engine/` | 与模型判断分离的图像执行能力 |
| `workbench/server/` | HTTP、队列、用户编辑、动图与任务展示 |
| `workbench/web/` | 前端交互与样式 |

添加阶段时同步修改主流程、模板注册表和工作流说明；不要把业务逻辑重新塞回调度器，也不要在 Client 或阶段函数中拼自然语言规则。
