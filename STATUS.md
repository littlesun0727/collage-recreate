# 当前状态与新会话续接

更新日期：2026-09-17（Asia/Shanghai）。

最新续接：四项修改与本次两图 Flash 实测已完成；用户认为识别效果可以，最新要求诊断“校验流程为什么这么长”。已只读核对同一次会话，原因与待实施精简方案见 PLAN 第 15 节及工作目录 LATENCY.md / STOP-RULES.md。不要自动重跑模型或开始新一轮流程改造。

## 一句话状态

当前采用六字段草案 background / slots / texts / overlays / layer_order / questions。摄影背景默认客户图片槽、设计纹理底板走固定背景；按制作组合合并装饰；草案仅 source_rect；refine_layout.py 批量细化并保留不确定项。116 项离线回归通过（16.13 秒），两份复核版浏览器检查通过。真实 Flash 两图一次会话 455.485 秒；背景与分类生效，局部范围仍需复核。模型正常结束后 OpenClaw 清理失败 exit 1；不重跑。

## 当前授权与边界

- 用户已授权“按照这个方案更新并测试效果”，并明确回复“批准本次两张参考图的 Flash 实测”；此前自动审批因外发图片授权不足拦截，已在该明确授权后执行一次隔离测试。
- 本次两图为原 reference-004-full.png 与本地合成格纹参考；仅分析/定位，无生图、无其他客户素材上传、无全局配置修改；本次会话已用完，不自动追加。
- 保留当前和历史未提交改动；旧仓库、原图、旧结果及全局配置只读，最终 86 项保护摘要与 25 项冻结 skill 摘要一致。
- 原始模型输出与 Codex 独立复核稿分开保存。实测后的源码修复仅经过离线与本地样例验证，没有伪称第二次 Flash 通过。
- 最新关于耗时的请求只作诊断和建议；没有因此启动另一次实现或实测。

## 本轮结果与证据入口

工作目录：D:/codes/collage-recreate-work/20260917-refine-draft。

- RESULTS.md：更新与测试结果；LATENCY.md：校验耗时诊断；review.md：独立视觉评审。
- run-01/analysis/a、b：Flash 原始草案和三轮定位；snapshot-01.json 与冻结 skill 保留。
- reviewed/a/review/index.html、reviewed/b/review/index.html：明确标记为独立复核修正的草案；对应 refine/ 为最终代码的本地定位结果。
- metrics.json/evidence-01.json：真实模型、工具、图片与保护证据；offline-tests-final-guards.xml：116 项最终回归。
- 原始 A：5 图片、2 文字、9 装饰；B：2 图片、2 文字、2 装饰。背景分流与字段分离通过观察；合并星群、完整范围及局部细节仍需修正，复核版已给出候选。
- 最终算法处理复核稿：A 5 refined / 10 needs_review / 1 unchanged；B 3 refined / 3 needs_review。算法采用不等于视觉精确。未生成素材、未组装工程、未做完整编辑性验收。

## 为什么校验长

两份初稿在 105.287 秒写出；之后 107.675 秒主要排查图层结构错误并完成首次定位；再花 242.523 秒复核、编辑、重复定位及收尾。共 59 次模型响应、63 次工具调用、10 次报错；两图各三轮成功定位，B 第二轮输入与输出均完全不变。定位执行本身约 1.1–2.4 秒/单张/轮，主要问题是模型往返和没有停止边界。

当前已经改善图层错误信息并兼容 inspect --reference，但尚未实测省时收益。建议正常路径为“看图→草案→一次 refine→一次看图→交付疑点”，只允许一次有收益的定向修正；无新增输入不重复定位，正常制作不查源码，开发测试不纳入客户任务。此精简策略为待实施建议。

## 阶段状态

