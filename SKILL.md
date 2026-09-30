---
name: collage-recreate-v5
description: 根据参考图和客户照片快速制作拼贴首版；最多三组裁图提取装饰，筛除不可用素材，简单元素本地绘制并集中复核。
---

# 快速首版

同一主控分析、绑定、集中筛选和整图复核。默认流程：prepare → analysis/bindings → build → 一次screen（有需要才用）→ review。默认不做像素修复，不启动OCR/LaMa，不逐素材请求模型。

本机解释器 `D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe`，入口 `scripts/workflow.py`。Windows命令使用 `tty:true`，文本读写UTF-8；以下PY、SKILL、RUN替换为实际路径。

1. `PY SKILL/scripts/workflow.py prepare --reference REF --materials DIR1 DIR2 --run RUN --width 1200 --cutout-model D:/codes/visual-recreate-validation/models/birefnet-lite-fp32.onnx`。
2. 读[分析与绑定](references/analysis.md)，集中查看原尺寸参考图和客户联系表，写analysis.json与bindings.json。按局部视觉单元建ID和bbox：有实际重叠或连接、能共同移动且相对其他对象前后关系一致的成员可以合并；距离很远或仅同处一张底板的成员分别表达。单个元素自身的笔画、纹样、描边和阴影不拆碎，不把整页或多照片区域当成一个装饰组合。弱背景纹理合入背景。简单底板、规则边框和几何装饰优先local，参数见[本地绘制](references/drawing.md)。只要求解析时到此停止。
3. `PY SKILL/scripts/workflow.py build --run RUN --reveal`。默认grouped：程序比较1至3组空间裁图，每图最多3次提取、每组最多20框，保留关联照片辅助框；不要求模型写分组计划。分组仅合并请求区域，不合并对象ID；一组内仍按各元素的独立bbox分别提取、回填。返回素材经程序门禁后合成；原始缓存保留。简单local不占提取请求。已有缓存可用`--reveal-cache CACHE`离线成图。提取坐标、续查及模式详见[提取与回填](references/reveal.md)，正常任务不必通读。
4. 集中查看完整对照图和非空提取检查表。缺失、混入旧照片/多余内容、明显模糊或不完整的素材不强行保留。有必要时按[筛选与补画](references/asset-gate.md)一次批量screen：保留、丢弃、用明确的简单shape补画；完整嵌入文字可确认归属避免重复。复杂缺失保留缺口，不反复修补或重新请求。
5. 查看筛选后当前成图，按[整图复核](references/review.md)登记review。客户照片可见不等于整体完成；关键缺口、旧照片残留和重复内容不能通过。

`final.png`是当前导出；`previews/first.png`保留首次成图。analysis描述设计，bindings记录客户素材选择，scene/result由工具生成。照片来自客户catalog，禁止生成替代人物或回填参考整图。显式layer_order保持，照片关联不自动把底板抬到照片上方。

只有用户明确要求精修时才读[局部恢复](references/recovery.md)，使用旧recover或按需generate；OCR/LaMa不是首版默认步骤。报告首版与筛选后效果、未解决项、提取/本地处理/模型耗时，不以缓存速度冒充首次出图速度。
