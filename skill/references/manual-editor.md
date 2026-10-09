# 手动画布编辑（仅客户明确操作时）

工作台 `--enable-editor` 提供确定性编辑入口，调用 `scripts/manual_edit.py`，不调用SDK、提取、生成或build。原分析成图流程默认不进入该入口。

对象可选 `editor_transform: {x, y}` 是参考坐标系中的最终平移，默认0。bbox、rotation、extracted_offset、照片裁切和素材文件保持原语义。实际展示位置为原渲染位置加该偏移。渲染时照片与自身遮罩一起移动；明确关联的相框/照片通过编辑能力表整组移动。独立文字可更改text，嵌入图片和融合素材内文字不可独立编辑。

手动修改在候选副本中执行，经过版本与素材校验后发布。手动版本的visual_review为空、renders_verified为false，记录origin=manual；不能编造模型看图结果。后续Agent继续修改必须读取最新scene，并保留已有editor_transform，不能把它重复计入bbox。仍按现有要求对新图进行真实复核。

旧任务没有该字段时保持原合成路径；`capture_layers` 默认关闭，只在编辑缓存副本导出RGBA图层。手动编辑结束后不要运行build覆盖编辑结果；若用户明确要求重新分析制作，应作为新制作范围处理。