- [x] 本轮 R1–R4：规则/schema/预览、批量定位脚本、离线验证、获准的两图真实 Flash 及独立复核。
- [x] 最新耗时诊断：只读同一会话并记录原因和方案。
- [ ] 精简校验运行策略：尚未实施，见 PLAN 第 15 节。
- [ ] 任意复杂图像的全自动精确定位：没有达到，也不作为已完成结论。
- [ ] 后续真实素材/字体/客户内容准备、project.json 组装和完整编辑性验收。
- 历史 P0–P2 已完成；P3 接口有证据但视觉与 runner 清理未通过；P4–P6 尚未完成。以下保留历史记录，不以旧“五字段”说明覆盖当前六字段规范。

## P1 证据与决策

- 已实际查看参考 960 × 1280、旧成图和六张客户风景图；原请求只明确 title=Moon，其余文字需核对参考。
- 旧仓库 HEAD dc5a04e，9 个已修改文件、3 个未跟踪文件；81 个文件的只读基线位于工作目录 old-repository-baseline.json。
- 五个旧模块有视频/旧 Task/Scene 耦合，未发现源码许可；新代码独立重写，完整审计和摘要见 PLAN.md 的 P1 落实记录。
- DejaVu 示例字体连同其许可复制；无客户图片进入代码仓库。
- v1 统一画布坐标，平面组 dx/dy 位移；独立图片/文字、裁切、遮罩、旋转、alpha；不含时间轴或自动抠图。

## P2 实际交付与验证

- SKILL.md：静态工作流、看图节点、停止条件及按需引用。
- references/project.md、tools.md、service.md：实际格式、命令、DashScope 配置与恢复。
- scripts/run.py 和 scripts/collage_recreate/：init/apply/inspect/check/render/generate/resume/export/demo。
- requirements.txt、pyproject.toml、config.example.json：独立环境与无密钥配置例。
- tests/：49 项离线测试；assets/fonts/：有许可的示例字体。

验证：
- 独立 .venv，Python 3.14.6；Pillow 12.3.0、pydantic 2.13.4、httpx 0.28.1、filelock 3.29.4、fonttools 4.63.0、pytest 9.0.3。
- 已运行命令：.venv\Scripts\python.exe -m pytest -q --basetemp D:\codes\collage-recreate-work\20260917-implementation\pytest-final-01 --junitxml=D:\codes\collage-recreate-work\20260917-implementation\offline-tests.xml --tb=short -x；结果 **49 passed in 11.46s**。
- skill-creator 的 quick_validate.py：Skill is valid!；全部运行脚本编译通过。
- 实际执行 demo、init、apply、inspect、check、export；render 经原脚本和导出 runtime 执行。generate/resume 的离线测试使用本地伪服务。
- 覆盖横竖图裁切、透明/旋转隐藏色、遮罩、分组、换图/改字、中文/长文字/缺字、路径、资源损坏、原子性、并发锁、失败状态、预算、防重复付费、下载恢复及跨目录独立导出。
- 已实际查看离线示例。原版与独立包的 PNG SHA-256 均为 18f1c3c0004b08e8f116bd0eac6f8d041d00b63f5fc4307a93e1964001cc3c31。
- 生成提交前拒绝覆盖参考图或字体；生成绑定前重新验证工程，有对应回归用例。
- 本次 P3 后未修改运行时代码，因此没有重复运行这 49 项测试；这是一份已完成的 P2 验证记录。
- Windows 沙箱辅助程序权限错误时采用获准提权命令；pytest 使用明确的新工作目录，没有修改系统 ACL。

## P3 本次实测结果

### 实际执行与模型证据

