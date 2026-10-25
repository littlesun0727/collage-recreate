# 拼贴素材下载工作流

采集、离线初筛、VLM 审核独立运行，使用 SQLite 和完整落盘文件交接。代码在本目录；图片、Live 视频、登录状态、数据库、日志及 HTML 产物放在外部数据目录。

## 安装（Windows / Python 3.13）

```powershell
Set-Location E:\codes\collage-recreate\template_download
.\setup-mediacrawler.ps1
$py = '.\.venv-mediacrawler\Scripts\python.exe'
& $py setup_codex_cli.py
```

采集使用本机 Chrome；审核使用本机已登录的 Codex。CLI 版本固定在 codex-cli.lock.json。MediaCrawler 固定版本及上游许可证见 mediacrawler.lock.json，上游源码由安装脚本下载，不提交仓库。

## 数据位置

默认根目录：**E:\datas\collage-recreate\template_download**。

```text
data/browser-profile/    独立 Chrome 会话（不提交）
data/pipeline-1000/      主数据集、SQLite、图片、Live、裁图、图库
data/...                既有 PoC 数据及报告
archive/                早期实验脚本、历史 README 和验收记录
migration-inventory.json 迁移前逐文件 SHA256 清单
```

`COLLAGE_DOWNLOAD_HOME` 环境变量可覆盖根目录；所有阶段使用同一配置。`--data-dir` 可指定单个数据集，浏览器仍使用根目录下 data/browser-profile。路径统一在 paths.py 定义，不依赖旧路径或目录链接。

## 日常入口

```powershell
& $py pipeline.py status
& $py pipeline.py login
& $py pipeline.py collect --collect-calls 200 --target 1000 --resume-access
& $py pipeline.py screen
& $py pipeline.py review --flush
& $py pipeline.py export
```

- login：打开采集用的同一 Chrome 会话；手动完成登录/验证后，在终端按 Enter 关闭。
- collect：只下载原图、Live 封面与 MP4，排除普通视频；不初筛、不调用模型。target 是累计原始封面数，不是最终通过数。collect-calls 默认 0，仅下载既有待下载项。
- screen：离线校验解码、尺寸、精确哈希与近似哈希；Live 等封面和视频齐全。
- review：每累计 100 个候选审核；分类 20 张/调用，裁剪定位 5 张/调用。flush 处理不足 100 张的尾批。
- export：更新 accepted.jsonl、candidates.jsonl、gallery.html。

默认 gpt-6-luna / low / Standard。定位上传原尺寸图，bbox 为 0–1000；程序寻找附近边界，再按标准比例小幅收缩，需要扩展则跳过比例修正。请求间隔及既有并发保持原配置。

## 恢复与测试

重复运行同一阶段即可续跑。普通采集进程故障最多重启 2 次，单次硬超时 360 秒；验证/访问限制立即保存状态退出。resume-access 只解除本地暂停标记，不会完成网站验证。已成功模型调用缓存复用。

详细说明见 [STAGED_WORKFLOW.md](STAGED_WORKFLOW.md)，未来优化计划见 [LATENCY_OPTIMIZATION_PLAN.md](LATENCY_OPTIMIZATION_PLAN.md)。

```powershell
& $py -m unittest discover -s tests -q
```

只提交代码、配置、锁文件、测试和文档；数据、会话、虚拟环境及第三方二进制均不提交。
