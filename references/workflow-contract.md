# 阶段交接约定

## 文件流向

| 来源 | 交接文件 | 消费者 |
|---|---|---|
| analysis | review/parsed-draft.json、旁边的 validation.json | binding、build-v2 |
| analysis | 根目录实际使用的 reference.jpg 或 reference.png | binding、build-v2 |
| binding | matching/bindings.json；批量任务为 JOB_ID/bindings.json | build-v2、renders |
| 单元规划 | unit-plan.json | build-v2 的 plan --unit-plan |
| 任务编译 | tasks/TASK.json、state.json | 制作、提交和复核工具 |
| 制作验收 | assets.json、result.json、复核证据 | 交付；renders |

当前没有根目录 workflow.py 或总状态管理器。主控使用各阶段现有文件衔接；完整自动执行与恢复仍待集成验证。

建议每次运行独立分配 analysis、binding、planning、build 和 renders 产物目录。analysis、binding 和 units-only 规划要求新目录；重复尝试保留旧结果。制作目录可由原工具更新版本。

Codex 与 OpenClaw 使用同一套交接文件；同一制作运行目录由一个主控推进，另一个会话可只读检查，避免同时制作、修订或提交。对比两个宿主时分别使用独立输出目录。

## 输入一致性

保留 draft 旁的 validation.json。下游会检查版本及参考图记录，bindings 也记录对应 draft 与客户素材。

分析准备图片时可能转正 EXIF 朝向或转换格式，因此默认传递实际分析使用的文件。review/reference.png 是展示图，不能仅因视觉一样就替换交接文件。由工具完成文件指纹检查，不增加用户操作。

单元规划与编译使用相同 draft、reference、bindings、width 和 objects。完整复刻默认不限制 objects；局部制作才指定原对象键。photo 和 cutout slot 均作为 passthrough，保留绑定、原框和层级；cutout 在 renders 通过固定 BiRefNet 入口处理，不属于 build 制作范围。photo_feather 暂仍由 build 处理。

用户要求应由主控保存，并明确映射到支持的接口：指定照片用 binding overrides，不可辨认文字的指定替代内容用 binding text-overrides，文案要求用 binding instructions；制作与独立编辑要求用规划 instructions。analysis 目前没有用户要求参数。bindings 的 text_bindings 保留新创作/用户指定/未解决状态，build 在校验原始 draft 指纹后应用，不修改原分析或绕过指纹检查。计划与编译必须使用同一份补全后的 bindings；旧计划不会随提示词更新自动改变，需正式修订受影响单元。

## 阶段完成条件

- analysis：调用 completed，draft_valid 为 true，制作相关疑点已有处理方式。结构合法不代替看图。
- binding：状态 completed，所需照片绑定完整；检查 text_completion 的未解决文字并如实列明。照片成功不代表所有文字已解决；素材目录缓存可复用。
- 单元规划：planned_units_not_executable 表示已有计划，继续编译为任务。
- build：当前范围的任务经过 submit 和 review；完整资源范围还检查 all_build_resources_accepted。
- build 为 deferred 时表示视觉返工到限，主控继续 renders 并交付带问题预览；它不等于 accepted。具体问题随 renders 的 source_notes 交付。
- renders：本地 prepare/render 导出成图，adjust 仅修改组装参数。最终通过要求 coverage_complete、source_assets_accepted 和 renders_verified 为 true；缺项或未验收素材仍可导出测试图，必须如实说明。

只有 photo/cutout slot、没有制作对象时，build 可交付空资源清单和 passthrough，资源阶段视为无需制作；继续 renders 处理客户照片。存在制作对象但未选中它们的空范围仍不能冒充完整资源通过。

## 中断与修正

先确认原命令是否仍在执行。远端请求超时或结果未知时，检查原调用记录与产物，再决定下一步，避免重复提交。

切换宿主接手任务时，先确认原主控已停止推进及其子命令、远端请求的状态，再交接用户要求、阶段产物路径、已知问题和下一步。新主控依据业务文件核对状态，沿用已验证产物，不依赖旧宿主的聊天历史、会话 ID 或进程句柄，也不因换宿主重新发送已完成的模型请求。

analysis/binding 输入修订后使用新输出目录。已有合法结果可以交接，版本或来源变化时重新校验并更新受影响阶段。

build 使用自身 state.json 管理任务；总入口不另写任务通过状态。status/resume 刷新清单后，按返回状态制作、复核或修订；它们不会自动执行未完成动作。计划修订与局部修正的区分见 [build.md](../build-v2/build.md)。

同一份交付和组合已经通过复核时直接复用；修改了输入、脚本、资源或依赖时依工具结果重新提交或验收。遵守现有尝试限制。

## 当前交付

交付资源清单、实际资源、对照页面、各阶段结果与未解决问题。生成或 compose 合并的文字属于图片单元；独立 text 的参数和 PNG 也不代表已导出特定编辑器的原生文字层。

完成全流程还需要 [本地合成](../renders/renders.md)。制作资源通过复核，不代表最终成图已经完成。
