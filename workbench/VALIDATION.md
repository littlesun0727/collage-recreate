# 首版验证记录

验证日期：2026 年 10 月 8 日。工程位于同一仓库的 `skill/` 和 `workbench/`，冻结基线标签 `collage-recreate-v5-frozen-20261008` 保持不变。

## 功能验收

- 原有 skill 回归 164 项、新增工作台与观察契约测试 13 项：共 **177 项通过**。
- skill 格式校验通过；浏览器 JavaScript 语法检查通过。
- 真实任务浏览器验收通过：六步进度、三任务切换、历史步骤固定、滑动对照、缩放、版本固定、原图弹窗、素材详情保留、对象定位、手机布局及刷新恢复。浏览器控制台无异常。
- 动态浏览器验收通过：实际渲染器执行时先显示完成的素材、心跳与本轮数量正确；查看历史步骤或版本时后台继续，新版本不会继承旧版本的复核结论；修改前后图可对照。该项使用明确标记的合成图片测试，和下述真实样片分开保存。
- 契约测试覆盖事件并发与半条记录恢复、未完成快照忽略、不可变图像、修改依据及差异、复核绑定、同图重渲染去重、新旧素材批次隔离、阶段产物校验、仅分析任务、历史任务、禁用观察记录、只读接口与重启恢复。

工作台完成记录与视觉质量分别展示：执行结束不等于图片通过复核。观察服务未启动时，skill 仍会保存事件和版本；浏览器轮询不会触发远端制作请求。

## 真实样片

从 `D:/datas/图片排版样图_去水印` 的 16 张参考图中选择以下 3 张，覆盖人像抠图、风景叠放和装饰提取。客户素材来自 `D:/视频素材/人像素材3.0` 与 `D:/视频素材/风景照片`，共读取 19 张照片，具体使用情况留在每个任务的 bindings 和结果中。本次没有宣称测试全部 16 张参考图。

| 任务 | 原始参考图 | 实际制作与修改 | 当前视觉结论 |
|---|---|---|---|
| 01-三段人像 | 20260915-170502_封面样图.png | 本地照片合成及 BiRefNet 抠图；调整顶部人像裁切位置；保留两版成图 | 待修改：顶部构图仍不理想；前景人物手臂有截断，衣服区域抠图有孔洞 |
| 02-风景叠放 | 20260908-192946.jpg | 真实提取 4 项装饰；使用客户风景照片；降低背景照片不透明度以改善白色装饰可见性；保留两版成图 | 待修改：手写文字与放射细线仍偏淡，局部虚线边缘与参考存在差异 |
| 03-装饰拼贴 | 20260915-170457_封面样图.png | 真实提取 12 项装饰，其中 11 项通过素材门禁、1 项拒绝；筛选确认主框隔离、保留下框可用局部；保留首版与筛选版 | 待修改：主照片粉笔框缺失、下框不完整、星形装饰及背景纹理仍有差异 |

三组均完成分析叠框查看、素材检查、整图及局部看图复核，并登记 `needs_changes`。第三组筛选前后画面可能基本相同，因为主框在首版中已被门禁拒绝；第二版保留筛选决定与对应场景，不能宣称因此补齐了边框。

本次目标是使制作过程和真实成片问题可见。上述视觉问题如实留在页面复核卡片与对应版本，未用“流程完成”冒充视觉验收通过；没有为提高测试结论而替换客户素材或伪造成图。

## 本地证据与启动

真实任务根目录：`D:/codes/collage_outputs/workbench-validation`。三个子目录内包含输入、绑定、分析、素材、最终图、复核、事件及 `observability/versions` 快照。

- 最终浏览器报告：`D:/codes/collage_outputs/workbench-validation/browser-final/browser-report.json`
- 最终桌面截图：`D:/codes/collage_outputs/workbench-validation/browser-final/01-workbench.png`
- 手机截图：`D:/codes/collage_outputs/workbench-validation/browser-final/03-mobile.png`
- 动态测试报告：`D:/codes/collage_outputs/workbench-live-test/live-report.json`
- 素材提前出现截图：`D:/codes/collage_outputs/workbench-live-test/01-material-before-final.png`
- 修改前后截图：`D:/codes/collage_outputs/workbench-live-test/02-modification-chain.png`

在仓库根目录执行：

```powershell
& 'D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe' -B -m workbench --runs D:/codes/collage_outputs/workbench-validation --port 8790
```

访问 http://127.0.0.1:8790 。本版是本机观察页面，远程客户分享尚未部署。图片、客户素材、模型、凭据和浏览器运行目录均保存在源码仓库外。
