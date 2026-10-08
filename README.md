# 拼贴制作与工作台

同一仓库包含 `skill/` 拼贴制作能力和 `workbench/` 本地观察页面。冻结基线标签为 `collage-recreate-v5-frozen-20261008`。

制作入口是 `skill/SKILL.md`。已迁移的脚本位置是 `skill/scripts/workflow.py`；旧的仓库根目录 scripts 路径不再使用。安装或引用此 skill 时指向完整的 `skill` 子目录。

在仓库根目录启动工作台：

```powershell
$py = 'D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe'
& $py -B -m workbench --runs D:/codes/collage_outputs --port 8790
```

打开 http://127.0.0.1:8790 。`--runs` 可以接多个任务根目录，也可以直接接单个任务目录。页面每两秒读取更新，不执行任何制作命令。

查看本次三组真实样片时，将 `--runs` 改为 `D:/codes/collage_outputs/workbench-validation`。

工作台服务只需要 Python 和 Pillow；制作环境依赖见 `skill/requirements.txt`。页面使用原生 HTML、CSS、JavaScript，不需要 npm 构建或联网加载资源。

六步进度展示脚本执行与 Codex 实际看图记录。大图支持并排、滑动及修改前后对照；素材完成后即显示；每次变化的成图保存独立快照。历史任务只显示已留存的信息。录入实际业务阶段的方式见 `skill/references/workbench.md`。

任务图片、素材和运行缓存保存在仓库外。工作台为本机服务，跨机器的客户访问尚未提供。

验证命令：

```powershell
$testRoot = 'D:/codes/collage_outputs/check-' + [guid]::NewGuid().ToString('N')
& $py -B -m pytest skill/tests workbench/tests -q -p no:cacheprovider --basetemp $testRoot
```

实施范围及验收标准见 [WORKBENCH_PLAN.md](WORKBENCH_PLAN.md)，本次测试结果、样片效果和剩余问题见 [workbench/VALIDATION.md](workbench/VALIDATION.md)。

浏览器交互测试使用本机 Edge 与带 `websocket-client` 的 Python（仅测试需要，服务无需此依赖）：

```powershell
C:/Users/admin/anaconda3/python.exe -B workbench/tests/browser_check.py --out D:/codes/collage_outputs/browser-check --final
C:/Users/admin/anaconda3/python.exe -B workbench/tests/browser_live.py --out D:/codes/collage_outputs/browser-live
```

第一个命令需要已启动工作台并载入本次三组样片；第二个命令自行在 8791 端口启动合成测试任务，用来验证素材逐步出现与制作过程中查看历史的行为。

用户指定的 `gpt-5.6-sol / medium` 全流程串行测试说明见 [workbench/evaluation/README.md](workbench/evaluation/README.md)。同时查看 SDK 样片与先前功能验收任务时，启动参数为：

```powershell
& $py -B -m workbench --runs D:/codes/collage_outputs/workbench-validation D:/codes/collage_outputs/sdk-serial-20261008-r1/tasks --port 8790
```