- OpenClaw 2026.9.4；独立 agent exec，code-mode=direct；没有安装到全局，也没有修改全局配置。
- 启动时间：2026-09-17 15:11:57（Asia/Shanghai）；整个 runner 墙钟耗时 **174.778 秒**，包含思考、工具与清理；这不是生图服务耗时或整个开发耗时。
- session_id：8e044c2e-141c-49f8-af99-554b046072d4。
- 保留的 SQLite transcript 共 42 个事件，12 次 assistant 响应、14 次工具调用；12 次响应的 provider 均为 bailian-token-plan，model 和 responseModel 均为 qwen3.8-flash。
- Flash 读取了冻结的新 SKILL.md、工具/服务文档；通过独立 .venv 执行新 skill 的 inspect/generate，不调用旧 runtime。
- 两次 view_image 均返回真实 image 内容：参考为 OpenClaw 压缩后的 900 × 1200 JPEG；候选为完整 1024 × 1024 PNG，接收图片的 SHA-256 与落盘素材完全一致。不是仅凭自报“看过”认定通过。
- 原始参考为 960 × 1280；发送给生图服务的规范化参考 SHA-256 为 4728288c0b03bd0cf65c857d90e5817b8d342f1d48d7b8ced083cc97a85e37a1。
- 可观察的额外开销：Flash 重复执行三次 inspect；第三次随后读取尚不存在的 requests.json 导致该 shell 命令 exit 1。没有产生额外生图，不能把这些检查当有效生成尝试。

### 图像服务与产物

- 精确使用已批准的 generation.json；requested_model=qwen-image-3.0-pro。
- **reported_model=null**：服务响应未单独报告图像模型，不能把请求字段当作服务报告证据。
- 本地代理 http://127.0.0.1:17861 转发既有 DashScope 协议。
- 2026-09-17 15:14:00 提交；HTTP 200，status=completed，attempt=1，cache_hit=false。
- 请求标识：83d019bd-c8be-484f-93f8-56c24046be2f。
- request_key：18cde3f6f59556983d7a6fc41b6c4230abe996ccb4041eb0fd69739220380a39。
- 生图服务耗时 **10.78 秒**；generate CLI 总耗时 **11.544 秒**。
- 实际计数：image_requests=1，moon_sample=1；max_requests=1，max_requests_per_asset=1，权限保持原样。
- 候选：openclaw-p3/task/assets/345fd0aac8d89551eacbc49487c4b78fc84c0e86348ae8ce5065c62980e3bf46.png。
- 尺寸 1024 × 1024；实际模式 RGBA，alpha=[255,255]，完全不透明。Flash 原始 review 写“RGB”不准确，透明性判断正确；原始评审保留不改。
- project revision=2，新增 moon_sample 资产；没有整版构图或可编辑成品验收。
- 没有依据确认具体费用；响应/主控日志中的计费元数据不能代替账单。

### 视觉判断与运行异常

Flash 已写入 openclaw-p3/review.json，visual_status=needs_revision。当前助手也实际对照了参考与候选：

- 七个月相相互分离、没有多余照片/文字，素材范围符合提示。
- 候选为明显干涩的彩铅/蜡笔排线，有较硬轮廓；参考更饱满、柔和，接近厚涂晕染并有光晕。
- 候选颜色较浅、月牙更瘦，关键装饰风格尚不成立。保留候选，不进入 P4。
- 白底不是透明图；将来选定素材后仍需实际验证去底/遮罩和边缘，当前没有完成抠图。

模型最后 stopReason=stop，评审及最终文字已写入隔离会话；随后 OpenClaw agent exec 返回 exit 1：
“Agent exec cleanup failed: Agent runtime cleanup did not settle; state ownership retained until this process exits”。

已只读检查安装代码：清理状态 uncertain 时会把成功 envelope 替换为错误 envelope，因此 stdout 的 model=null/final 为空不能推翻 SQLite 中的实际模型与工具记录。**具体哪个清理步骤失败尚未查明**；保存的 stderr 和 diagnostic_events 没有定位到步骤，不声称已经修复。没有改 OpenClaw 安装或配置，没有为排查重跑付费调用。

### 实测后的保护检查

