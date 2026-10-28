"""Build portable project flowcharts from reviewed local source; no remote calls.

Run this after build_atlas.py. Existing V9 evidence is preserved.
"""
from pathlib import Path
from datetime import datetime
from html import escape as e
import hashlib
import json
import re

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]


def node(key, title, label, lines, detail, output, sources):
    return dict(id=key, title=title, label=label, lines=lines, detail=detail,
                output=output, sources=sources)


def projects():
    cfg = json.loads((WORKSPACE/'template_download/config.1000.json').read_text(encoding='utf-8-sig'))
    paused = (WORKSPACE/'reveal-layer-lab/EXTRACTION_PAUSED.json').exists()
    collection = dict(id='collection', project='template_download', title='从平台原始素材到合格拼贴模板',
        subtitle='采集、初筛、审核独立运行；SQLite 和文件队列连接阶段，支持断点恢复。',
        status=f"配置快照：{len(cfg['keywords'])} 个启用关键词 / {len(cfg.get('paused_keywords', []))} 个暂停词 / 目标 {cfg['target']:,} 个原始封面",
        scope='这是源码与配置流程图，不是实时运行看板；目标数不等于已采集量或合格模板数。旧 README 和阶段文档的关键词数量已落后，以本页配置快照为准。',
        nodes=[
            node('search','关键词搜索与详情','01 / COLLECT',
                 ['平滑加权轮转 · 按词完成一轮',f"每轮最多 {cfg['search_scheduler']['max_pages']} 页",'无更多结果 / 连续低新增 → 换词'],
                 'collect-only.ps1 调用 pipeline.py collect；MediaCrawler 独立 worker 使用登录会话，导入搜索及笔记详情。按关键词文本保存轮次、页码、offset；只有成功批次推进游标。连续 3 页新笔记占比 ≤10% 可提前换词，冷却中的词不参与调度。权重分配整轮机会，不是图片配额或审核通过率。',
                 'collections/；catalog.sqlite：notes、images、pipeline_state',
                 ['collect-only.ps1','search_scheduler.py','mediacrawler_worker.py','stage_cli.py','config.1000.json']),
            node('raw','原始文件落盘','02 / DOWNLOAD',
                 ['封面下载并发 2 · Live 串行','临时文件 → 原子替换 → ready','Live 封面 + MP4 成对保留'],
                 'collect_media 只下载原始字节，不运行 Pillow、质量筛选或 VLM。封面保存 .bin，Live 保存 MP4；写完文件和 SHA-256 后才提交 raw_assets=ready。下载最多按配置尝试 3 次；访问限制保存断点并停止。collect-only 不会自动启动 screen/review/export。',
                 'raw/cover/、raw/live/；raw_assets 状态与 SHA-256',
                 ['stage_media.py','store.py']),
            node('screen','离线初筛与去重','03 / SCREEN',
                 [f"短边 ≥{cfg['min_short_edge']} · 像素 ≥{cfg['min_pixels']:,}",f"长短边比 ≤{cfg['max_aspect_ratio']} · dHash ≤{cfg['near_duplicate_distance']}",'校验文件 / EXIF / Live 配对'],
                 'screen_local 不发网络请求。校验原始 SHA-256、图片解码和 Live MP4，Live 文件不齐继续等待。质量合格后发布 images 路径，静态图用 SHA 与 dHash 去重；Live 按封面及视频哈希配对去重。失败或重复记录保留，原始文件不删除。',
                 'images/；ready 候选队列 / rejected / duplicate / near_duplicate',
                 ['stage_media.py','quality.py','live_media.py']),
            node('triage','VLM 只做分类','04 / REVIEW · TRIAGE',
                 ['100 张审核批次 · 每请求 20 张','缩略输入最长边 768 · low','single / collection / other / unsure'],
                 '一张完整拼贴（内部可以有多个照片槽）才是 single；一图多套独立设计是 collection。分类阶段只输出 id、label、needs_crop，不输出框。single 且无需裁剪直接进入最终处理；single 且 needs_crop 才送定位。collection/other 拒绝，unsure 待人工处理。尾批需 --flush。',
                 'review_cache；vlm-reports/*-triage-*.json',
                 ['pipeline.py','vlm_batch.py','vlm_stages.py','stage_cli.py']),
            node('locate','按需定位完整画布','05 / REVIEW · LOCATE',
                 ['只处理 single + needs_crop','原分辨率 · 每请求最多 5 张','crop / keep / review · 0–1000 框'],
                 '定位整套作品的外矩形，去掉外部 UI 和无关边缘；保留作品自己的留白、标题、装饰和所有照片槽。画布被截断或 UI 遮挡时返回 review。归一化框映射回原图，edge_snap 在本地调整边界；未确认边界保留 VLM 坐标。定位缺失保持 pending，不能当作 keep。',
                 'vlm-reports/*-locate-*.json；像素裁剪框与 edge_snap',
                 ['pipeline.py','vlm_stages.py','edge_snap.py']),
            node('export','裁剪复核与导出','06 / PUBLISH',
                 ['原图保留 · 裁片另存 PNG','最终像素哈希 / dHash 再去重','accept → accepted.jsonl + 图库'],
                 '裁剪面积不足原图 25% 转人工；裁后仅分辨率不足记录告警，不自动否决；其他质量失败转人工。Live 裁剪需人工确认视频几何。静态图再次按像素哈希与感知哈希去重，近重复不会直接 accept。review 或 export 更新图库和清单，collect/screen 不覆盖最终审核导出。',
                 'processed/crop-v2/；accepted.jsonl；gallery.html；裁剪报告',
                 ['crop_review.py','dataset_preview.py','crop_report.py','stage_cli.py','store.py'])],
        notes=[('分类分支','single 无裁剪 → 最终处理；single 需裁剪 → 定位；collection / other → reject；unsure → 人工。'),
               ('有界恢复','worker 默认超时 360 秒，普通故障最多重启 2 次。明确验证要求隔离账号并提醒；账号冷却回切预算持久化，不能靠重启刷新。人工验证后才使用 --resume-access。'),
               ('VLM 恢复','每次调用最长等待 300 秒；TimeoutError 保存成功缓存并重建 App Server，等待 15/30 秒，最多补跑两次。格式校验错误不自动重试；已完成判定先落库，缺结果项待补跑。'),
               ('存储与边界','各阶段使用同一 --data-dir；当前启动脚本默认 D:/datas/collage_templates。watch 仅轮询本地队列，不是系统自启动。图中展示的是代码能力，未新执行采集或付费模型。')],
        commands='.\\collect-only.ps1\npython pipeline.py screen --data-dir D:/datas/collage_templates --watch\npython pipeline.py review --data-dir D:/datas/collage_templates --flush --watch --model gpt-6.1-sol\npython pipeline.py export --data-dir D:/datas/collage_templates')
    decomposition = dict(id='decomposition', project='reveal-layer-lab', title='从参考原图到可重建的图层数据',
        subtitle='对象分析保留原图语义；提取素材决定最终 Alpha、位置和本地合成标注。',
        status='当前状态：360 提取暂停，使用 --analyze-only。' if paused else '当前快照：未发现提取暂停标记；是否调用仍由运行参数决定。',
        scope='以下前半段为分析路径，后半段为恢复提取后的代码流程。当前没有重新调用模型或 360；示意图不代表已完成拆层的实测结果。',
        nodes=[
            node('input','原图与运行目录','01 / PREPARE',
                 ['EXIF 转正 · 记录尺寸与哈希','单图 / 批量 / 导入已有分析','输入与配置相同才复用缓存'],
                 'data_workflow.py 是单图入口；batch_workflow.py 递归发现图片、串行处理，每图独立运行目录，单图失败继续下一张。可通过 --analysis-json 导入既有原图分析，跳过 VLM；最终 rendered-layer-analysis-v1 不能当旧原图分析重新导入。',
                 'source_reference.png；input/reference.png；workflow-config.json',
                 ['data_workflow.py','batch_workflow.py']),
            node('route','视觉分析与模型路由','02 / ANALYZE',
                 ['默认 Luna medium 先分析','前景 >18 → Sol medium 重分析','固定 Qwen / Codex 为替代入口'],
                 '默认 codex-auto 先用 gpt-6-luna，排除全画布背景后前景超过 18 个，升级 gpt-6.1-sol；18 个仍用 Luna。Sol 用同一原图与提示词独立分析，升级失败停止，不静默回退。固定 Qwen3.8-Flash 默认为 thinking off；不设置分析对象数上限。',
                 'analysis/luna/、analysis/sol/；routing.json；structure.json',
                 ['data_workflow.py','prompts/analyze-source.md']),
            node('validate','校验与像素坐标统一','03 / CONTRACT',
                 ['GPT：原图 + 尺寸 → 像素框','Qwen：原图 → 0–1000 → 像素','唯一全画布背景 → layer_0'],
                 '默认坐标策略按模型家族，不按服务商。原始模型对象写入 analysis/structure.json，转换后写 source-analysis.json。检查对象 ID、框、关系、层序和背景；背景必须唯一、全画布且在底层，不自动扩大错误背景框。显式 Codex normalized_1000 是另一条 1000×1000 副本路径；更改坐标/模型/提示词须新建 run。',
                 'source-analysis.json；analysis-boxes.png；input-transform.json',
                 ['data_workflow.py','prompts/analyze-source.md']),
            node('extract','暂停门与分批提取','04 / REVEAL · PAUSED',
                 ['analyze-only 在此之前结束','每批 ≤20 前景 · 从顶层向下','下一批输入 = 上批 base 背景'],
                 'EXTRACTION_PAUSED.json 存在即阻止主工作流的 360 提取；需明确恢复后才可继续。恢复后的 chain_workflow 按原 layer_order 从上往下分批，不提交背景框，不重新分析；框按当前画布缩放。每批提交、轮询、下载保留任务 ID，提交结果未知时不自动重复扣费。',
                 'chain-plan.json；batches/001/…；请求 / 回执 / task_id',
                 ['data_workflow.py','chain_workflow.py','reveal_lab.py','EXTRACTION_PAUSED.json']),
            node('layers','收集素材与最终背景','05 / LAYERS',
                 ['检查缺层 / 空 Alpha / 尺寸','只保留最后一批背景','base 与 aug 来自同一条链'],
                 '缺图层、缺 base/aug、空 Alpha 或画布尺寸变化立即停止。中间背景不重复叠加；只把最后一批 layer_0 作为最终背景。aug 与 base 由同一条 base 驱动链产生，不额外跑第二条提取链。原始 background_type 只作来源标注，不保证接口产生相同语义或透明背景。',
                 'layers/base/、layers/aug/；assets/base/、assets/aug/',
                 ['chain_workflow.py','reveal_lab.py']),
            node('render','本地合成与标注','06 / DATASET',
                 ['按原层序从下到上 Alpha 合成','最终 bbox = 素材非零 Alpha 边界','base / aug 分别配图与标注'],
                 '每个裁片按 z_index 升序，以原尺寸放在 crop_bbox 左上角做 source-over 合成；不要用 source_object.bbox 重定位或再次缩放。bbox 表示完整素材范围，不是遮挡后可见范围。新 reference 不要求与输入逐像素相同；complete 代表产物完整，visual_review 仍为 pending。',
                 'reference[-base/-aug].png；analysis*.json；dataset.jsonl；manifest.json；index.html',
                 ['chain_workflow.py','data_workflow.py','usage_report.py'])],
        notes=[('模型分支','Luna 前景数 ≤18：采用 Luna；>18：Sol 独立分析并采用 Sol；升级失败：停止。--analysis-json 可跳过这一步。'),
               ('当前可用终点','--analyze-only 输出 source-analysis.json 和 analysis-boxes.png 后结束。暂停标记保持原样；本次文档整理没有恢复或调用 360。'),
               ('分批回路','批次 N 的 base 剩余背景 → 批次 N+1；没有下一批时汇总。background_type、linked_to 不作为 Reveal 远端指令；只提交图像、前景 image_boxes 等参数。'),
               ('重建与质检','base 素材只配 reference-base，aug 素材只配 reference-aug。最初文字/字体/关系保留 source_object，不作为已核实语义。检查 index.html 和 comparison/index.html，再做人工视觉验收。'),
               ('续跑与用量','运行锁阻止同目录并发；相同输入、配置与计划可复用缓存，提交未知需核查响应。usage.csv/usage-summary.json 记录请求用量，缺失用量为未知，不重复加推理 token。')],
        commands='python data_workflow.py --reference input/reference.jpg --run runs/sample --analyze-only\npython batch_workflow.py --input-dir input/cases --output-dir runs/batch --pattern reference.jpg --analyze-only\npython data_workflow.py --reference input/reference.jpg --run runs/qwen --provider yibuapi --model qwen3.8-flash --thinking off --key-file .env --analyze-only')
    return [collection, decomposition]


