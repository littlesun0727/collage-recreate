# 拼贴 v11 前端对接说明

第一次响应就能显示版式，最终场景就绪后才能把用户的照片放入对应窗口。先打开这组展开的教学样例：[首包 analysis.json](frontend-example/analysis.json) · [最终 scene.json](frontend-example/scene.json) · [固定背景 PNG](frontend-example/layers/layer_000.png)。三份文件演示同一张卡片：底层是米色背景，上层是五角星照片窗。样例中的相册 ID 由前端用本地图片代入。

## 1. 前端给服务端什么

向 `POST /jobs` 发送 `application/json`，请求体如下。Base64 示例值需替换为实际文件编码。

```json
{
  "reference": "<参考图原始文件字节的 Base64>",
  "assets": [
    {"asset_id": "album-001", "image": "<用户照片原始文件字节的 Base64>"}
  ]
}
```

| 字段 | 谁提供、如何使用 |
| --- | --- |
| `reference` | 前端提供一张要复刻的版式参考图。服务端从中识别窗口、遮挡顺序和固定装饰。 |
| `assets[]` | 前端提供候选客户照片，服务端负责选片。每个独立照片槽需要一张不同的客户图，照片型背景也占一个槽。相同图像换 ID 仍可能被判重。无照片槽时可传空数组。素材不足会在后续绑定阶段失败。 |
| `asset_id` | 任务内唯一、非空且非纯空白的字符串。前端保留 ID 到本地原图的映射，最终按原 ID 取图。 |
| `image` | 和参考图一样，传单帧 JPEG、PNG 或 WebP 的纯 Base64；去掉 data URL 前缀及换行。单图最多 4000 万像素，整个 JSON 请求体最多 96 MiB（含 Base64 开销）。本地解码时应用与服务端相同的 EXIF 方向。 |

## 2. 首包与 analysis.json

分析完成后 POST 返回 HTTP 202。响应中的 `analysis` 就是完整分析对象；`analysis_url` 可再次获取同一份 JSON。`job_id` 标识本次任务，`status_url` 用于轮询，`bundle_url` 用于最终下载。以上 URL 是相对路径，基于服务地址解析。重复 POST 会创建新任务。

| 顶层字段 | 格式与用途 |
| --- | --- |
| `schema_version` | 固定字符串 `collage-analysis-v1`。 |
| `reference_size` | `[W,H]`，方向校正后的参考图像素宽、高，也是最终画布大小。 |
| `objects` | 对象数组，按 `id` 建索引。 |
| `layer_order` | 对象 ID 数组，从底向顶。按此数组绘制分析预览。 |

每个对象必有 `id`、`kind`、`bbox`、`label`、`description`。ID 在两次交付间保持一致。后两项是展示名和内容说明。`bbox=[left,top,right,bottom]`，单位是参考画像素，左上为原点，x 向右、y 向下。对窗口，这是旋转前的制作框，宽、高分别是右减左、下减上。`rotation` 为绕框中心顺时针旋转的角度，缺省按 0°。整个画布映射到屏幕时统一等比缩放。

| kind | 有关字段 | 前端关注点 |
| --- | --- | --- |
| `background` | `background_type`、`method`；纯色底有 `style.fill` | 铺满画布。类型 `photo` 用本地照片，其余按最终 PNG。常见固定底为 `solid_color`、`reference_clean`。 |
| `photo` | `mode`、窗口和效果字段见第 3 节；关联出框有 `linked_to` | 首包可以先画槽位轮廓，最终按绑定取照片。 |
| `frame` | `method:"extract"`、`photo_id`（一个照片 ID 或 ID 数组） | 装饰相框是独立固定层，有自己的层序。 |
| `text` | `text`（字符串或 null）、`text_status`（known / unreadable），可带 `style` | 可用于显示文字信息，成品按该层 PNG 合成。 |
| `overlay` | 装饰、贴纸、固定艺术字等，可带 `style` | 按最终固定层 PNG 合成。 |

`parent_id` 等可选关联和 `method` 描述分析对象。最终选择绘制分支用 `source.type`。完整字段、取值和条件约束见 [analysis JSON Schema](../contracts/analysis.json)。

## 3. photo 蒙版给什么，怎么画

窗口参数直接位于照片对象顶层，场景层会保留这些字段。客户端按参数生成路径。普通 photo 的 `mode` 缺省为 `window`；照片背景按 `bare`。三种模式的区别如下。

| mode | 本地绘制行为 |
| --- | --- |
| `window` | 保留原照片背景，按窗口路径裁切，应用可选描边和边缘渐隐。 |
| `bare` | 保留原照片背景，按制作框摆放，无异形蒙版。 |
| `cutout` | 本地分割客户图主体，用主体 alpha 合成，可沿主体外缘描边。独立抠图使用整幅源图；关联出框按下文复用底图变换。 |

