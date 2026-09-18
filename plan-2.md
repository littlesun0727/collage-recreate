# 弱 VLM 两阶段静态分析实施计划

更新日期：2026-09-18。当前状态：实现、全目录 16×2 批测、独立视觉复核和最终报告均已完成；证据不支持切换默认流程。

本计划负责本轮 reference image → inventory.json → draft.json 的改造。需求来源：[弱vlm识别更新计划.md](弱vlm识别更新计划.md)；历史计划见 [PLAN.md](PLAN.md)，实际进度与验证记录见 [STATUS.md](STATUS.md)。后续本轮的实施决策和阶段计划集中更新本文。

- 基线提交：`3148547`，在 main 保存开始时的全部 8 个变更文件，包含归一化入口、既有测试、规则/实验记录和原计划。
- 开发分支：`feat/weak-vlm-inventory-v1`，从该提交创建。
- 基线复验：158 passed in 18.41s。报告：`D:/codes/collage-recreate-work/20260918-inventory-plan/baseline-tests-02.xml`。首次因临时目录父目录不存在出现 154 个 setup errors / 4 passed；创建父目录后全套通过，代码未改，首次报告保留为 baseline-tests.xml。

## 1. 总体接法与文件范围

参考图 → Pass 1 inventory → 清单结构校验 → Pass 2 draft + mapping → 覆盖校验 → 必要的一次定向修正 → 既有 refine / preview → review / finish。

| 文件 | 计划改动 | 阶段 |
| --- | --- | --- |
| 新增 scripts/collage_recreate/inventory.py | Inventory / VisualObject schema、严格结构校验；复用 Strict、ID、ToolError、JSON 读取工具 | 1 |
| 新增 tests/test_inventory.py | 清单结构、未知项、输入保留和错误报告测试 | 1 |
| 新增 references/inventory.md | Pass 1 观察规则及最小 JSON 示例；不要求 bbox、业务角色、合并或图层 | 2 |
| 新增 scripts/review_inventory.py | inventory check 入口；接通编排时纳入同一任务状态 | 2 |
| 修改 SKILL.md | 简短路由到 legacy / inventory_v1；先观察、校验后再做制作决策 | 2–3 |
| 修改 references/analysis.md、references/tools.md | 保留旧路径，增加 Pass 2、映射、复核及实际可运行命令 | 2–6 |
| 新增 scripts/collage_recreate/analysis_pipeline.py | mapping 校验、validate_draft_against_inventory、风险结果与阶段衔接 | 3–5 |
| 修改 scripts/collage_recreate/analysis_session.py | 固定 pipeline、阶段摘要、一次共享修正机会、状态完整性及终态语义 | 2–6 |
| 修改 scripts/review_analysis.py、scripts/refine_layout.py | pipeline 参数和校验关口；新路径缺 inventory/mapping 时不能绕过校验 | 3–6 |
| scripts/collage_recreate/analysis.py | 复用 validate_draft / 坐标转换 / 预览；需要展示覆盖结果时才小改预览入口 | 4–6，按需 |
| 新增 tests/test_analysis_pipeline.py；扩展 tests/test_analysis_session.py、相关 CLI 测试 | 对象去向、修正范围、摘要失效、预算和旧命令兼容 | 2–6 |
| plan-2.md、STATUS.md | 每阶段记录已实现内容、验证、产物和下一步 | 每阶段 |

保持 models.py 的 Project / Patch / Permissions、core.py、render.py、service.py、export.py、主工程 CLI scripts/collage_recreate/cli.py / scripts/run.py 及 project.json 契约不动。复用 core.py 的摘要、原子写入与锁，不重写它们。localize.py 默认不改算法，沿用 refine_layout 输入/输出和元素缓存。当前没有自动 draft → project 编译器，本次也不新增。

## 2. 接口决策

