# 一次整图检查，记录问题项

集中打开previews/comparison.png，以及result.extraction_sheets列出的非空提取表；对照中已包含完整成图，无需例行再单独打开一次final。需要看细节时再放大局部。

先检查客户照片是否齐全、主体清楚、无拉伸或错误遮挡；再看主要布局、文字和关键装饰。客户人物/场景不同是正常替换。首版可接受允许细微字体、纹理、阴影差异；明显占位、缺字、关键装饰丢失和参考人物/背景污染均需修改。分割返回成功不代表视觉合格。

Reveal回填重点看：相框原照片是否残留、窗口边沿是否漏出参考人物、照片是否填满；装饰是否带入照片背景；文字是否叠印；完整贴纸成员是否被错分到其他层并盖住。返回名称不证明像素归属，必要时对照同批原始层。result中的unresolved缺口不能pass。assets/reveal/index.json记录复用、窗口像素提示、组合和按需清窗；提示不等于污染，素材保留也不等于视觉通过。先查看result.asset_gate_summary及非空疑难检查表，再集中处理；不逐素材调用模型。程序通过不证明图案语义完整。

填写review.json，只列有问题或需要说明的对象。render_id从本次build返回或result.json读取，是当前scene和成图绑定的短版本标识；代码自动附加完整哈希。

```json
{
  "schema_version":"collage-review-v1",
  "render_id":"0123456789abcdef",
  "checked_entire_composition":true,
  "verdict":"needs_changes",
  "summary":"照片完整；标题偏大，主贴纸分离污染",
  "items":[
    {"id":"title","action":"adjust","reason":"字号偏大","changes":{"style":{"font_size":32}}},
    {"id":"sticker","action":"generate","reason":"参考背景污染，需重新制作"}
  ]
}
```

示例render_id必须替换为当前实际值。确认看过整图才填checked_entire_composition=true。没有需修改项时可以pass且items=[]，代码仍拒绝占位、未知文字、几乎完全遮住的照片或来源异常。pass按本次任务标准判断，首版通过不宣称已达逐细节高相似度。旧格式的两个完整哈希仍支持。

actions为keep/adjust/generate/unresolved；adjust可修改bbox、rotation、style及crop_center/source_crop/mirror_x。改文案或素材必须正式更新analysis/bindings并build。基础圆角、描边、曲线、相纸边距等参数问题优先本地调整；复杂插画/摄影材质才考虑生成。不要把代码功能缺项统一归为必须生图。

提取层已包含参考位置、旋转和阴影，不再次加变换。带共享窗口的相框/照片要改几何时，集中修改analysis后重新build；仅调客户照片的crop_center/source_crop/mirror_x仍可apply。重复文字先核实归属；仅补embedded_in不会取消独立文字，需按素材门禁确认实际嵌入内容，或舍弃污染载体并用简单shape重画。现有缓存对应旧bbox时会回退并记录原因，不把旧图硬塞进新框。

`review --run RUN --file RUN/review.json`只登记结论。只要求首版测量时到此交付；明确要求修复时可执行离线recover，previews/first.png始终保持冻结。

只有用户明确要求像素精修时才使用以下流程；默认首版用一次screen筛选与简单补画。精修任务先处理返回素材问题：按[局部恢复](recovery.md)执行`recover --run RUN --file REPAIR.json`，离线恢复装饰组合或只清理已确认残留的窗口。恢复后再用`apply --run RUN --file RUN/review.json`集中调整参数；必要的非照片生成用`generate --run RUN --ids ... --allow-remote`。每次新成图用新的render_id复核。yibu失败读取generation记录，保留成功资源，避免无限重试。需要合并相邻非照片生成对象时可加--group，程序会拒绝穿插其他重叠层的融合。