def svg_for(p):
    out=['<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="1100" viewBox="0 0 1800 1100" role="img" aria-label="'+e(p['title'])+'">',
         '<style>text{font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;fill:#222}a:hover rect{stroke:#b00000;stroke-width:3}</style>',
         '<defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="none" stroke="#999" stroke-width="1.5"/></marker></defs>',
         '<rect width="1800" height="1100" fill="white"/>']
    def text(x,y,s,size=18,color='#222',bold=False):
        out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{700 if bold else 400}" style="fill:{color}">{e(s)}</text>')
    def box(x,y,w,h,fill='#fff',stroke='#d6d6d6',dash=False):
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}"'+(' stroke-dasharray="7 5"' if dash else '')+'/>')
    def arrow(d,dash=False):
        out.append(f'<path d="{d}" fill="none" stroke="#999" stroke-width="2" marker-end="url(#arrow)"'+(' stroke-dasharray="7 5"' if dash else '')+'/>')
    text(48,48,'COLLAGE / WORKFLOW ATLAS · '+p['project'].upper(),18,'#b00000',True)
    text(48,103,p['title'],36,bold=True)
    text(48,143,p['subtitle'],21,'#666')
    box(48,169,1704,52,'#fff5f5','#efcccc')
    text(68,202,p['status'],20,'#b00000',True)
    positions=[(48,260),(630,260),(1212,260),(48,575),(630,575),(1212,575)]
    # Two left-to-right rows, explicit outer connector between the rows.
    for i,(n,(x,y)) in enumerate(zip(p['nodes'],positions)):
        out.append(f'<a href="#{n["id"]}" aria-label="展开{e(n["title"])}">')
        box(x,y,540,235,'#fafafa' if p['id']=='decomposition' and i>=3 else '#fff',dash=p['id']=='decomposition' and i>=3)
        out.append(f'<path d="M{x+14} {y} H{x+526}" stroke="#b00000" stroke-width="3"/>')
        text(x+24,y+38,n['label'],17,'#b00000',True)
        text(x+24,y+79,n['title'],27,bold=True)
        for j,line in enumerate(n['lines']):text(x+24,y+121+j*32,line,20,'#555')
        out.append('</a>')
    for y in (377,692):
        arrow(f'M588 {y} H622');arrow(f'M1170 {y} H1204')
    arrow('M1482 495 V530 H26 V692 H40')
    text(58,550,'主线继续 →',16,'#666')
    if p['id']=='collection':
        arrow('M318 810 V848 H1482 V818',True)
        text(554,840,'single 且无需裁剪：跳过定位，进入最终处理',18,'#b00000')
        box(48,886,820,137,'#fafafa')
        text(72,922,'分类出口',22,bold=True)
        text(72,959,'collection / other → reject；unsure → 人工复核',20)
        text(72,993,'需裁剪但定位缺失 → pending，补跑缺失请求',19,'#666')
        box(894,886,858,137,'#fafafa')
        text(918,922,'恢复与持久化',22,bold=True)
        text(918,959,'SQLite 游标 + 成功调用缓存 + 独立阶段锁',20)
        text(918,993,'访问验证 / 重试耗尽 → 保存断点并提醒',19,'#666')
    else:
        text(180,550,'--analyze-only 在 03 后结束；04–06 是恢复提取后的路径',19,'#b00000')
        arrow('M900 810 V848 H318 V818',True)
        text(480,840,'仍有前景：上一批 base 背景 → 下一批提取',18,'#b00000')
        box(48,886,820,137,'#fafafa')
        text(72,922,'分析路由',22,bold=True)
        text(72,959,'Luna 前景 ≤18 直接采用；>18 → Sol 独立分析',20)
        text(72,993,'升级失败停止；全画布背景不参与计数',19,'#666')
        box(894,886,858,137,'#fafafa')
        text(918,922,'最终产物与验收',22,bold=True)
        text(918,959,'两套裁片 / reference / 标注 / 图层预览',20)
        text(918,993,'只保留最后背景；complete 仍需视觉验收',19,'#666')
    text(48,1065,'实线：主流程　虚线：条件分支 / 暂停后的路径　点击节点查看输入、判断、产物和源码依据',18,'#666')
    out.append('</svg>')
    return ''.join(out)


