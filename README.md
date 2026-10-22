# 拼贴复刻工作流 v6

这是固定步骤的 VLM 工作流。模型返回结构化 JSON，本地程序负责校验、抠图、图层合成、编辑和动图制作。

## 离线工作流报告

双击根目录的 [workflow-report.html](workflow-report.html)，默认打开论文式流程图总览。报告包含整体步骤、各次 VLM 调用的输入输出、提示词、Schema、真实样例，以及调用次数和训练数据整理说明；支持点击节点查看详情、全屏展示和下载总览图。

换电脑展示时，复制整个工程即可；也可以只复制 `reports/workflow-atlas/` 文件夹，在新电脑用浏览器打开其中的 `index.html`。该 HTML 已内嵌图片和脚本，也可单独复制。查看报告无需安装 Python、启动服务、配置 API Key 或联网。报告是 2026-10-22 整理的静态快照，不会随运行结果自动更新。

- `reports/workflow-atlas/workflow-overview.png`：高清总览图，适合放入汇报 PPT。
- `reports/workflow-atlas/workflow-overview.svg`：矢量总览图，可独立打开和缩放。
- `reports/workflow-atlas/workflow-data.json`：报告的结构化数据。

## 启动

双击 `start.vbs`，打开 <http://127.0.0.1:8796/>。

也可以在当前目录运行：

```powershell
& 'D:\codes\visual-recreate-validation\clean-env\Scripts\python.exe' -B launch.py
```

`start-local.ps1` 提供 Python、端口、凭证文件、抠图模型和数据目录的显式参数。

## 工程内凭证

三个服务的 Key 统一放在工程根目录的 `.env` 中。路径按脚本所在位置定位，不依赖盘符、工程目录名或终端当前目录。换机器时，将这个文件随工程一起迁移：

| 服务 | 工程根目录中的文件 | 读取字段 |
| --- | --- | --- |
| OpenRouter | `.env` | `OPENROUTER_API_KEY` |
| 360 Reveal | `.env` | `360_API_KEY` |
| yibuapi | `.env` | `YIBU_API_KEY` |

首次配置可将 `.env.example` 复制为 `.env`，再填入自己的三个 Key。`.env` 已被 Git 忽略，因此仅拉取代码不会得到该文件；不要把真实 Key 填入示例文件。

三个服务均优先读取同名进程环境变量，没有时读取 `.env`。如需另指定文件，仍可使用 `COLLAGE_VLM_ENV`（OpenRouter）、`COLLAGE_REVEAL_ENV`（360）、`COLLAGE_YIBU_CREDENTIALS`（yibu）或对应启动参数；这些显式设置会覆盖工程内默认路径。yibu 显式指定 `.json` 文件时仍兼容原有 `api_keys` 数组格式，但默认无需 JSON 文件。换机器时应清除指向旧机器目录的覆盖设置。修改路径配置后重启工作台服务。

## 按模型批跑与分组

`batch_run.py` 将多张参考图提交到工作台的持久化队列。每次启动生成一个独立批次，前端可按批次筛选、折叠和搜索；单独上传和旧任务归入“单独任务 / 历史任务”。批次记录保存在服务数据目录的 `service/batches/`，任务仍放在 `tasks/`，每个任务含有 `batch-run.json`，刷新或重启不会丢失归属。

复制 `batch.example.json` 到自己的配置文件，填写 `references` 参考图列表和固定的 `materials` 客户素材列表。两者均可指定文件或目录；目录递归按路径排序，显式列表保持顺序；配置内相对路径以配置文件所在目录为准。支持 PNG/JPG/WebP/BMP，客户素材也支持 MP4/MOV。每批合计最多 100 个输入文件、1 GB，单文件最多 128 MB。

