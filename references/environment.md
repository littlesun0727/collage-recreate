# 本机运行环境

入口是 `scripts/workflow.py`。当前实现使用 Windows 字体路径，默认解释器是 `D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe`。在其他环境部署时需配置依赖、字体、模型和凭证路径，不应直接套用本机路径。

## 文件用途

| 路径 | 用途 |
|---|---|
| `scripts/` | 分析校验、提取、合成、筛选、复核及按需精修的运行代码 |
| `schemas/` | analysis、bindings、review 的 JSON 校验规则，运行时必须保留 |
| `references/` | 按步骤加载的字段说明与操作细节 |
| `requirements.txt` | 首版运行依赖，统一安装到所用 Python 环境，不在 skill 内存放依赖副本 |
| `tests/` | 本地回归测试，用于维护时检查照片来源、图层、缓存与筛选行为 |

`.git/` 保存版本历史。`__pycache__/`、`.pytest_cache/` 是可再生缓存。开发计划、批量评测和任务输出不属于 skill 运行资料，应放在 skill 目录之外。

## 按需依赖

- 普通出图使用 `requirements.txt` 中的依赖。已有本机环境可直接运行入口，不必每次重装。新环境使用 `python -m pip install -r requirements.txt`；若环境由 uv 管理且未安装 pip，使用 `uv pip install --python Python解释器路径 -r requirements.txt`。
- 人物抠图使用 `prepare --cutout-model` 指定的 ONNX 模型，本机默认路径见 SKILL.md。缺失模型时不能声称完成真实抠图。
- Reveal 读取 `--reveal-key-file`，默认 `D:/codes/.env`；查看配置时不要输出密钥。离线缓存或纯本地流程不需要发起远端请求。
- 非照片生成使用 Node.js 和 `scripts/generation/`，凭证默认位于 `D:/codes/yibu_credentials.local.json`。仅在选择该精修路径时使用。
- 默认首版不使用 OCR/LaMa，skill 不附带其环境、模型或本机配置，也不将它们加入 `requirements.txt`。保留的恢复代码可处理离线组合、窗口及简单像素操作；若以后确需 OCR/LaMa，另行配置工具与模型后再用，不能假定已安装。工具先读任务内 `repair-tools.json`，再回退到 skill 根目录的 `repair-tools.local.json`；字段为 `python`、`ocr_det`、`ocr_rec`、`lama_model`、`environment_id`，详见 [局部恢复](recovery.md)。

## 维护验证

使用现有测试，不需要为每次文档修改新增测试。测试产物放在 skill 之外，避免重新堆积缓存：

```powershell
$py = 'D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe'
$skill = 'D:/codes/collage-recreate-v5'
$testRoot = 'D:/codes/collage_outputs/test-' + [guid]::NewGuid().ToString('N')
& $py -B -m pytest "$skill/tests" -q -p no:cacheprovider --basetemp $testRoot
```
