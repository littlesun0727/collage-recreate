# 拼贴制作与工作台

同一仓库包含 `skill/` 拼贴制作能力和 `workbench/` 本地观察页面。冻结基线标签为 `collage-recreate-v5-frozen-20261008`。

制作入口是 `skill/SKILL.md`。已迁移的脚本位置是 `skill/scripts/workflow.py`；旧的仓库根目录 scripts 路径不再使用。安装或引用此 skill 时指向完整的 `skill` 子目录。

在仓库根目录启动工作台：

```powershell
$py = 'D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe'
& $py -B -m workbench --runs D:/codes/collage_outputs --port 8790
```

打开 http://127.0.0.1:8790 。`--runs` 可以接多个任务根目录，也可以直接接单个任务目录。默认只读；加 `--enable-chat` 启用交付后的对话修改。

加 `--enable-editor` 启用“编辑画布”：照片、贴纸可直接拖动，明确关联的相框和照片一起移动，独立文字支持双击编辑；提供撤销/重做、本机草稿恢复、预览和保存新版本。该功能不需要SDK或模型，独立于 `--enable-chat`，也可同时启用。需要完整skill Python渲染环境。

拖动立即预览；文字输入后点击“预览文字 / 成图”查看现有字体渲染器的实际排版。旧版本保留，手动版本明确显示未重新复核。嵌入贴纸中的文字不可独立改字。SVG用于图层显示，本版没有素材缩放/旋转控件或自包含SVG导出。实施与兼容性约定见 [画布编辑计划](workbench/CANVAS_EDITOR_PLAN.md)。

启用本机对话修改（需已登录的 Codex CLI、Node.js 和安装好的 Codex SDK）：

```powershell
& $py -B -m workbench --runs D:/codes/collage_outputs/workbench-validation D:/codes/collage_outputs/sdk-serial-20261008-r1/tasks --port 8790 --enable-chat --chat-state D:/codes/collage_outputs/chat-validation-20261009/queue.sqlite --sdk-path D:/codes/collage_batch/node_modules/@openai/codex-sdk/dist/index.js --codex-path C:/Users/admin/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe
```

其他机器可在 `workbench` 下运行 `npm install` 安装固定版本SDK，省略 `--sdk-path` 使用该安装；`--codex-path` 可指定本机CLI。对话模式需使用完整的 skill Python 环境；模型固定为 `gpt-5.6-sol / medium`。API调用使用本机已有Codex登录，不向页面暴露凭据。

在右侧“继续修改”中直接描述要求，也可点击成图对象或下拉选择对象，再从客户素材中选替换照片。明确要求直接执行，含糊要求会追问。每轮显示四阶段、排队/执行与分段耗时；支持刷新恢复、失败重试及修改前后查看。请求全局串行，队列保存在 `--chat-state`；重启应继续使用同一个队列文件。服务重启中断的请求显示失败，可复用工作副本重试，不自动重复执行未知结果。

普通修改只复用现有素材。修改先生成候选图，真实看图复核后发布新版本；旧版本仍可查看。历史版本当前只读，排队期间基础版本变化会提示重新确认最新成图。暂不提供任意生成、历史分支或撤销；本地服务仍仅绑定127.0.0.1，远程客户登录与托管尚未提供。

查看本次三组真实样片时，将 `--runs` 改为 `D:/codes/collage_outputs/workbench-validation`。

工作台服务只需要 Python 和 Pillow；制作环境依赖见 `skill/requirements.txt`。页面使用原生 HTML、CSS、JavaScript，不需要 npm 构建或联网加载资源。

客户素材含 Live 视频时，安装 `skill/requirements-motion.txt`，在启动参数加 `--enable-motion --cutout-model D:/codes/visual-recreate-validation/models/birefnet-lite-fp32.onnx`。左侧可上传参考图、图片和 MP4/MOV；准备完成后将页面提供的任务说明交给 Codex 继续原六步封面制作。现有上传功能不会自行启动一次新的 Agent 分析。

含 Live 代表帧的封面完成后，“动态成片”区域可提交独立渲染，查看原视频、各阶段进度和耗时、播放与下载 MP4。保留原生帧时间，最长三秒，短素材循环，默认静音；人物先逐帧抠图再合成。源视频最多 60 秒、单文件 128 MB。动态队列串行执行，状态和帧缓存落盘；`--media-root` 可指定上传与队列保存目录，重启使用同一目录。修改封面后旧视频保留并标明过期，重新制作可复用抠图缓存。

具体规则见 [Live 使用说明](skill/references/live-media.md) 和 [实施计划](workbench/LIVE_MEDIA_PLAN.md)。原静态 `prepare/build/render` 行为保持不变，动态流程不需要逐帧调用 SDK。

六步进度展示脚本执行与 Codex 实际看图记录。大图支持并排、滑动及修改前后对照；素材完成后即显示；每次变化的成图保存独立快照。历史任务只显示已留存的信息。录入实际业务阶段的方式见 `skill/references/workbench.md`。

任务图片、素材和运行缓存保存在仓库外。工作台为本机服务，跨机器的客户访问尚未提供。

验证命令：

```powershell
$testRoot = 'D:/codes/collage_outputs/check-' + [guid]::NewGuid().ToString('N')
& $py -B -m pytest skill/tests workbench/tests -q -p no:cacheprovider --basetemp $testRoot
```

实施范围及验收标准见 [WORKBENCH_PLAN.md](WORKBENCH_PLAN.md)，本次测试结果、样片效果和剩余问题见 [workbench/VALIDATION.md](workbench/VALIDATION.md)。

浏览器交互测试使用本机 Edge 与带 `websocket-client` 的 Python（仅测试需要，服务无需此依赖）：

```powershell
C:/Users/admin/anaconda3/python.exe -B workbench/tests/browser_check.py --out D:/codes/collage_outputs/browser-check --final
C:/Users/admin/anaconda3/python.exe -B workbench/tests/browser_live.py --out D:/codes/collage_outputs/browser-live
```

第一个命令需要已启动工作台并载入本次三组样片；第二个命令自行在 8791 端口启动合成测试任务，用来验证素材逐步出现与制作过程中查看历史的行为。

用户指定的 `gpt-5.6-sol / medium` 全流程串行测试说明见 [workbench/evaluation/README.md](workbench/evaluation/README.md)。同时查看 SDK 样片与先前功能验收任务时，启动参数为：

```powershell
& $py -B -m workbench --runs D:/codes/collage_outputs/workbench-validation D:/codes/collage_outputs/sdk-serial-20261008-r1/tasks --port 8790
```
