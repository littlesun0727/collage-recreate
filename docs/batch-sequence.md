# 按模型顺序补跑

`run_batch_sequence.py` 依次提交 manifest 中的批次，前一批进入完成或失败状态后才提交下一批。每个配置文件使用 `batch_run.py --config` 的格式，明确列出参考图顺序、客户素材及模型参数。

```json
{
  "server": "http://127.0.0.1:18809",
  "stages": [
    {"name": "sol-check", "config": "sol-check.json", "stop_on_failure": true},
    {"name": "sol-rest", "config": "sol-rest.json"},
    {"name": "qwen", "config": "qwen.json"},
    {"name": "luna", "config": "luna.json"}
  ]
}
```

配置文件相对于 manifest 定位。运行：

```powershell
& .\.venv\Scripts\python.exe -u -B run_batch_sequence.py D:\collage_recreate-output\supplement-20261027\sequence-static.json
```

同目录的 `sequence.state.json` 保存已冻结上传的提交参数、批次 ID 和状态。中断后用同一命令恢复，使用相同 ID 重连已提交批次，不重复创建已完成任务；操作系统锁防止同一 manifest 同时启动两次。验证批失败会停止后续提交；其他批次中单个任务失败会保留失败状态，整批结束后进入下一个模型，不自动重试付费推理。

2026-10-27 静态补跑使用 22 张指定样图、历史批次 18 份客户视频的既有无损首帧及原模型策略，顺序为 Sol 6.1、Qwen3.8 Flash、Luna。每张首帧的文件哈希和 PNG 内的原视频哈希已核对，证据保存在 `*-provenance.json`。服务器缺少视频采集 DLL，首个直接导入视频的验证批失败后，改用 `sequence-static.json`；不重复调用视频解码，新增任务暂不包含动态制作。Sol 第一张单独验证成功后，再提交剩余 21 张。工作台 `buildTaskGroups` 中的 `preferredReferences` 列表决定这些样图在各模型组内的显示顺序。

运行前需要确认完整 Python 依赖、可导入的 PyAV、Node.js、`npm ci`、Codex 登录和本地 Yibu 网关。Windows Server 若缺少 `AVICAP32.dll`，仅安装 PyAV wheel 不能补齐系统媒体组件；应先修复系统依赖并验证 `python -c "import av"`。不应将批次提交成功等同于推理完成。