1. **Inventory 独立于工程模型。** 顶层 version / objects / questions；对象 id / visual_type / description / text_content / confidence。version 严格为整数 1，objects 非空，ID 合法且唯一，visual_type 使用原计划七项枚举，confidence 为 high / medium / low。text_content 为字符串或 null；unknown 合法，疑问保留。校验器不猜字、不改分类、不修改源文件。图片摘要与路径由程序记入状态，不要求模型填写。
2. **保留已验证的坐标入口。** 原计划写 source_rect，当前模型入口已是 source_bbox_1000。Pass 1 无 bbox；Pass 2 继续写整图归一化 xyxy，由 analysis._normalize_input_coordinates 转成像素 xywh。旧像素草案、六字段 Draft 和层级展开继续有效，不回退为模型估像素，也不趁此替换 layer_order。
3. **新路径必须有 mapping。** Draft 使用 extra=forbid，不能直接塞 source_objects。采用原计划允许的开发旁文件 analysis/draft_mapping.json，由 Pass 2 与 draft 一起生成。最小内容是制作项 {type,id,source_objects}，外加 ignored 的 object_id/reason 和 unresolved 的 object_id/question；固定背景目标无 id，背景槽指向实际 slot。多个装饰可映射一个 overlay；重复消费对象须有明确拆分说明，否则报冲突。
4. **映射双向校验。** 每个 inventory 对象必须有去向、明确忽略或对象级问题；source_object 与目标 ID 必须存在，实际制作项也须有来源。用户新增文字/指定背景等可以记录明确任务来源及理由，不能借此补造参考对象。纯底色等默认底层须与实际 background_candidate 区分。清单无 bbox，程序无法据此证明组合框完整，仍需看图。
5. **模式属于分析任务配置。** 拟用 --pipeline legacy|inventory_v1，初始默认 legacy。首次记录模式；以后省略参数沿用记录值，显式冲突拒绝，不能切模式重置预算。A/B 使用登记过的两个独立试验任务。配置不进入 image service config 或 project.json。旧任务状态不原地重置，新版状态采用独立版本并保留旧记录。
6. **unknown 可保存，不等于制作就绪。** 字段合法性与高风险疑点分开；unknown 无对象级问题时报告 UNKNOWN_UNRESOLVED。已记录问题的 unknown 可以进入诊断，影响主体去向/完整性时以 not_ready 结束；非阻塞的难认字等允许带疑点交付，不能仅凭 questions 非空一律失败或一律通过。

## 3. 原计划需要明确的实现边界

- **两阶段不等于两次 API 请求。** 当前 analysis/session/CLI 均不调用模型，模型在 OpenClaw 会话中读图、写文件、执行工具。最小路线先实现两个受程序检查的产出阶段 + 最多一次定向修正，另统计真实响应和工具次数。同一会话也不提供完全干净的 Pass 1 上下文。如果正常 2 次、最多 3 次是严格 VLM 请求上限，Phase 2 前必须另加窄的 VLM 调用适配器和独立阶段上下文，才有能力强制计数；这不是单改提示词能实现的，也不需要多 Agent。
- **预算不能机械复用。** inventory 校验、draft 完整性校验不能各占原有两次 refine/check 预算。新路径分别记录阶段产出和定位执行，但 inventory / draft / geometry 共用一次修正资格，不能三层各重试一次。Pass 2 后修 inventory 会使 draft/mapping 失效，必须重新验证依赖；如果严格三次 VLM 上限下已无请求余量重做依赖，则保存结果并 not_ready，不暗中发第四次。最终看图发现重大 geometry 问题同样消耗剩余共享资格。
- **修正范围由程序核验。** geometry-only 保持 inventory、mapping 和 draft 非坐标业务内容一致，只改指定对象坐标；分类修正不重写 inventory；inventory 改动使旧 mapping/draft 检查失效。需要同步改依赖但超出剩余机会时，报告缺口并结束。
- **已确认 fingerprint 缺陷。** analysis_session.fingerprint 排除了 review_notes/questions，而 Overlay.review_notes 必填；缺字段失败后只补此字段会被 UNCHANGED_FAILED_INPUT 提前终止。失败输入比较应包含完整结构和值；成功后的计算缓存与诊断更新分开，继续防止格式变化/改文件名导致重复计算。新 questions 不得因复用缓存丢失，须正确绑定采用结果/复核记录。
- **结束成功与草案合格分开。** 当前 finish 对已结束的失败任务回传 result.ok=false、CLI exit 1。应使“可靠结束为 not_ready”成为正常、幂等的结束响应，保留上次失败原因与草案未就绪事实；不把正常结束改成视觉通过。重复 finish 也须核验状态与采用产物摘要。
- **轻量 hash 的能力边界。** analysis_session 统一读写/验证 input_hash 与 payload_hash，后者覆盖除摘要字段自身之外的完整状态；绑定 source、pipeline、inventory、draft、mapping 和采用产物。CLI 复用/finish 前先核验，单改 result.ok、phase、attempts 或 fingerprint 报 STATE_TAMPERED。旧无摘要记录不能直接补摘要后冒充受保护证据。同一可写目录中的明文摘要只能发现未同步重算摘要的改写，不能抵御同时改 payload 与 hash；本次按原计划做轻量完整性检查，不承诺强防篡改，不引入数据库。

