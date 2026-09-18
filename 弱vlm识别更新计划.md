# collage-recreate：两阶段静态分析重构计划

仓库：

D:\codes\collage-recreate

## 1. 本次唯一目标

当前只解决：

> 静态参考图 → 正确、稳定的制作草案 `draft.json`

不要实现：

* Live Photo
* 视频输入
* 动画
* 时间轴
* Motion Canvas
* Remotion
* 视频生成
* 多 Agent
* 模板市场

现有静态 renderer、project.json、Qwen Image service、export 等能力尽量保持不动。

本次重点只改：

`reference image -> draft.json`

---

# 2. 当前问题

现有流程基本是：

```text
reference image
    ↓
qwen3.8-flash
    ↓
一次完成：
- 看到了什么
- 哪些是客户照片
- 哪些是文字
- 哪些是装饰
- 哪些装饰合并
- 哪些装饰拆开
- 什么是背景
- 每个元素 bbox
- layer order
    ↓
draft.json
```

这个任务对 qwen3.8-flash 太开放。

真实测试已经出现：

* 多个人物照片被吞进 background
* 主体照片被当成 decoration
* 猫、可颂等贴纸被当成客户 slot
* 装饰过度拆分
* 装饰过度合并
* description 包含完整对象，但 source_rect 裁掉成员
* CV refine 无法恢复模型根本没识别出来的对象
* 模型自评 usable，但独立视觉检查仍明显不正确

因此不要继续主要通过增加 `analysis.md` 自然语言规则解决。

---

# 3. 新流程：只拆成两步

新的核心流程：

```text
Reference Image
       ↓
Pass 1 — Visual Inventory
       ↓
inventory.json
       ↓
程序校验
       ↓
Pass 2 — Production Draft
       ↓
draft.json
       ↓
现有 refine_layout
       ↓
最终 review
```

核心思想：

> Pass 1 只回答“图里有什么”。

> Pass 2 才回答“这些东西应该怎么制作”。

不要让模型在观察画面的同时就开始决定 background、slot、grouping 和 bbox。

---

# 4. Pass 1：Visual Inventory

## 4.1 目标

Pass 1 只做视觉对象枚举。

它回答：

> 参考图中有哪些主要视觉对象？

它不回答：

* 怎么制作
* 是否合并
* 什么是最终 overlay
* 什么是 background
* layer order
* Qwen Image 怎么生成
* 精确 bbox

---

# 5. inventory.json

新增一个非常简单的中间格式：

`analysis/inventory.json`

建议 schema：

```json
{
  "version": 1,
  "objects": [
    {
      "id": "obj_001",
      "visual_type": "photo",
      "description": "左上方竖向人物照片",
      "text_content": null,
      "confidence": "high"
    },
    {
      "id": "obj_002",
      "visual_type": "photo",
      "description": "右侧另一张人物照片",
      "text_content": null,
      "confidence": "high"
    },
    {
      "id": "obj_003",
      "visual_type": "decoration",
      "description": "右下角猫咪贴纸",
      "text_content": null,
      "confidence": "high"
    },
    {
      "id": "obj_004",
      "visual_type": "text",
      "description": "底部英文标题",
      "text_content": "SUMMER",
      "confidence": "medium"
    }
  ],
  "questions": []
}
```

---

# 6. inventory visual_type

只允许以下枚举：

```text
photo
text
decoration
shape
line
background_candidate
unknown
```

模型不能自由创造新的 type。

---

# 7. Pass 1 的关键规则

这些规则必须保持简单。

## 7.1 先独立列对象，不合并

视觉上明显独立出现的主要对象，先各自列为 object。

例如参考中有四个人物照片：

```text
人物照片 A
人物照片 B
人物照片 C
人物照片 D
```

inventory 中应该至少有四个独立 photo object。

此阶段不要因为：

* 相邻
* 同主题
* 同颜色
* 都是人物
* 都属于同一个拼贴区域

而合并。

---

## 7.2 客户可能替换的主体优先不要漏

如果一个区域明显是一张拼贴照片，即使后续还不确定是不是 customer slot，也应该先记录成：

```text
visual_type = photo
```

Pass 1 不需要决定它是不是客户最终要替换。

先保证“看见”。

---

## 7.3 贴纸仍然是 decoration

例如：

* 猫贴纸
* 可颂贴纸
* 花朵贴纸
* 星星
* 胶带
* 手绘箭头

即使内容是一个具体物体，也不要因为它“是一张图片”就标成 photo。

---

## 7.4 文字单独记录

可辨认文字记录：

```json
"text_content": "..."
```

不确定时：

```json
"text_content": null
```