**七种窗口的字段与路径**。下表 JSON 均为 photo 对象的局部字段。令局部框宽高为 `w,h`，短边为 `d=min(w,h)`。局部单位坐标 `[u,v]` 映射为 `[u*w,v*h]`，完成轮廓后再应用整层旋转。

| 窗口 | 可能收到的字段 | 局部路径画法 |
| --- | --- | --- |
| 矩形 | `{"shape":"rectangle"}` 或省略 shape | 从 (0,0) 画到 (w,h)。倾斜由 rotation 表达。 |
| 圆角矩形 | `{"shape":"rounded_rect","radius":0.08}` | 四角等半径圆弧，像素半径 = radius × d；缺省 0.06，范围 0～0.5。 |
| 圆形 | `{"shape":"circle"}` | 圆心 (w/2,h/2)，半径 d/2。长方框中居中内接。 |
| 椭圆 | `{"shape":"ellipse"}` | 同一中心，横、纵半轴分别为 w/2、h/2。 |
| 心形 | `{"shape":"heart"}` | 使用固定的单位心形路径，沿 x、y 分别缩放到框内；凹口在上、尖端在下。下文给出控制点。 |
| 五角星／多角星 | `{"shape":"star","star_points":5,"inner_ratio":0.5}` | 默认五角、内外半径比 0.5；可取 3～12 角，比值 0.1～0.9。首个外尖朝上，内外顶点交替，闭合填充。 |
| 多边形 | `{"shape":"polygon","points":[[0.1,0],[1,0.1],[0.85,1],[0,0.9]],"corner_radii":[0.08,0.08,0,0]}` | 依数组顺序连接 3～32 个局部点并闭合，点各分量 0～1。四点窗口可带逐角半径，和顶点一一对应，0 表示尖角。四边形可能来自透视、裁断的矩形；直接使用返回的点。 |

**直接用场景层生成蒙版**。七种形状分别由同名的 `rectangle_outline`、`rounded_rect_outline`、`circle_outline`、`ellipse_outline`、`heart_outline`、`star_outline`、`polygon_outline` 函数生成局部像素轮廓。传整个照片层时，用统一入口 `window_outline` 即可。以下示例生成与画布同尺寸的黑白窗口蒙版，白色保留照片，黑色隐藏照片。它演示硬边裁剪，描边和渐隐需另行合成。[查看实现](../collage/layout/window_shapes.py)

```python
from PIL import Image, ImageDraw
from collage.layout.window_shapes import window_outline

# scene 来自 scene.json，layer 是其中一个 mode=window 的照片层。
points = window_outline(layer)  # 已包含位置和旋转，单位为画布像素
mask = Image.new("L", tuple(scene["canvas_size"]), 0)
ImageDraw.Draw(mask).polygon([tuple(point) for point in points.tolist()], fill=255)
```

**复现轮廓的细节**

- 星形设 `n=star_points`，依次取 `i=0…2n−1`，偶数点 `r=1`，奇数点 `r=inner_ratio`。单位顶点为 `u=0.5+0.5*r*sin(i*π/n)`、`v=0.5−0.5*r*cos(i*π/n)`。直接映射到框，保留后端约定的宽高比例。
- 心形从 (0.5,0.18) 起，右半边依次画四段三次 Bézier。每行依次为两个控制点和终点。左半边将右半边按 x→1−x 镜像并反向连接，最后闭合。严格比对时按后端函数每段采样 80 点、右半边按弧长重采样、镜像拼成 240 点，再按轮廓极值归一化。[七种窗口的轮廓源码](../collage/layout/window_shapes.py)

```text
(0.535,0.18)  (0.57,0)     (0.76,0)
(0.91,0)      (1,0.11)     (1,0.28)
(1,0.56)      (0.68,0.84)  (0.525,0.98)
(0.515,0.989) (0.507,1)    (0.5,1)
```

- 四边形逐角圆弧先在像素空间求边长。半径基准是两组对边平均长度中较小的一组；逐角乘 `corner_radii[i]`。相邻圆弧的切线长度超过共用边时，按同一比例缩小所有半径。
- `stroke` 存在即启用。例如 `{"width":0.02,"color":"#FFFFFF","placement":"outside"}`。宽度及留白 `offset` 都按照片短边计，默认留白 0。位置可为 inside、center、outside，默认 outside，center 要求留白 0；抠图沿主体外侧。线型 `pattern` 为 solid（默认）、dash 或 dot。参考实现使用圆线帽，以线宽为单位的线段／间隔分别为 1.7／1.5 和 0.12／3，换算为短边比例后各限制在 0.001～0.5。
- `fade` 仅用于 window。例如 `{"top":0.1,"direction":"inside"}` 表示顶边内侧 10% 窗高是透明度过渡区，越靠外越透明。上下按 h、左右按 w 计，缺边为 0，各边最大 0.5。inside 默认，相对两边之和最多 0.9。outside 保持窗口内部不透明，照片向外延伸并渐隐；实现时要预留外扩像素，外描边也需留空间。蒙版和照片一起旋转。

