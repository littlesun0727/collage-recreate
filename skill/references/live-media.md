# Live 素材与动态成片

这一入口适用于客户提供图片与 MP4/MOV 混合素材。手机 Live Photo 的照片/视频配对、GIF、任意选封面时间、音轨混音暂不支持；让客户提供对应视频文件即可。本机样本目录是 `D:/datas/live动图素材`。

## 输入与静态封面

动态模式额外依赖 `requirements-motion.txt` 中的 PyAV；静态流程不加载该依赖。

```powershell
& $py -m pip install -r "$skill/requirements-motion.txt"
& $py "$skill/scripts/workflow.py" prepare-live --reference $ref --materials $materials --run $run --width 1200 --cutout-model $cutout
```

`prepare-live` 保留源文件并生成方向正确的第一有效帧 PNG。`prepared/catalog.json` 与普通图片相同，`media/manifest.json` 保存视频哈希、原生帧时间、时长、方向和对应 `asset_id`。PNG 内的来源标记区分首帧相同的不同视频，不改变像素。代表帧保存在任务父目录 `.live-media/`，移动任务时要一起保留该缓存和原素材。

接着完整执行原六步：看参考图和联系表、分析、绑定、叠框检查、构建、筛选及整图复核。绑定视频代表帧的照片槽会自动成为动态槽；其他照片、文字与装饰保持静态。`mode: cutout` 才逐帧抠图，普通照片窗口直接使用视频帧。不要为了动态化改变参考布局或重新提取装饰。

浏览器上传只完成素材准备，返回可复制给 Codex 的已有任务路径。继续这个任务，不能再次 prepare，也不能把“上传完成”当作“封面完成”。

## 封面后制作动态视频

```powershell
& $py "$skill/scripts/workflow.py" motion-render --run $run
```

前端携带基础版本提交请求；CLI 可用 `--base-render-id` 固定基础成图，用 `--request-id motion-唯一编号` 幂等重试。未出封面、有未解决对象、源文件变更或封面不可重现时拒绝动态制作，原 PNG 保留。

- 成片时长：`min(3秒, 本任务提供的全部有效视频中最长画面时长)`，按视频流计算，不取音轨长度。未被选中的视频仍参与上限计算。
- 按每个源视频真实帧及 PTS 解码、抠图，保留可变帧率；没有固定 30fps 或 30 帧限制。多源合成在任意一路换帧时输出一帧，输出帧率可高于某一路源帧率。
- 短素材循环，长素材截取；只处理成片中实际用到的唯一源帧。重复槽位与循环复用同一份帧和透明前景。
- 人物映射固定在封面抠图的边界、缩放和锚点上，不按各帧人物边界重新居中。无框且未手动裁剪的人物可运动到原封面 bbox 外，画布和明确的照片窗口仍会裁切。
- 先完成透明前景序列，再合成编码；不逐帧调用 Agent，不重做分析。按原层级交替合成静态段和视频，保留遮罩、相框、描边、旋转和画布编辑位移。
- 默认静音 MP4。奇数画布尺寸仅在视频右侧/底部补一像素以兼容 H.264；PNG 不变。

原生时间戳处理依据 [PyAV 时间模型](https://pyav.org/docs/stable/api/time.html)，编码后重新解码检查帧 PTS、帧数、无音轨和精确结束时间。

## 产物、修改与复核

产物位于 `chat/motion/versions/<request-id>/`：`motion.json`、`timeline.json`、`progress.json`、`matting.json`（抠图任务）、`result.json`、`final.mp4` 和首中末采样图。该位置让现有对话/画布副本自动排除动态大缓存。`chat/motion/cache/` 保存源帧与 RGBA，保留完整来源和模型哈希。

查看 `progress.json` 的分阶段耗时、实际帧数，`matting.json` 有逐帧耗时、命中缓存、alpha 面积和位置。动态版本关联原 `render_id`；手动或对话修改封面后旧视频仍可看，显示待更新。再次制作复用源帧与抠图，仅重新合成。不得改旧版本关联或覆盖定稿 PNG。

编码通过只得到动态预览 `needs_review`，不继承封面的视觉通过结论。实际播放检查人物边缘闪烁、运动/遮挡、循环接缝；检查首中末图只能辅助，不能代替播放。面积突变记录为复核提示；逐帧 BiRefNet 不保证时间连续性，当前不默认加补帧或简单平均蒙版。交付时说明真实复核结果和耗时。
