# Pass 1：视觉对象清单

仅在 inventory_v1 路径的第一阶段读取。产物是 analysis/inventory.json，回答“图里有哪些主要视觉对象”。此阶段不判断客户 slot、制作组合、背景来源、图层或坐标，也不读取 Pass 2 的完整制作规则。

先看完整参考一次；必要时最多一批四个局部。清单列视觉上独立出现的主要照片、文字、装饰、形状、线条和底层候选。同类、相邻、同主题不构成合并理由；也不枚举同一贴纸的每个笔触。独立照片按出现区域分别记录；贴纸中的动物、食品、物体仍按视觉装饰判断，不因为它是一张位图就叫 photo。照片和外框可分别列对象。平台界面可以先记录，取舍在下一阶段说明。

每项用相对方位和可辨外观区分，描述可覆盖完整走向，不给 bbox。准确可辨的文字写 text_content，读不清为 null；无法判断类型用 unknown，并在 questions 写含对象 ID 的具体疑问。不要猜字或把不明确内容静默当背景。

## 格式

顶层只有 version、objects、questions。version 为整数 1，objects 至少一项；每项必填 id、visual_type、description、text_content、confidence。ID 为小写字母开头的字母/数字/下划线/短横线，最长 64，全清单唯一。description 非空；text_content 是字符串或 null。

visual_type 只允许 photo / text / decoration / shape / line / background_candidate / unknown。confidence 只允许 high / medium / low。questions 为非空字符串数组，无问题用 []。不要加入 bbox、slot、attachment、layer_order 或生成指令。

示例仅说明格式，不暗示目标对象数量：
~~~json
{"version":1,"objects":[{"id":"obj_001","visual_type":"photo","description":"上方独立照片区域","text_content":null,"confidence":"high"}],"questions":[]}
~~~

保存后运行一次：
~~~powershell
python scripts/review_inventory.py --task analysis --reference reference.png --input analysis/inventory.json
~~~

工具成功表示清单结构有效，不能证明对象没有漏掉。保存原稿；若结构错误，结合错误另存 inventory.corrected.json 后使用 --reason 描述修正。inventory / draft / geometry 共用一次修正机会，不能每个阶段各修一次。未知内容可以先带问题进入 Pass 2，但影响主体完整性的疑问未解决时须 not_ready。

成功后才读取 [Pass 2 草案规范](analysis.md) 的 inventory_v1 段落，并结合同一参考与已校验清单决定制作方式。不得为了进入下一步而改写 .state 或省略清单校验。
