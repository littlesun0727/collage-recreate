---
name: collage-recreate-v5
description: 根据参考图和客户照片制作拼贴首版。主控输出严格JSON，Reveal提取装饰，固定工具回填照片；整图复核后可离线恢复碎片组合、按需清理旧照片，yibu仅用于必要的装饰生成。
---

# 快速首版，整图迭代

同一主控看图、绑定和复核。正常路径：准备 → 联合分析与绑定 → build完整首版 → 集中看图。没有外部分析模型、独立制作规划或逐元素验收。

360默认整图提取，保留相框对应的照片辅助框；同批辅助框去重，实际请求框总数不超过20时一轮提取，超过20才整图分批，关联照片框随相框同批。单张图仍最多3次提交、每次最多20框。模型不写分组计划，不为提高分辨率自动裁图；原空间裁图模式仅通过`--reveal-layout grouped`显式启用。正常build --reveal使用整图模式，具体与旧缓存用法见[提取与回填](references/reveal.md)。

## 执行

本机解释器 `D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe`；入口 `scripts/workflow.py`。Windows命令使用 `tty:true`，文本读写UTF-8。以下PY、SKILL、RUN替换为实际路径。

1. 准备一次：

   ```text
   PY SKILL/scripts/workflow.py prepare --reference REF --materials DIR1 DIR2 --run RUN --width 1200 --cutout-model D:/codes/visual-recreate-validation/models/birefnet-lite-fp32.onnx
   ```

2. 读 [分析与绑定](references/analysis.md)，集中查看原尺寸参考图和客户联系表，写 `analysis.json`、`bindings.json`。先判照片内容与固定装饰归属，按完整设计单元组织，再选制作方式；不按本地绘图零件拆分。常规外观用默认值，必要时查 [本地绘制](references/drawing.md)。用户只要求解析时，交付JSON和解析可视化后停止，不进入以下制作步骤。
3. `PY SKILL/scripts/workflow.py build --run RUN --reveal`。一次命令自动校验、提取复杂装饰、填入客户照片并完整合成；默认保留返回图层的像素，不强制清窗，也不因覆盖多张照片就弃用素材。照片定位、裁切及原图坐标还原仍保留。简单几何仍本地绘制。请求框默认每边外扩原宽/高10%，analysis仍写原始布局框，主控不要预先扩大。有已下载缓存时用 `--reveal-cache CACHE`（离线），允许补充缺失目标时再加 `--reveal`。见 [素材回填](references/reveal.md)。schema报错时只修报错字段。叠框图自动产出供定位排错，正常路径不单独做一次看图验收。不写临时整图制作脚本，不新增例行制作清单。
4. 读 [整图复核](references/review.md)，集中打开完整对照图和非空的提取检查表，写问题项并登记review。先检查客户照片、布局、文字，再看关键装饰。首版允许细微字体、纹理和阴影差异；明显占位、污染、主体遮挡或关键装饰缺失不能通过。

## 局部演进

用户只要求首版测试时，保留 `previews/first.png`，登记真实问题后交付，不生图或精修。完整制作任务中可集中执行一批 `apply --run RUN --file REVIEW` 参数修正；仍有关键复杂素材缺口时，使用现有 `generate --run RUN --ids ... --allow-remote --workers 2` 调用yibu。生成失败保留原因和成功资源，不无上限重试。重新成图后复核当前版本。

复杂装饰优先由Reveal恢复。返回层名称与实际内容可能不一致：检查完整组合，不能把错分到“纸底”的猫脸压到客户照片下面。确有碎片错层或旧照片残留时，按[局部恢复](references/recovery.md)写可选修复JSON并执行离线 `recover`；同一组合可由多个返回层选区构成，原图坐标不变。相框仅对已确认残留的规则窗口清理，不接纳返回的整图背景层或辅助照片层。复杂镂空、缺失或无法确认归属的内容仍登记问题，不冒充干净素材。先恢复组合和清窗，再apply或generate，避免重编译丢失后续调整。yibu只用于仍需重新制作的非客户照片，没有外部分析调用。

## 保持的约束

- 客户照片来自本次catalog，不能生成替代人物或用参考整图回填。photo只表示可替换窗口；相纸/相框及附属固定设计统一为overlay，不因制作方法改变分类。分析描述设计，bindings记录素材选择，scene/result由代码生成。
- 已知文字准确保留；不可辨文字明确标注。客户人物、场景与参考不同是正常替换，检查裁切和可读性，不以内容不同要求生成原人物。
- `final.png`是当前导出，`renders_verified`表示本次任务标准的视觉复核通过；首版可接受不等于逐细节高相似度成品。
- JSON合法不证明位置或外观正确。分割成功、图片存在、来源核验也不代替看图。

交付首版、对照、当前导出与JSON路径，说明首版耗时、主要缺口。不要用后续修图覆盖首次成图。