p3-preservation-check.json 已验证：
- 旧仓库 81 个基线文件无变化，无新增或移除的跟踪/未忽略文件。
- 旧图像配置和 OpenClaw 全局配置内容摘要均未改变。
- 冻结 skill 的 18 个文件及新仓库对应运行文件均未改变。
- 旧静态参考与实测 workspace 副本一致。
- 旧 P3 收尾时新仓库尚无提交；用户随后已保存 87c6fd7。本次 A1 不新增提交或远端，没有运行旧项目或修改旧样本。

## 当前下一步：针对 JSON 观察质量的定向改进

1. 先读 D:/codes/collage-recreate-work/20260917-analysis-json/RESULTS.md 和 review.md；在浏览器打开 run-01/analysis/review-03/index.html，原始结果为 run-01/analysis/analysis.json。
2. 本轮确定的缺口：月相方向写反；框未覆盖描述中的完整对象；不同方向/归属合为大组，关系不能定位具体起点；certainty 与 questions 不一致。不要将字段校验通过或模型 self_checked 当作独立通过。
3. 本轮唯一一次主控运行已结束。不重跑 run-analysis-01.py，也不重新启动旧 A1/P3 runner。后续验证需新日志及明确范围，不向通用规则注入本例答案。
4. A2 工程拆解、素材修正、整版制作和本例编辑性未在本轮执行。改为 JSON 不代表这些能力已完成。
5. OpenClaw 清理异常仍存在。模型回复正常 stop、JSON/预览有效与 runner exit 1 分别记录；不得因此重发生图。

## 关键路径与证据

- 代码：D:\codes\collage-recreate。
- 工作目录：D:\codes\collage-recreate-work\20260917-implementation。
- 静态参考：D:\datas\0916live复刻2\diagnostics\inspection\reference-004-full.png。
- 旧成图：D:\datas\0916live复刻2\previews\r000004-009bf69f\cover.png。
- 客户素材线索：D:\datas\0916live复刻2\request-input.json。
- P2 离线目录：工作目录下“离线 示例”“离线 CLI”“独立交付”；测试报告 offline-tests.xml。
- P3 workspace：工作目录下 openclaw-p3；APPROVAL.md 已追加实际批准记录。
- P3 脱敏审计：p3-evidence.json；只读核验脚本 audit-p3.py；保护核验 p3-preservation-check.json。
- P3 runner：p3-run-summary.json、p3-openclaw-output.json、p3-openclaw-stderr.log；run-p3.py 已经执行，不再重跑。
- P3 评审：openclaw-p3/review.json；从会话恢复的最终文字 p3-recovered-final.md。
- P3 原始会话：openclaw-p3-state/agents/main/agent/openclaw-agent.sqlite（本地私有，含图片和消息）。
- task/.state/remote 下保留远端原始响应，可能含签名链接；不进入源码或交付包。
- request-preview.json 是调用前的静态预览，其中 submitted_requests=0 仅反映准备时状态；当前计数以 task/.state/requests.json 为准。

## 本次授权经过

先前自动审批因参考上传和可能付费生成缺少明确授权而拒绝启动。用户随后明确批准，本次执行获得批准并完成唯一一次生图。这一审批阻塞已经解除；当前剩余的是素材质量和 runner 清理问题，不再把“等待首次授权”列为当前状态。

## A1 最终实施与实测记录

工作目录：D:\codes\collage-recreate-work\20260917-reference-analysis。代码基线 87c6fd7，工作区为本次未提交变更。

### 文件

- 新增 references/analysis.md、scripts/inspect_reference.py、tests/test_inspect_reference.py。
- 修改 SKILL.md 第一制作步、references/tools.md 工具说明、references/project.md 职责说明、PLAN.md、STATUS.md。
- 未修改工程 schema、生图协议或其他运行模块；未写入样例专有答案。
- 第二轮只修订通用分析规范，首轮冻结快照和原报告保留。

### 验证与统计

