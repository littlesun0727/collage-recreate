# 工具与安装

Python >=3.11。已验证环境为独立 Python 3.14.6 + requirements.txt 中的固定版本；其他环境需安装后验证。渲染依赖 Pillow、pydantic、httpx、filelock、fonttools；草案定位另用 NumPy 与 OpenCV headless，不需要视频工具或旧项目。

在 skill 目录安装自己的环境：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-localization.txt
```

以下命令均以 skill 目录为当前目录；从别处执行时，将 scripts/run.py 改为该 skill 下的绝对路径，并使用上述环境的解释器。Windows 下含空格的路径加引号；有引号的解释器路径前加 PowerShell 调用运算符 &。

## 命令

| 操作 | 命令参数 | 用途 |
| --- | --- | --- |
| init | --input request.json --task TASK | 归一化并导入参考/图片/字体，建立空工程。 |
| inspect | --task TASK | 返回工程路径、revision、资源/元素 ID、准确文本及请求状态摘要。 |
| apply | --input patch.json --task TASK | 检查 revision、合并资源/文本或更新布局；非空布局检查通过后原子保存。 |
| check | --task TASK | 检查资源、字形、文字边界、透明遮罩及画布边界，不写 PNG。 |
| render | --task TASK | 输出正式画布尺寸 PNG，不调用生成服务。 |
| generate | --input generation.json --config CONFIG --task TASK | 固定模型生成一个素材，复用同请求缓存。 |
| resume | --request REQUEST_KEY --config CONFIG --task TASK | 从已有响应恢复下载/解码，绝不提交新生成。 |
| export | --output NEW_DIRECTORY --task TASK | 导出成图、工程、所需素材、字体说明和独立 runtime。目标需尚不存在，且在源任务之外。 |
| demo | --task NEW_DIRECTORY | 创建并渲染虚构几何示例，用于安装/离线验证，不代表参考复刻质量。 |

统一入口：`python scripts/run.py OPERATION ...`。成功返回 JSON ok=true、产物路径/状态、elapsed_seconds；失败 ok=false、error.code/message，退出码 1。--help 输出普通帮助。

check/render/apply/export 支持重复 `--font ID=PATH`，仅用于显式提供字体；不会自动搜索或回退。生成配置必须显式给 --config，不读旧仓库或全局配置。

## 参考查看辅助工具

制作第一步可在 init 之前使用本地 scripts/inspect_reference.py。它只读原图，不请求模型；视觉判断需要主控随后用图片工具实际打开图像。工程 inspect 只读 project.json 摘要，不承担图片分析。

~~~powershell
python scripts/inspect_reference.py --input reference.png
python scripts/inspect_reference.py --input reference.png --crop LEFT TOP RIGHT BOTTOM --output analysis/crops/detail.png
~~~

第一条返回归正后的 source_size、source_sha256 和 alpha_range；第二条额外返回 crop_pixels、crop_normalized、output、output_size 与 output_sha256。将占位符换成当前原图内的整数像素边界，右/下边界不包含；不会拉伸、旋转或放大裁切区域。输出必须是新 PNG，拒绝覆盖原图或已有文件。它不生成工程素材绑定，也不替主控选择区域。

成功返回 JSON ok=true；失败返回 ok=false/error，退出码 1；命令行语法错误退出码 2。查看记录和原图区域由工具会话保留。首次没有清晰局部需求时，读取元数据并看完整图即可。

## 草案定位与结束

使用六字段 draft.json，正常路径仅一次 refine、一次看图、一次 finish。每张参考在同一 --task 内最多两次检查，结构报错也计数；普通 needs_review 不重试。详细判断见 [草案规范](analysis.md)。

~~~powershell
python scripts/refine_layout.py --task analysis --reference reference.png --input analysis/draft.json --output analysis/refine-01
python scripts/review_analysis.py finish --task analysis --reference reference.png --decision usable_with_questions --note "实际看图结论与具体待确认项"
~~~

refine 包含结构校验、定位和预览，不另跑 check/preview。结果含 draft、comparison、preview、needs_review、workflow；直接看一次 comparison 后 finish。完整诊断留在 localization.json，调试 mask 不是生产素材。HTML 可点选、按图片/文字/装饰筛选并查看归属；原图、输入和旧产物不覆盖。工具先移除输入 layer_order 中的附属装饰引用，再按 attachment 的 below → 照片 → above 重建，同侧按 overlays 数组顺序；有效草案、预览和 expanded_layer_order 使用同一完整顺序。附属项多列或重复不会报错，图片槽、文字、独立装饰仍必须齐全且仅出现一次。

只有明确的结构错误、主要对象遗漏/归错类、框选错对象、违背客户要求，才能另存修正草案并用第二次机会：

~~~powershell
python scripts/refine_layout.py --task analysis --reference reference.png --input analysis/corrected.json --output analysis/refine-02 --issue structural --reason "具体错误对象与修正"
~~~

issue：structural / missing_element / wrong_assignment / wrong_object / customer_requirement。工具自动使用同一参考上次定位的缓存，--reuse 仅用于已有兼容缓存。人工修正无需再定位时，可用 review_analysis.py preview --task ... --reference ... --input ... --output ... --issue ... --reason ... 替代第二次 refine。独立 check 也共用该两次预算，不是额外机会。正常客户流程不调用它。

finish 的 decision 为 usable / usable_with_questions / not_ready。它保存主控实际复核说明并关闭该参考任务，不重新定位；有待复核项不会被声明为全通过。相同输入直接复用旧结果，finish 后不同输入被拒绝。终态、尝试次数及输入摘要保存在 TASK/.state/analysis/<参考摘要>.json。同图副本、更换输入/输出路径不重置预算。不要删状态或改 task 绕过限制。

宿主可设置 COLLAGE_ANALYSIS_TASK_ROOT 为固定任务绝对路径，CLI 会拒绝不同的 --task；隔离实测使用此机制。看图次数由 skill 约束并以工具会话核对，本地脚本只能计数它实际执行的检查/预览生成，不能拦截宿主直接调用 view_image。

定位另装 requirements-localization.txt（当前 NumPy 版本要求 Python >=3.12），基础渲染/导出不依赖 OpenCV。环境缺依赖时返回错误并结束，不让生产主控现场安装或读源码调试。无 pip 的开发虚拟环境可使用 uv pip install --python .venv/Scripts/python.exe -r requirements-localization.txt。

## 离线可执行示例

```powershell
.venv\Scripts\python.exe scripts/run.py demo --task "../collage-demo"
.venv\Scripts\python.exe scripts/run.py inspect --task "../collage-demo"
.venv\Scripts\python.exe scripts/run.py check --task "../collage-demo"
.venv\Scripts\python.exe scripts/run.py export --task "../collage-demo" --output "../collage-export"
.venv\Scripts\python.exe "../collage-export/runtime/run.py" render --task "../collage-export"
```

示例的图像由 demo.py 本地构造；DejaVu 字体及许可随 skill 携带。示例工程可依据 project.md 用 apply 改字、换图、移组，再次 render。

## 文件职责

- scripts/run.py、collage_recreate/cli.py：统一命令入口与紧凑结果。
- core.py、models.py：静态契约、路径/资源、原子更新和任务锁。
- render.py：裁切、文字、遮罩、旋转和透明合成；技术检查与实际渲染共用逻辑。
- service.py：唯一 DashScope 生图接口、调用预算、缓存和请求恢复。
- export.py：筛选工程使用的资源并打包独立运行代码。
- demo.py：可执行离线示例。
- scripts/review_analysis.py、collage_recreate/analysis.py：六字段草案校验、附属层展开和编号/交互预览。
- collage_recreate/analysis_session.py：共享检查预算、未变输入复用与结束状态。
- scripts/refine_layout.py、collage_recreate/localize.py：批量局部定位、缓存与前后对照。
- scripts/inspect_reference.py：分析前的尺寸/摘要读取和原图坐标局部裁切；无远端调用。

任务中的 project.json 是布局事实来源；.state 是私有恢复记录，private 保存原始输入，assets 是归一化/生成的不可变素材，outputs 是按图像内容命名的 PNG。不要直接覆盖哈希素材；用 apply 导入替换。
