# 参考图到制作草案

第一步产出 analysis/draft.json，决定客户图片、可改文字、装饰制作组合及叠放顺序。随后运行本地定位细化并看对照预览；真实素材和 project.json 属于下一阶段。无需另写分析 Markdown。

## inventory_v1：Pass 2 制作草案

仅在任务使用 inventory_v1 且 inventory.json 已通过 review_inventory.py 后使用本节。输入为同一参考、清单和客户任务，不重新从零枚举对象。清单明显错漏时记录证据，使用唯一共享修正机会校验修正清单，再更新受影响的 draft/mapping；不静默补造参考对象。

先逐一处理 photo 的替换用途、text 的可编辑需求，再组合装饰，最后确定剩余底层。装饰组合主要看是否需独立编辑、跨越主要元素、分开控制前后遮挡；同一局部成品默认整体制作。photo 不等于最终 slot，background_candidate 不等于最终 background，按实际任务决定并保留依据。

复用下文六字段 draft.json，仍用 source_bbox_1000；框覆盖组合全部可见成员，CV 仅微调局部边缘。同时保存 analysis/draft_mapping.json，不能把映射字段塞进 draft。

### mapping 格式

顶层 version=1、bindings、ignored、unresolved。每个实际 slot/text/overlay/固定 background 恰好一条 binding：
~~~json
{"version":1,"bindings":[
  {"type":"background","origin":"default","reason":"主体之外的剩余纯色底层"},
  {"type":"slot","id":"photo_a","source_objects":["obj_001"]},
  {"type":"overlay","id":"decor_a","source_objects":["obj_002","obj_003"]}
],"ignored":[],"unresolved":[]}
~~~

- type 为 slot / text / overlay / background；background 不填 id，照片背景绑定实际 slot ID。
- source_objects 只能引用清单 ID；多个装饰可合成一个 overlay。一个对象确须拆成多个制作项时，每条相关 binding 都写 split_reason。
- 用户明确新增文字/背景等不在参考里的内容，用 origin=user、source_objects=[]、reason 记录任务依据。仅固定背景可用 origin=default 表达剩余底层，不用于掩盖漏识别。
- 每个清单对象须有去向，或在 ignored 写 {object_id,reason}，或在 unresolved 写 {object_id,question,blocking}。blocking 默认 true；明确不影响主要内容/制作时才写 false。不要为消除错误把主要照片丢进 ignored。
- photo 被设为背景/装饰、贴纸被设为 slot、unknown 直接采用会提示复核。确有依据的例外在 binding.reason 写明，工具保留警告；一句理由不证明视觉正确。多个独立 photo 共用一槽/背景仍须复核。
- 平台界面等明确不属设计的项以 ignored + reason 说明取舍。未知字保持 null 和具体问题。

### 定位和共享修正机会

直接运行一次，不另跑 check 消耗定位次数：
~~~powershell
python scripts/refine_layout.py --task analysis --pipeline inventory_v1 --reference reference.png --input analysis/draft.json --mapping analysis/draft_mapping.json --output analysis/refine-01
~~~

inventory 默认使用任务登记的清单，可显式传 --inventory。工具先检查草案、映射及覆盖，再定位/预览。COVERAGE_REVIEW_REQUIRED 的 coverage.issues 包含 stage/object_ids/code/evidence/suggested_action；coverage.warnings 保留有理由的例外。结构/覆盖通过不等于视觉通过。

清单、分类、geometry 共用一次修正资格。首次失败或预览发现重大问题时，按原因另存修正稿：
~~~powershell
python scripts/refine_layout.py --task analysis --reference reference.png --input analysis/draft.corrected.json --mapping analysis/mapping.corrected.json --output analysis/refine-02 --issue wrong_assignment --reason "对象、观察证据及要修正的角色" --review-stage draft
~~~

只有坐标错时从实际采用稿另存，保留非坐标字段与同一 mapping，使用 --review-stage geometry 和 --object-id 指定制作项 ID（可重复）。清单本身错时另存清单，用 review_inventory.py --reason 校验；旧 draft/mapping 的检查随后失效，不能直接 finish 可用。修正仍失败、次数用尽或依据不足时 not_ready。

看一次有效预览后使用下文 finish 命令。finish 的 ok=true 表示结束动作成功，draft_ready 与 decision 才表示草案就绪状态；not_ready 仍保留 last_error。文件/状态摘要不一致就停止，不手改 .state。下面的字段规范两条路径共用；“一次定位”中的旧预算说明适用于 legacy，新路径还受共享修正资格约束。

## legacy：一步制作流程

