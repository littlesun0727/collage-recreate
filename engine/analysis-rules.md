# 参考分析与独立绑定

v6 的首版流程为 analyze → photo_windows → boxes → materials → bind → build → screen（按需）。参考分析只看参考图，客户素材理解与绑定独立进行。

## 参考分析的唯一规则来源

完整的坐标、内容归属、局部组合、完整范围和 local/extract 判断见 [参考图制作规则](../../collage_v6/design_policy.md)。该文件进入 analyze 的实际 prompt；boxes 使用简短的改框、补框说明，保留已有分析决策。

照片描边与独立框体的判断见 [窗口归属规则](../../collage_v6/photo_window_ownership.md)，同一段规则进入 analyze 和 photo_windows。详细窗口参数由 photo-windows.json 管理，boxes 保留已有参数。

analyze 使用的本地绘制能力见 [参考分析绘制参数](../../collage_v6/reference_drawing.md)；历史参数及交付后操作见 [完整绘制说明](drawing.md)，不混入新分析的参数要求。

## analysis.json 与 text-units.json

analysis 输出参考内容、对象 ID、原图 bbox、rotation、layer_order 和关联。照片 description 简述主体/人数、场景、景别、姿势、主体位置及留白，为独立绑定提供观察依据；不能只写“客户照片”。固定素材 description 记录可见成员及关系，不能用文字代替完整框。

每个对象必需 id/kind/bbox/label/description。photo 用已有 mode=cover/cutout/feather/stroke 初筛主要效果，在 description 说明组合及依据；形状、描边、渐隐等最终参数交给 photo_windows，不写 appearance=polaroid、style.card 等兼容字段。mode=stroke 表示保留照片并描边，不等于抠像；cutout 或 feather 也可组合描边。需单独提取的单窗装饰相框用 frame、method=extract，必填 photo_id 指向承载照片；frame 是外沿、photo 是内口，相框在照片之上。简单几何单色轮廓属于 photo；规则本地纸底、胶带、普通装饰和连续多窗载体仍用 overlay。普通 text 必需 text/text_status；固定艺术字及带底板标签用整体 overlay，框体自身印字随 frame，并在 text_units 记录 OCR，避免重复渲染。

背景分类、文字记录仍服从现有 v6 schema 和业务校验；与 v5 的保留差异见 docs/vlm-prompts/v5-alignment-notes.txt。当前阶段不读取客户指令，text_units.edit_requested=false。严格 JSON 的实际字段以输出 Schema 为准。

## bindings.json

读取prepared/catalog.json的实际asset ID，查看联系表后选择素材。每个photo恰好一个绑定，包括铺底的背景照片。普通独立照片槽优先选择不同素材；同一客户照片的背景与关联抠图前景需要保持一致、或素材数不够时允许复用，并在 reason 说明；源文件由程序核验。

```json
{
  "schema_version":"collage-bindings-v1",
  "photos":[{
    "slot_id":"photo_main",
    "asset_id":"asset_012345abcdef",
    "crop_center":[0.5,0.5],
    "source_crop":[0,0,1,1],
    "mirror_x":false,
    "reason":"按完整源图居中适配照片槽，不镜像"
  }],
  "texts":[]
}
```

示例asset ID必须替换为catalog的真实值。每个绑定必须显式填写crop_center、source_crop、mirror_x，不得省略；不调整时也必须分别写出[0.5,0.5]、[0,0,1,1]、false。示例展示未指定额外裁切且不镜像的完整绑定，实际值须根据客户素材与照片槽决定，不能直接照抄。

crop_center和source_crop使用0..1的局部坐标；source_crop按[左,上,右,下]填写，只有确需水平镜像时mirror_x才为true。reason描述的裁切与镜像必须落实到这些字段，不能只写文字理由。cutout的source_crop相对于已抠出主体的透明边界；本阶段尚未看到实际抠图边界时，遵守绑定提示词中的完整保留主体及linked_to规则。

不可辨文字可在texts中补`{"object_id":"caption","text":"新创作短句","origin":"generated","reason":"替代不可辨内容"}`；origin为user/generated，不能冒充识别原文或猜事实姓名日期。已知文字不由bindings覆盖。

## 提取前叠框纠偏

程序 validate 生成当前叠框；boxes 输入干净参考图、当前分析叠框图和 analysis/text_units。已有 photo_windows 随上下文传入以保持数据完整，不传窗口效果预览。

先逐个修正已有对象的 bbox/rotation，补全耳朵、尾部、气泡、文字边缘等漏框部分；再通看参考图，确有完整对象遗漏才新增。已有 ID、描述、分类、制作方式、样式、关联及相对层级保留。程序拒绝删除、改名和其他越界改写。

有修改返回 replace 和完整修正数据，但只改允许的部分；无修改返回 keep。text_units 只同步 bbox 或补遗漏记录；已有窗口参数原样保留，新增 photo 才补对应窗口项。新增对象插入 layer_order，旧对象不重排。重复对象、错误分类或窗口参数问题可在 summary 说明，本步骤不修正这些问题。

程序在替换后重新校验并生成预览。当前工作流仍只执行一轮 boxes；没有新增改框后的第二次 VLM 验证。审核失败保留告警并沿用已有分析，不能把降级视为完成了视觉审核。
