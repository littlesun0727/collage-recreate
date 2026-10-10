# 拼贴制作与工作台

同一仓库包含 `skill/` 拼贴制作能力和 `workbench/` 本地观察页面。冻结基线标签为 `collage-recreate-v5-frozen-20261008`。

本机上传后自动成图：在仓库根目录运行 `./start-local.ps1`，打开 **http://127.0.0.1:8795**，点击“上传图片 / Live 素材”，选择一张参考图及客户素材，然后点击“上传并开始制作”。后台使用现有 Codex 登录、`gpt-5.6-sol / medium`，执行完整 `skill/SKILL.md`；复杂装饰由既有 360 Reveal 接口提取，再本地合成和看图复核。SDK 与 360 会产生实际调用。

启动脚本使用本机已有 Python、SDK、CLI 和抠图模型，隐藏运行服务；上传、队列和任务保存在 `D:/codes/collage_outputs/local-workbench`。重复执行脚本会复用已运行的服务。可用 `./start-local.ps1 -Port 8796` 指定端口，但同一数据目录只能运行一个服务。服务 PID 与日志保存在该数据目录的 `server.pid`、`server-out.log`、`server-error.log`。

自动首版由 `--enable-create` 开启（同时启用上传入口），可与 `--enable-chat --enable-editor --enable-motion` 同用。上传后的素材预处理使用独立串行队列，避免被出图或视频渲染阻塞；页面分别显示排队、视频读取帧数和联系表生成状态。素材准备完成后自动进入持久化制作队列，首版与动态渲染仍串行执行；浏览器关闭不影响制作，刷新后在任务列表继续查看。“首版完成”与“复核通过”分别显示，`needs_changes` 会保留待改问题。首版执行期间禁止对同一任务进行对话修改、画布编辑或动态成片。Live 上传自动制作静态封面，动态视频仍由“制作 / 更新动态成片”按钮发起。

失败时点击“继续未完成的制作”，复用原任务、Codex thread 和提取缓存。重启时已排队任务继续，仍在执行的子进程先等待退出并核验产物；中断且未完成交付的任务显示失败，需明确点击继续，不盲目重提 360 请求。若成图已被后续编辑改变，不能再续跑首版。每轮最长 45 分钟，诊断记录在 `service/creation/<任务编号>/`。运行前会检查 SDK、CLI 登录、Python 依赖和 360 密钥配置；凭据不会传给浏览器。

2026-10-09 验证：完整回归 **225 passed / 94.19 秒**。通过真实 Edge 上传一张参考图和五张客户风景照片，实际 `gpt-5.6-sol / medium` 会话自动完成两组 360 提取、一次 screen、一次 apply、复核和交付，总耗时 **494.2 秒**，保留三个成图版本。服务重启后仍为 completed、执行次数仍为 1；PNG 下载哈希与当前成图一致，页面刷新、移动布局和控制台检查通过。证据：`D:/codes/collage_outputs/create-validation-20261009/audit.json` 及其 `browser/` 子目录。Agent 自评 pass；人工查看仍可见规则虚线近似和右上框局部折角覆盖左下照片，自动流程通过不代表逐像素还原或人工视觉验收通过。

制作入口是 `skill/SKILL.md`。已迁移的脚本位置是 `skill/scripts/workflow.py`；旧的仓库根目录 scripts 路径不再使用。安装或引用此 skill 时指向完整的 `skill` 子目录。

在仓库根目录启动工作台：

```powershell
$py = 'D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe'
& $py -B -m workbench --runs D:/codes/collage_outputs --port 8790
```

打开 http://127.0.0.1:8790 。`--runs` 可以接多个任务根目录，也可以直接接单个任务目录。默认只读；加 `--enable-chat` 启用交付后的对话修改。

加 `--enable-editor` 启用“编辑画布”：照片、贴纸可直接拖动、旋转、等比缩放或删除，明确关联的相框和照片一起变换。上方圆点旋转、右下圆点缩放，侧栏可输入角度和比例；Delete 删除当前素材，也可单独选择删除照片及关联素材。独立文字支持双击编辑；提供撤销/重做、本机草稿恢复、预览和保存新版本。该功能不需要SDK或模型，独立于 `--enable-chat`，也可同时启用。需要完整skill Python渲染环境。

拖动、旋转、缩放、删除和图层顺序立即预览；侧栏提供“上移一层、下移一层、置顶、置底”，照片与关联相框整组调整且保留内部顺序，仅改变层级也可保存。文字输入后点击“预览文字 / 成图”查看现有字体渲染器的实际排版。旧版本保留，手动版本明确显示未重新复核。嵌入同一提取或生成素材的内容整组变换和删除，不能独立改字；滚轮缩放视图不会改变素材大小。SVG用于图层显示，尚无自包含SVG导出。自然语言修改也支持删除独立图层，以及将背景替换为选定客户素材。实施与兼容性约定见 [画布编辑计划](workbench/CANVAS_EDITOR_PLAN.md)。

启用本机对话修改（需已登录的 Codex CLI、Node.js 和安装好的 Codex SDK）：

```powershell
& $py -B -m workbench --runs D:/codes/collage_outputs/workbench-validation D:/codes/collage_outputs/sdk-serial-20261008-r1/tasks --port 8790 --enable-chat --chat-state D:/codes/collage_outputs/chat-validation-20261009/queue.sqlite --sdk-path D:/codes/collage_batch/node_modules/@openai/codex-sdk/dist/index.js --codex-path C:/Users/admin/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe
```

其他机器可在 `workbench` 下运行 `npm install` 安装固定版本SDK，省略 `--sdk-path` 使用该安装；`--codex-path` 可指定本机CLI。对话模式需使用完整的 skill Python 环境；模型固定为 `gpt-5.6-sol / medium`。API调用使用本机已有Codex登录，不向页面暴露凭据。

在右侧“继续修改”中直接描述要求，也可点击成图对象或下拉选择对象，再从客户素材中选替换照片。明确要求直接执行，含糊要求会追问。每轮显示四阶段、排队/执行与分段耗时；支持刷新恢复、失败重试及修改前后查看。请求全局串行，队列保存在 `--chat-state`；重启应继续使用同一个队列文件。服务重启中断的请求显示失败，可复用工作副本重试，不自动重复执行未知结果。

普通修改只复用现有素材。修改先生成候选图，真实看图复核后发布新版本；旧版本仍可查看。历史版本当前只读，排队期间基础版本变化会提示重新确认最新成图。暂不提供任意生成、历史分支或撤销；本地服务仍仅绑定127.0.0.1，远程客户登录与托管尚未提供。

查看本次三组真实样片时，将 `--runs` 改为 `D:/codes/collage_outputs/workbench-validation`。

工作台服务只需要 Python 和 Pillow；制作环境依赖见 `skill/requirements.txt`。页面使用原生 HTML、CSS、JavaScript，不需要 npm 构建或联网加载资源。

客户素材含 Live 视频时，安装 `skill/requirements-motion.txt`，在启动参数加 `--enable-motion --cutout-model D:/codes/visual-recreate-validation/models/birefnet-lite-fp32.onnx`。左侧可上传参考图、图片和 MP4/MOV；加 `--enable-create` 后自动启动首版 Agent，仅开启 `--enable-motion` 时仍提供交给 Codex 的接续说明。

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
