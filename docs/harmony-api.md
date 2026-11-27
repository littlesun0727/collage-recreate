# 鸿蒙 App 对接协议 v1

## 1. 上传与首份响应

`POST /jobs`，`Content-Type: application/json`。图像传原始文件字节的 Base64 字符串。支持单帧 JPEG、PNG、WebP。请求体上限 96 MiB，单图最多 4000 万像素。

```json
{
  "reference": "<参考图 Base64>",
  "assets": [
    {"asset_id": "album-123", "image": "<客户静态图 Base64>"}
  ]
}
```

App 在一次任务内给每张客户图分配唯一、非空、可在本地解析的字符串 ID。文件名和相册路径均可变，绘制时按 ID 查原图。上传和本地绘制使用同一张原图并应用同一 EXIF 方向。

分析通过即返回 HTTP 202。响应包含 `job_id`、完整的 `analysis` 对象、`analysis_url`、`status_url` 和 `bundle_url`。可直接使用响应体中的分析，或从 `analysis_url` 获取独立的 `analysis.json`。分析失败返回错误，本次请求至此结束。重复 POST 会创建新任务；轮询使用 GET。

分析和绑定遇到 HTTP 502、503、504 时分别等待 2 秒、4 秒后重发同一请求，每次逻辑调用最多发送 3 次。分析的每条 description 以 100 字为限，接收端截短超长文字，缺失时用对象类型和 ID 作标签。响应中的常见标点错误在本地修补，再执行结构与几何校验。有效输入只需一轮模型分析。

分析提供 `reference_size: [宽, 高]`、`objects` 和自底向顶的 `layer_order`。对象 ID 在同一任务的分析和场景之间保持一致。GPT 分析按原始参考图宽高返回像素坐标。沿用 v10 自动策略的指定 Qwen 型号使用 0～1000 输入坐标，服务端换算为参考图像素。窗口和对象统一以像素交付，App 可以据此提前显示版式。

## 2. 取得最终包

轮询响应里的 `status_url`。状态为 `processing`、`ready` 或 `failed`。进入 `ready` 后 GET `bundle_url`，解压得到 `scene.json` 和 `layers/*.png`。任一分支失败，状态含 `error`，已成功的分析仍可读取。未就绪时下载请求返回 HTTP 409。

## 3. 绘制 scene.json

```json
{
  "schema_version": "harmony-collage-scene-v2",
  "canvas_size": [1000, 1500],
  "layers": [
    {"id": "bg", "kind": "background", "bbox": [0, 0, 1000, 1500],
     "source": {"type": "png", "file": "layers/layer_000.png", "placement": "tile", "offset": [0, 0], "size": [1000, 1500]}},
    {"id": "p1", "kind": "photo", "bbox": [100, 180, 900, 1100], "rotation": 0, "mode": "window", "shape": "rectangle",
     "source": {"type": "customer_asset", "asset_id": "album-123", "source_crop": [0, 0, 1, 1],
                "crop_center": [0.5, 0.5], "mirror_x": false}}
  ]
}
```

示例省略描述和可选样式。按数组顺序从底向顶绘制。坐标原点为左上角，x 向右、y 向下，画布整体等比缩放。

| 图层来源 | App 如何绘制 |
| --- | --- |
| `png` | 读取 ZIP 中 `file` 指向的 PNG，以 source.offset 为左上角、source.size 为宽高绘制。所有值均为参考画布像素，随整个场景统一缩放。对象的 bbox 和 rotation 可供选中、检查；重复应用会导致错位。固定文字、设计相框、贴纸和固定背景均按 PNG 绘制。 |
| `customer_asset` | 凭 `asset_id` 读取本地照片。`source_crop=[左,上,右,下]` 是客户原图的 0～1 坐标，`crop_center` 是裁后 cover 对齐位置，`mirror_x` 表示水平镜像。按对象的制作框 bbox 和顺时针角度 rotation 放置，应用 mode、形状、描边和渐隐等字段。 |

普通照片、照片型全画布背景以及关联出框前景都可以是 `customer_asset`。出框以单个 mode=popout 照片槽表示，bbox 和 popout_bbox 均为画布像素坐标。窗口和主体使用同一变换，主体使用交付包内 source.mask_file；可选 popout_above 指定延后叠加的层。动画时间轴、关键帧和编码参数需双方另定。

独立 `kind=text` 的框仅用于本地匹配和排字，360 请求只携带其他拆层目标。服务端检查文字下方相交的相框、贴纸和清底背景。中文按汉字数、英文按词数计，两种混写取较大值。含其他文字系统的字母、或全为数字和符号时按短文案处理；纯 ASCII 拉丁词按英文计数。达到 5 时，匹配到残留笔画就用 OpenCV inpaint 擦除，再把文字单独绘制一次。较短的文字若已完整嵌在可见载体上，保留载体原字，服务端在 lettering.json 记录归属，最终 scene.json 和 ZIP 省略该独立文字层；其他可读文字均用默认字体按 bbox、换行、颜色和对齐绘制。App 按 scene 的 PNG 直接合成。无法识读的文字层标记为 `unreadable`。

固定图层拆取允许 360 接收参考图中客户槽的范围作为清底上下文。发给 360 的像素来自参考图或上一批的残余画布。客户上传图只用于选材，最终 ZIP 只含场景引用的固定图层。纯色和透明画布在服务端直接生成 PNG。

固定 PNG 按最终非透明像素裁边，包括半透明阴影。全透明层从最终绘制清单和包中省略。历史 v1 包的 `full_canvas` 仍按原点和整画布尺寸绘制。