CSS='''*{box-sizing:border-box}body{margin:0;background:#f6f6f4;color:#222;font:16px/1.75 "Microsoft YaHei",system-ui,sans-serif}main{max-width:1500px;margin:auto;padding:30px}nav{display:flex;gap:12px;flex-wrap:wrap;border-bottom:1px solid #ddd;padding-bottom:20px}a{color:#a00000;text-decoration:none}nav a,button,.download{border:1px solid #ddd;border-radius:7px;background:white;padding:7px 14px;cursor:pointer;font:inherit}nav a.active{border-color:#b00000;color:#b00000}h1{font-size:36px;line-height:1.35;margin:30px 0 12px}h2{font-size:25px}h3{font-size:21px;margin:0 0 12px}.muted{color:#666}.eyebrow{color:#b00000;font-size:13px;letter-spacing:2px;margin-top:30px}.notice{background:#fff3f3;border-left:3px solid #b00000;padding:14px 20px;margin:22px 0}.toolbar{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:16px 0}.viewport{overflow:auto;border:1px solid #ddd;background:white;border-radius:12px}.viewport svg{width:100%;height:auto;display:block;min-width:950px}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin:25px 0}.card,article,details{background:white;border:1px solid #ddd;border-radius:12px;padding:24px;margin-bottom:20px}article{scroll-margin-top:20px}article:target{border:2px solid #b00000}.output{background:#f5f5f5;padding:12px 16px;overflow-wrap:anywhere}.source{font-size:13px;color:#666;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:20px;font:13px/1.7 Consolas,monospace}summary{cursor:pointer;font-weight:bold}.arrow{color:#b00000;font-size:28px}footer{margin-top:35px;border-top:1px solid #ddd;padding:20px 0;color:#666;font-size:13px}@media(max-width:760px){main{padding:16px}h1{font-size:28px}.cards{grid-template-columns:1fr}.card,article{padding:18px}}@media print{body{background:white}nav,.toolbar{display:none}main{padding:0;max-width:none}.viewport{overflow:visible}.viewport svg{min-width:0!important;width:100%!important}article{break-inside:avoid}details{display:none}}'''

