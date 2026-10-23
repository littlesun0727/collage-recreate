# 图像执行引擎

上层流程先决定“是什么、放哪里、用哪张照片”，本目录执行图像操作。初次阅读主流程时不需要从这里开始。

`scripts/workflow.py` 是本地命令入口，由 `collage/workflow/context.py` 和工作台调用；它不是另一个 VLM 工作流。`schemas/` 定义引擎持久化数据格式。

| 能力 | 主要实现 |
| --- | --- |
| 准备图片、联系表、预览 | `prepare.py`、`observation.py`、`review_previews.py` |
| 照片窗口、抠图、描边、渐隐、关联前景 | `photo_windows.py`、`photos.py`、`photo_links.py`、`window_drawing.py` |
| 固定素材提取、批次计划和检查 | `reveal.py`、`reveal_plan.py`、`reveal_grouped.py`、`reveal_chain.py`、`reveal_assets.py`、`asset_gate.py` |
| 提取失败后的本地处理 | `extraction.py`、`frame_fallback.py`、`background_fallback.py`、`screening.py`、`recovery.py` |
| 场景、图层、字体和合成 | `scene.py`、`render.py`、`overlays.py`、`text.py`、`effects.py`、`patterns.py`、`paths.py` |
| 用户编辑、素材修复/重制 | `editor_scene.py`、`manual_edit.py`、`revision.py`、`asset_repair.py`、`repair_worker.py`、`generation/` |
| 视频与动图 | `live_media.py`、`motion_io.py`、`motion_render.py` |
| 共用 I/O、锁和校验 | `common.py`、`validate.py` |

这些模块由首版制作、画布编辑、素材操作或动图功能引用。首版中暂未触发的编辑能力仍是工程功能，不因某个示例没有调用就删除。
