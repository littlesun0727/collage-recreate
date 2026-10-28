# 离线抠图结果导入

另一台机器返回原尺寸的 8 位灰度 PNG 蒙版：白色保留、黑色透明，保留柔和边缘。不要裁剪、缩放或添加描边。本机从原始导出包取 RGB，描边、窗口及突出效果继续由原场景渲染器处理。

导入工具需要原始导出包、返回包里的 `manifest.json`、`processing-report.json` 和 `masks/<filename>`。报告包含原包 SHA256、模型 SHA256、每张蒙版 SHA256。原始包包含 `images/<filename>`。工具校验来源、尺寸、格式和校验和，跳过全黑蒙版。

在仓库目录运行（路径按实际情况修改）：

```powershell
.\.venv\Scripts\python.exe .\import_cutout_results.py --results D:\cutout-results.zip --inputs D:\collage-cutout-inputs-20261027.zip --tasks D:\collage_recreate-output\model-comparison-20261023\runtime\tasks --task-glob 'task-20261027-*' --report-dir D:\collage_recreate-output\cutout-import-20261027 --repair
```

不传 `--repair` 时只导入匹配原图和模型的任务抠图缓存；传入时还会重新合成有可修复照片降级的已导出任务，发布新版本，不调用排版模型。每个任务串行处理，旧成品与结果保存在报告目录。工具不会自动重跑失败的排版任务，也不会用空蒙版隐藏照片。

新任务建立后，可对新任务目录重新执行导入。缓存按原图 SHA256 和模型 SHA256 匹配，与模型生成的素材名字无关。更换原图或抠图模型需要重新导出对应蒙版。该工具不自动覆盖已经成功渲染的照片特效。
