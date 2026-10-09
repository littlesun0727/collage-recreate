# 提取与回填一次build，局部修复按需执行

```text
PY SKILL/scripts/workflow.py reveal-plan --run RUN
PY SKILL/scripts/workflow.py build --run RUN --reveal-cache DOWNLOADED_CASE --reveal-layout legacy
PY SKILL/scripts/workflow.py build --run RUN --reveal
PY SKILL/scripts/workflow.py build --run RUN --reveal --reveal-layout grouped
```

reveal-plan与新build --reveal默认grouped空间裁图模式，比较1至3组的请求成本和预计采样收益；每张图最多3次提交，每次最多20框。full整图模式仅通过--reveal-layout full显式使用。legacy仅兼容旧缓存。已有RUN无参数build沿用原配置；对比新模式使用新RUN，不能超过已有任务提交上限。--no-reveal用于纯本地。凭证由固定工具读取，不输出密钥。

full由代码依据现有photo_id、parent_id/embedded_in组织不可拆组合。实际框数包含素材与照片辅助框，同批共享照片框去重；合计不超过20框时必须一批，超过20才按容量分批，每批仍上传完整参考图，bbox保持原图坐标。优先较少批次，同批尽量安排相近素材并减少辅助框重复；不使用10框软目标，不为分辨率收益增加批次。单张图最多3次提取提交，每次最多20框，均为硬约束。不增加analysis字段或模型规划。

full与grouped均保留相框和关联photo同批；多窗口通过photo.parent_id关联，大范围装饰可携带实际扩框范围覆盖的小照片窗口，满版照片不作空间辅助框。照片辅助框保持原始bbox，不扩框，返回的辅助照片层不参与回填。分组仅安排同批请求与区域裁图，不合并analysis中的元素ID或bbox；一组可以输出多个独立素材层。analysis先为独立元素或满足局部叠贴/连接、共同移动、层级一致的组合建框；单个元素内部笔画不切碎，也不能把远处无关联装饰打包成整页素材。grouped默认模式比较1、2、3组的空间划分，综合请求次数、预计原生像素损失、10框软目标和零碎单元素组，允许在不超过20框时为了局部分辨率拆组。

无法在3组×20框内保留全部关联时，计划标记blocked且不提交任何部分组。执行层也持久记录每个RUN的提交名额，未知提交占用名额，续查同一任务不重复计数，最多3次；不能通过更改分组/重启继续提交第4次。此上限指提取任务提交，不是状态查询或下载次数。用户要求暂停远端时只用reveal-plan查看，不运行build --reveal。

full上传整图；仅grouped模式使用组外接矩形加少量上下文，裁切收益不足时使用整图。局部返回层按实际画布尺寸还原到裁切大小，再贴回原图坐标的透明画布；保留像素缩放记录，按需清理余量由修复记录显式指定。最多两批并发。完整参考哈希、裁切、全部辅助框、参数和计划版本共同决定缓存。两种模式均通过RUN/assets/reveal提供缓存，目录中的grouped/batch_*为共用执行器的历史命名，不代表一定裁图；以request-plan.json中的mode及crop_box为准。新full计划使用独立版本，不混用旧full对照或grouped缓存；grouped保留原计划版本及缓存身份，legacy缓存仍需legacy模式。

目标由analysis的复杂overlay派生，计数包含照片辅助框；相框也是独立overlay，photo只表示照片内口。历史一体相纸可用legacy模式，新分析不使用这一表达。简单local不新增请求；legacy缓存已经包含时可以复用。接口使用ecommerce_layer v1.1、crop、steps 28、seed 42；返回boxes_mapping_index对应请求顺序，图层从01开始，00背景不参与合成。

overlay请求默认使用capped策略：令`s=min(1,1024/max(参考图宽,参考图高))`、`p=min(padding×min(原框宽,原框高),6/s)`，四边使用同一个p，左上向下取整、右下向上取整并裁到参考图边界。这里的6是把参考图长边归一到1024后的设计像素上限，不是360接口参数，也不代表已知的服务内部解析度。计算始终基于analysis原始bbox和完整参考图尺寸，包括grouped模式；不对已扩框再次外扩。接口既有的最小框安全处理仍保留，因此极小框可能为满足最低尺寸而超过上述padding上限。photo辅助框不扩，过小而不满足接口最低尺寸时明确报告。analysis及照片窗口保持原始坐标，不要求模型多写框。`--reveal-padding 0.1`可显式指定比例，`--reveal-padding-mode ratio`可复现旧版按宽、高分别向每边外扩10%的策略。新CLI配置默认`capped`；历史保存配置缺少padding_mode时按`ratio`解释，旧缓存身份不变。assets/reveal/request-plan.json保存分组、裁切与原图/局部bbox，grouped/batch_*/保存input.png、group.json、请求响应、原始层和restored整图层。旧版小框缓存需legacy模式并显式使用`--reveal-padding-mode ratio --reveal-padding 0`。