legacy 直接观察并制作 draft，不需要 inventory/mapping。

## 看图和决定制作范围

先用图片工具看完整参考，用 inspect_reference.py 读尺寸。**初稿前每张图只看一次整图，最多一批、4 个必要局部**，同一局部不反复扩大/缩小重看。达到该上限就写草案：框按完整对象的可见范围定位，读不清的文字为 null，不确定归属写 questions。

**模型负责识别对象及其完整位置范围；程序负责坐标转换和局部边缘微调。** refine 不会重新识别对象、找回漏项或纠正整体错位。初稿前不另写像素扫描、颜色阈值、边缘检测或坐标搜索代码；不增加观察轮次或重置预算。轻微边缘误差可记疑点，选错对象或裁缺主体不能交给 refine 兜底。

目标：**保留必要的可编辑性，用尽量少的制作单元完成复刻。**

- **可替换内容 slots / texts**：按任务中需要替换的图片和需要修改的文字设置独立项。分类依据是内容用途，写实程度、有无边框等外观特征不能单独决定分类。
- **装饰 overlays**：以能够整体制作和摆放的局部组合作为一项。组合内部的图案、描边、衬片和点缀默认一起制作；只有实际编辑需求或与其他元素的前后遮挡关系要求独立时，才拆开。内部细节写入组合描述。
- **背景 background**：先识别主体内容与装饰，再确定实际底层及其来源。摄影底图按图片槽处理，设计底板按固定背景处理，用户指定来源优先。多个主体铺满画面或互相融合，不代表它们共同构成一个背景槽。

图片槽只框照片内容，不含另做的边框/标签；保留环境用 photo，柔边用 photo_feather，明确剪影才用 cutout，不确定用 unknown。有客户素材才绑定具体文件，没有则记录待提供。可改文字逐字记录准确内容，读不清为 null 并提问，不猜词；客户已提供的文案优先。设计底板有干净素材就复用，有覆盖内容才清版/重绘；纯色或简单渐变用确定性工具。

- **半透明衬底**：遵循上述组合原则；需独立编辑或与其他元素分层时才单列 overlay，并表达完整范围、颜色、透明度和层级。独立的简单几何使用 basic_shape，以 shape.fill 的 #RRGGBBAA 表达颜色和透明度；无法确定时使用近似值并记录疑点，不声称恢复了原始精确透明度。
- **连接线**：直线、规则折线、虚线优先用 basic_shape 的 line/polyline，由程序画透明素材。自由手绘曲线才写入 reference_generate 的 generation_brief。同归属、同层级、共同调整的线可成组；需要分别跟随不同对象的线按归属分组，不必逐笔拆分，也不要求弱 VLM 输出复杂路径。
- **可辨文字优先走 texts**，无论它将来是否允许客户改字。工程阶段用字体渲染，并可和底板、边框放入同一 group 共同移动。只有商标、无法由普通字体表达的特殊手写字形，或文字与插画不可分割时，才作为 reference_generate 的固定装饰；准确内容仍写 text_content，不能同时在 overlay 与 texts 重复。
- **attachment 只表达归属**：相同 attachment 不等于已经合成一份素材。合并要减少 overlays 条目；合并边框的照片开口保持透明，不将参考中的照片烘焙进去。
- 外观说明写完整对象的形状、方向、颜色、质感及内部排列，不穷举笔触。未知标识记录疑点，不猜词或自行作删除决定。平台界面不属于重建设计内容。

## draft.json

顶层恰为 background、slots、texts、overlays、layer_order、questions。所有 ID 跨三类元素唯一，为小写字母开头，后接小写字母、数字、下划线或短横线，最长 64。

模型初稿中，每个元素只写 source_bbox_1000：**完整参考图的 0–1000 归一化整数 [x_min,y_min,x_max,y_max]**。整图左上角为 [0,0]，右下角为 [1000,1000]；横纵轴各自归一化，右下角不是宽高。直接按对象四边占整图宽高的比例定位，不先估像素再除以图片尺寸；看图工具等比例缩图不改变此比例。框应贴合并覆盖描述中全部制作内容，包括附属字、点缀及完整线段，不能只框主图案。

refine/check/preview 按朝向归正后的原图宽高换算四边（四舍五入），再得到像素 source_rect=[x,y,width,height]；原始输入不覆盖。工具输出与旧草案继续使用像素 source_rect，不会再次转换。同一元素不能同时写两种字段，也不根据数字大小猜坐标系。修正模型初稿仍用 source_bbox_1000；复用工具输出时保留其 source_rect。工程 project.json 的像素布局不变。inspect_reference.py 的 --crop 仍用原图像素 LEFT TOP RIGHT BOTTOM，不接受归一化坐标。

