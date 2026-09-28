# 按制作单元执行

读取任务包、完整参考和单元裁图，按计划 brief 补齐制作参数。owned_keys 是本任务的单元 ID；每个单元包含的全部 members 都要保留，每个单元交付一张 RGBA PNG。

路径占位符见 [运行环境](../references/runtime.md)。参数文件放在当前任务工作目录。

## generate：整组生成

```text
python SKILL/build-v2/workflow.py tool --run BUILD --task TASK --key UNIT --parameters RECIPE_JSON --allow-remote
```

recipe 示例：

```json
{"prompt":"本单元具体制作要求","background":"key","key_color":"#00FF00"}
```

固定背景使用 opaque。提示词描述本组全部成员及相对位置、留白、连接关系，工具自动补入准确文字与内容排除要求。相框开口保持透明，照片和外部邻居不生成进装饰。

配置参数按运行环境中的现有服务适配填写。返回 tool-result.json 后保留工具图片及 tool_receipt，原样交付；全图空间对应 target_box。

## compose：部件制作后合成

在任务脚本中制作或读取部件，再合成一张单元图。部件需要生图时，上述 tool 命令添加 --members 指定本单元内的原成员键。工具输出是中间部件，最终只提交合成后的单元图片。

记录部件文件、实际参数及本地代码依赖。部件位置从成员的布局范围映射到单元画布，保留整体留白和比例。

compose 不等于全部程序绘制。执行 brief 中的部件路线：材质显著的标签可通过 tool --members overlay:ID 生成无字底纸，再用任务中的准确文案排字并合成；规则几何可本地画；已有匹配资源可复用。先检查生成部件的可见边界、留白和透明区域，再定位文字，不能直接把含透明边距的整幅图拉伸后随意放字。--members 选择原成员，不能选择单个 overlay 内未拆出的底纸；这种情况不能假装工具已支持内部拆分。

## draw 与 text

使用 [绘图接口](drawing-tools.md)。draw 根据实际笔触范围分配画布；独立 text 用任务中的准确文案和字体信息排字，保留 render_text 返回的测量记录。

本地绘制的随机细节使用固定 seed，并记录在交付参数中。衬底绘制完整形态，通过原有图层遮挡露出边缘；复核关注主要外形、边缘粗细、质感和层次，不要求每个撕裂缺口与参考完全一致。

选择本地绘制的薄撕纸衬底，可用受控扰动轮廓、细微颗粒和柔和投影；先画完整纸面，再由客户照片遮住中央。实体纸面纹理只改变颜色或叠加纹理层，不直接把纸面 alpha 改低造成穿孔。不要从含大量照片的裁图生成带有照片残留的装饰；这类残留不能通过复核。

generate/compose 中的文字由复核检查 exact_text_by_member，无需为合并文字重复输出独立图片。

## 客户 slot 与 feather

photo、cutout slot 只作为位置与遮挡上下文，不属于 build 的 owned_keys，也不能放进 compose 成品。客户人物抠像固定交给 renders/cutout.py 的 BiRefNet 入口，禁止在 make.py 中另写人物分割、描边或宽高变形。feather 仍沿用 build 的 tool 入口和任务绑定素材。

## 脚本与提交

脚本放在 BUILD/work/TASK，由 run-script 执行。--task 是文件路径，脚本先读取 JSON；任务 ID、revision 和成员内容取实际任务值。生图调用在 tool 命令完成，本地脚本负责绘制、处理或汇总。

交付字段见 [protocol.md](protocol.md)。seed 未使用时为 null，独立 text 保留完整测量记录。汇总全部单元后按 [制作流程](build.md) 执行 submit → preview → review。

复核检查成员、准确文案、相对位置，以及整组大小、留白、连接和遮挡。预览中的普通照片是占位，最终照片呈现由 renders 检查。
