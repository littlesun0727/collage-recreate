# 单元制作任务示例

用于已完成规划和任务编译的制作阶段。路径与配置按 [运行环境](../../references/runtime.md) 填入。

1. 读取 [制作说明](../build.md) 和 BUILD/tasks/TASK.json。查看 input_root 下的完整参考和单元 crop。
2. 根据 objects 中的 method 和 brief 制作 owned_keys。每个单元保留全部 members，输出一张 RGBA PNG。
3. 在 BUILD/work/TASK 编写 make.py，接收 --task 和 --output。--task 是 JSON 文件；从中读取 id、revision、字体、文字与映射。
4. 生成和分割使用 tool；本地脚本通过 run-script 执行，保留实际输出、参数和依赖。
5. 汇总 delivery.json：outputs 恰好覆盖 owned_keys；generate 单元带 tool_receipt，独立 text 带测量记录，扩展 draw 带 paint_box，seed 未使用时为 null。
6. 依次 submit、preview、review。依据报告局部修正；完成后交付 assets.json 与结果。

生成和 compose 单元内的文字随单元交付，不额外按原成员重复出图。脚本示例仅演示接口，用实际参考确定路径与布局。

## 现有代码示例

- [single_draw.py](single_draw.py)：绘制曲线的函数，可在单元绘图脚本中调用。
- [text_measure.py](text_measure.py)：比较真实字体测量结果。

真实单元制作的交付格式见 [protocol.md](../protocol.md)，绘图坐标与画布分配见 [drawing-tools.md](../drawing-tools.md)。
