# 整图复核与一次本地排版修正

查看当前 `previews/comparison.png`、`result.review_regions` 列出的局部对照，以及非空 `result.extraction_sheets`。局部对照按相交区域自动选取，最多四处，左为参考、右为当前；它不是错误检测，也不覆盖所有重叠。整图中另有疑点时，再看对应区域。看图与转发均使用 `original`。

## 先判断哪些问题能直接修

先看三个关系，再看文字和素材完整性：

1. **照片与相框谁压谁。** 对照每处交叉边缘，按整张相片的前后关系排列照片、纸底和边框；不能把所有边框统一压到所有照片上。检查窗口是否填满、相纸是否错位。
2. **装饰是否误挡人物。** 换了人物后，原坐标不保证关系正确。参考中没有遮脸而当前遮住眼睛或脸部时，保留现有素材，调整人物位置/大小、窗口内裁切或独立装饰整体位置。不拆开装饰、不改星形/曲线/描边来绕开人物，也不重新绘制或生成素材。
3. **重叠与出框是否保留。** 参考中位于前方的相片、穿过分隔线的人物，当前是否仍有同样的前后关系。已有对象就调整位置或层级；缺少前景对象、蒙版或素材则记录缺口，不能靠排序假装修好。

发现本轮可通过现有素材和支持参数修复的排版问题，必须集中执行一次 `apply`，不能只写建议或直接判 pass。没有问题无需制造调整；超出能力或调整后仍有问题，写明对象、差异和限制。纸纹、圆点、普通字体与阴影可按首版近似，关键遮挡、缺字符号、图案轮廓和越框关系不能按细节近似放行。

- **排版问题：** 图层前后、普通照片位置/大小/角度、照片裁切、本地相纸布局、独立文字位置/字号。集中用一次 `apply` 修正。
- **素材问题：** 旧照片残留、多余内容、缺失或不完整图案、嵌入文字重复。先按 [筛选与补画](asset-gate.md) 做必要的一次 screen，再调整排版；复杂缺失保留说明，不重新提取或生图。

程序门禁接受不代表图案完整；照片可见也不证明遮挡正确。还要对照本地绘制层：手绘装饰或固定艺术字若被通用图形、普通字体替代，属于制作方式错误，不能以“允许近似”判pass。按 [分析与绑定](analysis.md) 记录具体对象及差异，不为修正层级继续简化装饰。检查表中的疑点要对照实际像素，不能凭素材名或透明度统计判断。重复嵌入文字通过 screen 确认归属，不靠改层级隐藏问题。

## 有必要时，一次 apply

写 `RUN/adjustment.json`，沿用 review 格式，使用当前 `result.render_id`。仅调整层级时可以 `items: []`；其他修改只列相关对象。

```json
{
  "schema_version": "collage-review-v1",
  "render_id": "当前实际的16位render_id",
  "checked_entire_composition": true,
  "verdict": "needs_changes",
  "summary": "大照片应在相纸后；相纸与照片整体向右移动",
  "layer_order": ["background", "large_photo", "card_photo", "card"],
  "items": [
    {"id": "card", "action": "adjust", "reason": "整体向右移动20像素",
     "changes": {"bbox": [120, 100, 420, 500]}},
    {"id": "card_photo", "action": "adjust", "reason": "保留人物头部",
     "changes": {"crop_center": [0.5, 0.35]}}
  ]
}
```

示例 ID、bbox 和 render_id 必须替换。上例假设只有四个对象；实际 `layer_order` 从当前 `scene.json` 取完整列表后调整，所有 ID 恰好出现一次，从底到顶排列。不改顺序时省略该字段。

| 调整目标 | 写法与范围 |
|---|---|
| 前后关系 | 顶层 `layer_order`；提取层也可排序，但不能拆开已经嵌入同一素材的内容 |
| 未锁定窗口的照片（含 cutout）/独立文字 | 对象 `changes.bbox`、`rotation`、受支持的 `style`；bbox 为原图像素 |
| 窗口内照片 | `crop_center`、`source_crop`、`mirror_x`；不移动相纸 |
| 独立提取装饰 | `changes.bbox` 的四个坐标加同一平移量，保持宽高；复用原始透明层，内部成员一起移动。不能改 rotation/style、拉伸或拆分；关联照片窗口、融合组及有独立嵌入子层的载体仍受保护 |
| 整张本地相纸 | 修改 local 载体的 `bbox`、`rotation`；通过 `photo_id` 或照片 `parent_id` 关联的照片自动平移、等比缩放和旋转 |
| 固定提取相框 | 可改层级和窗口内照片裁切；本轮不移动/变形提取像素及其固定窗口 |

整张相纸联动时只改载体几何，不再给关联照片同时写 bbox/rotation；允许同时改照片裁切。改变 bbox 宽高时保持载体宽高比，不进行非等比拉伸。多窗载体的已关联照片一同跟随，其他文字/装饰不自动联动，需要移动时明确列出。局部照片窗口单独改变意味着调整内口，不能把它当作整张相纸移动。历史窗口、提取相框和融合生成素材仍受保护，遇到限制保留问题，不绕过校验。

```text
PY SKILL/scripts/workflow.py apply --run RUN --file RUN/adjustment.json
```

apply 只更新当前 scene 并本地重新合成，复用已有提取素材，不提交360或生成请求，`previews/first.png` 保持不变。决定存入 `reviews/revision-*.json`；analysis/bindings 保留初始设计，调整后的状态以 scene 为准。之后不要重新 build，否则会从初始设计重编译并丢失调整。无需手工编辑 scene 或 result。

## 确认新图，登记最终结论

若执行了 screen/apply，重新读取 result，查看最新完整对照图及修改处的局部对照，确认目标关系已修正且没有新遮挡。使用新的 render_id；summary 简述改了哪个对象、修正是否生效，未修好则在 items 保留具体差异。默认只集中排版修正一轮，不再进入循环。

写新的 `review.json`：沿用上述基础字段，移除 `layer_order` 和已执行的 changes，只列剩余问题或必要说明；无问题可 `verdict: "pass", items: []`。确实看过整图才填 `checked_entire_composition: true`。旧 render_id、待执行调整、关键缺失、旧照片污染、重复内容或几乎完全遮住的客户照片不能通过。

```text
PY SKILL/scripts/workflow.py review --run RUN --file RUN/review.json
```

review 只登记，不执行调整。首版通过不代表逐细节高相似度；如实报告允许的近似和剩余问题。只要求测量未经调整首版时，跳过 apply。

仅当用户明确要求素材精修，才按 [局部恢复](recovery.md) 选择 recover 或非照片 generate。recover 要在 apply/generate 前执行；不要为修改层级而进入素材修复。