模型初稿共同字段：id、label、source_bbox_1000；review_notes 可省略，默认为空（overlays 的必填项见下文）。可选 locate_colors 为 1–8 个 "#RRGGBB" 或标准颜色名，仅给已观察到的主要笔触色，作为局部定位提示。复杂背景上的彩色笔触可提供近似色；不清楚时省略，不要求精确取样，也不把摄影图片的主要颜色当笔触提示。

### background

摄影底图默认：
~~~json
{"mode":"slot","kind":"photo","slot_id":"background_photo","review_notes":"摄影背景，待提供客户素材"}
~~~
必须有对应 photo 图片槽，初稿 source_bbox_1000=[0,0,1000,1000]，作为唯一最底层；不再叠一层固定背景。槽位规划不表示已经选择/确认客户文件。

固定设计底板：
~~~json
{"mode":"fixed","kind":"texture","background_brief":"复用干净纸纹；确有覆盖时清版或重绘","review_notes":""}
~~~
kind 可为 photo / texture / solid / unknown。固定 photo 必须另有非空 preserve_reason，记录客户明确保留摄影底图的要求，不能编造。unknown 说明不确定性，在 questions 记录；不能据此擅自清版。客户明确指定其他类型背景替换，也可使用 slot。

### slots：客户图片

~~~json
{"id":"photo_a","label":"照片","source_bbox_1000":[100,200,450,650],"mode":"photo","upload_hint":"提供替换照片","review_notes":""}
~~~
mode 为 photo / photo_feather / cutout / unknown。upload_hint 可省略。没有 type、default_text。

### texts：由字体确定性绘制的文字

~~~json
{"id":"caption","label":"文案","source_bbox_1000":[100,50,800,120],"default_text":"准确原文","style_brief":"白色细体，居中","review_notes":""}
~~~
default_text 必填，可为 null；style_brief 可省略。不填图片 mode/upload_hint。字体绑定、字号和实际排版留到工程阶段。

### overlays：独立制作的装饰组合

必填 action、generation_brief、requires_exact_content、attachment、review_notes，另有共同字段。action 为 basic_shape / reference_generate，后者要求非空外观说明。它仅表示制作方案，不触发生图。

可附 text_content（准确装饰字或 null）。requires_exact_content 为布尔值，不代表生成模型能保证字形正确。

attachment 为 null，或 {"slot_id":"photo_a","position":"above"} / below，仅能归属一个非背景图片槽。跨照片装饰独立保留。无需将一份组合内每个笔触再列成子对象。

basic_shape 表达程序可确定绘制的一个几何形状或一条简单路径，不能包含 text_content；标签拆成 basic_shape 底板/边框和 texts 文字，后续用工程 group 共同移动。basic_shape 必须有 shape；reference_generate 不写 shape 或为 null。shape：
- kind：rectangle / rounded_rectangle / ellipse / dashed_rectangle / line / polyline。
- 必填 outline（颜色或 null）、width（0..1024 像素）；fill 可省略或 null。
- rounded_rectangle 另有 radius（0..8192）；dashed_rectangle 另有 dash（1..8192）和 gap（0..8192）。
- line 必须恰有两个 points；polyline 至少两个。points 使用装饰框内部 0..1000 整数坐标 [x,y]，要求 outline 非空、width>0、fill=null。虚线另同时给 dash/gap；直线或简单折线之外的笔触使用 reference_generate。
- 颜色为 #RRGGBB / #RRGGBBAA 或 Pillow 标准色名，不填无关形状参数。

reference_generate 只用于确定性图形和字体排版无法合理还原的视觉素材，例如撕纸毛边、复杂手绘插画、特殊字形或真实纹理。它是制作建议，不自动调用生图；不得仅因元素是固定内容、有文字或需要整体移动而选择它。

### layer_order / questions

layer_order 从底到顶，条目为 {"type":"background"} 或 {"type":"slot|text|overlay","id":"…"}，其中 type 写实际一种值。背景必须最底：固定背景用 {"type":"background"}；客户图片背景用 {"type":"slot","id":"对应背景槽 ID"}，两者不同时写。每个图片槽、文字、独立装饰**必须出现且仅出现一次**。

questions 为非空字符串数组，无疑点用 []。写清对象、未确定内容及影响。定位工具会补充失败项，未解决前不视为制作就绪。

## 一次定位、一次看图、结束

同一客户请求使用一个固定任务根目录，例如 analysis；多张参考按图片摘要分别累计预算。写好对象与完整范围后直接 refine，内部完成结构校验、坐标转换、局部微调和预览，前后都不额外 check/preview：

