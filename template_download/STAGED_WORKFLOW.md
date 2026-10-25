# 阶段与恢复机制

| 组件 | 责任 |
|---|---|
| pipeline.py / stage_cli.py | 日常入口、阶段锁、调度、检查点及状态 |
| paths.py | 外部存储根目录和默认数据集 |
| mediacrawler_worker.py / mediacrawler_adapter.py | 独立采集进程、脱敏诊断、笔记导入 |
| stage_media.py | 原始文件下载与离线初筛分离 |
| store.py / quality.py / live_media.py | 持久化、质量和哈希、Live 校验 |
| vlm_batch.py / vlm_stages.py | 模型运行时、匿名输入及两阶段提示词 |
| crop_review.py / edge_snap.py | 裁剪与边界后处理、最终去重 |
| dataset_preview.py / crop_report.py | 图库和裁剪报告 |
| reprocess_crop_policy.py | 手动重放已有裁剪判定的维护工具 |
| run.py / download.py / live_download.py | 旧接口兼容及辅助函数，新主线不调用旧串行下载入口 |

早期 benchmark/PoC 脚本及历史说明归档至外部 archive/，不作为主线入口。

## 持久化与恢复

原始文件原子落盘后 raw_assets 才标记 ready；初筛后发布 images 路径。Live 必须封面和 MP4 齐全。重复素材单独记录，原始文件不删除。旧数据保留原有审核结果。

pipeline_state 保存关键词索引、页码、offset；成功批次才推进游标。失败重跑同一批，通过唯一键避免重复导入。JSONL 只允许忽略崩溃产生的未结束末行，其余格式损坏报错。

review_cache 按原图哈希、模型、推理档位及 prompt 保存成功调用，补跑只处理缺失调用。已完成最终审核的图片不自动重审。

stage_runs 保存开始、结束和结果。硬杀进程可能留下 running 记录，它不是心跳；实际运行锁由操作系统管理。各阶段独立锁，采集和登录还共享浏览器锁。

## 采集失败

- worker-timeout 默认 360 秒，超时终止工作进程及其子进程。
- worker-retries 默认 2，普通故障最多重启两次，保持搜索位置。
- HTTP 401/403/429/461/471 或明确验证要求：保存 blocked 并退出，不自动连续重试。
- 手动完成验证后，用 collect 的 resume-access 选项继续；该选项不会解决网站验证。
- 媒体下载按 max_attempts 重试；耗尽后保留 error，避免无限循环。

## 状态和导出

默认处理现有队列后退出。screen/review 的 watch 选项每 5 秒检查本地队列，Ctrl+C 停止。review 的 flush 选项处理尾批。

status 读取最新状态，各阶段的 *-status.json 为快照。图库在审核后或 export 时更新，浏览器需刷新；仅采集不会生成审核结果。

主数据集默认位于 `E:\datas\collage-recreate\template_download\data\pipeline-1000`。gallery.html 和 accepted.jsonl 位于此目录。数据库素材路径主要为相对路径；导出清单绝对路径按当前目录生成。

## 整理记录

迁移前关闭浏览器，4569 个文件约 1.28 GB 移至外部存储，移动前后逐文件 SHA256 一致。历史审计备份原样保留；当前清单和报告旧路径另行修复，主图库重建。核验记录放在外部 migration-inventory.json 和 migration-verification.json，不提交仓库。
