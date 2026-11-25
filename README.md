# 拼动图 v11 · 鸿蒙 App 联调服务

上传一张参考图和一组客户静态图，取得版式分析，再取得可在 App 中逐层绘制的场景素材包。首版联调协议见 [鸿蒙接口](docs/harmony-api.md)。

```text
POST /jobs
  └─ 解码静态图，记录 App 素材 ID
      └─ analyze 一次 → 校验并编译像素布局 → analysis.json（第一份响应）
          ├─ bind：选客户图、裁切、镜像
          └─ 360 Reveal Layer：根据参考图和版式拆出固定 PNG
              两个分支汇合 → scene.json + layers/*.png → delivery.zip
```

## 工作目录

| 文件或目录 | 职责 |
| --- | --- |
| `server.py` | 接收 App 上传、返回分析和任务地址、提供状态与 ZIP 下载 |
| `collage/pipeline.py` | 准备输入、并行调度、将内部素材 ID 换回 App ID、打包 |
| `collage/vlm.py`、`prompts/` | 单次 analysis 和 binding 请求，以及从 v10 保留的两份提示词 |
| `collage/layout/`、`contracts/` | 沿用 v10 的背景归属、窗口几何、层序和出框编译逻辑；保存分析和选图约束 |
| `collage/binding.py`、`crop_quality.py` | 照片槽、独立选材、裁切及关联前景共用素材 |
| `collage/reveal.py`、`reveal_transport.py` | 固定素材拆层、服务返回索引映射、残余底图、下载与提交记录 |
| `docs/`、`tests/` | App 协议和离线联调测试 |

运行数据默认写入相邻的 `collage-recreate-v11-output/jobs/<job_id>/`。其中 `prepared/`、`catalog.json`、`reveal/` 属于服务端；App 读取 `analysis.json` 和 `delivery.zip`。可用 `COLLAGE_RUNS` 设置持久化目录。

## 启动

在本目录用 Python 3.11+ 创建虚拟环境，安装 `requirements.txt`。配置当前进程的环境变量：

| 名称 | 内容 |
| --- | --- |
| `COLLAGE_VLM_URL` | 可选。默认直连 `https://yibuapi.com/v1/chat/completions`。旧的本机 17860 审计地址自动切换为 Yibu 官方地址。VLM 客户端禁用系统 HTTP 代理。 |
| `COLLAGE_VLM_MODEL` | 该端点可用的视觉模型 ID |
| `COLLAGE_VLM_KEY` | 该端点的服务端凭证 |
| `360_API_KEY` | 360 Reveal 服务凭证 |

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -B server.py
```

默认监听 `127.0.0.1:8811`。同一可信局域网的真机联调可用 `--host <电脑的局域网IP>`。此入口用于研发联调，公网部署需要单独接入鉴权、HTTPS 和任务生命周期管理。当前任务调度在进程内，重启会中断在途任务，已发布的任务包可继续读取。

```powershell
.\.venv\Scripts\python -B -m unittest discover -s tests -v
```

测试替换供应商网络调用，检查 HTTP 两阶段交付、分支并发、PNG 与素材 ID、空槽和失败路径。