不要猜。

---

## 7.5 不确定可以 unknown

不要逼模型一定做出判断。

例如：

```json
{
  "visual_type": "unknown",
  "description": "右侧半透明区域，可能是装饰底板",
  "confidence": "low"
}
```

同时写进 questions。

---

## 7.6 第一版 inventory 不需要 bbox

这是本次重构的重要约束。

不要在 Pass 1 同时要求：

```text
识别对象
+
精确定位
```

Pass 1 先把：

> 有什么

搞明白。

geometry 继续放到 draft 阶段。

---

# 8. inventory validator

新增 deterministic validator。

建议实现：

`validate_inventory`

或放进现有 analysis 模块。

程序只负责结构校验，不负责视觉判断。

至少检查：

* version 正确
* objects 非空
* ID 唯一
* ID 格式合法
* visual_type 属于枚举
* confidence 属于枚举
* text_content 类型正确
* questions 类型正确
* unknown 允许存在

validator 不自动修改模型结果。

---

# 9. Pass 2：Production Draft

Pass 2 输入：

```text
reference image
+
inventory.json
+
用户任务说明 / 客户素材信息
```

目标：

> 把已经发现的视觉对象转换成现有 draft.json。

Pass 2 不应该重新从零枚举画面。

如果认为 inventory 有明显遗漏，应通过 question / review 返回，而不是悄悄创造大量新的视觉对象。

---

# 10. Pass 2 要做的事情

只做以下制作决策：

1. 哪些 inventory photo 是客户可替换 slot
2. 哪些 text 是 editable text
3. 哪些对象属于 decoration
4. decoration 怎么组成 production unit
5. 最终 background 是什么
6. 每个制作单元的粗 source_rect
7. layer_order
8. questions

最终仍使用现有 `draft.json` schema：

```text
background
slots
texts
overlays
layer_order
questions
```

不要为了这次重构重新设计 draft schema。

---

# 11. Pass 2 的客户 slot 判断原则

`customer slot` 判断依据不是：

> 这是不是写实照片？

而是：

> 用户未来是否应该用自己的素材替换这里？

例如：

```text
拼贴中的人物照片
→ slot

拼贴中的风景主体照片
→ 可能是 slot

猫咪贴纸
→ decoration

可颂贴纸
→ decoration

拍立得边框
→ decoration

拍立得里的照片
→ slot
```

---

# 12. Decoration grouping 简化

不要继续给 Flash 一大套复杂 grouping 法规。

Pass 2 中，对于 decoration，只需要围绕三个问题判断：

```text
是否需要独立编辑？
是否跨越其他主要元素？
是否需要独立控制 z-order？
```

如果都否：

```text
可以作为一个 overlay 制作
```

如果任意一个是：

```text
需要独立 production unit
```

不要要求模型记大量特殊情况。

---

# 13. Background 判断必须晚于主体判断

Pass 2 的处理顺序必须明确：

```text
1. 先处理 inventory 中所有 photo
2. 再处理 text
3. 再处理 decoration
4. 最后判断 background
```

不要一开始看到大面积人物/照片就判断成 background。

背景定义应更接近：

> 把主要照片、文字、装饰拿掉之后，真正剩下的底层视觉是什么？

---

# 14. Geometry 仍然只是粗定位

Pass 2 输出现有：

`source_rect`

但只要求：

> 完整覆盖制作单元。

不要要求 Flash 做像素级定位。

后面继续交给：

`refine_layout.py`

进行本地 geometry refinement。

明确职责：

```text
Qwen：
决定“这个对象是什么、完整范围大概在哪”

CV refine：
细化已有对象的边缘
```

CV refine 不负责发现漏掉的对象。

---

# 15. draft completeness validator

在 Pass 2 之后、进入 refine 前，增加一个程序检查。

不要依赖模型自己判断 draft 是否合理。

建议增加 `validate_draft_against_inventory`。

至少检查：

## 15.1 inventory coverage

每个重要 inventory object 必须有去向：

```text
slot
text
overlay
background
明确标记 ignored + reason
question
```

不允许对象无声消失。

---

## 15.2 photo coverage

如果 inventory 有多个明显 photo object：

程序至少检查它们是否在 draft 中都有明确去向。

例如：

```text
inventory:
4 photos

draft:
1 background + 0 photo slots
```

应标记高风险：

```text
POSSIBLE_SLOT_OMISSION
```

程序不自动判断一定错。

但必须触发 review。

---

## 15.3 decoration consistency

如果一个 overlay 引用了多个 inventory object，需要记录成员。

不要出现：

```text
模型说合并了几个装饰
但我们不知道合并了哪些
```