- pytest：57 passed in 12.84s，报告 offline-tests.xml；新增 8 项用例覆盖裁切像素/源坐标、透明度、原图与已有输出保护、无效范围和中文路径 CLI。
- skill-creator 结构校验通过；元数据及裁切命令已实际执行。第二轮未改脚本，因此没有重复整套测试。
- 第一轮：174.361 秒，14 次 assistant 响应，13 次工具（read 2 / exec 4 / view_image 6 / write 1）；实际收到 1 整图 + 12 局部；工具无错误。
- 第二轮：319.132 秒，24 次 assistant 响应，23 次工具（read 3 / exec 7 / view_image 12 / write 1）；实际收到 3 次整图 + 18 局部；一次看图路径转义错误，主控自行修正后成功。
- 两轮实际 provider=bailian-token-plan，所有 responseModel=qwen3.8-flash；读取了对应冻结 SKILL.md 和 references/analysis.md。第二轮另读 tools.md。
- 30 个局部均与原图像素一致，且全部进入主控图片上下文；两轮冻结的 20 个文件均未改变。
- 两轮合计生图请求 0；旧 P3 请求计数仍为 1。
- 时间为 OpenClaw 启动到退出的墙钟时间，包含处理/等待/工具/清理，不包含本次开发和人工审阅时间。次数按实际工具调用计，包含失败及重试，一次 exec 可以生成多个局部。

### 结果与限制

- 两轮原始报告分别在 run-01/analysis/analysis.md、run-02/analysis/analysis.md。
- 首轮开口方向写反、连接关系不一致；第二轮方向改正，但多记一条连接，并有局部裁切范围和擅自决定复刻取舍的问题。
- 报告自评“可进入工程拆解”不被独立评审接受；质量仍 needs_revision。
- 两轮均正常写报告并 stop，随后发生同一 OpenClaw 运行时清理错误，最终 exit 1。未修复安装或全局配置。
- 只有一个复杂样例、两次独立运行；不能据此声称稳定提升或泛化通过。

### 证据与保护

- RESULTS.md：可阅读的结果入口；review-01.md / review-02.md：独立评审。
- metrics.json：耗时和调用统计；evidence-01.json / evidence-02.json：模型、工具、看图与裁切校验。
- run-summary-01.json / run-summary-02.json、openclaw-output-*.json、openclaw-stderr-*.log：原始运行结果。
- snapshot-01.json / snapshot-02.json：冻结版本；state-01 / state-02：私有原始会话，不公开或纳入源码。
- preservation-check.json：原图、旧仓库 81 个文件、旧 P3 文件、旧图像配置和 OpenClaw 全局配置未变；本次未新增提交。

## A1 JSON 最终实施与实测

工作目录：D:/codes/collage-recreate-work/20260917-analysis-json。计划见 PLAN.md 第 11 节；代码基线仍为用户保存的 87c6fd7，本轮未提交。

### 实际变更与验证

- 新增 scripts/collage_recreate/analysis.py、scripts/review_analysis.py、tests/test_analysis.py。
- 更新 references/analysis.md、SKILL.md、references/tools.md、references/project.md、PLAN.md、STATUS.md；既有工程 schema 和生图协议保持原样，无新依赖。
- 正式观察为 analysis.json；HTML/编号 PNG/validation.json 自动派生，不要求 Agent 再写分析 Markdown。init 写真实来源元数据，check 不改结果，preview 拒绝覆盖已有目录。
- 79 passed in 14.41s，包含 22 项新增行为测试；报告 offline-tests.xml。skill 结构校验和 git diff --check 通过。
- Edge headless 已测试最终交互页：元素点选、关系双端高亮、类型筛选、框线开关、移动屏幕无横向溢出、无 JS 错误。截图 final-preview.png。
- 独立 check 复查了最终 JSON 和保留的全部裁切；预览摘要及规范化数据与最终 JSON 一致。

### 真实运行