## 4. 按七个 Phase 执行

1. **Phase 1：Inventory 契约和离线测试。** 仅新增 inventory.py、tests/test_inventory.py，并更新 plan-2.md / STATUS.md。复用依赖，先写行为测试再实现；读取并验证模型保存的 inventory，不写回/覆盖源文件。此阶段生产路径和 skill 提示词保持现状。
2. **Phase 2：单独验证 Pass 1。** 添加 inventory 规则、check CLI 和模式/阶段登记。先明确“阶段数还是严格 API 次数”，采用最小路线时如实报告上下文隔离限制。用多人物/贴纸、相框、长线等已有失败参考只跑清单，人工核对召回，未证明改善前不急着接 draft。fingerprint 与状态保护在开放新阶段入口前完成基础回归。
3. **Phase 3：Pass 2 输出旧 draft + mapping。** 输入参考、已校验 inventory 和任务说明；先处理照片、文字和装饰，再判断底层。保留现有字段、归一化入口、附属层展开和 draft 校验。Pass 2 不静默修写 Pass 1 文件。
4. **Phase 4：完整性检查。** 双向映射、有效引用、显式去向；结构缺失给明确错误。POSSIBLE_SLOT_OMISSION / POSSIBLE_WRONG_BACKGROUND 是待复核风险；OBJECT_DROPPED / UNKNOWN_UNRESOLVED / GEOMETRY_MISSING 按实际缺口报告。增加 decoration → customer slot 风险提示，并允许客户明确替换用途的有理由例外。程序不根据描述字符串猜语义真值。
5. **Phase 5：一次定向复核。** 返回 stage / object_ids / code / evidence / suggested_action，记录共享修正资格，校验修正范围和依赖失效。每稿另存，不覆盖模型原始证据；同一问题仍阻塞则 not_ready。
6. **Phase 6：接回定位与收尾。** 在 run_draft_operation 前接新路径校验，复用 localize 算法、每元素缓存、preview 和产物；finish 核验对应摘要及阻塞问题。视图可增加对象去向/缺口摘要，不新建编辑器。
7. **Phase 7：受控 A/B。** 先少量已有真实失败图，再扩到冻结 16 图集合。两路径保持原图字节、模型/端点、图像处理上限、客户任务一致，避免把旧 1200 缩图结果与新 2048 上限结果的差异归因为架构变化。保存实际输入尺寸/摘要、实际模型、全部初稿/修正、调用/工具数、耗时、not_ready 和人工修改量；语义与 bbox 分别评估。有证据优于 legacy 后再考虑改默认模式。

真实图片和会话仍放 D:/codes/collage-recreate-work，不纳入 Git；新实测用新目录，不再次启动旧 runner。四个人物 photo、猫/可颂归装饰等是具体样图的验收事实，不变成通用数量阈值或运行提示词答案。实测已按用户授权启动，执行记录见第 6 节。

## 5. 测试与阶段出口

- Phase 1：合法七类及中文/null 文本；错误 version（含 bool/string）、空清单、重复/非法 ID、非法类型/confidence、错误 text_content/questions、空白描述、额外 bbox/制作字段被拒绝；unknown 可保存且不自动归背景；成功/失败均保留输入字节，不触发模型或图像服务。
- 映射：真实失败结构的脱敏清单不能无声丢 photo；多个 photo 映射同一背景产生风险；贴纸误入 slot 可见；照片内区和边框有不同制作去向；合法局部合并通过；ignored 缺理由、无效引用、未解释来源和摘要不匹配被拒绝。
- 流程：缺 review_notes 后补齐可重验；复用保留新疑点；所有入口共享状态；切 pipeline/改文件名不重置预算；一次修正后再次请求被拒绝；inventory 改动使下游失效；geometry-only 不能偷改分类；覆盖状态改写、并发、中断和 not_ready 幂等 finish。
- 接入：新旧归一化/像素草案、EXIF 非正方图、附属层唯一性、预览/采用稿一致性、旧 CLI 默认路径继续通过。依据新变更风险扩测，不为文案修改重复整套回归。
- 视觉：人工记录主要对象/照片/客户槽召回、错误槽、错误背景、装饰遗漏、过并/过拆、重大裁缺。先看语义完整性，再看边缘精度；schema pass、主控自评、runner stop 和独立视觉结论分别统计。

