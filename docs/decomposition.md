# 素材分解与清洗

拆层独立生成数据集，不改变拼贴 workflow 的坐标、绑定和渲染行为，不覆盖源任务的 analysis.json。

## 使用

```powershell
python -m collage analyze --reference D:\images\original.png --run runs\example
python -m collage decompose --run runs\example
python -m collage decompose --run runs\example --extract --key-file .env
```

analyze 仅执行 VLM；decompose 默认生成计划、预测 JSON、框图和原图；加 --extract 执行 Reveal 和导出。

直接使用已有分析，跳过 VLM：

```powershell
python -m collage decompose --run runs\imported --analysis D:\data\analysis.json --reference D:\data\original.png
```

支持 v9 compiled analysis、带 coordinate_mode 的 structure.json 和本模块导出的 normalized analysis.json。无坐标声明的模型 JSON 须加 --coordinate-mode pixel 或 --coordinate-mode normalized_1000，不猜测坐标模式。reference_size 为 [宽, 高]，必须匹配原图尺寸。输入原图的方向、尺寸应与分析一致。

已有提取结果可仅本地重新导出：

```powershell
python -m collage decompose --run runs\example --export-only
```

不调用模型，不需要 API key。旧任务尚未完成导出时要求已有 manifest 和 aug 图层；已完成的任务直接复用交付结果并清理多余文件，不重复调用接口。

仓库的 EXTRACTION_PAUSED.json 暂停标记保留；存在时 --extract 保存计划后停止，不提交远程请求。明确授权的单次试跑可加 --resume-extraction；--export-only 不受远程暂停影响。

## 产物：run/decomposition/

| 文件 | 内容 |
| --- | --- |
| analysis.json | 预测框，coordinate_mode=pixel；reference_size 为 original.png 的像素尺寸 |
| bbox.png | 原图上显示 analysis.json 预测框和 ID |
| original.png | 输入拆层模型的原图，以 PNG 保存 |
| layers/aug/ | 清洗后的 aug 全画布透明图层 |
| reference.png | aug 图层按 layer_order 从底到顶叠合 |
| analysis-refined.json | 按实际 alpha 边界更新整数像素 bbox，与 analysis.json 字段结构相同；reference_size 为 reference.png 的提取输出画布尺寸 |
| reference.svg | 内嵌素材、按 refined bbox 定位的独立图层 SVG；浏览器直接打开可拖动 |

两份 JSON 的 bbox 均为 [left, top, right, bottom] 像素坐标。normalized_1000 输入按 reference_size 换算为 round(x × width / 1000)、round(y × height / 1000)；已经匹配 original.png 的像素框保留原值，不重复缩放。精修框直接使用提取图层的整数像素边界。精修只修改几何，文字、样式及关联沿用预测，未额外复核语义。背景映射为 layer_0，ID 冲突和关联同步映射。旧的 normalized 交付可用原输入参数加 --export-only 本地转换，不产生额外文件。

成功导出后只保留表中 7 项。接口缓存、base 图层、清洗前副本、assets、调试图、预览页面和中间 JSON 均直接删除，不另行归档。SVG 已内嵌截取的素材，不依赖 assets 目录。未完成任务暂留中间文件用于续跑。源 run/analysis.json 保持不变。

每批最多 20 个前景框，从顶到底提取；下一批使用上一批 aug 剩余底图，仅最终剩余底图作为背景。续跑检查原图、分析哈希和批次参数。

清洗使用预测框附近区域定位主体，只移除区域外的小型孤立噪点，保留软边和阴影。较大远处区域、连续越界或无法可靠定位主体时保留像素，并在命令结果中返回 needs_review。

验证：python -m pytest tests/test_decomposition.py tests/test_decomposition_geometry.py tests/test_pipeline_integration.py -q。测试使用模拟接口，不产生付费调用。