- OpenClaw 2026.9.4，实际 provider=bailian-token-plan；56 次 assistant 响应的 responseModel 均为 qwen3.8-flash。
- 单次运行 484.48 秒，55 次工具：read 4、exec 28、view_image 16、write 2、edit 5。时间含主控/工具/等待/退出清理，不含开发及 Codex 评审。
- 5 次工具失败：重复 init、两次关系错误、一次字段/类型错误、一次证据丢失。均由 Flash 在同一会话修正；未另起模型重跑。
- 22 个实际图片内容：整图、18 个不同局部和三版编号图。裁切成功执行 24 次（含误删后重建 6 次），最终保留 15 个局部。18 个局部均可从保留文件或会话图像核对原图像素；最终引用的 15 个均实际进入模型上下文。
- Agent 在清理中删除前两版预览及部分裁切；误删仍被引用的 6 个局部后，被 check 报错并重建。最终 review-03 与 JSON 一致；完整会话和清理前截图保留在工作目录。
- 最终 JSON：15 元素、9 关系、16 视图、3 疑点；SHA-256=c4301e012d87bf590e8e6b7e48a705e55007dd2a6db12cc0304aa1c4453ba09c。
- session_id=dd4c6dac-7133-472b-9f0f-4cc951ed94ca；模型正常 stop，但随后发生同一 cleanup did not settle 错误，runner exit 1。未改全局配置/安装。
- 本轮生图 0；旧 P3 计数仍为 1。测试 workspace 未提供旧分析或样例答案；冻结 22 个文件未变。

### 结论与证据

- 实施与实测完成；独立观察质量 needs_revision。方向、框选范围、关系粒度和确定性仍有实质问题，不接受模型将其全部归为非阻塞精度问题。
- 本轮工具次数和时间高于前两轮，不能宣称 JSON 提升了准确率或速度；仅证明结构校验、派生复核和实际主控工具链执行。
- RESULTS.md 为结果入口；review.md 为独立评审；metrics.json/evidence-01.json 为统计与会话审计；model-json-writes 保留模型最初写出的 JSON。
- 原始分析 run-01/analysis/analysis.json；最终预览 run-01/analysis/review-03/index.html 及 numbered.png。
- preservation-check.json：旧仓库 81 个文件、旧报告、原参考、全局 OpenClaw/旧图像配置未变；无新增提交或远端。
- A2 尚未实施；本轮不再新增模型调用。

## 实测后的用户反馈（讨论中，未实施结构替换）

用户指出当前 analysis 的通用关系表达过于抽象、造成返工，提议采用 figcopy draft 的业务结构：background、slots、overlays、layer_order，以及 questions。已只读核对指定 draft.json 和 draft_prompt.py；这更直接对应背景来源、客户可替换内容、重建装饰及叠放顺序。后续应先明确以该业务草案替换当前观察关系结构的范围；本轮原始 JSON、预览和评审保留，没有据此启动新修改或实测。当前规范和代码仍为本轮已验证的 analysis JSON 版本。


## 历史：简化草案实施

- 已保存修改前 32 个仓库文件摘要、9 个相关文件副本，以及上一轮产物摘要；工作目录 D:/codes/collage-recreate-work/20260917-simple-draft。
- 新草案仅保留五个业务字段；复用原有 crop、运行时资源/渲染工具，不改变后续 project.json 执行格式。
- 已改校验/预览与说明，正进行离线验证。旧 analysis.json 不是新格式；旧工作目录带自己的冻结运行时代码，不做批量迁移。

- D1/D2 完成：97 passed in 14.28s，其中草案行为测试 40 项；Edge headless 验证点选、附属装饰高亮、层级、筛选、移动宽度及无 JS 错误；quick_validate、git diff --check 通过。
- 测试环境记录：首次未指定独立临时目录的 pytest 运行返回 E，未保留完整错误正文；改用本轮独立 --basetemp 后草案/全套分别通过，最终 JUnit 留存。skill 校验需使用现有带 PyYAML 的开发 Python 并开启 -X utf8，未给运行时增加依赖。
- D3 已准备：冻结当前 skill，测试只含原图与第一步草案目标，不含样例答案、旧报告或客户已确认换背景的假设；随后执行一次真实 Flash。