```powershell
$batchPython = 'D:\codes\visual-recreate-validation\clean-env\Scripts\python.exe'

# 仅校验模型参数与本地输入，输出实际文件清单，不上传或发起模型调用。
& $batchPython -B batch_run.py --config D:\my-batches\comparison.json --dry-run

# 按配置提交；服务未启动时自动启动，--open 打开该批次页面。
& $batchPython -B batch_run.py --config D:\my-batches\comparison.json --open

# 固定同一份输入，只更换模型与推理强度；每次提交都是新批次。
& $batchPython -B batch_run.py --config D:\my-batches\comparison.json --provider yibu --model gpt-6.1-sol --reasoning medium --name 'GPT medium' --open
& $batchPython -B batch_run.py --config D:\my-batches\comparison.json --provider yibu --model qwen3.8-flash --reasoning high --name 'Qwen high' --open

# 使用原图像素坐标；改为 normalized_1000 可使用 0～1000 归一化坐标。
& $batchPython -B batch_run.py --config D:\my-batches\comparison.json --coordinate-mode pixel --name '原图像素对比' --open
```

模型 ID 使用供应商实际提供的名称。`--model` 和 `--reasoning` 统一应用于首次分析和后续全部 VLM 步骤，并固定到每个任务；不会改写前端的全局默认模型。推理可选 `default/off/on/minimal/low/medium/high/xhigh`；不受现有供应商适配支持的组合会在提交前报错。预算模式使用 `--reasoning-budget 4096`，不能同时指定推理档位。坐标模式可用 `--coordinate-mode normalized_1000/pixel/auto` 指定，分别表示 0～1000 归一化、原图像素和按模型自动选择；命令行优先于配置文件中的 `coordinate_mode`，两者均未指定时默认归一化。`max_tokens`、`response_format` 通过配置固定；图片提取、抠图与合成仍使用现有实现。高级对比可在配置中用完整 `model_policy` 指定 `analyze` / `subsequent`，此时不要同时传单模型命令行参数；仍可用 `--coordinate-mode` 统一覆盖这两个阶段的坐标模式。已有批次保留提交时的设置，`--resume` 不能同时传 `--coordinate-mode`。

客户素材只上传一次，整批共用上传后的快照；参考图顺序、素材顺序和 SHA-256 保存到批次记录。相同输入会产生相同的输入指纹，便于核对模型之间是否使用同一批数据；输入指纹不代表流程参数或代码版本相同。关闭前端或脚本不会停止已入队的制作。单张失败仍保留在该分组，队列继续处理其余任务；批次“已完成”表示制作结束，视觉是否通过仍看任务自己的复核结果。

提交前会保存回执到 `collage-recreate-v6-output/batch-receipts/`。提交响应中断时，使用 `--resume <回执.json>` 续接同一个批次；已有失败任务不会因此重跑。`--wait` 持续报告进度，整批结束时有失败则返回非零退出码。需要重做整批时使用原配置再次启动，生成新分组。

若旧工作台仍在执行任务，使用独立端口及数据目录启动新版，保留旧队列继续执行：

```powershell
& $batchPython -B batch_run.py --config D:\my-batches\comparison.json --server http://127.0.0.1:8797 --data D:\codes\collage-recreate-v6-output\batch-runtime --open
```

`--data` 仅在自动启动服务时生效。同一个服务数据目录只能由一个工作台占用。恢复默认端口前，请等旧任务结束再重启旧服务。

## 目录

| 路径 | 用途 |
|---|---|
| `collage_v6/` | 工作流调度、模型调用、提示词、坐标转换和结构化输出校验 |
| `engine/scripts/` | 图片准备、窗口处理、渲染、抠图、素材提取、编辑和动图实现 |
| `engine/schemas/` | 工作流实际使用的 JSON Schema |
| `engine/analysis-rules.md` | 运行时会读入分析提示词的规则 |
| `workbench/server/` | 本地 API、制作队列、编辑和动图服务 |
| `workbench/web/` | 前端界面 |
| `launch.py`、`start.vbs`、`start-local.ps1` | 启动入口 |
| `requirements*.txt` | 运行依赖；lock 文件记录已验证版本 |

