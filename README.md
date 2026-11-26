# 拼动图 v11 · case 目录入口

在工程根目录执行 `python pipeline.py D:\jobs\case-001`。将参考图和所有客户素材平铺于 `inputs/photos/`，在 `inputs/catalog.json` 中写入原文件名。每个客户素材的编号贯穿预处理、选材和最终场景：

```json
{
  "reference": "IMG_7412.jpg",
  "materials": {
    "materials_001": "IMG_8231.png",
    "materials_002": "IMG_9056.png"
  }
}
```

分析完成后即可读取 `outputs/analysis.json`。其余阶段完成后发布 `outputs/scene.json` 和 `outputs/delivery.zip`，ZIP 最后就位。用 `artifacts/status.json` 查看阶段和错误。用 `bindings.json`、`catalog.json` 和 `fixed-index.json` 分别追溯选图、原文件名和提取图层，这三个文件均在 `artifacts/` 内。

测试时可指定独立的结果根目录，例如 `python pipeline.py D:\jobs\case-001 D:\tests\round-02\case-001`。输入始终从第一个目录读取；第二个目录会自动创建，里面分别生成 `outputs/` 和 `artifacts/`。也可使用 `--output-dir` 指定这个目录。省略时，两者仍写在 case 下。

再次使用同一个结果根目录时，历史交付和中间产物保存在该目录的 `artifacts/history/`，新运行重新调用所需服务。异常退出若遗留 `artifacts/.running`，确认旧进程已退出后再移除该文件。目录入口和以下旧 HTTP 入口共用依赖及模型环境变量。

## 旧 HTTP 联调入口

上传一张参考图和一组客户静态图，取得版式分析，再取得可在 App 中逐层绘制的场景素材包。首版联调协议见 [鸿蒙接口](docs/harmony-api.md)。

```text
POST /jobs
  └─ 解码静态图，记录 App 素材 ID
      └─ analyze 一次 → 校验并编译像素布局 → analysis.json（第一份响应）
          ├─ Yibu binding：选客户图、裁切、镜像
          ├─ 360 Reveal Layer：按需拆出固定元素 PNG
          └─ 讯星 gpt-image-2：仅 reference_clean，使用原参考图和已有背景描述生成底图
              并行分支汇合 → scene.json + layers/*.png → delivery.zip
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
| `COLLAGE_VLM_URL` | 分析和选片的完整请求地址，可使用 IP 或域名。 |
| `COLLAGE_VLM_MODEL` | 该端点可用的视觉模型 ID |
| `COLLAGE_VLM_KEY` | 该端点的服务端凭证 |
| `360_API_KEY` | 360 Reveal 服务凭证 |
| `XUNXING_API_KEY` | 讯星生图凭证，用于 reference_clean 背景 |

Yibu 和讯星遇到超时、断连或 HTTP 408、429、500、502、503、504 时，各自最多请求三次，重试前分别等待 2 秒和 4 秒。讯星返回图片 URL 后，下载单独重试。同步生图请求遇到响应超时时，供应商可能已接受上一笔请求。360 沿用按批次的提交、查询和下载重试，已拿到任务 ID 后继续查询原任务。360 返回的映射缺少固定层时直接跳过该层。

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

## 固定批测入口

单 case、批测和原有服务启动脚本默认读取工程根目录的 `api-config.json`，也可用 `--api-config 路径` 指定文件。新检出工程可复制 `api-config.example.json` 为 `api-config.json`。更改后下次启动生效。

| 配置段 | 地址及模型 | 凭证 |
|---|---|---|
| `vlm` | `url` 填完整 chat/completions 地址，`analysis_model` 和 `binding_model` 分别用于分析和选片 | 两阶段共用 `api_key` |
| `reference_clean` | `url` 填完整图片编辑地址，`model` 选择兼容模型 | 本段 `api_key` |
| `reveal` | `base_url` 填 360 接口根地址，程序追加 submit_task 或 query_task，`model` 同时用于提交和查询 | 本段 `api_key` |

JSON 中的非空值优先，其次读取进程环境变量、工程 .env、上一级 .env 和程序默认值。`api_key` 留空时沿用已有密钥。本地配置文件已加入 .gitignore。

运行期间终端实时显示时间、case、阶段和操作。单个 case 的相同记录在 `artifacts/pipeline.log`，批测报告提供“查看运行日志”链接。长时间没有返回的 HTTP 请求每 30 秒报告一次等待时长；同一请求的开始和结束可按 `HTTP #编号` 对照 `timings.json` 中的请求 ID。

在 v11 工程目录下执行。每次启动都会先清空指定输出目录中的旧文件，再从头批测。请将该目录专用于这一批结果。

```powershell
python benchmark.py --case-dir "D:/codes/collage-recreate-v11/benchmark-202-cases" --output-dir "D:/codes/collage-recreate-v11-output/benchmark-run-001"
```

默认同时跑两个 case，可传 `--workers 4`。case-dir 可以是测试集根目录，也可以是包含 inputs 的单个 case。脚本读取环境变量、工程 .env 和上一级 .env，按这个顺序优先。设置 YIBU_API_KEY（或 COLLAGE_VLM_KEY）、360_API_KEY 和 XUNXING_API_KEY。批测直连 Yibu，默认分析模型 gpt-6.2-sol，binding 模型 qwen3.8-flash，可分别用 COLLAGE_VLM_MODEL 和 COLLAGE_BIND_MODEL 覆盖。

打开输出目录的 index.html 查看三图对照。每个 case 结束后刷新页面可看到新结果。各 case 的 artifacts/timings.json 记录阶段墙钟耗时、每次 HTTP 请求耗时和包含本地预览的端到端耗时。reveal 阶段包含 360 轮询、下载和本地处理，也可能等待清底。并行阶段耗时不能相加得到端到端耗时。批次总墙钟时间在 summary.json 的 batch_seconds。

参考图按原分辨率校正后写入 artifacts/prepared/reference.png，随后启动 analysis。同时，prepare_materials 阶段生成最长边 1024 像素的 materials_*.jpg 供 binding 使用。artifacts/catalog.json 记录原文件名、校正后的原图尺寸、原文件 SHA-256 和缩略图文件名。

artifacts/analysis-bbox.png 以橙色显示 bbox，青色显示按旋转角度定位的照片窗口。artifacts/final.png 按交付包内 scene.json 和裁切后的 PNG 回贴，客户照片按选中的 asset_id 从 inputs/photos 读取原图并校正方向。抠像层优先使用照片自带透明度，不透明照片用本地 GrabCut 估算，可能与 App 的人像分割有差异。预览失败的原因在 artifacts/preview-errors.json 及报告中。
