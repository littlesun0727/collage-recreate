# 工作台进度记录

制作仍使用当前 skill 的命令。工作台读取任务内的 `events.jsonl` 和 `observability/versions`，不启动工作台也会保存记录。浏览器关闭不影响制作。

prepare 完成后、开始看图前记录分析开始：

```powershell
& $py "$skill/scripts/workflow.py" progress --run $run --stage 2 --status running --summary '正在查看参考图与客户照片，分析布局和照片对应关系'
```

完成 JSON 校验、实际查看当前叠框并完成必要纠偏后，再记录 stage 2 complete。仅分析任务附加 `--analysis-only`，随后交付分析产物并停止制作。

| 实际边界 | stage | status | summary 内容 |
|---|---|---|---|
| 开始查看参考图和客户照片 | 2 | running | 当前分析动作 |
| 校验及叠框看图完成 | 2 | complete | 布局和绑定结果及必要说明 |
| 首版后开始检查素材 | 4 | running | 检查哪些问题 |
| 执行 screen 后或无需筛选 | 4 | complete | 实际处理结果，或检查完成无需调整 |
| 开始整图复核或检查调整后图 | 5 | running | 当前检查动作 |
| 当前版本执行 review | 自动 | 自动 | review 负责登记实际结论 |
| 汇总并交付当前已复核版本 | 6 | complete | 改动摘要、近似及剩余问题 |

使用同一命令更换 stage、status 和 summary。遇到确实阻塞可记录 blocked；等待外部结果可记录 waiting。只记录有意义的动作变化，不逐笔描述思考。summary 面向客户，不含凭据和本机绝对路径。

build、screen、apply、render 等命令记录执行状态，并在成图完成后发布不可变快照。review 关联对应成图，不能作为排版修改命令。变更仍由原有 screen/apply/recover/generate 规则约束。

所有任务输出留在 skill 外。`COLLAGE_OBSERVE=0` 可关闭观察记录；该模式不会生成完整工作台历史。进度记录失败时终端及工作台标注记录不完整，不能将缺少的记录说成已留存。

本机启动工作台（在仓库根目录）：

```powershell
& $py -B -m workbench --runs D:/codes/collage_outputs --port 8790
```

访问 `http://127.0.0.1:8790`。可用多个任务根目录或单个任务路径限定展示范围。工作台只提供读取和下载，不发起提取或修改制作任务。