原 `skill/scripts` 和 `skill/schemas` 是实际运行依赖，已迁入 `engine`。不再保留 Skill 包装、历史方案和实验报告。

## 产物在项目目录外

默认位置：`D:\codes\collage-recreate-v6-output`。

```text
collage-recreate-v6-output/
  runtime/
    tasks/          每个任务的输入、analysis、窗口参数、图片、视频与版本记录
    service/        上传文件、制作队列、VLM 请求与响应
    chat.sqlite     对话编辑队列
    server-*.log    服务日志
  validation/       历史实验与对比结果
  reports/          历史文档、导出的提示词及报告
  maintenance/      移出的测试和实验工具
  checks/           迁移验证记录
  archive/          清理前备份
  path-relocations.json
```

`COLLAGE_OUTPUT_ROOT` 可覆盖默认产物根目录；`launch.py --data <目录>` 可指定本次服务的数据目录。正常启动默认使用 `-B`，不向源码写入 Python 缓存。

历史任务含有绝对路径和哈希校验。`path-relocations.json` 用于在读取时解析迁移后的路径，保留原始 JSON 和图片的校验值；不要删除这个文件。旧任务的 `portable-task.json` 保留原工作台任务 ID。

## 工作流及提示词

```text
准备输入
→ analyze：生成 analysis.json 与文字单元
→ photo_windows：判断窗口形状、主体处理、描边和渐隐
→ boxes：叠框复核
→ materials：理解客户素材
→ bind：选择素材与裁切
→ build：提取素材并合成首版
→ screen：仅审核相框照片残留，其他装饰保留
→ register：登记并交付结果
```

准确执行分支以 `collage_v6/runner.py` 为准。实际提示词入口为 `collage_v6/prompts.py`，并读取同目录下的规则 Markdown 和 `engine/analysis-rules.md`。

默认首次 `analyze` 使用 Qwen3.8-Max thinking，后续 VLM 调用使用 Qwen3.8-Flash，不开 thinking。参考分析的 Qwen 坐标模式默认采用 0–1000 归一化，并在本地还原为原图像素；具体模型及预算以 `collage_v6/config.py` 为准。

## 切换供应商和模型

工作台左侧点击“供应商与模型设置”，或在上传弹窗点击“更换模型”。首次分析和后续 VLM 可独立选择 OpenRouter / yibuapi、模型 ID、thinking 开关、token 预算或推理档位。模型列表支持搜索，也可手动输入模型 ID。可在高级选项选择 normalized / pixel 坐标及 JSON 输出方式。

保存设置只做本地校验，不调用推理模型。新任务默认配置保存在 `runtime/service/model-settings.json`；上传提交时保存配置快照，准备完成后写入任务的 `workflow-models.json`。改变默认设置不会改变排队任务、旧任务续跑或该任务的对话修改模型。制作记录显示实际供应商及模型。

VLM 使用 Chat Completions 兼容接口。OpenRouter 使用其 `reasoning` 参数；yibu 按 Qwen、GPT、Gemini、Claude 分别适配推理参数，未知模型仅开放“模型默认”。模型目录不能证明所有参数被执行，具体兼容性以实际接口响应为准。所有输出仍经过本地严格 JSON Schema / 业务校验；不支持远端 Schema 的模型可选择 JSON 对象或提示词约束 JSON。不会自动切换供应商或模型。

yibu 默认从工程根目录 `.env` 的 `YIBU_API_KEY` 读取凭证，通过现有 `http://127.0.0.1:17860` 审计网关访问上游，不会将 key 放进前端或任务快照。网关须保持运行；另一台机器也需要部署该网关，复制凭证不会启动或安装网关。`COLLAGE_YIBU_CREDENTIALS` 可以覆盖凭证文件位置。响应中未提供费用时，`usage.cost_complete=false`，不能把已报告费用的汇总零值理解为免费。

CLI 可传 `--model-config <JSON文件>`，格式与前端保存的 `model-settings.json` 相同，例如：

