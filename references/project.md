# 静态工程格式 v1

这是运行时格式。开发审计与阶段计划见 PLAN.md；制作时只需按需读取本文。

## 输入和任务

`init --input request.json --task <目录>` 导入一张静态参考、图片、字体和准确文案。输入路径相对 request.json，或显式绝对路径；原文件只读。图像归一为朝向正确的 sRGB RGBA，原始文件保存在任务 private/originals，导出时不携带原始文件。

请求示例：

```json
{
  "version": 1,
  "canvas": {"width": 800, "height": 1000, "background": "#f4f0e8"},
  "reference": "reference.png",
  "assets": {
    "photo": {"kind": "image", "path": "customer.jpg"},
    "font": {"kind": "font", "path": "font.ttf", "redistributable": true, "license": "LICENSE.txt"}
  },
  "texts": {"title": "我的假日"},
  "permissions": {"image_generation": false, "max_requests": 0, "max_requests_per_asset": 1, "upload_asset_ids": []}
}
```

任务的单一布局文件是 project.json；version=1，revision 从 1 开始。资源是任务内相对路径并带 SHA-256；禁止越界、URI、符号链接逃逸和隐式外部路径回退。图片资源与字体资源独立，reference 是参考资源 ID，不会自动变成背景层。

## 坐标、层级和图层

单位为输出像素，原点为画布左上，x 向右、y 向下。元素 x/y 是未旋转框的左上角；width/height 是旋转前尺寸。rotation 为绕框中心顺时针角度。数组从后向前叠放，不另设冲突的 z 字段。

分组为平面数组 `{"id":"card","dx":0,"dy":0}`。组内元素仍写画布坐标；统一放置函数只增加 dx/dy，不要求主控减父坐标。第一版不支持嵌套组、组旋转或组裁切。

图层公共字段：id、type、x、y、width、height，可选 group、rotation（0）、opacity（1）、allow_clip（false）。图层超出画布默认报错；有意裁出画布需显式 allow_clip=true。元素 ID 和组 ID 不重复。

图片层：

```json
{"id":"picture","type":"image","asset":"photo","x":60,"y":140,"width":300,"height":400,"fit":"cover","focus":[0.5,0.5],"group":"card"}
```

- fit=cover 等比例填满、裁去溢出；fit=contain 等比例完整容纳，余区透明，不拉伸。
- focus 控制 cover 多余部分的裁去比例，0 左/上，1 右/下；不是自动识别人脸。
- 可选 crop=[left,top,right,bottom]，相对于归一化原图的 0–1 区间，先裁切再 fit。
- 可选 mask_shape=ellipse/rounded；rounded 用 radius 像素。或 mask_asset 引用与图层框适配的 alpha 遮罩，需有实际透明像素，RGB 白底不算遮罩。
- 素材保留原始 alpha。遮罩与 opacity 乘入 alpha 后才旋转和合成。

文字层：

```json
{"id":"heading","type":"text","text":"title","font":"font","x":60,"y":40,"width":600,"height":80,"font_size":42,"color":"#242424","align":"left","wrap":true,"line_spacing":1.2}
```

text 引用 texts 中的准确字符串；font 引用字体资源。支持显式换行、自动折行、中英文字形、left/center/right 对齐。字号不自动缩小；缺字体、缺字形、文字超出框均报错，不替换字体或静默截断。字体集合可在字体资源中用 font_index 指定字面。

## 修改

`apply --input patch.json --task <目录>` 在任务锁内校验 expected_revision；通过后 revision 加 1，原子替换 project.json。冲突或校验失败保留旧工程。

```json
{
  "expected_revision": 1,
  "texts": {"title": "新的标题"},
  "assets": {"photo": {"kind": "image", "path": "replacement.jpg"}},
  "groups": [{"id":"card","dx":12,"dy":-8}],
  "element_updates": {"heading": {"font_size": 38}}
}
```

texts/assets 按 ID 合并替换；提供 elements/groups 时替换完整数组；element_updates/group_updates 按已有 ID 更新。更新不能重命名 ID。图片替换重新导入源文件，其他素材保持不变。非空布局在保存前检查资源、文字和边界；初始空工程可保存，check/render 会报告 EMPTY_LAYOUT。

## 渲染、导出与限制

render/check 均不调用生图。渲染输出 PNG，结果始终标记视觉待审。技术校验不能证明接近参考。export 携带使用中的资源、project.json、成图和独立 runtime 代码，不携带服务配置、请求响应、参考图和未使用的客户资源。

字体默认不可分发；仅 redistributable=true 且提供许可文件时复制字体和许可。否则导出字体需求清单，接收者用 `--font 字体ID=本地字体路径` 显式提供字体；缺字体验证不能跳过。位图内部笔触不能独立编辑。

第一版不包含时间轴、视频、PSD 或网页编辑器。图层混合使用标准 source-over；不支持透视、复杂混合模式和自动抠图。
