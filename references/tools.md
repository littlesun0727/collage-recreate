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

## 制作草案与复核预览

scripts/review_analysis.py 处理第一步 draft.json，字段见 [草案规范](analysis.md)。使用同一 Python 环境和随附 DejaVu；不创建 project.json，不调用生成服务。

~~~powershell
python scripts/review_analysis.py check --reference reference.png --input analysis/draft.json
python scripts/review_analysis.py preview --reference reference.png --input analysis/draft.json --output analysis/review-01
~~~

- 无分析 init；Agent 直接写六字段草案。check 校验字段、原图像素矩形、唯一 ID、背景类型与槽、文字、装饰归属和层级完整性，只读输入。
- preview 包含相同校验，在新目录生成 index.html、numbered.png、validation.json。HTML 内含原图和草案，可点选、筛选、查看照片归属和展开层级；PNG 编号先 slots、texts，再 overlays。
- validation.json 自动记录真实参考的摘要/尺寸、草案摘要、编号映射及 expanded_layer_order。模型不填写来源或视图证据列表；查看/裁切操作保留在工具会话中。
- 工具检查的是显式 --reference 与当前草案，不会凭空证明模型看过这张图。visual_status 始终为 unreviewed，须实际看预览和原图。
- 原图、草案和已有预览只读；修改草案后选择新的 preview 输出目录。成功为 ok=true，失败 ok=false/error 和退出码 1，参数语法错误退出码 2。

## 批量细化粗定位

scripts/refine_layout.py 使用 requirements-localization.txt；基础 requirements.txt 仍可用于只渲染/交付，不带定位依赖。无 pip 的已有虚拟环境可使用 uv pip install --python .venv/Scripts/python.exe -r requirements-localization.txt。

~~~powershell
python scripts/refine_layout.py --reference reference.png --input analysis/draft.json --output analysis/refine-01
python scripts/refine_layout.py --reference reference.png --input analysis/draft.json --output analysis/refine-02 --reuse analysis/refine-01
~~~

输出新目录中的 draft.refined.json、localization.json、comparison.png、index.html 与 review/；masks/ 为局部搜索区调试掩码，坐标映射见 search_rect，不能作为生产素材。成功返回 counts、needs_review ID、cache_hits、耗时及路径。ok=true 只表示完成定位流程；未解决项保留粗框并进入新草案 questions。算法采用的候选仍需视觉复核。输入与已有输出不覆盖；没有模型调用、OCR 或素材生成。详细能力与失败规则见 [草案规范](analysis.md)。

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
- scripts/refine_layout.py、collage_recreate/localize.py：批量局部定位、缓存与前后对照。
- scripts/inspect_reference.py：分析前的尺寸/摘要读取和原图坐标局部裁切；无远端调用。

任务中的 project.json 是布局事实来源；.state 是私有恢复记录，private 保存原始输入，assets 是归一化/生成的不可变素材，outputs 是按图像内容命名的 PNG。不要直接覆盖哈希素材；用 apply 导入替换。
