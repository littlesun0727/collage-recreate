# 任务脚本绘图接口

任务脚本使用 `build_v2_core`，run-script 自动设置本模块的导入路径。运行方式见 [运行环境](../references/runtime.md)。

常用接口：

| 接口 | 用途 |
|---|---|
| script_api.drawing_canvas(target_box, content_box, padding=4, scale=1) | 根据原范围与实际笔触范围分配透明画布，返回 image 与交付映射 |
| script_api.reference_to_layout(point, reference_size, canvas_size) | 参考像素转布局像素 |
| script_api.layout_to_local(point, target_box, raster_size) | 布局像素转对象局部像素 |
| script_api.local_to_layout(...) | 对称逆变换 |
| script_api.stroke_pixels(layout_width, target_box, raster_size) | 同比栅格下统一布局线宽 |
| script_api.pixel(value) | 最近整数，半值远离零 |
| script_api.render_text(size, text, font_info, font_size_px=..., color=...) | 测量真实字形、缩小适配，返回 (PIL.Image, 参数字典) 二元组 |
| primitives.shape_path / handdraw.wobbly_path | 可选基础几何和可复现笔触 |
| drawing.draw_resource(size, recipe) | 可选的参数化几何绘图工具 |
| drawing.feather_resource(size, recipe) | 生成独立羽化遮罩及预览 |

`render_text` 参数还包括 align、line_spacing_px、padding_px、min_font_size_px。字号以本次导出栅格像素为单位，任务记录目标 box。精确字体由 task.fonts 的 ID/路径/摘要/index 决定；不静默换字体。

`render_text` 返回图像与测量记录的二元组。保存图像，将测量记录放进交付的 text 字段：

```python
image, text_record = render_text(size, text, font_info, font_size_px=36, color='#204791')
image.save(output_dir / 'caption.png')
output = {'key': obj['key'], 'file': 'caption.png', 'target_box': obj['target_box'],
          'raster_size': list(image.size), 'text': text_record}
```

`stroke_pixels` 返回浮点数。传给 Pillow 的 line/rectangle 等整数 width 参数前，使用 `max(1, math.ceil(width))` 向上取整（需 `import math`）；坐标仍可保留小数。

draw 先在布局坐标中计算路径及笔触范围，再分配画布。content_box 包含线宽、曲线和抖动的实际范围，padding 为布局像素；已有 PNG 被裁掉的部分不能靠补透明边恢复。示例：

```python
import math

image, mapping = drawing_canvas(obj['target_box'], content_box, padding=4, scale=2)
local_points = [layout_to_local(p, mapping['paint_box'], mapping['raster_size']) for p in points]
width = max(1, math.ceil(stroke_pixels(layout_width, mapping['paint_box'], mapping['raster_size'])))
# 使用 local_points 和 width 绘制，再保存 PNG。
output = {'key': obj['key'], 'file': 'drawing.png', **mapping}
```

原 target_box 不改，paint_box 记录完整图片的实际放置范围；两者的差就是位置偏移。不要裁紧或缩放塞回旧框。绘制完使用现有组合预览检查位置与连接，按需要局部修正。

自由绘制可用 Pillow ImageDraw 或计算曲线，参考 [single_draw.py](examples/single_draw.py)；字体测量比较见 [text_measure.py](examples/text_measure.py)。完整单元任务流程见 [production-task.md](examples/production-task.md)。

相框在透明画布画描边，中心保持透明；接收器不修改图像。使用 draw_resource 时，recipe 的 openings 通常为 []；确需特殊遮罩时明确记录。生成相框遵守 [生成规则](prompts/generate.md) 的内外同色键与内容范围。

任务脚本写入 [protocol.md](protocol.md) 定义的 delivery.json。测得的坐标、笔触和字号放入 parameters，依赖的本地辅助脚本列入 dependencies。单元模式每个 owned_key 交付一次。