建议在 draft 生成阶段保留 development-only mapping，例如：

```json
{
  "overlay_id": "decor_group_a",
  "source_objects": [
    "obj_003",
    "obj_006",
    "obj_007"
  ]
}
```

如果不希望修改正式 draft schema，可以放在：

`analysis/draft_mapping.json`

而不是最终交付文件。

---

# 16. Targeted Review

如果 inventory 或 draft validator 发现明显高风险问题，不重新跑全部流程。

只做一次定向复核。

## 情况 A：Inventory 本身漏对象

例如人工或程序证据明确：

```text
inventory 看起来只有 2 个 photo，
但参考明显存在多个独立 photo 区域
```

允许重新执行 Pass 1 一次。

但不要无限重试。

---

## 情况 B：Inventory 正确，draft 分类错误

例如：

```text
inventory:
4 个 photo

draft:
只有 2 个 slot，
另外 2 个被吞进 background
```

不要重跑 Pass 1。

只重新运行 Pass 2，明确告诉模型：

```text
inventory 已经确认有 4 个独立 photo object。
请重新检查这些 photo 在 draft 中的业务角色。
不要重新枚举视觉对象。
```

---

## 情况 C：只有 bbox 错

inventory 和 draft 分类正确：

```text
只修 source_rect
```

不要重新识别和分类。

---

# 17. 调用次数策略

不要固定要求一次，也不要固定要求四次。

推荐：

## Fast Path

```text
Pass 1 inventory
Pass 2 draft
validators pass
→ refine
```

模型调用：

**2 次**

这应该成为正常路径。

---

## Review Path

如果其中一个阶段有明确问题：

```text
Pass 1
Pass 2
+
一次 targeted review
```

模型调用：

**最多 3 次**

---

## Stop

同一问题复核一次仍无法解决：

```text
not_ready
```

不要继续第四、第五次绕圈。

---

# 18. 为什么这里建议正常路径用 2 次而不是 1 次

这次重构的核心就是：

> 把“观察”与“制作决策”分离。

因此不要重新把 inventory 和 draft 塞回一个模型调用。

第一步：

```text
只要求看清楚图里有什么
```

第二步：

```text
基于已经列出的对象决定怎么制作
```

这两步本身就是不同的认知任务。

对 qwen3.8-flash 来说，分两次更容易稳定。

我们接受多一次模型调用，以换取更好的可诊断性和更少的整体返工。

---

# 19. 不新增复杂 intermediate schema

本次只新增：

```text
inventory.json
```

继续复用：

```text
draft.json
project.json
```

不要新增：

```text
classification.json
groups.json
background.json
geometry.json
analysis.json
```

除非后续真实数据证明必须拆。

本次目标是最小架构升级。

---

# 20. 新流程最终形态

```text
Reference
    ↓
Pass 1
    ↓
inventory.json
“图里有什么”
    ↓
inventory validator
    ↓
Pass 2
    ↓
draft.json
“这些东西怎么制作”
    ↓
draft-vs-inventory validator
    ↓
必要时 targeted review
    ↓
现有 refine_layout
    ↓
review
    ↓
finish
```

---

# 21. 状态保护

当前真实测试中出现过 Agent 直接修改 `.state` 中工具结果的问题。

本次至少实现轻量保护：

重要 state 文件保存：

```text
input_hash
payload_hash
```

CLI 读取时验证。

如果内容被外部改写：

```text
STATE_TAMPERED
```

不得信任修改后的：

```text
result.ok
finished
input fingerprint
```

不要顺便设计复杂数据库。

hash verification 足够。

---

# 22. 与现有代码的关系

尽量保持以下模块不动：

```text
models.py 中 Project / Patch / Permissions
core.py
render.py
service.py
export.py
project.json
Qwen Image 调用
任务 revision
asset hash
atomic writes
```

主要修改范围：

```text
SKILL.md
references/analysis.md
analysis.py
analysis_session.py
相关 CLI / helper
相关 tests
```

新增：

```text
inventory schema
inventory validator
draft-vs-inventory validator
必要的 mapping / targeted review helper
```

---

# 23. 测试优先使用现有失败样例

优先使用真实已经失败过的参考图，而不是造简单 synthetic test。

至少覆盖：

## Case A：多个人物照片

参考有四张人物照片。

要求：

```text
inventory:
至少四个独立 photo objects
```

draft 阶段：

```text
这些主体不能被整体吞进 background
```

---

## Case B：猫 / 可颂贴纸

要求：

```text
inventory:
decoration

draft:
overlay
```

不能变 customer slot。

---

## Case C：拍立得

