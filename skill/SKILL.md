---
name: collage-recreate-v5
description: 根据拼贴参考图和客户照片制作替换照片后的拼贴首版，或仅解析参考图的布局、图层与素材绑定；支持装饰提取、本地绘制、集中筛选和整图复核。
---

# 拼贴参考图复刻

用客户照片替换参考图中的照片，保留主要构图、文字、装饰与遮挡关系。默认流程：准备输入 → 分析绑定与叠框纠偏 → 出图 → 必要时一次筛选 → 整图复核（按需一次本地排版修正）→ 交付。同一主控完成分析和看图，不逐素材请求模型。

客户要求继续修改已交付任务时，读 [交付后修改](references/revisions.md)，基于当前版本换照片、改独立文字或调整排版，保留旧版本；不要重新开始首版流程。

## 1. 确认输入，准备任务目录

需要参考图、客户照片目录，以及用户指定的文案和输出要求。先利用已有路径与附件；缺少必要输入时再询问。用户只要求解析时，在第 2 步完成后停止。

本机 PowerShell 使用以下变量；将参考图、照片目录和任务名替换为实际路径。`$run` 必须是尚不存在的新目录，位于 skill 和客户素材目录之外。

```powershell
$py = 'D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe'
$skill = 'D:/codes/collage-recreate-v5/skill'
$run = 'D:/codes/collage_outputs/任务名'
$ref = 'D:/实际路径/参考图.png'
$materials = @('D:/实际路径/客户照片')
$cutout = 'D:/codes/visual-recreate-validation/models/birefnet-lite-fp32.onnx'

& $py "$skill/scripts/workflow.py" prepare --reference $ref --materials $materials --run $run --width 1200 --cutout-model $cutout
```

- `1200` 是输出宽度，按用户要求调整；分析坐标始终使用参考图原尺寸。
- `--cutout-model` 用于人物抠图；模型不可用且任务不需要抠图时可省略，需要抠图时先解决模型路径。
- Windows 的 `exec_command` 使用 `tty: true`，文本读写使用 UTF-8。本机依赖说明见 [运行环境](references/environment.md)。
- 继续已有任务时读取其 `input.json`，不要再次 `prepare`。

**完成条件：** 得到 `input.json`、`prepared/reference.png`、`prepared/catalog.json` 和客户联系表。记录返回的实际 `reference_size`、素材数与警告。

工作台默认从任务目录读取事件和版本快照，不需要启动网页才能制作。prepare 后先读 [工作台进度记录](references/workbench.md)，在分析、筛选、看图复核和交付的实际边界执行 `progress`；脚本命令及素材处理进度自动记录。记录只描述实际已做的操作，不以命令成功代替看图判断。

## 2. 看图，写分析与绑定，叠框纠偏

先读 [分析与绑定](references/analysis.md)，集中查看原尺寸参考图及 catalog 列出的客户联系表。写入任务目录中的两个严格 UTF-8 JSON：

所有看图调用都必须同时使用 `view_image({path, detail: "original"})` 和 `image(result.image_url, "original")`；批量转发时每张也显式传入 `"original"`，不可省略任一处参数。代码示例见分析文档。

| 文件 | 内容 |
|---|---|
| `analysis.json` | 背景、照片窗口、文字、装饰的 ID、原图 bbox、制作方式和从底到顶的 `layer_order` |
| `bindings.json` | 每个照片窗口对应的真实 catalog 素材 ID，以及允许替换的不可辨文字 |

分析时守住以下边界，字段和完整示例以参考文档为准：

- 客户照片内容随照片替换；不要把参考人物的服饰、手持物或场景误当固定装饰。
- 按完整局部设计单元建对象，内部允许透明空隙；只有实际替换、编辑或对外层级需求才拆分。不逐笔拆，也不整页合并。
- 照片 bbox 表示内口，固定相纸或相框另列 overlay 并关联照片；承托底板在照片后，覆盖相框在照片前。
- 仅提供底层质感的弱纹理可并入背景；必要遮挡、关键载体和文字不能因此省略。
- 仅当规则几何能保留原有外观时用 `local`；带手绘笔触、特殊轮廓、纹样或固定艺术字的装饰用 `extract`，不能因有同名 shape 就简化重画。选择依据见分析文档，参数查 [本地绘制参数](references/drawing.md)。
- 文字只制作一次；提取载体内的文字要确认实际归属，不能仅凭框相交取消文字层。

两个 JSON 写好后，**必须先执行离线校验并看叠框，再进入 build 或提取**：

```powershell
& $py "$skill/scripts/workflow.py" validate --run $run
```

按分析文档的“提取前叠框纠偏”集中查看 `previews/analysis-boxes.png`，修正明显偏移、漏框和范围不完整的问题；有修改则重新 validate 并查看更新后的叠框。程序校验通过只代表数据合法，不能替代看图。