```powershell
python -B -m collage_v6 check --model-config D:\codes\collage-recreate-v6-output\runtime\service\model-settings.json
python -B -m collage_v6 run --run <任务目录> --model-config <配置文件>
```

已有任务续跑时省略 `--model-config` 会读取任务快照；显式传入不一致的配置会拒绝续跑。首次分析的格式纠错仍使用该阶段模型。

yibu GPT 可继续选择 JSON Schema：请求前将有限枚举上的条件约束转换为 `anyOf` 分支，将可选字段转换为可空必填字段，并复用重复结构。返回后仅移除适配产生的可选空值，再执行原始完整 Schema、业务规则和坐标校验；真实可空字段、`0` 和 `false` 均保留。图片尺寸数组的固定值由本地严格检查。原始规则不为适配接口而删除。不支持的条件形式会在本地报错，不静默丢弃约束。

每次模型调用最多进行一次格式纠错。首次分析先整理不影响布局的可恢复问题：忽略非照片对象的照片专用参数、当前形状不支持的纸边参数，整理空文字识别元数据；误写为嵌入像素的照片、相框或嵌套布局归属改为父级关联，保留独立图层。保留全部对象、坐标与提取方式。文字单元与载体范围不一致只记录警告，交给后续叠框复核。处理记录保存在 `workflow/reference-cleanup.json`，原始模型返回保持不变。

JSON 结构、有效坐标、对象 ID 和关联、客户素材绑定等执行所需的校验仍保留。纠错后仍存在阻塞错误或接口请求失败时停止，工作台显示原因。已完成的历史结果不会自动重算。

yibu GPT 使用流式传输并请求用量统计，减少长时间非流式等待造成的网关超时；仍然接收完整 JSON、检查结束状态后再校验和写入业务文件，不使用半截结果。格式纠错也保持兼容的严格 JSON Schema。

每个任务目录的关键产物：

- `analysis.json`：布局与对象。
- 独立提取的单窗装饰相框使用 `kind=frame`、`method=extract` 和已有 `photo_id`，不增加业务字段或计划文件。相框外沿与照片内口分别定位，同批 360 请求包含框体和关联照片框；照片上下文返回层不作为客户素材使用。几何窗口单色轮廓和主体描边仍归 `photo-windows.json`，规则本地纸底、普通装饰及连续多窗载体仍为 overlay，历史 overlay 关联保持兼容。
- 仅 frame 进入视觉审核。内口不透明比例仅供参考，不等于原照片残留；轻微脏边保留。模型必须标记 `photo_content=present/absent/uncertain`，程序仅允许明确 present 的可用候选被自动淘汰，不能确认时保留。keep 保留框边；backing 保留不透明信封/文件夹纸底并放到照片下方；drop 拒绝残留原照片的提取像素，自动按原外框、旋转和照片内口绘制替代相框，标记近似。缺失或空像素也由程序相框补位。其他装饰跳过视觉淘汰，文件为空/损坏仍报告。旧任务不会自动重算。
- `photo-windows.json`：照片窗口参数。
- `previews/analysis-boxes.png`：bbox 叠框。
- `previews/photo-windows.png`、`previews/photo-window-masks-*.png`：窗口与透明度预览。
- `bindings.json`：客户素材绑定。
- `scene.json`：编译后的渲染场景。
- `final.png`、`result.json`：成图和结果。

`photo_windows` 在分析后、叠框复核前执行；已经完成构建的旧任务不会自动补跑这个阶段。

照片在 `analysis.mode` 中初筛为 `cover / cutout / feather / stroke`，组合效果写入已有 `description`。`photo-windows.json` 核实并提供最终参数，不增加分类字段或计划文件。`stroke` 不等于抠像：`subject.mode=preserve` 配合 `stroke.target=subject` 保留摄影背景，仅用主体蒙版描边；静态和 Live 共用同一窗口处理。没有描边的照片不额外分割，旧 cutout 与窗口描边保持原有处理。