下载缓存必须与参考图像素一致。复用完整RGBA画布，按返回画布尺寸映射回参考原尺寸，不将alpha裁剪图拉伸至bbox，不重复旋转或加阴影。assets/reveal/index.json记录来源哈希、缩放、窗口和回退；scene加载会核验这些文件。

提取检查表显示扩大后的请求范围，合成保留完整图层，不把新增边缘又裁回原框。扩大框可减少因定位偏小造成的缺边，也可能引入邻近素材；仍须在集中看图时检查完整性和污染。API任务timing.json与run/events.jsonl记录提交、查询、轮询等待、下载及整段API耗时，与模型和本地处理时间分开。

原始缓存保留；候选经[素材门禁](asset-gate.md)检查后才接入合成，analysis的显式layer_order保持。photo_id、parent_id只建立关联，不把关联载体自动移到照片上方；承托底板在照片后，覆盖相框在照片前，由分析明确前后关系。规则矩形/圆角/旋转窗口限制的是客户照片显示范围，不等于已清空载体窗口；不自动擦除提取层，不扩大照片bbox。对关联的cover照片检查各自窗口内的可见像素比例，上层载体窗口仍有明显内容时记录layer_checks；超过一半窗口被占用才隔离，少量边缘内容只提示，不据此自动降层或挖洞。历史一体相纸没有显式载体层级，仅在每个窗口内可见alpha大于16的像素比例不超过5%时将派生框插到照片上方，否则保留在照片下方并记录阻止原因；该阈值仅是防错条件，不代表视觉验收。单窗用photo_id，多窗使用photo.parent_id建立关系。覆盖多张照片、面积大或相框关系不明不单独成为拒绝依据；缺失、近透明空层或缓存不匹配仍登记缺口。看见窗口像素也可能是合法装饰，透明度统计不是旧人物识别。

默认整图复核发现素材不可用时，使用[screen筛选与补画](asset-gate.md)。只有用户明确要求像素精修时才使用[局部恢复](recovery.md)。`recover`只读取已有设计层，支持组合选区与指定规则窗口清理，强制离线并保留first.png。组合恢复不直接采用00背景层或辅助照片层，也不保证补回服务根本未返回的内容。

接口任务ID与下载结果可续用；超时后继续相同RUN，不重复提交未知结果的任务。已经被服务过滤掉的目标不会自动再次付费请求。缓存bbox变更、任务失败、未知提交结果均保留原因；需要人工确认状态或显式使用新的任务目录再试。一次请求默认等待最多360秒，可用`--reveal-timeout`调整。API失败保留完整首版和明确缺口，不宣称提取成功。

远端 `done` 但下载失败时，先检查同组 `query_response.json`、`download-report.json` 和已有图层，不能把下载中断解释成模型未提取。单文件暂时性网络错误最多尝试三次，并继续下载其他文件；合法且身份匹配的已下载对象逐件回填，缺失对象保留原因。对尚未做 apply/generate 的任务，沿用原配置执行同一 RUN 的 `build --reveal` 可续下载已有任务，已有有效文件跳过，不能重新 prepare 或另提交替代任务。恢复后重新看素材和整图并登记当前版本复核；补下载和缓存合成耗时与首次端到端耗时分开记录。

返回尺寸可能被服务分别取整，导致长宽比变化。超出原长宽比容差时，只有响应的`image_boxes`与`resized_image_boxes`逐项符合实际返回画布的横纵缩放（最多2像素取整误差），才允许还原；无证据或映射不一致仍报错。修复适配后可用已下载任务离线回填，不必重新提取。

正常路径不增加独立agent：联合分析 → validate叠框看图纠偏 → build提取与内置程序门禁 → 集中复核；必要时一次screen筛选与简单补画后复核，不默认进入像素修复。离线缓存成图时间、远端提取时间、模型分析/复核时间分别记录，不能用缓存速度代表首次端到端速度。