要求：

```text
内部照片：
photo → slot

外框：
decoration → overlay
```

---

## Case D：半透明底板

inventory 可以：

```text
shape / decoration
```

draft 根据层级需要决定是否独立 overlay。

---

## Case E：跨区域长线

inventory：

```text
line
```

Pass 2 source_rect 必须覆盖完整可见范围。

CV refine 不能被要求补一条模型根本没发现的线。

---

## Case F：unknown

无法可靠判断的对象：

```text
unknown
+
question
```

不得静默归为 background。

---

# 24. 评估指标

不要只统计 schema pass。

人工评估：

```text
major object recall
photo recall
customer slot recall
wrong customer slot count
wrong background count
major decoration omission
over-merge count
over-split count
major bbox cut count
```

优先级：

```text
P0：主要照片不要漏
P1：贴纸不要变 slot
P2：主体不要误进 background
P3：主要装饰不要消失
P4：grouping 合理
P5：bbox 精度
```

不要为了边缘精度牺牲语义完整性。

---

# 25. A/B 测试

暂时保留旧流程。

加入开发期开关，例如：

```text
analysis_pipeline = legacy | inventory_v1
```

同样一批真实参考：

```text
legacy
vs
inventory_v1
```

比较：

```text
准确率
模型调用次数
工具调用次数
平均耗时
人工修改量
not_ready rate
```

不要只比较 wall-clock。

如果新方案多一次模型调用，但显著减少错误和返工，仍然属于成功。

---

# 26. Phase 实施顺序

## Phase 1 — inventory schema

先实现：

```text
inventory schema
validator
测试
```

不改生产流程。

出口：

> 可以可靠保存和校验 inventory.json。

---

## Phase 2 — Pass 1

增加新的 inventory prompt / workflow。

用现有真实失败样图测试。

此阶段只看：

> “画面里有什么”是否比当前流程稳定。

不要急着接 draft。

---

## Phase 3 — Pass 2

让模型基于：

```text
reference + inventory
```

生成现有 draft。

不要重新设计 draft schema。

---

## Phase 4 — inventory ↔ draft validator

确保 inventory 中重要对象不会无故消失。

加入有限高风险错误：

```text
POSSIBLE_SLOT_OMISSION
POSSIBLE_WRONG_BACKGROUND
OBJECT_DROPPED
UNKNOWN_UNRESOLVED
GEOMETRY_MISSING
```

---

## Phase 5 — Targeted Review

如果：

```text
inventory 错 → 只修 inventory
draft 错 → 只修 draft
geometry 错 → 只修 geometry
```

最多一次。

---

## Phase 6 — 接回 refine_layout

继续使用当前：

```text
refine
preview
review
finish
```

不要重写。

---

## Phase 7 — A/B 实测

用真实样例比较 legacy / inventory_v1。

只有有实际证据证明新流程更好，再考虑淘汰 legacy。

---

# 27. 第一阶段成功标准

不要求一次成功。

只要求：

1. 多人物照片识别明显改善；
2. 贴纸误判 slot 明显减少；
3. wrong background 明显减少；
4. 主要视觉对象更少无声消失；
5. 出错后可以知道是 inventory 错还是 draft 错；
6. 不再需要整体重新分析才能修一个局部错误；
7. qwen3.8-flash 的任务从“设计完整工程”缩小为两个明确任务；
8. 现有 draft / project / renderer 不被破坏。

---

# 28. Codex 开始实现前

先阅读：

```text
SKILL.md
references/analysis.md
references/project.md
scripts/collage_recreate/analysis.py
scripts/collage_recreate/analysis_session.py
scripts/collage_recreate/models.py
当前 analysis 相关 tests
STATUS.md 最近真实 qwen3.8-flash 批跑结果
```

然后先给出一个简短 implementation map：

```text
1. 哪些现有文件保持不动
2. inventory schema 放在哪里
3. Pass 1 接口怎么加
4. Pass 2 如何继续兼容现有 draft
5. legacy / inventory_v1 如何切换
6. Phase 1 准备修改哪些文件
7. 先写哪些测试
```

不要重新设计整个仓库。

然后直接从 Phase 1 开始实现。

---

# 29. 最核心原则

整个重构只解决一个问题：

> 当前模型一边观察画面，一边做制作决策，导致它在“看见什么”和“怎么制作”两个任务之间互相干扰。

新的流程强制：

```text
Pass 1：
只看见。

Pass 2：
再决定怎么做。
```

也就是：

```text
inventory.json
= 我看到了什么

draft.json
= 我准备怎么制作
```

这是本次唯一需要新增的核心抽象。