照片描边支持 `pattern=solid/dash/dot`、`dash` 长度/间隔、`line_cap`、`placement=inside/center/outside`、`offset`，长度为窗口短边比例。外侧描边对称扩展画布，保持照片原位置。静态与 Live 共用实现；旧参数仍沿用原实线内描边。不支持任意指定子主体或精确手绘笔触。

连续出框前景在窗口参数中用 `linked_to` 指向底图：强制共用客户素材、源坐标裁切、镜像、缩放、平移和视频时间。主体蒙版保留完整源图坐标，不再紧裁后独立 fit；独立贴纸不建立该关联。画布中整组变换，裁切编辑底图。

真实渐隐接缝用 `seams` 明确 edge、underlay_id、width，并落实到 fade。没有足够下层覆盖时仅收缩相应渐隐并记录告警，不丢弃其他窗口效果；主体轮廓柔化仍独立使用 edge_softness。

背景来源在原 analyze 调用中选择：`photographic` 绑定客户照片；`simple_design` 本地绘制；`reference_clean` 使用 `kind=background, method=extract`，通过 Reveal 素材清底保留固定模板。无需独立编辑、也不参与前后穿插的固定底板可以整体保留，包括实拍材质与结构。旧 `complex_design` 继续按本地近似读取，不自动改写历史任务。

full 模式保持原素材选择及关联分组，每次最多 20 个框（含照片上下文）。超限批次串行读取上一轮 `layer_0`，直接转为 RGB 后继续提取，不检查底板透明度，素材逐批累计；素材漏提单独记录，只要底板有效就继续后续轮次。仅 reference_clean 背景继续清除 analysis 中未被返回映射覆盖的 photo、独立 text、overlay 和 frame，包括前一轮漏提的前景。已嵌入完整载体的内容不重复清除，parent_id 或框相交不代表像素归属。没有素材提取时从原参考开始清底；没有剩余清底目标时直接使用最后底板。不新增 VLM 调用，不自动识别像素残留。映射和文件检查不等于视觉审核。

`assets/reveal/chain-report.json` 记录各轮输入、映射、素材缺失、清底 ID 和最终底板。新批次缓存绑定实际输入底板哈希，坐标按输入尺寸从原分析映射。清底失败时，背景转为本地近似绘制：优先使用已有 style，缺少底色时按 analysis 框排除前景后取背景颜色中位数；没有足够样本则使用中性底色。兜底不会复制原图照片或文字，结果标记为 approximate/local_background，并保留失败原因，不冒充清底成功。full 模式默认总提交上限 16，可用 `--reveal-max-requests` 或 `COLLAGE_REVEAL_MAX_REQUESTS` 调整，HTTP 重试也计数；执行前检查素材与清底计划是否超限。grouped/legacy 保留原路径及限制。

`style.patterns` 只用于有周期依据的纹样；图元参数与支持范围见 `collage_v6/reference_drawing.md`。`reference_tile` 从干净完整的重复单元取样后本地平铺，与任何照片 bbox 相交时拒绝取样。降级图案必须与参考相符，没有合适替代时记录缺失；不能用任意图元或平铺代替非周期性设计。

## 本机外部依赖

- Python：`D:\codes\visual-recreate-validation\clean-env\Scripts\python.exe`
- 抠图模型：`D:\codes\visual-recreate-validation\models\birefnet-lite-fp32.onnx`
- 动图需要可用的 FFmpeg。

通过启动参数或 `COLLAGE_CUTOUT_MODEL` 等环境变量覆盖本机依赖配置。凭证默认放在工程目录内，按上面的“工程内凭证”配置；抠图模型仍单独配置。

离线配置检查：

```powershell
& 'D:\codes\visual-recreate-validation\clean-env\Scripts\python.exe' -B -m collage_v6 check
```

端到端 CLI 保留 `python -B -m collage_v6 create/run`，用 `--help` 查看参数；任务的 `--run` 路径请放在项目目录外。