### 简化草案最终结果

- 当前规范：references/analysis.md；主入口与工具/工程文档已同步。模型直接写 analysis/draft.json，check/preview 自动记录来源摘要和尺寸，不再要求 init 或模型填 evidence/relations。
- 本次变化共 9 个文件，改前版本在本轮 before/；旧 analysis.json 和旧冻结 skill 保留，不做迁移。
- 实测目录：D:/codes/collage-recreate-work/20260917-simple-draft。
- 用户优先查看：run-01/analysis/review-03/index.html；原始数据 run-01/analysis/draft.json；独立判断 review.md；汇总 RESULTS.md；指标 metrics.json。
- 最终 JSON SHA-256：f3c0012080a006fd5bf9866c44f513200fdff6fd7c6431adbb9c62057e79ba9c。三版预览保留，review-03 与最终 JSON 一致。
- OpenClaw 2026.9.4 / 实际 bailian-token-plan + qwen3.8-flash；session 19fc8f6a-642b-4ef9-be6f-cc10e327cc5a；350.945 秒、38 个 assistant 响应、39 次工具（read 3 / exec 16 / view_image 17 / write 2 / edit 1）。
- 三项工具错误：PowerShell head 不存在、读错文档相对路径、inspect_reference 漏 --input；随后在同一会话恢复。草案校验无报错；六次 check/preview 均有效。工具数不是模型响应数或图片数。
- 收到 26 次图片内容；17 个不同裁切全部逐像素匹配原图并送达，含多次完整图与最终编号图。生成调用 0。
- 业务上改进：四张照片 photo 模式、三段文字槽；背景未被无依据设成客户替换槽；11 项装饰 attachment 正确归属对应照片，跨照片装饰独立；层级可展开为 26 层。
- 独立视觉/制作评审：needs_revision。蓝色短划误写黄色，Moon/吊线/星星等范围截断，未知 logo 先猜字再默认删除；18 个 overlays 仍偏细。不接受模型结尾“框覆盖合理”作为验收结论。
- 本次实际工具命令已审阅，无生图、工程组装或冻结 skill 修改；模型 stop 后再次出现清理失败，runner exit 1。原图、旧仓库 81 文件、上一轮 879 文件、早先 Markdown 报告、全局与旧图像配置均保持原样，HEAD 仍 87c6fd7。
- 下一步：保留五字段结构，优先修正本例局部范围、外观与未知标识取舍，收敛同照片共同制作的装饰，再进入真实资源准备和工程组装。本轮不自动启动第二次 Flash、不消耗生图预算。

## 历史：四项用户反馈的设计评审（当时未实施）

- 已读当前计划、状态、规范、草案校验/预览及裁切实现，实际查看原始参考，并核对 simple-draft 的最终 draft.json 和独立 review.md。保留已有未提交修改。
- 根因：规范明确要求未指定更换背景就清版；Slot 模型混合 image/text；attachment 只归组而不合并素材；source_rect/target_rect 同时必填；现有校验仅检查数值边界，inspect_reference.py 不做视觉定位。
- 用户方向：摄影背景优先客户素材，设计纹理底板才考虑清版/重绘；图片 slots 与文字 texts 分开；按独立制作/移动/遮挡需求合并装饰；减少重复坐标并由脚本细化 VLM 粗定位。
- 建议细节见 PLAN.md 第 13 节。尚需在实施时验证局部定位方法与新增依赖；可编辑文字不能因装饰合并而变成不可改位图，粗框细化失败必须显式保留待复核状态。
- 本次只更新 PLAN.md 和 STATUS.md，没有修改运行文件、样例或旧证据，没有执行测试、模型调用或生成请求。文档静态检查单独记录，不计作新实现通过。
- 下一步为按第 13 节实施业务规则/schema/预览，再离线验证 refine_layout 原型；本次“帮我想想如何修复”不自动扩展为实施或新的 Flash 实测。
