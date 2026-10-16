# 在另一台电脑继续编辑现有项目

本分支 `demo/offline-20261013` 默认启动原来的可编辑工作台。60 个项目、122 版成图、83 个视频版本、原始客户素材、图层和 CPU 抠图模型一起迁移。只读浏览快照同步包含这些结果。

## 项目分组

- **本次拼贴类测试：22 个。** 与 `D:\datas\做同款_摸底参考图\拼贴类照片模板` 中的 22 张参考图逐一核对，按本次制作顺序排列，列表显示参考图文件名。
- **历史测试：38 个。** 保留原有历史项目、成图、编辑能力和版本记录。

两组可以分别折叠；搜索支持参考图文件名、项目名和分组名。默认打开本次拼贴类的第一个项目。创意类任务不纳入此 demo；已有工作区中的创意类任务也不会再显示或通过旧链接访问。打开已移除任务的链接会自动选择本次测试的第一个项目。原始参考图目录未作修改。

更新后重启工作台，新增的 21 个拼贴类项目会自动补入。原有已恢复项目及本机编辑不会被覆盖；分组由 [task-catalog.json](task-catalog.json) 管理，不需要移动任务目录。完整同步与验证记录见 [collection-validation-20261016.json](collection-validation-20261016.json)。

2026-10-16 已同步任务 `654d3268a99aef37`（`Live-445b6017208a`）：2 版成图、18 段原始 Live 视频、2 个动态成片版本及完整复核记录。SDK 制作耗时 519.095 秒，静态 Agent 自评 `pass`；照片硬切、左下光晕与蓝蝶弱化的近似说明保留，动态状态仍为 `needs_review`，不代表完整播放已验收。同步验证见 [task-sync-20261016.json](task-sync-20261016.json)。

其中 `Live-445b6017208a` 在本次拼贴类组中显示为 `wufeng_7.jpg`，仍可通过 `?task=654d3268a99aef37` 访问。

## 下载 / 更新

```bash
git clone --branch demo/offline-20261013 --single-branch https://github.com/littlesun0727/collage-recreate.git
cd collage-recreate
```

已经下载过这个分支的电脑，在仓库根目录执行 `git pull --ff-only`。不要下载 `main` 分支的 ZIP；下载 ZIP 时先切到 `demo/offline-20261013`。

## Windows 10/11，x64

双击 `demo/start.vbs`。首次启动会恢复原始项目，约需数分钟；完成后自动打开浏览器。以后启动直接使用已恢复的项目。结束时双击 `demo/stop.vbs`。

Python、渲染依赖及抠图模型已经随包提供，不需要安装 GPU、CUDA、Node 或另下业务素材。若不能运行 VBS，可在仓库根目录执行：

```powershell
.\demo\runtime\python.exe .\demo\edit.py
```

## Linux，x86_64

需要 Python 3.11+、venv 和 pip。首次运行下面的脚本会在独立虚拟环境安装 Linux Python 依赖，因此首次安装需要网络；素材和模型已在仓库里，不需要重新下载。Windows 的二进制依赖不能直接用于 Linux。

```bash
bash demo/start-edit.sh
```

若系统默认 Python 较旧，但已安装 Python 3.12，可用 `COLLAGE_PYTHON=python3.12 bash demo/start-edit.sh` 指定解释器。

已经有完整 Python 渲染环境时，可以直接执行：

```bash
python3 demo/edit.py
```

服务只监听本机。没有桌面浏览器的 Linux 主机可用 `--no-browser` 启动，然后通过 SSH 转发访问，例如 `ssh -L 8795:127.0.0.1:8795 user@host`。端口 8795 被占用时自动选空闲端口，实际地址见 `demo/editor-url.txt`；SSH 转发使用该实际端口。

Linux 缺少原 Windows 字体时使用随包的 Noto Sans CJK 字体。历史图片保持原样；重新渲染的文字字形可能与 Windows 不同。

### 点击“编辑画布”出现 503

已修复跨平台 PNG 编码不同导致的误报：首次准备图层时比较解码后的像素，保留原始成图文件；同一版本再次打开使用缓存。真实画面差异仍会报错。

更新代码后需要重启后台。在仓库根目录执行下面的命令；使用自定义 `--workspace` 时，停止和启动都传入同一个目录。

```bash
git pull --ff-only
bash demo/start-edit.sh --stop
bash demo/start-edit.sh --no-browser
```

启动后刷新网页。已有项目及编辑记录仍保存在 `demo/workspace`，无需重新导入。Windows 更新后先双击 `demo/stop.vbs`，再双击 `demo/start.vbs`。

若仍失败，页面会显示画布处理的具体错误及任务目录下的相对日志路径，例如 `chat/requests/editor-export-…/editor-log.txt`。任务目录位于 `demo/workspace/tasks/`。只有“HTTP 503”且没有详细原因时，也需检查访问代理是否自行返回了 503。

## 打开后如何用

打开任一现有项目，点击原来的“编辑画布”，继续编辑、预览、保存新版本。历史版本保留。动态成片入口及原 Live 素材也一起迁入，可使用原有的 CPU 渲染流程。

这次迁移没有新做一套编辑器。原来的功能边界也保留：已合并在图片里的文字不能直接作为独立文字编辑。

“继续修改”的 AI 对话以及自动制作首版需要新电脑自己的 Node、Codex SDK、CLI 登录和服务配置。账号登录或 API 密钥不随公开仓库迁移。完成仓库根 README 的 AI 环境配置后，可通过以下参数启用原有功能：

```bash
python3 demo/edit.py --enable-chat --sdk-path /path/to/codex-sdk/dist/index.js --codex-path /path/to/codex
```

启用自动首版另加 `--enable-create`，并按原项目说明配置 Reveal 服务。默认的本地画布编辑不依赖这些账号。

## 编辑后的文件在哪里

`demo/workspace/tasks/` 是真正可写的项目目录。首次启动从 `demo/editable/` 和 `demo/data/` 恢复，后续启动及 `git pull` 不会覆盖已编辑的项目。

`workspace` 被 Git 忽略；编辑保存的是本机项目，不会自动同步到另一台电脑或推送 GitHub。要继续搬运自己的修改：关闭工作台，复制整个 `demo/workspace` 到新电脑，再在新电脑运行路径迁移：

```powershell
.\demo\runtime\python.exe .\demo\relocate_editor.py .\demo\workspace
```

Linux 使用 `python3 demo/relocate_editor.py demo/workspace`。迁移会先备份 JSON，再更新文件引用和校验哈希；完成后正常启动工作台。不要只复制最后一张 PNG。

## 保留的只读演示入口

Windows 双击 `demo/start-viewer.vbs`；Linux 执行 `python3 demo/viewer.py`。只读快照不需要第三方 Python 包，适合只浏览原始结果。关闭它可执行 `python3 demo/viewer.py --stop`，Windows 将 `python3` 换为 `demo/runtime/python.exe`。

启动异常时查看 `demo/editor-error.txt`，或在命令行运行 `demo/edit.py`。验证记录见 `demo/editable/VALIDATION.json`；原浏览包记录见 `demo/VALIDATION.json`。
