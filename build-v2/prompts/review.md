检查独立拼贴资源：联系表左侧为参考，右侧为制作结果。按对象、制作计划和 content_scope 判断，不要求复制属于其他对象的照片、文字或装饰。
只返回以下结构，不返回任务版本、证据摘要、reviewer 或 scope_usable/template_ready 等汇总字段，这些由程序填写：
{"reviews":[{"key":"对象key","status":"passed","issues":[],"observed_text":"","unexpected_content":[]}],"combination_issues":[]}
每个 required_keys 中的 key 恰好一次。单项 status 只用 passed（通过）或 needs_changes（需修改），不用 accepted；accepted 是程序汇总后的任务状态。passed 时 issues=[]；有问题写具体位置和原因。combination_issues 列本任务对象在组合中的连接、位置或遮挡问题，无则 []。仅检查本次 required_keys，未制作的邻居和明确标注的照片占位不算漏画。
检查形状、数量、相对排列、配色、清晰度、边缘和准确文字。允许已声明且符合基本风格的字体/笔触近似；明显缺失、错误比例、残留背景、错字或切掉主体为 needs_changes。photo、cutout slot 在 build 中均为占位，人物抠像与身形检查由 renders 完成，不因占位判资源漏画。
材质和字形也是验收内容：实体纸面不应出现透出背景的碎点或孔洞；以纸纤维、磨损、厚度为辨识特征的标签不能仅因轮廓相似就通过。文字内容正确不代表字体风格、行距或比例正确。给出能执行的主要问题，避免逐轮追加不可见的微小差异。
生成相框只做视觉检查：框线完整，内部干净透明，没有照片、底板或不属于它的字；不要求精确开口坐标。最终照片对齐、旋转和透视由 renders 验证，不在资源阶段假装已完成。
所有制作方法（draw、text、feather、generate）都必须返回 observed_text 和 unexpected_content：逐字抄录右侧实际文字（无字为 ""，无法可靠读全为 null），不能照抄左侧或 allowed_text；unexpected_content 列多余文字、照片、邻居或内衬，无则 []。不得以 passed 覆盖准确文字、内容归属或组合关系中的具体失败项。
生成图中的字必须与 allowed_text 一致：纯框中的 Summer 即使拼写正确也应失败；目标本身是文字或自带字贴纸时，不可一律禁字。不要因检查标签或透明棋盘格判失败。只评资源，不宣称最终整稿通过。


制作单元任务：对象若有 member_keys/members，required_keys 是单元 ID；每个单元只给一个结论。检查其全部成员、exact_text_by_member 中每项文案以及内部留白、字形与装饰关系。observed_text 按 content_scope.allowed_text 的顺序用换行连接实际读到的文字，不漏词也不重复。组内文字是合法内容，不是要排除的邻居。结合 combined 对照检查单元之间大小、位置、连接、遮挡；interleaved_context_keys 提示原先穿插的层级，特别检查是否因合并遮住了外部对象。照片为占位，不因未复刻照片内容判失败。未制作的非本轮对象是上下文。
