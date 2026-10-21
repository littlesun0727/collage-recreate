## 照片描边与独立框体的统一归属

analysis 为每个 kind=photo 用已有 mode 初筛主要效果：cover 普通裁剪；cutout 实际去除摄影背景的主体层；feather 有可见透明混合的边缘渐隐；stroke 保留摄影背景、带窗口或主体描边的照片。抠像加描边仍可用 cutout；渐隐加描边可用 feather，并在 description 写清组合。圆形、心形等几何窗口不等于 cutout。只初筛类型及可见依据，不在 analysis 填具体窗口参数；photo_windows 根据原图核实，允许纠正初筛并在 reason 说明，不能把单选 mode 当作其他效果的禁用开关。

沿照片人物、物体或主体组合轮廓、需要随客户照片变化的描边属于 photo，由 photo_windows.stroke 的 target=subject 表达；完整摄影背景仍保留时 subject.mode=preserve，仅用分割蒙版定位描边，不去背景。即使轮廓是手绘的，也不能把参考主体连同描边提取为固定 overlay。description 记录围绕谁，例如杯子与耳机、手与杯子及吸管。独立星星、文字等固定装饰仍保留为完整 overlay；不能把主体轮廓和这些固定装饰合并提取，也不能因拆分而丢掉装饰。

照片几何窗口上的单色轮廓属于照片，由 photo_windows.stroke 绘制，不另建 overlay 或 frame。判断依据是轮廓沿窗口边界、没有独立纸底或图案；不要按“相框”这个名称分类。线条只有细微毛边时，当前允许实线内描边近似，窗口 reason 必须说明近似。

不依赖照片主体轮廓、需要提取其毛边、印花、独特造型或固定手绘笔触的单窗装饰相框，使用 kind=frame、method=extract，并以已有 photo_id 关联承载的 photo。frame 覆盖完整外沿，photo 表示内口，frame 在 photo 之上。能准确本地表达的规则纸底、胶带及普通装饰仍为 overlay；连续多窗载体沿用 overlay 与各 photo.parent_id，不拆碎整体。不能把有设计特征的固定框体降级为普通照片描边。photo_windows 读取 frame.photo_id 或旧 overlay 关联判断归属：框体自身的边线随框体保留，不重复画进 photo_windows.stroke；只有照片另有独立可见细边时才同时启用。

若 analysis 误把同一条简单照片细边建成 overlay，photo_windows 仍输出实际 stroke，并在 reason 指出重复对象 ID。boxes 只改框和补漏，不负责删除或重新分类已有对象；重复表达的问题保留为说明。若发现缺少复杂独立框体，窗口阶段只记录缺口，由 boxes 补充遗漏对象；不能用增粗 stroke 冒充框体。

没有可见照片描边就关闭 stroke。完整摄影窗口内保留人物周围的环境；有人物、处于前景或有白边都不等于抠主体。分析阶段不写旧 photo.style 的相纸、圆角、描边或四边羽化参数；形状、主体处理和逐边渐隐统一交给 photo_windows。
