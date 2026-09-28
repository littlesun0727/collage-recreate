# 制作拼贴资源

## 输入与输出

输入已校验的 draft、对应参考图和 bindings，按制作单元生成背景、文字、装饰及需要处理的照片资源。每个单元输出一张 RGBA PNG，准确文案与位置继承自草案。

输出 assets.json、result.json 和资源对照页。photo 和 cutout slot 作为 passthrough 保留绑定及位置，在本阶段预览中为占位；人物抠像、照片裁剪与最终合成属于后续 renders。制作脚本不读取或处理 cutout 客户照片。

本文使用的路径占位符见 [运行环境](../references/runtime.md)。PLAN_DIR 是新的规划目录，BUILD 是独立的制作运行目录。

## 1. 规划制作单元

先观察整体组合，决定哪些内容共同制作以及制作方法：

```text
python SKILL/build-v2/workflow.py plan --units-only --draft DRAFT --reference ANALYSIS_REFERENCE --bindings BINDINGS --output PLAN_DIR --credentials CREDENTIALS
```

需要指定输出宽度、字体目录或用户制作要求时，使用 `--width`、`--font-dir`、`--instructions`。完整复刻默认包含全部制作对象；局部修改才使用 `--objects` 指定原对象键。

规划得到 unit-plan.json 后，将它编译成任务：

```text
python SKILL/build-v2/workflow.py plan --draft DRAFT --reference ANALYSIS_REFERENCE --bindings BINDINGS --unit-plan PLAN_DIR/unit-plan.json --output BUILD
```

两步使用相同输入、宽度和对象范围；所需字体目录传给编译命令。编译已有计划不会再次请求规划模型。读取 BUILD/tasks 中的任务包，继续制作。规划结果的 `planned_units_not_executable` 表示尚未编译，不是制作已经完成。

## 2. 按任务制作

查看任务引用的完整参考图和裁图。按 `owned_keys` 制作，每个 key 对应一个单元；`members` 和 `exact_text_by_member` 给出该单元包含的对象与准确文字。照片窗口和 neighbors 用作布局上下文。

读取 [制作方法](unit-production.md)，补齐该单元的生成提示词、绘图参数、字体或素材处理参数。方法和成员归属沿用计划：generate 整组生成，compose 制作部件后合成，draw/text/feather 按各自工具执行。cutout 不属于 build 制作方法。

按计划逐单元实现，不因方便写脚本或减少制作单元而改变分组、方法或简化 brief。只有发现具体执行问题、遮挡错误或用户提出修改时，才修订受影响单元，其余保持原计划。

任务脚本与部件放在 BUILD/work/TASK。绘图和排字接口见 [drawing-tools.md](drawing-tools.md)，交付清单见 [protocol.md](protocol.md)。脚本经 `run-script` 执行，运行后端在开始制作前按运行环境说明确定；生图走独立 tool 入口，客户人物抠像固定使用后续 renders/cutout.py。

```text
python SKILL/build-v2/workflow.py run-script --run BUILD --task TASK --script make.py --image IMAGE --timeout 120
```

制作时保留这些关系：

- 准确文字来自任务；合并单元中的文字随单元交付，独立 text 保留字体测量记录。
- 相框开口保持透明，照片内容不画入框或装饰。
- 连接线的位置依据参考图的可见起点、终点和转折点确定。
- 扩展绘图画布时用 paint_box 记录完整放置范围，保留透明边距和原比例。
- 一个单元只放置一次；若外部对象需要穿插在组内成员之间，应拆分该单元，不能通过合并更多元素或整体调整层级解决。

## 3. 提交和复核

同一任务的所有单元准备齐后，提交 delivery.json，再生成对照并复核：

```text
python SKILL/build-v2/workflow.py submit --run BUILD --task TASK --delivery BUILD/work/TASK/delivery.json
python SKILL/build-v2/workflow.py preview --run BUILD --task TASK
python SKILL/build-v2/workflow.py review --run BUILD --task TASK --credentials CREDENTIALS
```

submit 校验文件、范围、映射和依赖；preview 生成实际资源对照图；review 调用模型并登记报告。读取返回的报告，依据内容、文字、位置或遮挡的具体问题修正。报告由工具生成。

脚本失败时先读取返回的 stderr；提交失败时修正清单或资源。视觉返工默认最多两轮（首次复核 + 两次修正复核），由编译时 --max-corrections 设定，允许 0。网络/执行失败与视觉轮数分开；--max-attempts 控制技术失败次数及单元工具调用次数，不再把成功脚本执行计为返工。

复核修正版时，工具只发送变化或尚未通过的单元及直接重叠的邻居单件图，同时保留整图对照检查关系；未变化的通过结果按内容和输入指纹复用。selection.json 记录本次检查与复用范围。

最终一轮仍需修改时状态为 deferred：停止资源返工，使用当前已通过机器校验的资源继续 renders，交付带问题的预览并保留具体问题。不能称为全部验收通过。主控应执行后续 prepare/render；status 本身不启动合成。缺文件、无法解码、来源校验失败仍须先解决。不得另发模型请求再导入回执以绕过次数上限；--receipt 导入也受同一视觉轮数限制。

## 4. 修订与完成

字号、笔触和局部几何调整在当前任务中完成。成员或方法改变时，生成或校验新的单元计划，以 `plan --unit-plan NEW_PLAN --revise --reason REASON` 更新同一 BUILD，保留原输入等必需参数。重新调用 `--units-only` 时使用新的 PLAN_DIR。

每次继续已有任务前可读取 `status --run BUILD`；`resume --run BUILD` 也只刷新状态，不执行制作。pending 需要制作，awaiting_visual 需要对照与复核，needs_changes 需要局部修正，stale 需要检查发生变化的输入或依赖。

所需资源全部通过复核后交付 assets.json 和 result.json。完整制作同时检查 `all_build_resources_accepted`；局部任务的 `scope_usable` 只说明选定范围可用。字段与状态见 protocol.md。

进一步制作示例见 [production-task.md](examples/production-task.md)。空制作范围和最终照片合成的当前限制见 [工作流约定](../references/workflow-contract.md)。
