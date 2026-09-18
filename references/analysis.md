# 参考图到制作草案

第一步产出 analysis/draft.json，决定客户图片、可改文字、装饰制作组合及叠放顺序。随后运行本地定位细化并看对照预览；真实素材和 project.json 属于下一阶段。无需另写分析 Markdown。

## 看图和决定制作范围

先用图片工具看完整参考，用 inspect_reference.py 读尺寸。**初稿前每张图只看一次整图，最多一批、4 个必要局部**，同一局部不反复扩大/缩小重看。达到该上限就写粗草案：读不清的文字为 null，边界给可靠粗范围，不确定归属写 questions，不为了消除疑点拖延初稿。

**初稿前禁止另写像素扫描、颜色阈值、边缘检测或坐标搜索代码。** 精定位统一交给一次 refine；主控负责粗范围和制作语义。不要把检查工作提前到“观察”阶段规避预算，也不要重新计为一轮分析。颜色、边缘与细微笔触的精度不足，不阻止先交付草案；主要内容仍须识别并覆盖完整。框选与分类以原图为准。

目标：**保留必要的可编辑性，用尽量少的制作单元完成复刻。**

- **可替换内容 slots / texts**：按任务中需要替换的图片和需要修改的文字设置独立项。分类依据是内容用途，写实程度、有无边框等外观特征不能单独决定分类。
- **装饰 overlays**：以能够整体制作和摆放的局部组合作为一项。组合内部的图案、描边、衬片和点缀默认一起制作；只有实际编辑需求或与其他元素的前后遮挡关系要求独立时，才拆开。内部细节写入组合描述。
- **背景 background**：先识别主体内容与装饰，再确定实际底层及其来源。摄影底图按图片槽处理，设计底板按固定背景处理，用户指定来源优先。多个主体铺满画面或互相融合，不代表它们共同构成一个背景槽。

图片槽只框照片内容，不含另做的边框/标签；保留环境用 photo，柔边用 photo_feather，明确剪影才用 cutout，不确定用 unknown。有客户素材才绑定具体文件，没有则记录待提供。可改文字逐字记录准确内容，读不清为 null 并提问，不猜词；客户已提供的文案优先。设计底板有干净素材就复用，有覆盖内容才清版/重绘；纯色或简单渐变用确定性工具。

- **半透明衬底**：遵循上述组合原则；需独立编辑或与其他元素分层时才单列 overlay，并表达完整范围、颜色、透明度和层级。独立的简单几何使用 basic_shape，以 shape.fill 的 #RRGGBBAA 表达颜色和透明度；无法确定时使用近似值并记录疑点，不声称恢复了原始精确透明度。
- **连接线**：局部短笔触可并入所属主体；跨区域连接不同对象的线条应表达完整可见走向和连接对象，写在 generation_brief 中。同归属、同层级、共同调整的线可成组；需要分别跟随不同对象的线按归属分组，不必逐笔拆分。
- **固定装饰字**可与图案合并，准确内容写 text_content，并说明位图字不能单独修改；客户要求可改的字保留 texts，以工程 group 共同移动。不能同时把同一串字画在 overlay 又重复放入 texts。
- **attachment 只表达归属**：相同 attachment 不等于已经合成一份素材。合并要减少 overlays 条目；合并边框的照片开口保持透明，不将参考中的照片烘焙进去。
- 外观说明写完整对象的形状、方向、颜色、质感及内部排列，不穷举笔触。未知标识记录疑点，不猜词或自行作删除决定。平台界面不属于重建设计内容。

## draft.json

顶层恰为 background、slots、texts、overlays、layer_order、questions。所有 ID 跨三类元素唯一，为小写字母开头，后接小写字母、数字、下划线或短横线，最长 64。

每个元素只写一套 source_rect：朝向归一后的**原图整数像素 [x,y,width,height]**，正宽高、在画布内。粗框必须覆盖该项描述中全部纳入制作的内容，包括附属文字、分散点缀和完整可见线段；不能描述一个大组合却只框住主图案。程序负责局部边界细化，不能代替模型补齐未识别或未覆盖的远处内容。实际输出布局由 project.json 的 x/y/width/height 及画布映射表示。inspect_reference.py 的 --crop 仍用 LEFT TOP RIGHT BOTTOM，换算交给脚本或简单算术，不混用。

