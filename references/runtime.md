# 运行环境与工具配置

## 路径与依赖

操作文档的命令为参数示例。将大写占位符替换为本机实际路径，有空格的路径按当前 shell 加引号；python 替换为已安装且依赖齐备的解释器。

| 名称 | 含义 |
|---|---|
| SKILL | collage-recreate-v3 根目录 |
| REFERENCE、MATERIALS | 用户参考图及客户素材目录 |
| ANALYSIS、BINDING_DIR、PLAN_DIR | 对应阶段的独立新输出目录 |
| ANALYSIS_REFERENCE、DRAFT、BINDINGS | 上游实际交付文件 |
| BUILD、TASK、UNIT | 制作运行目录、任务 ID、单元 ID |
| CREDENTIALS | yibu 凭证文件，默认 D:/codes/yibu_credentials.local.json |
| IMAGE | 预装依赖的本地 Docker 镜像名 |

Python 依赖分别见 analysis/requirements.txt、binding/requirements.txt、build-v2/requirements.txt 和 renders/requirements.txt。阶段视觉/生图工具另需 Node.js，不依赖 @openclaw/ai、OpenClaw 安装目录或百炼 CLI。为本次任务选定依赖齐备的 Python 解释器。字体通过现有字体扫描或 --font-dir 提供；客户分割由 renders 的 BiRefNet 入口使用已有 ONNX 权重，可通过 --cutout-python 复用单独的本地分割环境。

CLI 可以用绝对路径从任意 cwd 启动。运行产物独立保存，不放进技能源码或客户素材目录。现有 Python 环境可以复用，不需要随技能复制。

## 当前会话与执行环境

Codex 或 OpenClaw 的当前会话直接担任主控，负责读取阶段说明、执行命令、查看图片、编写制作脚本和处理结果。使用当前宿主提供的文件、命令、看图及进程等待工具，不依赖另一宿主的工具名称或会话接口。命令仍在运行时等待原进程，不因宿主返回方式不同而重复启动。

主控使用当前宿主会话选择的模型。本技能不修改宿主模型、全局配置或登录方式，也不要求当前会话先执行 controller/launch.py。主控模型与下面的阶段模型是两套独立配置。

输入目录、技能目录、凭证和输出目录须在当前宿主允许的访问范围内；模型请求还需能访问本机 17860 代理。已有授权在其范围内沿用，超出权限时使用当前宿主的权限机制处理。阶段参数（包括 --allow-remote 和 --trusted-host）不改变宿主的文件或网络权限。

## 阶段模型配置

阶段工具通过 model_api 共用请求层调用 yibu，阶段视觉和生图请求固定走 http://127.0.0.1:17860；这是反向代理 API Base URL，不是 HTTP_PROXY。代理不可达时失败，不直连上游，不回退百炼，不自动重试。这些阶段工具不需要 Token Plan 凭证。

阶段 VLM 默认 gpt-5.6-sol，使用 /v1/chat/completions；图片经 image_url 内联传入，JSON 契约经 response_format.json_schema 传入。分析、素材描述、匹配、规划、复核统一 reasoning_effort=high、max_completion_tokens=32768（包含推理与可见输出），不发送 Qwen 专属 enable_thinking 字段或旧 max_tokens 字段。默认请求限时 600 秒。显式指定 kimi-k3 时保留其 high 与 max_tokens 兼容分支。实际发送参数、响应模型、结束原因及 usage 保存在 call.json；发送了参数不代表上游一定兑现全部额度。

--credentials 指向含 api_keys 数组的本地 yibu JSON，读取第一个非空字符串；默认路径为 D:/codes/yibu_credentials.local.json。该文件的 base_url 不作为直连目标。Qwen CLI 暂兼容 --config 作为凭证路径别名，但不接受 OpenClaw 配置格式；--openclaw-root 是无作用的旧参数，不再需要传入。凭证只在发送时读取，不复制到任务目录，不写入提示词或日志。

--dry-run 检查请求而不发送；它不能代替真实调用验收。各调用保存 call.json、脱敏请求及最终答复；不保存模型推理正文。

## 本地脚本执行

```text
python SKILL/build-v2/workflow.py doctor
```

doctor 报告运行包和 Docker 命令是否存在；还需确认 Docker 服务可用、指定镜像已安装。run-script 默认用 Docker 执行，限制网络、输入挂载和资源，不自动下载镜像。

当前另有宿主执行方式：用户已明确允许该方式时，run-script 可使用 --trusted-host --approval-reason REASON 替代 --image。它限时并清理子进程环境，但没有操作系统文件或网络隔离。沿用范围内的已有授权，不因切换阶段重复确认；没有对应授权时明确说明缺口。