~~~powershell
python scripts/refine_layout.py --task analysis --reference reference.png --input analysis/draft.json --output analysis/refine-01
~~~

工具输出简短 JSON：draft、comparison、preview、needs_review 和 workflow。直接打开 comparison 图片看一次；不常规读取完整 localization.json、源码或探测内部函数。需要细看已发现的异常时，最多追加一批针对这些区域的 crop。

### 可用即结束

主要图片、可改文字、装饰及其完整范围已覆盖且归属明确，草案结构有效，没有已知阻止素材准备的问题，就结束。轻微边缘偏差、线宽或纹理差异、光晕、颜色和透明度不确定、难认的字及待提供素材，可以保留疑点后结束。整块衬底或主体遗漏、主要连接线缺段、框裁掉组合成员，属于完整性问题，不能因为线细、颜色浅或半透明就忽略。needs_review 是提示，不自动触发重试；是否可用仍按内容完整性判断。算法采用不等于视觉精确；不要声称已完成素材抠图或工程制作。

实际看过预览后，记录一次最终决定：

~~~powershell
python scripts/review_analysis.py finish --task analysis --reference reference.png --decision usable_with_questions --note "主要对象与分类已核对；具体未确认对象及影响……"
~~~

decision 为 usable / usable_with_questions / not_ready。note 写真实观察；有程序待复核项或 questions 时，工具会保留 usable_with_questions，不会为了结束把疑点删掉。finish 不重新校验/定位，不要求追加看图；记录后立即返回草案、预览、状态和未解决项。工具的 visual_status 仍为 unreviewed，review_note 是主控观察声明，不是独立视觉验收。

### 仅一次定向修正

首次复核发现结构错误、主要内容遗漏或归错类、框明显选错对象或裁掉组成部分、违背客户明确要求时，使用剩余的一次机会定向修正；没有可靠证据支持修正则以 not_ready 结束并说明。结构排错和视觉修正共用预算。修改前明确对象、已有证据和预期改变，另存 corrected.json；不微调大量阈值追求精确：

~~~powershell
python scripts/refine_layout.py --task analysis --reference reference.png --input analysis/corrected.json --output analysis/refine-02 --issue wrong_object --reason "photo_a：粗框包含旁边文字，依据局部原图修正为照片内容区域"
~~~

issue 可为 structural / missing_element / wrong_assignment / wrong_object / customer_requirement。上次定位缓存自动复用，不要求再读缓存或显式传 --reuse。若只做了明确人工修正、不再需要算法定位，可用 review_analysis.py preview 替代第二次 refine，同样提供 --task / --issue / --reason，消耗同一预算。不要两者都跑。

第二次检查后只看修改对象及受影响邻居并 finish，不能再检查第三次。格式/图层仍失败，或已知主要照片、文字、衬底、连接线或组合成员仍有遗漏/裁缺时，返回 not_ready 和具体错误；不能因修正机会用完就把重大错误改称轻微偏差。已有有效草案但仅局部不确定则 usable_with_questions。工具输出 stop_rechecking=true 表示不再执行检查，若 next_action 为看一次预览再 finish，则只完成该结束动作。

以下任一情况立即结束当前自动复核：相同输入已有结果；失败输入未变；修正后同一问题仍存在；预算已用完；没有新证据说明下一次能解决什么；继续需要读源码、试参数或修环境。不要更换文件名、任务根目录、参考副本或删除状态来重置预算。只有用户提出新的修改任务才能开始新任务。

### 定位能力与文件

需要 requirements-localization.txt。输出 draft.refined.json、localization.json、comparison.png、index.html 和可点击 review/index.html；粗框/候选/参数在诊断文件，每个草案元素仍仅一套 source_rect。原图、草案与已有输出不覆盖。

- photo 使用局部边缘寻找照片内容边界，强外框不等于照片内区。
- texts / overlays 使用近似色、局部对比度及多个连通区域，没有 OCR 或语义分割。cutout/photo_feather 不假装完成抠图。
- 不确定候选保留粗框；背景槽保持满画布。红为粗框、绿为已采用候选、黄为待复核候选。候选可能误收邻居或漏笔触，needs_review 是提示，不是重试指令。
- 缓存按图像、相关元素、算法与依赖失效；独立任务状态另按参考摘要记录检查次数、输入摘要、错误、最终决定。相同输入/仅诊断备注变化直接复用，不重复计算。
- 编号顺序为图片、文字、装饰。有效草案供后续 [工程格式](project.md) 使用，不等于已经准备好客户素材或 project.json。
