# 本地合成 renders

使用 build 制作资源、客户照片绑定和原排版坐标进行组装。客户人物保持 mode=cutout 的 slot，在本阶段通过固定入口 renders/cutout.py 调用本地 BiRefNet 抠像。允许等比缩放、裁切、旋转、局部位置及层级调整、应用已有蒙版。依赖见 renders/requirements.txt；普通合成只需 Python 和 Pillow，人物分割另需 NumPy、ONNX Runtime、OpenCV 和本地 BiRefNet FP32 ONNX 权重。此阶段无需远端 API，也不生成替代客户人物。

## 准备与导出

```text
python SKILL/renders/workflow.py prepare --build BUILD --output RENDERS
python SKILL/renders/workflow.py render --layout RENDERS/layout.json
```

存在 cutout slot 时，prepare 需要本地权重，可指定已安装分割依赖的 Python 环境：

```text
python SKILL/renders/workflow.py prepare --build BUILD --output RENDERS --cutout-model BIREFNET_ONNX --cutout-python CUTOUT_PYTHON --outline-width "2%" --outline-color "#FFFFFF" --cutout-cache CACHE
```

--cutout-python 与 --cutout-cache 可省略：默认使用当前 Python，缓存放在本次 cutouts 目录下。重试或比较参数时可显式共用 CACHE。prepare 从 bindings 取每个 cutout 的客户原图，调用唯一抠像入口，登记为独立 cutout 图层，并沿用 slot 的位置和层级。人物不进入 build owned_keys，也不烘进制作单元。旧 build 中的独立 cutout 成品会由客户原图重新抠像；若旧人物与其他成员已经合成一张图片，需先拆分该单元，不能重复叠加人物。

RENDERS 必须是独立的新目录，不能放在 BUILD 内。读取 build-unit-assets-v1 的 assets.json、state.json 及它们指向的 draft、reference、bindings；验证输入指纹、目录版本、照片绑定及素材指纹。普通照片直接使用客户原文件。不会刷新 build 状态、消耗 build 重试次数或修改原素材。

默认使用 assets.json 精确指向的版本。如果确实要测试尚未提交的修正版，明确追加 `--delivery BUILD/work/TASK/delivery.json`，可多次指定不同任务的交付。工具核对任务版本和成员归属，并将覆盖素材记录为未验收；不扫描文件名推断“最新”，也不把 work 文件冒充通过验收的资产。

缺少素材或文案时导出已有内容，并在 result.json 中列出缺项；不弹出补生成流程。输入指纹不符、版本冲突、非法参数等错误会停止本次导出，应先纠正路径/参数，不自动回到制作阶段。

## 固定抠像入口与描边参数

agent 可单独运行抠像入口检查效果，再确定 prepare 的参数；不要在任务 make.py 中另写分割或人物几何处理：

```text
CUTOUT_PYTHON SKILL/renders/cutout.py --source CUSTOMER_PHOTO --model BIREFNET_ONNX --output CUTOUT_OUTPUT --outline-width "2%" --outline-color "#FFFFFF" --cache CACHE
```

描边通过脚本参数传入，无需在 layout 中维护参数配方。默认宽度为 2%，默认颜色为白色；agent 根据参考效果尝试后采用合适值。

| 参数示例 | 含义 |
|---|---|
| --outline-width "2%" | 未描边人物可见包围框短边的 2%，不是整张原图或最终画布的比例 |
| --outline-width 12 或 "12px" | 抠图原生输出中的 12 像素；后续整个人物等比缩放时描边也随之缩放 |
| --outline-width 0 | 关闭描边 |
| --outline-color "#FFFFFF" | 描边颜色，支持 #RRGGBB 或 #RRGGBBAA |

输出 foreground.png（原图尺寸、未描边人物）、alpha.png（原图坐标下的蒙版）、asset.png（裁去透明留白并加描边的合成素材）及 cutout.json（来源、模型指纹、描边参数、坐标偏移）。分割预处理可以缩放模型输入，但最终颜色来自客户原像素，蒙版恢复至原图尺寸，人物没有单独宽高缩放。缓存按客户源文件、模型权重和算法版本校验；改变描边不会重新分割。

只修改描边时复用未描边人物图，并输出到新目录：

```text
CUTOUT_PYTHON SKILL/renders/cutout.py --foreground CUTOUT_OUTPUT/foreground.png --output OUTLINE_VARIANT --outline-width "12px" --outline-color "#FFFFFF"
```

代码接口 outline_pixels(value, subject_size) 解析百分比或像素值；outline_rgba(foreground, width_px, color) 在原 RGBA 画布上加描边，不改人物几何或重新分割。后续 live 可逐帧传入人物图，改变宽度/颜色；调用方为描边预留画布空间。当前接口保留了逐帧调用能力，尚不包含视频读取、跟踪或时序稳定处理。