**场景绘制伪代码**（函数名代表前端需要实现的操作）

```text
按 scene.canvas_size 建立逻辑画布
按 scene.layers 原顺序逐层合成：
    若 source.type == "png"：在 source.offset 处按 source.size 绘制 source.file，进入下一层
    用 source.asset_id 查本地原图并校正 EXIF
    若为 linked_to 组合：先按前景 bbox 匹配分割主体，缓存底图和前景共用的缩放、位移及镜像
    普通窗口或 bare：按 source_crop 取源图子矩形，等比 cover 到制作框
        scale = max(w / 裁后宽, h / 裁后高)
        放置偏移 = ((w - 裁后宽*scale)*crop_center.x, (h - 裁后高*scale)*crop_center.y)
    按 mirror_x 应用水平镜像；组合内用同一变换
    window：生成局部路径和渐隐 alpha，合成照片，再绘制窗口描边
    cutout：合成分割主体及其可选外描边；关联前景复用组合变换
    bare：合成保留背景的照片
    将局部图层按 bbox 中心和 rotation 放入画布
```

`source_crop=[l,t,r,b]` 是已校正方向的**原图**上的 0～1 范围。`crop_center=[x,y]` 是裁后 cover 的对齐比例，0 靠左／上，1 靠右／下。关联前景的 `linked_to` 指向底图 ID，两层共用 `asset_id/source_crop/crop_center/mirror_x`；前景 bbox 用来匹配主体范围。应先求组合变换，再按两层各自的层序合成，才能让相框等元素夹在中间。具体分割模型、渐隐插值曲线和抗锯齿策略需在客户端联调时对齐。

## 4. 最后回传什么

轮询 `GET status_url`。HTTP 200 状态体为 `{"status":"processing"}`、`{"status":"ready"}` 或 `{"status":"failed","error":"原因"}`。ready 后 GET `bundle_url` 返回 `application/zip`。下图按展开后的内容展示，示例目录可直接浏览。

```text
首次交付   analysis.json                布局，可由 analysis_url 单独获取
最终交付   scene.json                   最终画布及图层数组
           layers/layer_000.png         示例中的固定背景
           layers/layer_001.png …       有额外固定层时逐层提供
```

`scene.json` 顶层固定为 `schema_version:"harmony-collage-scene-v2"`、`canvas_size:[W,H]`、`layers:[...]`。拆层服务漏回的装饰或相框直接跳过，其余层按分析中的相对顺序交付。前端以 `scene.layers` 为最终绘制清单。每层的 source 有以下两种形式。

| source.type | source 完整示例 | 资源在哪里 |
| --- | --- | --- |
| `png` | `{"type":"png","file":"layers/layer_000.png","placement":"tile","offset":[393,640],"size":[352,23]}` | 读取指定相对路径。offset 是小图左上角在参考画布的像素位置，size 是实际 PNG 宽高。旋转已烘焙，只随整个画布统一缩放。以数组决定层序，文件名只作寻址。 |
| `customer_asset` | `{"type":"customer_asset","asset_id":"album-001","source_crop":[0,0,1,1],"crop_center":[0.5,0.5],"mirror_x":false}` | 原图保留在前端。服务端交付引用与绘制参数，主体 alpha 由前端生成。 |

交付粒度是可重新合成的静态图层场景。若产品要显示合成预览，由前端绘制；若要播放动画，时间轴、关键帧和编码参数需另行约定。当前 HTTP 服务只为首包分析提供独立 JSON 地址，最终场景和 PNG 在同一次下载内。

异常处理：POST 请求体长度无效或超过上限返回 413；输入解析、校验或首阶段分析失败返回 422，读取 `error`。任务或路径不存在返回 404。processing 或 failed 时请求成品返回 409，响应体是当前任务状态。后阶段失败仍可读取已成功的 analysis。

实现入口：[HTTP 路由](../server.py) · [解码与两阶段交付](../collage/pipeline.py) · [窗口默认值](../collage/layout/window_contract.py) · [素材绑定](../collage/binding.py)。

固定 PNG 按最终非透明像素裁边，包括半透明阴影。全透明层从最终绘制清单和包中省略。历史 v1 包的 `full_canvas` 仍按原点和整画布尺寸绘制。

