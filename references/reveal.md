# 提取与回填一次build，局部修复按需执行

```text
PY SKILL/scripts/workflow.py reveal-plan --run RUN
PY SKILL/scripts/workflow.py build --run RUN --reveal-cache DOWNLOADED_CASE --reveal-layout legacy
PY SKILL/scripts/workflow.py build --run RUN --reveal
PY SKILL/scripts/workflow.py build --run RUN --reveal --reveal-layout full
```

reveal-plan仅生成本地分组JSON和预览，不请求API；正常build --reveal默认grouped裁切模式，full为整图+照片辅助框对照，legacy读取历史整图overlay缓存或重现旧请求。参数保存在RUN/reveal-config.json，后续无参数build沿用旧配置；旧配置缺layout仍按legacy解释。`--no-reveal`切回纯本地。key从环境360_API_KEY或`--reveal-key-file`（默认D:/codes/.env）读取，日志不包含密钥。

grouped由代码依据现有photo_id、parent_id/embedded_in组织不可拆组合，再从完整区域出发比较1、2、3组的空间划分。单张图最多3次提取提交，每次最多20框，均为硬约束；10框仅为软目标，不为凑10框强拆。不增加analysis字段或模型规划。选择时综合请求次数、预计原生像素损失和零碎单元素组，不再强制大元素独立。相框和关联photo同批；大范围装饰可携带实际扩框范围覆盖的小照片窗口，满版照片不作辅助框。组内辅助框去重，辅助照片返回层不参与回填。完整分析元素不切碎。

无法在3组×20框内保留全部关联时，计划标记blocked且不提交任何部分组。执行层也持久记录每个RUN的提交名额，未知提交占用名额，续查同一任务不重复计数，最多3次；不能通过更改分组/重启继续提交第4次。此上限指提取任务提交，不是状态查询或下载次数。用户要求暂停远端时只用reveal-plan查看，不运行build --reveal。

overlay每边扩框、photo保持原框；组外接矩形再加少量上下文，裁切收益不足时使用整图。实际上传原尺寸裁切及局部bbox。局部返回层按实际画布尺寸还原到裁切大小，再贴回原图坐标的透明画布；保留局部像素缩放记录，按需清理余量由修复记录显式指定。最多两批并发。完整参考哈希、裁切、全部辅助框、参数和计划版本共同决定缓存，grouped不自动复用legacy缓存。grouped缓存路径为旧RUN/assets/reveal。

目标由analysis的复杂overlay派生，计数包含照片辅助框；相框也是独立overlay，photo只表示照片内口。历史一体相纸可用legacy模式，新分析不使用这一表达。简单local不新增请求；legacy缓存已经包含时可以复用。接口使用ecommerce_layer v1.1、crop、steps 28、seed 42；返回boxes_mapping_index对应请求顺序，图层从01开始，00背景不参与合成。

overlay请求默认每边外扩原宽/高的10%，宽高最多成为原来的120%，向外取整并裁到图像边界；photo辅助框不扩，过小而不满足接口最低尺寸时明确报告。analysis及照片窗口保持原始坐标，不要求模型多写框。`--reveal-padding 0.1`可显式指定。assets/reveal/request-plan.json保存分组、裁切与原图/局部bbox，grouped/batch_*/保存input.png、group.json、请求响应、原始层和restored整图层。旧版小框缓存需legacy模式并显式加`--reveal-padding 0`。

下载缓存必须与参考图像素一致。复用完整RGBA画布，按返回画布尺寸映射回参考原尺寸，不将alpha裁剪图拉伸至bbox，不重复旋转或加阴影。assets/reveal/index.json记录来源哈希、缩放、窗口和回退；scene加载会核验这些文件。

提取检查表显示扩大后的请求范围，合成保留完整图层，不把新增边缘又裁回原框。扩大框可减少因定位偏小造成的缺边，也可能引入邻近素材；仍须在集中看图时检查完整性和污染。API任务timing.json与run/events.jsonl记录提交、查询、轮询等待、下载及整段API耗时，与模型和本地处理时间分开。

默认保留返回层原始像素。规则矩形/圆角/旋转窗口仍限制客户照片的显示范围，相纸置于对应客户照片上方；不自动擦除提取层，不扩大照片bbox。单窗用photo_id，多窗使用photo.parent_id建立关系。覆盖多张照片、面积大或相框关系不明仅提示检查，不因此整块回退；缺失、近透明空层或缓存不匹配仍登记缺口。看见窗口像素也可能是合法装饰，透明度统计不是旧人物识别。

整图复核发现碎片错分、错层或旧照片残留时，使用[局部恢复](recovery.md)。`recover`只读取已有设计层，支持组合选区与指定规则窗口清理，强制离线并保留first.png。组合恢复不直接采用00背景层或辅助照片层，也不保证补回服务根本未返回的内容。

接口任务ID与下载结果可续用；超时后继续相同RUN，不重复提交未知结果的任务。已经被服务过滤掉的目标不会自动再次付费请求。缓存bbox变更、任务失败、未知提交结果均保留原因；需要人工确认状态或显式使用新的任务目录再试。一次请求默认等待最多360秒，可用`--reveal-timeout`调整。API失败保留完整首版和明确缺口，不宣称提取成功。

返回尺寸可能被服务分别取整，导致长宽比变化。超出原长宽比容差时，只有响应的`image_boxes`与`resized_image_boxes`逐项符合实际返回画布的横纵缩放（最多2像素取整误差），才允许还原；无证据或映射不一致仍报错。修复适配后可用已下载任务离线回填，不必重新提取。

正常路径没有新的agent阶段：联合分析 → 一次build → 集中复核。离线缓存成图时间、远端提取时间、模型分析/复核时间分别记录，不能用缓存速度代表首次端到端速度。
