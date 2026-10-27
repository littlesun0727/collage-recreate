# 拼贴复刻

从参考图识别版式，使用客户照片重建拼贴，并提供画布编辑、对话修改和动图输出。

[查看 2026-10-23 三模型全量批跑进度与成图对比](reports/model-comparison-20261023/README.md)：116 张参考图、348 个任务，Qwen 与 Codex 并行，Codex 按全部 Luna → 全部 Sol 串行执行，结果持续同步。

[打开 VLM 工作流详解](reports/workflow-atlas/index.html)：可离线查看的交互式 HTML，重点展开 Analysis、Photo-window、Binding 的输入、完整 Prompt、JSON 格式、实测记录与本地校验。范围校准和相框审核仅在首页以灰色关闭节点表示。[高清总览图](reports/workflow-atlas/workflow-overview.png)可用于汇报。

## 按这个顺序读

1. [工作流](docs/workflow.md)：每个阶段做什么、读什么、写什么，什么时候调用模型。
2. [主流程代码](collage/workflow/pipeline.py)：从上到下就是执行顺序。具体实现进入同名 `stages/` 文件。
3. [提示词与请求](docs/prompts.md)：每次请求怎样组成，以及如何查看实际发送的内容。
4. [首次分析全文](collage/prompts/analyze.md)：任务、类型总览、设计规则、定位层级和输出格式集中在一个文件。[组装器](collage/prompts/builder.py)负责坐标、图片及各阶段输出约束。
5. [运行与开发](docs/development.md)：启动、配置、批跑、验证和目录职责。

## 目录

```text
collage/
  workflow/
    pipeline.py        执行顺序、条件分支和续跑
    context.py         任务状态、文件读写、本地命令及事务
    stages/            七个阶段的业务实现
    support/           范围几何、结果整理和本地降级布局
  prompts/             各次 VLM 完整正文和统一请求组装
  vlm/                 请求预算、缓存、校验、坐标转换及供应商适配
  editing/             用户发起的对话修改与复核
  contracts.py         引擎数据契约与共用校验
  config.py            模型和凭证配置
  storage.py           原子存储、锁及哈希
  paths.py             源码外的产物位置
engine/                图像处理与本地执行引擎，见 engine/README.md
workbench/             HTTP 服务与前端画布
examples/              不含凭证的配置样例
tests/                 离线回归与集成测试
docs/                  阅读和使用说明
```

## 在这台机器上启动

双击 `start.vbs`，或执行：

```powershell
& './.venv/Scripts/python.exe' -B launch.py
```

地址为 `http://127.0.0.1:8809/`。新任务和日志默认写到相邻的 `collage-recreate-v9-output/runtime/`。

工作台按「准备素材 → 分析素材 → 判断窗口蒙版 → 素材绑定与提取 → 首版成图」展示；各节点可直接查看 JSON 和图片产物。节点映射集中在 `workbench/server/workflow_view.py`，不沿用引擎旧的六步进度编号。可在运行目录的 `task-roots.json` 中列出额外的案例目录，启动后一起查看。

查看流程或提示词模板，无需模型调用：

```powershell
python -B -m collage workflow
python -B -m collage prompt analyze --mode normalized_1000
```

本工程独立创建新任务；原工程中的任务继续在原工程中查看和续跑。[整理范围](docs/migration.md)说明了保留和移除的内容。
