# 按实际返回内容局部恢复

仅在用户明确要求像素精修时读本页；默认首版使用screen筛选与简单补画，不执行这里的修复。没有问题不写修复文件，不新增独立模型阶段。先看同批原始层：请求名、boxes_mapping_index只提供来源对应，不保证主体全部在该层。组合请求也可能漏成员。

`PY SKILL/scripts/workflow.py recover --run RUN --file REPAIR.json`

此命令始终离线，读取已下载缓存重编译并合成，不请求360或yibu；保存生效配置为RUN/recovery.json，后续build沿用。first.png不覆盖。需要有build产物，且应在apply/generate之前执行；若已有这些后续修改，命令拒绝重编译以防丢失。修复JSON必须指向当前参考哈希。用groups/windows均为空的同格式计划可撤销修复。

## 多返回层恢复一个固定组合

```json
{
  "schema_version":"collage-recovery-v1",
  "reference_sha256":"从input.json的reference.sha256复制",
  "groups":[{
    "primary":"ornament",
    "member_ids":["carrier","detail","ornament"],
    "parts":[
      {"source_id":"detail"},
      {"source_id":"carrier","bbox":[100,700,400,1000]}
    ],
    "reason":"主体片段错分到carrier层；ornament层仅有重复底形，选取主体与detail恢复完整装饰"
  }],
  "windows":[]
}
```

示例ID、坐标与哈希必须替换。parts按从下到上叠放，可同源选取多个不重叠区域；省略选区保留全层，bbox为原图像素半开区间，也可改用polygon:[[x,y],...]。仅显式选区改变alpha，RGB不移动、不拉伸，不自动裁回分析框。

member_ids列出被整个组合替代的overlay，primary是其中一个现有ID，组合在其原layer_order位置绘制；其他成员不重复绘制，来源仍保留。所有parts.source_id必须在member_ids内，不能引用客户照片、00背景、辅助照片或任意文件。若某返回层还含需要独立保留的纸底，不要把它整体消费进前景组合；先明确范围和前后关系，无法表达时登记缺口。重复内容选择一份，不能为凑完整全部叠加。带照片窗口的载体不与普通贴纸融合。

此功能不会自动识别主体归属、猜碎片顺序或补回缺失图案。缺在00层里的成员不能整层回填。修复后仍集中看整图及组合素材，检查污染、漏项、重复与遮挡。组合素材及来源证据在assets/reveal/recovered-groups，原始缓存不修改。

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

## 明确选区与文字修复

当前 skill 不附带 OCR/LaMa 环境和模型。`inpaint`、`remove_text` 需要另行配置对应后端；普通 `erase`、`fill` 以及上述组合和窗口恢复不依赖这些模型。未配置时保留问题，不假定工具可用。

同一collage-recovery-v1增加可选edits，旧文件继续兼容。示例字段应替换为实际对象、区域和原因；以下只演示格式，不是默认清理所有文字。

```json
{
  "schema_version":"collage-recovery-v1",
  "reference_sha256":"当前参考图哈希",
  "edits":[
    {"id":"ornament","operation":"erase","polygon":[[10,20],[50,20],[50,60],[10,60]],"reason":"已确认该独立片段多余"},
    {"id":"carrier","operation":"remove_text","bbox":[80,100,400,190],"texts":["原有文案"],"backend":"lama","padding":2,"reason":"此文案将由独立text层重绘"}
  ]
}
```

每项必须提供bbox或polygon之一，坐标仍为原图。operation支持erase（清透明）、fill（必须提供fill颜色）、inpaint（LaMa修补）、remove_text（OCR定位后填色或LaMa）。remove_text需要texts精确匹配，或显式all_text=true仅清理选区内全部检测文字；backend为lama或fill，后者必须提供fill颜色。padding为OCR蒙版外扩，0..8原图像素，最终限制在授权选区。矩形OCR区域不是逐笔画分割，跨越装饰或边缘时应缩小选区或改用明确polygon，不盲目扩大。

修复不移动、不缩放素材。erase只改alpha；fill/inpaint/remove_text保留alpha且只写回蒙版内RGB。操作失败保留该步前的像素，门禁拦截整层并记录失败。选区外不变是程序验证，纹理、文字残影和误删仍需看图。原始缓存不变，蒙版和工具日志写入assets/repairs。

## 对疑难候选作明确决定

assets/gate/report.json给出当前input_key。看图确认后，可在同一recovery.json添加decisions：

```json
"decisions":[{
  "id":"carrier",
  "input_key":"从当前门禁记录复制64位input_key",
  "verdict":"accepted",
  "reason":"已对照参考确认窗口边缘为合法装饰，文案完整",
  "embedded_ids":["caption"]
}]
```

verdict为accepted/rejected/local。accepted只解除语义疑点，不能越过空层、修复失败等硬错误。embedded_ids仅填写实际完整包含且原分析明确关联的非照片对象；没有则省略。local仅适用于已有受支持shape的简单独立对象，复杂载体或组合继续保留缺口。决定绑定当前候选、参考/分析/绑定、修复和规则；过期时pending，不静默套用。

edits/windows会改变候选，旧input_key自然失效。通常先执行修复，再集中复核修复后的候选；没有剩余疑点的程序结果无需再写accepted。recover始终离线，不能用重编译绕过先前的apply/generate保护。
