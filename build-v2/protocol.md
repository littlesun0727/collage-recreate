# 制作任务与交付协议

## 计划与任务

默认单元计划为 `build-unit-decision-v2`。每个单元指定 member_keys、brief 和 method；程序分配 unit_id，并从原对象继承文字、范围和层级。通过 plan --unit-plan 编译成 build-task-v2 任务，production_mode 为 units。

photo 和 cutout slot 不属于制作成员，随 assets.json 的 passthrough 交给 renders；build 不接收 cutout 方法或其输出。绑定客户人物的抠像、描边和视觉检查在 renders 完成。

| 字段 | 含义 |
|---|---|
| id、revision | 当前任务及其输入版本 |
| owned_keys | 本任务须交付的 unit_id 集合 |
| objects | 制作单元，含 members、member_keys、plan 和映射 |
| exact_text_by_member | 单元各原始成员的准确文案 |
| layer_index | 单元在合成中的放置层级 |
| interleaved_context_keys | 原成员层级之间的外部对象，需检查遮挡关系 |
| input_root、reference、crop | 参考文件根目录及相对位置 |
| fonts | 可使用的字体记录 |

从 BUILD/tasks/TASK.json 读取任务；run-script 传入的 --task 也是 JSON 文件路径。脚本读取实际任务值生成交付。

兼容入口仍接受逐对象 build-plan-v2。该模式 owned_keys 是对象键、每个对象一张图；只在处理已有逐对象任务时使用，不将其输出边界套用到单元任务。

## delivery.json

清单位于 BUILD/work/TASK/delivery.json。以下为字段示例，任务、单元与几何值需来自实际任务：

```json
{
  "schema_version": "build-delivery-v2",
  "task_id": "TASK",
  "task_revision": "REVISION",
  "script": "make.py",
  "dependencies": [],
  "parameters": {"stroke_layout_px": 3},
  "seed": null,
  "outputs": [
    {"key": "UNIT", "file": "unit.png", "raster_size": [400, 300], "target_box": [20, 30, 220, 180]}
  ],
  "approximations": [],
  "unresolved": [],
  "timing": {"script_seconds": 1},
  "reason": "initial production"
}
```

outputs 恰好覆盖 owned_keys，每个单元一张独立 RGBA PNG。同一任务整体提交。script、dependencies、产物和工具凭据路径相对当前任务 work。

- target_box 保留任务范围。draw 可附带包含它的整数 paint_box，整个 PNG 按 paint_box 放置；未提供时用 target_box。
- draw/text/feather 的栅格与实际放置范围同比例。透明边距属于映射的一部分。
- 独立 text 附带 render_text 返回的完整 text 记录；提交器重新渲染并核对像素。
- generate 单元附带相对路径 tool_receipt，图像须与该次工具输出一致。
- 可选 masks 为 `[{file,slot_id,max_alpha}]`，声明已有照片窗检查遮罩；提交器不据此修改图片。
- 历史资源导入先复制到当前 work，并在 output.source 记录原 path 和 sha256，再走本次验收。
- seed 必填，未使用随机数时为 null；approximations 与 unresolved 无内容时用空数组。

脚本、依赖和资源版本会保留记录。输入或已提交依赖变化后，旧验收可能失效。

## 状态与复核

| 状态 | 下一步 |
|---|---|
| pending | 制作并提交 |
| machine_failed | 修正执行或交付错误 |
| awaiting_visual | 生成对照并调用 review |
| needs_changes | 按复核问题局部修正，再提交与复核 |
| deferred | 视觉返工到限，停止修正并进入 renders，保留问题且不标记通过 |
| accepted | 输入和资源未变时复用 |
| blocked | 解决计划记录的缺口 |
| stale | 检查变化的输入或依赖，修订或重提 |

state.json 是制作状态来源，assets.json、result.json 和任务包由它派生。status/resume 检查变化并刷新清单，不执行脚本或模型请求。相同交付重复提交可识别为 duplicate。

review 读取 [prompts/review.md](prompts/review.md)，将实际参考和对照图交给模型。模型返回 reviews 与 combination_issues，单项状态为 passed 或 needs_changes；工具填写证据、版本和计时，并登记任务状态。

复核报告为 build-review-v2，保存于 BUILD/model-reviews 下。submission_id 标识交付版本，context_id 标识组合版本。问题、缺失检查或 unresolved 不能被 passed 覆盖。相同提交与组合已复核时复用报告。

导入已有完整报告才用 review --receipt；它与模型模式 review --config 二选一，遵守同一视觉轮数上限。visual_budget 显示已完成复核数与剩余修正数；首次复核加最多两轮返工为默认值，成功登记的复核才计入视觉轮数，技术失败独立计数。正式修订保留同一批原成员的轮数。

## 资源清单与完成条件

单元清单为 build-unit-assets-v1。每项包含 member_keys、layer_index 和放置范围；成员仅说明内容归属，单元图片只放置一次。逐对象兼容清单为 build-assets-v2。

scope_usable 表示选定制作范围已验收；all_build_resources_accepted 还要求全部制作对象都被选中。两者都不能替代最终成图检查。当前工具保留 template_ready=false、renders_verified=false。

下游合成还需要 bindings 和普通照片 passthrough，优先使用资源的 paint_box，并显式支持单元清单版本。