NAV='<nav aria-label="项目导航"><a href="start.html">三项目全览</a><a href="collection.html">A · 数据采集</a><a href="decomposition.html">B · 模板拆层</a><a href="index.html">C · V9 照片重建</a></nav>'
JS='''let zoom=1;const v=document.querySelector('.viewport'),s=v?.querySelector('svg');document.querySelectorAll('[data-zoom]').forEach(b=>b.onclick=()=>{zoom=b.dataset.zoom==='reset'?1:Math.max(.5,Math.min(3,zoom+Number(b.dataset.zoom)));s.style.width=(zoom*100)+'%';document.querySelector('#zoom').textContent=Math.round(zoom*100)+'%'});document.querySelector('#print')?.addEventListener('click',()=>window.print());document.querySelector('#full')?.addEventListener('click',async()=>{try{await v.requestFullscreen()}catch{}});document.querySelector('#svg')?.addEventListener('click',()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(s)],{type:'image/svg+xml;charset=utf-8'}));a.download=document.body.dataset.flow+'-workflow.svg';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)});'''


def page(title, body, key='', script=''):
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(title)+'</title><style>'+CSS+'</style></head><body data-flow="'+key+'"><main>'+NAV+body+'<footer>离线源码快照 · '+datetime.now().astimezone().isoformat(timespec='seconds')+' · 阅读无需 Python、Node、网络或原项目路径</footer></main><script>'+script+'</script></body></html>'


