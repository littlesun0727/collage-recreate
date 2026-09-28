# 分析参考图

## 输入与调用

输入一张完整参考图，输出背景、照片槽、设计文字、装饰与层级草案。模型任务规则由工具读取 [analysis.md](analysis.md)；主控按本文操作。

路径占位符及模型环境见 [运行环境](../references/runtime.md)。ANALYSIS 指向本次尚不存在的输出目录。

```text
python SKILL/analysis/analyze_reference.py --reference REFERENCE --output ANALYSIS --credentials CREDENTIALS
```

工具准备参考图，调用一次现有视觉模型，然后校验返回的 JSON 并生成框线预览。当前 CLI 没有传入用户编辑要求的参数；需要替换文案或改变拆分规则时，保留要求并明确这一接口缺口，不能声称工具已经采用。用户已经提供的修订草案可先用下面的离线入口校验，再交给下游。

## 结果与交接

| 文件 | 用途 |
|---|---|
| result.json | 调用状态、草案是否合法 |
| input.json、reference.jpg 或 reference.png | 本次实际分析图及其来源记录 |
| review/parsed-draft.json | 下游使用的草案 |
| review/validation.json | 格式、版本、图像记录与待处理项 |
| review/index.html | 分类框线预览 |
| request/call.json、request/raw-response.txt | 调用记录与原始响应 |

`status=completed` 且 `draft_valid=true` 表示分析和结构校验完成，视觉状态仍为 unreviewed。查看框线预览，确认主要照片、内口、文字和装饰没有明显错位或遗漏；草案中的 questions 逐项判断是否影响后续制作。

给 binding 的参考图是 ANALYSIS 根目录中的 reference.jpg 或 reference.png，需与 validation.json 记录一致。review/reference.png 是预览用图片，重新编码后文件指纹可能不同，不作为默认交接图。

## 离线校验与修订

已有草案或原始 JSON 时，可单独校验：

```text
python SKILL/analysis/review_draft.py --reference ANALYSIS_REFERENCE --input DRAFT_JSON --output NEW_REVIEW
```

输入是完整 UTF-8 JSON，NEW_REVIEW 必须为新目录。修订保留原草案与原因，重新校验后将新路径交给下游。当前版本为 v2-2，坐标为 0–999，详细规则以 analysis.md 为准。

`draft_invalid` 时读取 validation.json 的具体字段错误；调用失败时查看 result.json 与 request/call.json。保留原始响应，截断结果不作为成功草案。工具不自动重试，重新执行使用新的输出目录。

`--dry-run` 只检查模型请求，不发送请求，也不会产出可继续制作的有效分析结果。