**完成条件：** 两个 JSON 校验通过，每个照片槽都有真实绑定，所有对象在 `layer_order` 中恰好出现一次；已按原图像素查看当前叠框并完成定位纠偏。

**只要求解析**时交付 JSON 和最新叠框后停止，不提取、不抠图、不出成品；正常制作进入第 3 步。

## 3. 构建首版

包含需要远端提取的复杂装饰时：

```powershell
& $py "$skill/scripts/workflow.py" build --run $run --reveal
```

默认 grouped 模式由程序安排空间裁图：每个任务最多 3 次提取提交，每组最多 20 框（含照片辅助框）。分组只合并请求区域，不合并对象 ID；不要为了少发请求而合并分析对象。返回素材经过程序门禁后合成，原始缓存保留。

按任务条件选择替代命令，三者不必依次执行：

```powershell
# 已有匹配缓存：离线回填
& $py "$skill/scripts/workflow.py" build --run $run --reveal-cache 'D:/实际路径/提取缓存'

# 纯本地制作：禁用 Reveal
& $py "$skill/scripts/workflow.py" build --run $run --no-reveal
```

旧缓存兼容、提取计划、坐标还原或超时续查时，再读 [提取与回填](references/reveal.md)。超时继续同一任务查状态，不重复提交未知结果的请求，不另建任务绕过提交上限。

**完成条件：** 得到 `final.png`、冻结的 `previews/first.png`、`previews/comparison.png` 和 `result.json`。读取当前 `render_id`、`incomplete_objects`、`asset_gate_summary` 及 `extraction_sheets`；成功导出不等于视觉通过。

## 4. 集中检查，必要时筛选一次

查看完整对照图，以及 `result.extraction_sheets` 列出的非空素材检查表。重点找缺失、旧照片残留、多余内容、明显模糊、不完整图案和重复文字。程序门禁通过不代表内容完整。

没有需要处理的候选就跳过本步。否则读 [筛选与补画](references/asset-gate.md)，用当前 `result.render_id` 写 `screen-plan.json`，一次列出要处理的对象：`keep` 保留、`drop` 隔离、`draw` 用明确支持的简单形状重画。

```powershell
& $py "$skill/scripts/workflow.py" screen --run $run --file "$run/screen-plan.json"
```

screen 离线重新合成，不重新提取；`previews/first.png` 保持不变。简单相纸、规则边框和几何装饰可补画，复杂缺失保留问题，不用简单图形冒充完整还原。默认首版不擦像素、不启动 OCR/LaMa，也不反复请求修补。

**完成条件：** 所有本轮筛选决定已执行；若成图更新，重新读取 `result.json`，后续复核使用新的 `render_id`。

## 5. 复核当前成图，登记并交付

按 [整图复核](references/review.md) 查看当前完整对照图及 `result.review_regions` 的局部对照，检查相片前后关系、装饰与脸部遮挡、人物出框。发现本轮可修的排版问题就执行一次 apply，再看修改处是否生效；细节允许近似不代替排版纠偏。

发现可用现有素材解决的问题时，写 `adjustment.json`，集中修改图层顺序、普通照片位置/裁切或本地相纸布局，然后执行一次：

```powershell
& $py "$skill/scripts/workflow.py" apply --run $run --file "$run/adjustment.json"
```

此步骤离线复用素材并重新合成，不调用 360 或生图。遮挡纠偏通过移动现有装饰、人物或调整层级完成，不重画装饰来适配人物。独立提取装饰可整体平移；本地相纸几何调整时关联照片自动跟随，提取相框等限制见复核说明。无问题就跳过；调整后查看新对照图，用新的 `render_id` 登记最终复核，不再运行 build 覆盖调整。

写 `review.json`，只列问题或需要说明的对象；使用当前 `render_id`。只有确实看过整图才能填写 `checked_entire_composition: true`。关键缺口、旧照片残留、重复内容或未解决项不能判 `pass`。

```powershell
& $py "$skill/scripts/workflow.py" review --run $run --file "$run/review.json"
```

`apply` 执行调整，`review` 只登记结论。默认集中调整一轮，剩余复杂素材问题如实交付，不为得到 pass 反复生图或修补。

**交付内容：** `final.png` 与对照图的路径、复核结论、近似或未解决项；如执行过筛选或排版调整，说明与 `previews/first.png` 的变化。有计时数据时区分远端提取、本地处理和模型阶段，不能用缓存成图速度代表首次出图速度。

## 用户明确要求精修时

已有成图的手动拖动与文字编辑使用工作台入口，见 [手动画布编辑](references/manual-editor.md)；此入口不属于默认分析成图步骤。

再读 [局部恢复](references/recovery.md)，按实际问题选择离线 `recover` 或必要的非照片 `generate`。这些操作不是默认排版修正；每次新成图都重新复核，失败保留原因和已成功资源。

客户照片必须来自 catalog，不能生成替代人物或把参考整图回填。`analysis` 描述设计，`bindings` 记录客户素材；`scene`、`result` 及来源校验记录由工具生成。