脚本经 run-script 执行，使用 --task 和 --output 接收任务及输出路径，只负责本地制作。远端调用使用 tool 命令。run-script 返回错误日志和剩余技术失败次数；--max-attempts 控制技术失败及单元工具调用上限，--max-run-seconds 控制本地运行时间，--max-corrections 单独控制首次视觉复核后的返工轮数（默认 2）。不改状态文件、新建任务或额外导入报告绕过上限。

## 现有素材工具

以下参数用于 build-v2/workflow.py tool；制作方法与 recipe 见 [unit-production.md](../build-v2/unit-production.md)。

- 生图默认 --image-provider yibu --image-model gemini-3-pro-image，凭证可用 --credentials CREDENTIALS 指定；默认 --generation-timeout 300。Gemini 通过代理的 /v1beta/models/gemini-3-pro-image:generateContent 发送参考裁图 inlineData 与提示词，要求返回一张图。generate 和 compose 的生成部件共用该适配。显式指定 Qwen Image 时保留原兼容适配器。
- Qwen 若返回图片下载链接，适配器只下载 HTTPS 阿里云 OSS 产物，不附 API key、不跟随重定向，限制为 30 MiB，并由 Pillow 实际解码。下载使用本机网络代理 http://127.0.0.1:7890，与模型调用审计代理 17860 区分；下载结果记录在原 call.json，不重新生图。
- 客户分割已移至 renders/cutout.py；build tool 不再提供 cutout。renders prepare 使用 --cutout-model BIREFNET_ONNX，权重需已存在，不自动下载或回退到 U²-Net。
- 羽化：使用任务来源图片和参数文件，无需新增远端生图请求。

生图在本次数据与费用授权范围内使用 --allow-remote。凭证由适配器读取；失败先检查 tool-result.json 和调用记录，服务请求没有隐式重试。

成功要求响应完成且包含唯一图片，随后由 Pillow 实际解码并执行原有透明处理。HTTP 200 但无图（例如 IMAGE_RECITATION）仍是失败。保留原生图片分辨率；模型使用最接近目标框的支持比例，最终摆放仍按原 geometry 协议，不强行拉伸生成像素。

## 受限文件访问

若宿主文件工具无法读取技能目录，由启动环境提供可读的阶段说明、必要接口和示例副本，保持相对链接。先确认实际访问限制，再准备所需文件；这是环境适配，不是每次制作的业务步骤。

本版本没有新增自动复制说明的启动器。发生访问限制时记录具体不可读路径并解决，不能假设打包已经完成。任务脚本输入文件的位置按任务中的 input_root、reference 和 crop 解析。

## 可选：从会话外启动 OpenClaw

controller/launch.py 只用于需要独立 OpenClaw 进程的外部启动，不是 Codex 或 OpenClaw 会话使用本技能的前置步骤。阶段工具和业务产物不依赖该启动器。

这个启动器沿用 controller/openclaw.json 中的 bailian-token-plan/qwen3.8-max / high，使用 Token Plan 的 https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic（anthropic-messages）接口，无备用模型，不修改宿主全局默认模型。该设置只作用于它启动的 OpenClaw 进程。

启动器优先读取环境变量 BAILIAN_TOKEN_PLAN_API_KEY，否则只读 ~/.openclaw/openclaw.json 中 models.providers.bailian-token-plan.apiKey，支持其中的环境变量引用。--credentials-config 可指定另一份已有 OpenClaw JSON 配置。密钥仅放入子进程环境，项目配置只存占位符。非默认安装路径使用 --openclaw-entry 指定 openclaw.mjs。这些 OpenClaw 安装与 Token Plan 凭证要求只适用于此启动方式。

```text
python SKILL/controller/launch.py --check
python SKILL/controller/launch.py --message-file TASK_MD --cwd WORKSPACE --state-dir STATE_DIR
```

--check 只校验 OpenClaw 配置，不调用模型。TASK_MD 写明本技能的绝对路径和当前任务；STATE_DIR 如需保留宿主会话状态，须是已存在且没有其他进程占用的目录，也可省略以使用临时状态。STATE_DIR 是宿主状态，不代替 analysis、binding 或 build 的业务产物与状态文件。

## 本地最终合成

renders 的普通合成依赖 Python 和 Pillow；cutout slot 另需 renders/requirements.txt 中的依赖和本地 BiRefNet FP32 ONNX 权重。可用权重示例为 D:/codes/visual-recreate-validation/models/birefnet-lite-fp32.onnx，依赖环境示例为 D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe，使用前检查实际存在。直接本机执行，不需要 Docker、API 凭证或新增网络请求。输出放到独立 renders 目录；描边参数、固定入口及缓存见 [本地合成](../renders/renders.md)。