共同字段：id、label、source_rect；review_notes 可省略，默认为空。可选 locate_colors 为 1–8 个 "#RRGGBB" 或标准颜色名，仅给已观察到的主要笔触色，作为局部定位提示。复杂背景上的彩色笔触可提供近似色；不清楚时省略，不要求精确取样，也不把摄影图片的主要颜色当笔触提示。

### background

摄影底图默认：
~~~json
{"mode":"slot","kind":"photo","slot_id":"background_photo","review_notes":"摄影背景，待提供客户素材"}
~~~
必须有对应 photo 图片槽，source_rect=[0,0,原图宽,原图高]，作为唯一最底层；不再叠一层固定背景。槽位规划不表示已经选择/确认客户文件。

固定设计底板：
~~~json
{"mode":"fixed","kind":"texture","background_brief":"复用干净纸纹；确有覆盖时清版或重绘","review_notes":""}
~~~
kind 可为 photo / texture / solid / unknown。固定 photo 必须另有非空 preserve_reason，记录客户明确保留摄影底图的要求，不能编造。unknown 说明不确定性，在 questions 记录；不能据此擅自清版。客户明确指定其他类型背景替换，也可使用 slot。

### slots：客户图片

~~~json
{"id":"photo_a","label":"照片","source_rect":[30,80,160,210],"mode":"photo","upload_hint":"提供替换照片","review_notes":""}
~~~
mode 为 photo / photo_feather / cutout / unknown。upload_hint 可省略。没有 type、default_text。

### texts：可编辑文字

~~~json
{"id":"caption","label":"文案","source_rect":[20,30,180,40],"default_text":"准确原文","style_brief":"白色细体，居中","review_notes":""}
~~~
default_text 必填，可为 null；style_brief 可省略。不填图片 mode/upload_hint。字体绑定、字号和实际排版留到工程阶段。

### overlays：独立制作的装饰组合

必填 action、generation_brief、requires_exact_content、attachment、review_notes，另有共同字段。action 为 basic_shape / reference_generate，后者要求非空外观说明。它仅表示制作方案，不触发生图。

可附 text_content（准确装饰字或 null）。requires_exact_content 为布尔值，不代表生成模型能保证字形正确。

attachment 为 null，或 {"slot_id":"photo_a","position":"above"} / below，仅能归属一个非背景图片槽。跨照片装饰独立保留。无需将一份组合内每个笔触再列成子对象。

basic_shape 仅表达一个几何形状，不能包含 text_content；带固定标签的完整装饰使用 reference_generate，需可改的字使用 texts。basic_shape 必须有 shape；reference_generate 不写 shape 或为 null。shape：
- kind：rectangle / rounded_rectangle / ellipse / dashed_rectangle。
- 必填 outline（颜色或 null）、width（0..1024 像素）；fill 可省略或 null。
- rounded_rectangle 另有 radius（0..8192）；dashed_rectangle 另有 dash（1..8192）和 gap（0..8192）。
- 颜色为 #RRGGBB / #RRGGBBAA 或 Pillow 标准色名，不填无关形状参数。

### layer_order / questions

layer_order 从底到顶，条目为 {"type":"background"} 或 {"type":"slot|text|overlay","id":"…"}，其中 type 写实际一种值。背景必须最底：固定背景用 {"type":"background"}；客户图片背景用 {"type":"slot","id":"对应背景槽 ID"}，两者不同时写。每个图片槽、文字、独立装饰**必须出现且仅出现一次**。

questions 为非空字符串数组，无疑点用 []。写清对象、未确定内容及影响。定位工具会补充失败项，未解决前不视为制作就绪。

## 一次定位、一次看图、结束

同一客户请求使用一个固定任务根目录，例如 analysis；多张参考按图片摘要分别累计预算。先写粗草案，直接 refine，内部已经完成结构校验、定位和预览，前后都不额外 check/preview：

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