Phase 1–7 已完成实现、离线验证、16 图双路实测和独立视觉复核。结果未达到替换默认流程的出口条件，默认继续使用 legacy。


## 6. 本轮实施记录

- 用户已要求执行完整计划并批测全部 16 张，随后明确授权将数据传递至 OpenClaw 和 Qwen；首次自动审批拒绝已解除，之后未重复请求授权。
- inventory.py / review_inventory.py / analysis_pipeline.py / inventory_session.py 已实现；analysis_session.py 统一锁、模式与摘要，避免将新旧状态转换混为一个大函数。
- 保留现有归一化输入和六字段 Draft；缺坐标等结构错误由原 Draft 校验报告 INPUT_INVALID，不另造一套几何 schema。新路径在 refine 前拦截覆盖风险。
- 最小阶段编排继续 OpenClaw + qwen3.8-flash，两个产出阶段；真实 API 响应、工具数及耗时另计，没有直接 VLM 适配器或独立上下文隔离。
- 两阶段实现时全套 **199 passed in 20.99s**；加入确定性文字/线条分流后最终为 **207 passed in 20.44s**，报告 offline-tests-program-draw.xml；skill 校验、compileall、diff 检查通过。
- 两组使用同一冻结 runtime，隔离 imageMaxDimensionPx=2048。冻结后另修复 legacy 终态缓存不得合并新备注，20 项定向回归通过；运行中的快照不改，报告注明差异。
- 三图 inventory-only 试测有主要照片召回价值，但贴纸分类、照片内部对象过拆仍错。Phase 2 未独立视觉通过；继续完整 A/B 是为验证后续阶段是否恢复，不能把它当作精度已验收。
- 全 16 图两路对照已完成，每图每路一次。原图、会话、各次模型 write 及实际输入尺寸/摘要保存在独立工作目录，未生图或生成 project.json。
- public/index.html 分开显示逐图视觉评价和流程状态；失败草案的框图明确标为诊断。用户反馈首批看起来尚可，与首批部分框图较好的独立观察一致；映射失败不自动等于视觉失败。
- 32 个结果审计和逐图人工评价已完成。legacy 正式 ready 15/16、结构有效 15/16；inventory_v1 正式 ready 1/16、结构有效 11/16。人工对照为新流程明显较好 3 例、旧流程明显较好 6 例、其余 7 例相当或互有得失。新流程主要收益是复杂对象召回和错误定位，主要失败是 Pass 2 draft + mapping 的输入/映射负担；当前保持 legacy 默认。完整结论见工作目录 RESULTS.md 和 public/index.html。


## 7. 用户追加观察：程序绘制分流（已实现）

- 用户指出普通文字、简单线条也大量被分配 reference_generate。检查确认原 Shape 只支持矩形/圆角矩形/椭圆/虚线矩形，且旧说明把部分固定文字误导到生成路径。
- Shape 已增加 line / polyline；点位使用元素局部 0..1000 坐标，并严格校验点数、描边、宽度和成对 dash/gap。复杂自由曲线仍不要求弱 VLM 输出逐笔路径。
- 新增 render_overlay.py 与 draw_overlay.py：先完整校验 Draft，再将 basic_shape 以 4 倍抗锯齿绘制为新的透明 PNG，返回摘要、尺寸和透明度范围，且拒绝覆盖已有文件。工具不调用图像生成服务。
- 普通可辨文字统一进入 texts，无论文字是否允许客户修改；工程 renderer 绑定准确文字与字体。标签的底板/边框与文字可分层后通过工程 group 共同移动。reference_generate 只保留给特殊手写字形、商标、复杂纹理/插画或文字与插画不可分割的情况。
- 特殊手写字形、复杂纸纹/插画是否需要生成，按外观要求判断。不能把所有 reference_generate 自动判错，也不能因为存在文字就一律生成。
- 本轮冻结 A/B 不改制作分流，以免运行中改变对照条件；报告增加 action 分布及待复核候选。程序绘制另做离线回归，批测仍未发生实际生图。