def build():
    data=projects()
    evidence=[]
    for p in data:
        svg=svg_for(p)
        (HERE/(p['id']+'-workflow.svg')).write_text(svg,encoding='utf-8')
        body='<div class="eyebrow">'+e(p['project'].upper())+'</div><h1>'+e(p['title'])+'</h1><p class="muted">'+e(p['subtitle'])+'</p><div class="notice"><b>'+e(p['status'])+'</b><br>'+e(p['scope'])+'</div>'
        body+='<div class="toolbar"><button data-zoom="-.25" aria-label="缩小">−</button><span id="zoom">100%</span><button data-zoom=".25" aria-label="放大">＋</button><button data-zoom="reset">适应</button><button id="full">全屏</button><button id="svg">SVG ↓</button><a class="download" href="'+p['id']+'-workflow.png" download>PNG ↓</a><button id="print">打印 / PDF</button></div><div class="viewport">'+svg+'</div><p class="muted">点击图中节点跳到阶段说明。图为源码流程示意，未冒用其他项目图片作为实测。</p>'
        body+='<h2>阶段输入、判断与产物</h2>'
        for n in p['nodes']:
            body+='<article id="'+n['id']+'"><div class="eyebrow">'+e(n['label'])+'</div><h2>'+e(n['title'])+'</h2><p>'+e(n['detail'])+'</p><p class="output"><b>产物 / 状态</b><br>'+e(n['output'])+'</p><p class="source">源码依据：'+e(' · '.join(p['project']+'/'+x for x in n['sources']))+'</p></article>'
        body+='<h2>分支与恢复机制</h2>'
        for title,content in p['notes']:body+='<article><h3>'+e(title)+'</h3><p>'+e(content)+'</p></article>'
        body+='<details><summary>对应入口命令（在原项目配置环境后执行）</summary><pre>'+e(p['commands'])+'</pre></details><details><summary>源码快照与 SHA-256（离线可审阅）</summary>'
        files=sorted(set(f for n in p['nodes'] for f in n['sources']))
        for name in files:
            path=WORKSPACE/p['project']/name
            if not path.exists():continue
            raw=path.read_bytes()
            record=dict(project=p['project'],file=name,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
            evidence.append(record)
            body+='<details><summary>'+e(name)+'</summary><p class="source">SHA-256 '+record['sha256']+'</p><pre>'+e(raw.decode('utf-8-sig'))+'</pre></details>'
        body+='</details>'
        (HERE/(p['id']+'.html')).write_text(page(p['title'],body,p['id'],JS),encoding='utf-8')
    body='<div class="eyebrow">COLLAGE / WORKFLOW ATLAS</div><h1>数据采集 · 模板拆层 · 照片重建</h1><p class="muted">三个独立项目，沿同一条素材生命周期阅读。解压后双击本页即可离线浏览。</p><div class="cards">'
    for key,title,desc in [('collection.html','A / 数据采集','平台搜索 → 原始下载 → 质量初筛 → VLM 分类与裁剪 → 合格模板'),('decomposition.html','B / 模板拆层','参考原图 → 对象分析 → 分批提取 → 图层素材 → 新 reference 与标注'),('index.html','C / V9 照片重建','参考设计 → Analysis → Photo-window → Binding → 客户照片合成')]:
        body+='<a class="card" href="'+key+'"><h2>'+title+'</h2><p>'+desc+'</p><span class="arrow">打开流程图 →</span></a>'
    body+='</div><div class="notice"><b>项目衔接边界</b><br>采集导出的合格图片可作为拆层或 v9 的参考图；需要显式选择 / 复制 / 传入路径，并非自动串接。拆层导出的 rendered-layer-analysis-v1 不等于 v9 输入契约，不能直接混用标注。360 提取当前暂停；v9 原有报告和实测证据保留。</div><h2>离线打开与分享</h2><p>整包解压，打开 start.html。也可直接打开 collection.html、decomposition.html 或 index.html；每页内嵌样式、脚本和数据。SVG 是可缩放矢量图，PNG 用于图片查看器和文档插图。移动整个文件夹即可，阅读不依赖 D 盘路径或本机运行环境。</p><p>新增两图依据本地源码与配置；原有 v9 报告包含历史请求和保存的图像证据。所有快照均不代表实时任务状态。</p>'
    (HERE/'start.html').write_text(page('拼贴工作流总览',body),encoding='utf-8')
    (HERE/'project-flows.json').write_text(json.dumps(dict(generated_at=datetime.now().astimezone().isoformat(),projects=data,sources=evidence),ensure_ascii=False,indent=2),encoding='utf-8')
    # Repack the existing V9 snapshot with the shared navigation, without rerunning analysis.
    snapshot=json.loads((HERE/'workflow-data.json').read_text(encoding='utf-8'))
    packed=json.dumps(snapshot,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    template=(HERE/'atlas.template.html').read_text(encoding='utf-8')
    html=template.replace('/* INLINE_STYLE */',(HERE/'atlas.css').read_text(encoding='utf-8')).replace('/* INLINE_DATA */',packed).replace('/* INLINE_SCRIPT */',(HERE/'atlas.js').read_text(encoding='utf-8'))
    (HERE/'index.html').write_text(html,encoding='utf-8')
    (HERE/'READ-ME.txt').write_text('工作流图册 · 离线版\n\n1. 完整解压 ZIP。\n2. 双击 start.html；也可以分别打开 collection.html、decomposition.html、index.html。\n3. 无需安装软件、启动服务器或访问原 D 盘项目。推荐现代 Edge / Chrome / Firefox。\n4. 三张主流程图均附 SVG / PNG；页面支持缩放、节点跳转与打印。\n5. 新增项目图来自源码快照；当前 360 提取暂停。文档不会执行采集、模型请求或提取。\n6. 附带源码证据及哈希，不包含账号配置、Cookie、API 密钥或原始采集数据库。\n\n维护者：修改流程后运行 build_project_flows.py，再运行 verify_project_flows.py 导出图片并核验；最后运行 package_atlas.py。\n',encoding='utf-8-sig')
    print(json.dumps({'built':['start.html','collection.html','decomposition.html','index.html'],'source_files':len(evidence)},ensure_ascii=False))


if __name__=='__main__':build()
