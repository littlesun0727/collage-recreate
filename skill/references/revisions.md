# 交付后按客户要求修改

已有任务交付后，客户可以继续要求换照片、改独立文案、裁切、位置、大小和遮挡层级。每轮依据当前 `scene.json`、`result.render_id` 和客户最新要求修改；不要重新 prepare/build 抹掉历史调整。首版的一轮调整限制不禁止客户另发一轮新要求。

沿用 review 格式，`items[].changes` 新支持：

- `asset_id`：仅照片对象，必须来自当前 `prepared/catalog.json`，源文件哈希仍需正确；默认清除旧照片的裁切和镜像，明确写在本轮 changes 中的值保留。
- `text`：仅独立本地文字，长度1–2000，不能修改提取贴纸、嵌入文字或生成素材中的文案。
- 位置、裁切、层级仍遵守 [review.md](review.md) 的几何约束。提取装饰只允许整体平移；缩放照片中的人物应使用合适的 `source_crop`，不能用移动裁切中心代替缩放。

工作台使用两个受校验的命令，将失败隔离在工作副本中：

```text
PY SKILL/scripts/workflow.py revision-prepare --run RUN --request-id REQUEST_ID --file PLAN.json
PY SKILL/scripts/workflow.py revision-commit --run RUN --request-id REQUEST_ID --file REVIEW.json
```

`revision-prepare` 校验基础 render_id，复用素材，在 `RUN/chat/requests/REQUEST_ID/candidate` 创建修改后成图。当前成图和版本快照不变。查看候选完整成图、前后对照及局部后，使用候选 render_id 写真实复核；仍有问题就 needs_changes。

`revision-commit` 再次核对基础版本与候选哈希，登记候选复核后发布新版本；文件写入失败恢复旧产物。提交已完成时重试不重复生成版本。期间原任务有其他修改则拒绝发布，不能悄悄把旧请求应用到新版本。

工作台的持久化队列、对话和模型调用位于 workbench，skill 只接收结构化操作并负责制作与校验。客户消息不作为 shell 命令执行。此流程不提取或生成新素材，不支持从历史图片快照直接恢复编辑分支。

中断发布的备份记录在 `RUN/chat/commit.json`；工作台仅可对原 request_id 执行 `revision-recover --run RUN --request-id REQUEST_ID` 后重试。未知写锁或仍存活的写进程不得擅自清除。
