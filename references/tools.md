# 工具与安装

Python >=3.11。已验证环境为独立 Python 3.14.6 + requirements.txt 中的固定版本；其他环境需安装后验证。代码只依赖 Pillow、pydantic、httpx、filelock、fonttools，不依赖旧项目、OpenCV 或视频工具。

在 skill 目录安装自己的环境：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
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

任务中的 project.json 是布局事实来源；.state 是私有恢复记录，private 保存原始输入，assets 是归一化/生成的不可变素材，outputs 是按图像内容命名的 PNG。不要直接覆盖哈希素材；用 apply 导入替换。