## 排版规则

- layout.json 记录实际素材的路径与 SHA256、原始框、当前框、层级、照片绑定结果和附件关系。画布尺寸直接沿用 build；所有 box 为半开区间像素坐标 `[left, top, right, bottom]`。
- 制作单元只绘制一次，沿用编译的 layer_index；普通照片按 draft 的 layer_order 插入。合并单元内部的关系已烘焙到素材，不重复拆装；存在跨层合并时需看图检查。
- draw/compose 成品及显式 paint_box 使用 stretch，将完整栅格映射到已声明的框，与 build 坐标约定一致。其他独立素材默认 contain 等比缩放；透明留白默认保留。若已确认留白导致可见主体太小，可显式 source_crop，记录后再放大。
- cutout 是 renders 独立图层，默认 contain 等比缩放。需要局部裁切时可使用 cover 或调整 source_crop；禁止 stretch，工具会拒绝。若人物放不进参考框，调整框、位置或可见范围，不压窄或拉高身体。
- 照片默认 cover 等比铺满原窗口，允许调整 crop_center 避免裁掉主体。它表示裁切余量的分配位置，`[0.5, 0]` 对齐上方，`[0.5, 1]` 对齐下方；不进行人脸识别或自动换图。
- 不根据自然语言自动猜旋转和撕纸边缘。旋转需要明确角度；边缘需要既有蒙版。build delivery 的 masks 是检查约束，不能直接当作照片蒙版。没有蒙版的照片保持矩形窗口，并如实说明效果限制。

## 调整与再次导出

先看 final.png 和 comparison.png（左参考图、右合成图），再创建 JSON patch。所有改动需要 reason；只改参数，原文件不变。

```json
{
  "reason": "保留人物头顶；装饰去掉已确认的透明留白后等比放入原框",
  "changes": [
    {"key": "slot:photo_1", "crop_center": [0.5, 0.15]},
    {"key": "unit-example", "source_crop": [0.1, 0.1, 0.9, 0.9], "fit": "contain"}
  ]
}
```

```text
python SKILL/renders/workflow.py adjust --layout RENDERS/layout.json --patch PATCH_JSON
```

adjust 校验并保存调整记录后重新导出。可调整字段：box、fit（contain/cover/stretch；cutout 禁止 stretch）、crop_center、source_crop、rotation（顺时针角度）、layer_index、mask、mirror_x。mirror_x 为布尔值，true 时水平镜像整个图层及其蒙版，不改变人物宽高比例；镜像在裁切、适配窗口和应用蒙版后、旋转前执行。source_crop 为原素材宽高的 0–1 比例框，不自动裁 alpha。mask 格式为 `{"file":"existing-mask.png","channel":"alpha"}`，路径相对 patch；channel 也可为 luminance。工具记录蒙版指纹，蒙版缩放到窗口后乘入 alpha。

照片与边框一起移动时，在同一 patch 中给出双方的 box；layout.relationships 提供原草稿的关联依据。不隐式移动邻近装饰。改变图层顺序必须说明遮挡原因。反复 render 输出确定一致，不累积缩放损失。手动修改 layout 也必须重新 render，旧验收不继续生效。

## 交付与验收

输出 final.png、comparison.png、layout.json、result.json。区分：

- exported：本次图像已写出。
- coverage_complete：草稿所有对象均有可用输入；不代表视觉相似。
- source_assets_accepted：引用的制作资源已在 build 验收。
- renders_verified：覆盖完整、资源已验收，且本次成图经看图通过。

查看实际图片后记录人工或主控视觉判断：

```text
python SKILL/renders/workflow.py review --layout RENDERS/layout.json --verdict pass --note "已检查照片裁切、尺寸、位置、遮挡和文字"
python SKILL/renders/workflow.py review --layout RENDERS/layout.json --verdict needs_changes --note "具体剩余问题"
```

review 只记录调用者的判断，不自行调用视觉模型；绑定 layout 与 final 的指纹，检查素材未变。未验收资源或缺项可以用于效果测试，但不标记全流程通过。重新 render 会清空此前视觉判断。失败时保留之前的输出供排查；以成功命令和 result 的 layout_sha256/final_sha256 是否匹配判断当前输出，不能仅凭文件存在判定成功。

验收重点是客户照片未被替换、主体裁切合理、位置尺寸接近参考、层级和边框关系正确、没有重复单元、文案完整。人物身份、姿态、服装和身体比例以绑定客户原图为准；参考模特只约束摆放与贴纸效果。检查抠像残留、发丝边缘、描边宽度与透明区域，不能用改变人物宽高解决构图差异。现有制作素材的文字差异、贴纸底色、撕纸轮廓等问题只记录，不在 renders 中生成修复。
