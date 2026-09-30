# 按实际返回内容局部恢复

仅在整图与提取表暴露问题时读本页。没有问题不写修复文件，不新增模型阶段。先看同批原始层：请求名、boxes_mapping_index只提供来源对应，不保证主体全部在该层。组合请求也可能漏成员。

`PY SKILL/scripts/workflow.py recover --run RUN --file REPAIR.json`

此命令始终离线，读取已下载缓存重编译并合成，不请求360或yibu；保存生效配置为RUN/recovery.json，后续build沿用。first.png不覆盖。需要有build产物，且应在apply/generate之前执行；若已有这些后续修改，命令拒绝重编译以防丢失。修复JSON必须指向当前参考哈希。用groups/windows均为空的同格式计划可撤销修复。

## 多返回层恢复一个固定组合

```json
{
  "schema_version":"collage-recovery-v1",
  "reference_sha256":"从input.json的reference.sha256复制",
  "groups":[{
    "primary":"cat",
    "member_ids":["paper","star","cat"],
    "parts":[
      {"source_id":"star"},
      {"source_id":"paper","bbox":[100,700,400,1000]}
    ],
    "reason":"实际猫脸位于paper返回层；cat层仅是重复底形，选取猫脸与星星恢复贴纸"
  }],
  "windows":[]
}
```

示例ID、坐标与哈希必须替换。parts按从下到上叠放，可同源选取多个不重叠区域；省略选区保留全层，bbox为原图像素半开区间，也可改用polygon:[[x,y],...]。仅显式选区改变alpha，RGB不移动、不拉伸，不自动裁回分析框。

member_ids列出被整个组合替代的overlay，primary是其中一个现有ID，组合在其原layer_order位置绘制；其他成员不重复绘制，来源仍保留。所有parts.source_id必须在member_ids内，不能引用客户照片、00背景、辅助照片或任意文件。若某返回层还含需要独立保留的纸底，不要把它整体消费进前景组合；先明确范围和前后关系，无法表达时登记缺口。重复内容选择一份，不能为凑完整全部叠加。带照片窗口的载体不与普通贴纸融合。

此功能不会自动识别猫脸、猜碎片顺序或补回缺失图案。缺在00层里的成员不能整层回填。修复后仍集中看整图及组合素材，检查污染、漏项、重复与遮挡。组合素材及来源证据在assets/reveal/recovered-groups，原始缓存不修改。

## 只清理已确认残留的窗口

```json
{
  "schema_version":"collage-recovery-v1",
  "reference_sha256":"从input.json复制真实哈希",
  "groups":[],
  "windows":[{
    "overlay_id":"frame",
    "photo_id":"photo_main",
    "padding":0,
    "reason":"原始返回层在窗口中保留了旧人物"
  }]
}
```

要求已通过overlay.photo_id或photo.parent_id关联，photo.mode为cover。同一载体可逐项列出多个窗口。使用既有bbox、rotation和corner_radius；padding是可选的清理余量，0至16原图像素，默认0，不改变客户照片bbox、裁切中心或显示mask。余量需以实际残边为依据，否则可能误删边框。仅支持规则矩形/圆角及旋转，不保证透视窗、复杂镂空、跨窗装饰正确；这些情况保留问题，不能只扩大清理范围。

已经透明的窗口不清理。index里的可见像素比例只供定位，无法区分旧人物和合法装饰。未经看图不能据此自动写windows。

若已确认某层带有旧人物，且现有窗口无法可靠分离，可在顶层增加`"omit":[{"id":"border","reason":"原人物与撕纸边连在一起，不能直接复用"}]`。这只隐藏明确指定的污染层，保留原始来源并登记unresolved，不能pass；不是按面积自动回退，不画误导性占位框。不得与同一层的groups/windows操作同时使用。空计划可撤销隐藏。
