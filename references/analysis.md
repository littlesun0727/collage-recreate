# 参考图到制作草案

第一步产出 analysis/draft.json，决定客户图片、可改文字、装饰制作组合及叠放顺序。随后运行本地定位细化并看对照预览；真实素材和 project.json 属于下一阶段。无需另写分析 Markdown。

## 看图和决定制作范围

先用图片工具看完整参考，用 inspect_reference.py 读尺寸。按需看局部，足够理解主要元素后就写草案；不要反复裁切所有笔触。框选、分类和外观判断均以原图为准。

- **背景按类型处理**：风景、人物、生活照等摄影底图默认设为客户背景图片槽，不清除整张参考上的前景再补景。有客户素材才绑定具体文件，没有则记录待提供。格纹、报纸、纸张等设计底板走固定背景；有干净底板就复用，有覆盖内容才清版/重绘。纯色/简单渐变用确定性工具。客户明确保留原摄影背景时优先遵从，并记录该要求；混合背景分清摄影底图与前景装饰。
- **图片 slots**：仅客户可替换图片。框选照片内容，不含另做的边框/标签；保留环境用 photo，柔边用 photo_feather，明确剪影才用 cutout，不确定用 unknown。
- **文字 texts**：需要单独改字的文案；逐字记录准确内容，读不清为 null 并提问，不猜词。客户已提供的文案优先。
- **装饰 overlays**：每项是一份独立制作和摆放的素材。相同归属、共同移动/缩放、相同外部遮挡关系、没有单独编辑需求，默认合成一项；不因颜色不同、笔触断开而拆层。同张照片的框、固定手写标签和局部强调笔触通常共同制作。不同照片的装饰分别保留；需要分处照片前后、穿插其他层或单独修改的元素必须拆分。不要把整个前景合为一项。
- **主图案与周围点缀**：共同构成同一视觉组合的主图案、小装饰、标题与强调笔触，默认共同制作。若拆开，应写出当前确有的单独编辑、移动或遮挡需求，不以假设的未来需求拆分。
- **固定装饰字**可与图案合并，准确内容写 text_content，并说明位图字不能单独修改；客户要求可改的字保留 texts，以工程 group 共同移动。不能同时把同一串字画在 overlay 又重复放入 texts。
- **attachment 只表达归属**：相同 attachment 不等于已经合成一份素材。合并要减少 overlays 条目；合并边框的照片开口保持透明，不将参考中的照片烘焙进去。
- 外观说明写完整对象的形状、方向、颜色、质感及内部排列，不穷举笔触。未知标识记录疑点，不猜词或自行作删除决定。平台界面不属于重建设计内容。

## draft.json

顶层恰为 background、slots、texts、overlays、layer_order、questions。所有 ID 跨三类元素唯一，为小写字母开头，后接小写字母、数字、下划线或短横线，最长 64。

每个元素只写一套 source_rect：朝向归一后的**原图整数像素 [x,y,width,height]**，正宽高、在画布内。先给包住整个制作组合的粗框，脚本随后细化。实际输出布局由 project.json 的 x/y/width/height 及画布映射表示。inspect_reference.py 的 --crop 仍用 LEFT TOP RIGHT BOTTOM，换算交给脚本或简单算术，不混用。

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

layer_order 从底到顶，条目为 {"type":"background"} 或 {"type":"slot|text|overlay","id":"…"}，其中 type 写实际一种值。背景必须最底；每个图片槽、文字、独立装饰出现一次。附属装饰不重复列，工具自动展开为 below 装饰 → 照片 → above 装饰，同侧按 overlays 数组顺序。

questions 为非空字符串数组，无疑点用 []。写清对象、未确定内容及影响。定位工具会补充失败项，未解决前不视为制作就绪。

## 批量定位与复核

先写粗草案，直接调用 refine（内部包含结构校验），无需先重复 check/preview：

~~~powershell
python scripts/refine_layout.py --reference reference.png --input analysis/draft.json --output analysis/refine-01
~~~

工具需要 requirements-localization.txt；输出 draft.refined.json、localization.json、comparison.png 和 index.html，附 review/index.html 可点击预览。草案每项仍只有 source_rect；粗框、候选、算法参数、待复核原因在诊断文件中。参考、原草案和已有输出不覆盖。

- photo：搜索粗框附近有支持的照片边界，强边框不等于照片边界。
- texts / overlays：有 locate_colors 时使用局部颜色与连通区域；否则尝试均匀背景上的局部对比度。保留多个分散笔触，不只取最大连通区。本版本不包含 OCR，不能纠正错字或辨认目标语义。
- 搜索触边时有限扩展；无前景、背景复杂、边界不稳定或照片边缘冲突时 needs_review，**保留粗框**，候选只在诊断中展示。cutout/photo_feather/unknown 不假装已完成语义分割。
- 背景槽保持满画布。仅 status=refined 的候选自动写入新草案；它表示算法已采用，visual_status 仍为 unreviewed。全流程不是生产抠图，被遮挡部分和透视也没有自动恢复。
- 打开 comparison.png/index.html：红为粗框，绿为采用候选，黄为待复核候选。核对照片内区、文字尾笔、分散装饰、整体范围以及误收进来的邻居。需要看局部时再 crop；仅调整失败项，避免每次遍历重做全部元素。

草案修改后可用 --reuse analysis/refine-01 复用旧定位缓存，输出改为新目录。输入图、相关元素、依赖/算法变化会失效。若手动修正并保留原框，使用 review_analysis.py 做普通复核即可，不必不断 refine。

~~~powershell
python scripts/review_analysis.py check --reference reference.png --input analysis/draft.json
python scripts/review_analysis.py preview --reference reference.png --input analysis/draft.json --output analysis/review-01
~~~

编号顺序为图片、文字、装饰；HTML 可分类筛选并查看照片归属及展开层级。结构校验与算法状态不等于视觉通过；所有疑点、原始草案和旧预览保留。本步交付有效草案、对照预览和待确认项，再按 [工程格式](project.md) 准备素材。
