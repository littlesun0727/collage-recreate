# 一次筛选，简单元素补画

build自动检查来源、空层、严重越界及关联前景窗口占用；accepted进入合成，pending/rejected隔离。弱文字归属、少量边缘内容只提示，不把所有疑点当坏素材。程序接受不证明语义完整；主控集中对照参考、素材表与成图，筛除明显缺失、污染或不可用素材。

正常首版不清窗、不擦像素、不调用OCR/LaMa。本地补画沿用 [分析与绑定](analysis.md) 的外观标准；工具有同名shape不等于可替代原装饰，复杂图案缺失如实报告。

## screen

`PY SKILL/scripts/workflow.py screen --run RUN --file RUN/screen-plan.json`

一次批量操作，直接使用已编译scene和候选，离线重新合成；不重新提取、不重建全部素材、不需要抄逐素材input_key。只需当前result.render_id来防止套用旧成图决定。

```json
{
  "render_id":"当前result的render_id",
  "decisions":[
    {"id":"carrier","action":"keep","embedded_ids":["caption"],"reason":"已看清载体内完整保留该文字，取消重复层"},
    {"id":"border","action":"draw","style":{"shape":"paper","fill":"#FFFFFF","texture":"none"},"reason":"提取有旧图污染；参考为简单相纸，改画外形并按已有照片窗口留孔"},
    {"id":"ornament","action":"drop","reason":"关键主体缺失，现有简单形状无法表达"}
  ]
}
```

示例ID、样式、原因来自实际看图，不能直接套用。只列需要处理的对象。

- keep：明确保留当前候选，可解除程序疑点，但空层、缺失和修复失败不能放行。embedded_ids只能列出完整包含的非照片对象：原分析明确归属，或门禁报告已列为兼容的空间候选；不能吞掉用户替换的文字。看不清不猜。
- drop：隔离污染或不可用的提取层，保留缺口。
- draw：舍弃提取像素，使用[已有shape参数](drawing.md)重画，原bbox和rotation保持。可补普通纸底、胶带、边框、线条与几何装饰。关联cover照片自动按原窗口留透明孔，可表达不同边宽的简单相纸；客户照片位置、裁切和透明度不变。不会补回缺失的插画或印字，必要文字仍由独立text层绘制。

已有明确shape的缺失/被拦素材可自动本地回退；没有参数时由这次screen补充。不要仅凭标签猜shape，也不要用方框冒充复杂插画。本地结果标approximate，需整图复核。

每个首版只做一次screen，复核后保留剩余问题。screening.json、assets/gate/report.json记录决定与来源，候选原像素不变；previews/first.png不覆盖。筛掉不是修好，缺失关键元素仍不能pass。

用户明确要求像素精修时才读[局部恢复](recovery.md)。本地OCR/LaMa实现仍可用，但不属于默认筛选流程。
