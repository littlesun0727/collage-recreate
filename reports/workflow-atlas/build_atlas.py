"""Build the offline VLM workflow report from local source and saved requests.

Run with Python + Pillow + jsonschema. No workflow execution or remote requests.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime
import hashlib
from html import escape
import io
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CHECKS = ROOT.with_name(ROOT.name + '-output') / 'checks'
sys.path[:0] = [str(ROOT), str(ROOT / 'engine/scripts')]

from PIL import Image
from jsonschema import Draft202012Validator
from collage.prompts import builder
from collage.vlm.coordinates import ReferenceCoordinates
from collage.workflow.stages import analyze, photo_windows, bind


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def image_url(path, size=1100):
    with Image.open(path) as original:
        im = original.convert('RGB')
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=86)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')


NODES = [
    dict(id='analyze', name='Analysis', title='理解参考设计', question='画面由哪些设计单元构成，它们怎样叠放？',
         subtitle='把参考图拆成可替换的照片与需要保留的设计单元，确定对象、范围、归属、层级和关联。',
         output='objects + layer_order → structure.json', count='1 次 + 最多 1 次修复',
         inputs=[['图片', '仅 1 张完整参考图。没有客户素材、成图或照片局部裁片。'],
                 ['结构上下文', '像素模式提供 reference_size；0–1000 模式向模型提供 reference_aspect_ratio，隐藏显式原图尺寸。'],
                 ['不进入本步', '客户选材、具体旋转、窗口绘制参数、提取与合成。']],
         rules=[['先确定完整设计单元', '摄影内容属于 photo；固定贴纸、带字标签、艺术字及其载体整体保留。内部图案、笔画、阴影不因可辨认就拆成多个对象。'],
                ['照片描述承担后续语义', 'description 描述主体、人数、景别、姿态、摄影环境与可见描边/渐隐。当前不输出 mode、effects 或制作参数。'],
                ['关联与顺序分别表达', 'photo_id 表示相框对应照片；parent_id 表示载体归属；linked_to 表示连续出框底图，可指向客户素材背景。layer_order 单独表达从底到顶顺序，objects 列举顺序不决定层级。'],
                ['唯一画布背景', '先识别独立照片，再确定唯一 background：photo 使用客户素材铺底，reference_clean 保留清版底图，solid_color 使用均匀底色。多张照片铺满画布时可用透明底色占位，不合并独立照片。'],
                ['文字按视觉形式分类', '普通排版才作为 text；有特殊字形、纸底和装饰的文字保留在 overlay/frame 等完整载体中。文字可辨认并不意味着应拆成 text。']],
         fields=[['objects', '必填 · 1–160 项', '对象清单；包含恰好一个全画布 background。'],
                 ['layer_order', '必填 · 对象 ID 数组', '明确从底到顶顺序；唯一 background 位于第一项。'],
                 ['background_type', 'background 必填', 'photo / reference_clean / solid_color；固定背景不参与客户选材。'],
                 ['id / kind / bbox', '每项必填', '唯一 ID；五种 kind；覆盖完整可见范围的水平外接框 [左,上,右,下]。'],
                 ['description', '非 text 必填 · 1–400 字符', '内容、成员、判断依据；photo 还要说明可见效果。'],
                 ['photo_id', 'frame 必填', '指向实际存在的 photo；不决定两者的前后顺序。'],
                 ['parent_id', '可选', '所属载体 ID；不能自指或成环，不自动联动位置。'],
                 ['linked_to', '仅连续出框前景', '指向普通 photo 或客户素材背景；独立贴纸或不同姿态不建立该关联。'],
                 ['text', 'text 必填', '普通文字原文；已确定为普通文字但无法辨认时为 null。'],
                 ['color / font / align / bold / italic', '文字排版与底色', 'text 保留逐行内容与换行，填写颜色、字体、对齐、粗体、斜体；字号和行距本地适配。solid_color 背景需明确颜色。']],
         processing=[['结构与业务校验', '检查类型、字段、框范围、唯一 ID、有效关联和循环；不根据关联重排图层。'],
                     ['有限修复', '首次请求使用 single_request。出错后仅追加一次 analyze_repair：能定位字段则局部修复，否则整体重试。仍不合法则停止。'],
                     ['整理层序并编译', '本地删除 layer_order 未知 ID、去重并补齐遗漏 ID，保留已有合法 ID 的相对顺序；不按关联重排。保存 structure.json，编译像素 analysis.json 和 text-units.json；背景固定全画布、旋转归零。']],
         artifacts=['structure.json', 'analysis.json', 'text-units.json', 'workflow/structure-analysis.json'],
         note='首次分析的字段表与 JSON 示例已写在 analyze.md 内。默认不把整份 Schema 再追加到 system；完整本地 Schema 校验仍执行。',
         example_note='历史案例 38：返回 18 个对象，其中 3 个 photo。此处保留当时的原始判断；窗口案例使用的是另一份已保存的对象清单，三段记录并非一次端到端运行。',
         sources=['collage/workflow/stages/analyze.py', 'collage/prompts/analyze.md', 'collage/prompts/analyze_repair.md', 'collage/vlm/response_normalization.py', 'collage/object_roles.py', 'collage/text_units.py']),
    dict(id='photo_windows', name='Photo-window', title='确定照片的显示方式', question='照片放在哪里，保留哪些像素，边缘怎么画？',
         subtitle='将第一步的自然语言描述转换为可执行的摆放、主体、窗口和渐变参数；每张照片恰好一项。',
         output='photos → photo-windows.json', count='有照片时 1 次，格式失败最多纠正 1 次',
         inputs=[['图片', '完整参考图 + 每张 photo 的周边裁片；裁片的位置由 crop_regions 说明。'],
                 ['结构上下文', 'photo/frame 及 background_type=photo 的背景，包含相对层级、范围、描述和关联。固定背景、文字和独立装饰记录被过滤。'],
                 ['视觉判断顺序', '先整图确认对象与跨窗口关系，再查看局部边缘；原图优先于第一步 description。此时没有客户素材或绑定结果。']],
         rules=[['摆放：矩形先定位内口四角', '矩形与圆角矩形使用 rectangle_corners，程序从四角拟合制作框与旋转；异形或无固定窗口用 box_rotation，不能给水平外接框直接加角度。'],
                ['客户素材背景保持全画布', '使用 box_rotation、全画布 bbox、rotation=0；保留摄影环境，关闭固定窗口与渐变，不填写 linked_to。可按证据描主体。'],
                ['主体：背景与描边独立', 'preserve_background 决定是否去背景；subject.stroke 沿后续客户分割 alpha 描边。可以保留摄影背景，同时沿主体描边。'],
                ['窗口：普通矩形也是固定窗口', 'mask.enabled=true 时必须给 shape 与 stroke；纯主体贴纸没有独立窗口时关闭。窗口轮廓不能描摹参考人物，主体轮廓来自客户素材分割。'],
                ['渐变：只作用于固定窗口', 'edge_gradient 定义向内/向外及四个局部方向的宽度。mask 关闭时不允许形状、窗口描边或有效渐变。'],
                ['连续出框：保持同一份素材变换', '底图保留摄影环境，前景去背景；linked_to 让两层共用客户素材、裁切、镜像、变换和视频时轴。缺少必要前景层时报告结构问题。']],
         fields=[['schema_version', '必填 · 固定值', 'collage-photo-windows-v3。当前契约不兼容转换旧窗口格式。'],
                 ['coordinate_space', '模型全图坐标声明', '请求/原始响应使用 pixel 或 normalized_1000；本地解码后不保留此模型传输字段。'],
                 ['photos[].photo_id', '必填 · 客户照片对象 ID', '每个 photo 和客户素材背景正常情况下恰好一次；frame 不作为照片输出。'],
                 ['placement', '必填 · 两种表示', 'rectangle_corners 输出内口四角；box_rotation 输出旋转前 bbox 与 rotation（−180～180）。编译后回写 analysis，最终窗口条目不保留 placement。'],
                 ['subject', '必填', 'preserve_background + stroke；主体描边轮廓来自客户分割，不输出轮廓顶点。'],
                 ['mask', '必填', '关闭仅 {enabled:false}；开启必须给 shape 和 stroke。'],
                 ['mask.shape', '开启窗口时必填', 'rectangle、rounded_rect、ellipse、circle、star、heart、polygon；polygon 顶点为局部 0～1。'],
                 ['stroke（两种轮廓共用画笔）', '开关及参数显式填写', 'enabled、width、color、pattern、line_cap、placement、offset；虚线可有 dash。width 为短边比例 0～0.1。'],
                 ['edge_gradient', '必填', 'enabled、direction、top/bottom/left/right；方向为 inside/outside，四边比例为 0～0.5。'],
                 ['linked_to / reason', '关联可选；reason 必填', '连续出框底图 ID；中文说明主体、窗口、过渡与不确定性的可见依据。'],
                 ['structure_issues', '可选 · 最多 12 条', '对象缺失、重复、缺少前景层或错误关联；非空则停止主流程，不把局部线宽不确定当成结构错误。'],
                 ['frame_fallbacks', '提示词要求为已有 frame 提供', 'frame_id、fill、border 及可选 radius。border 四边宽度相对照片制作框短边，各为 0～0.3；替代框随照片旋转，不复刻印字或纹理。']],
         processing=[['校验与编译', '完整覆盖普通照片和客户素材背景；保持层级及有效关联。四角先转像素再拟合旋转矩形；偏差超过短边 5% 提示复核，超过 30% 拒绝。placement 回写 analysis，窗口计划独立保存。'],
                     ['本地覆盖检查', '使用完整场景检查渐变是否有下层承接；必要时缩减渐变并记录原因。完整场景不等于全部送入本步模型上下文。'],
                     ['失败与待复核', '格式失败最多纠正一次；仍失败保留保守窗口参数并标记 unresolved。明确结构问题停止。去背景 + polygon + 窗口描边组合保留原参数并标记 needs_visual_review。']],
         artifacts=['photo-windows.json', 'analysis.json', 'workflow/window-geometry.json', 'previews/photo-windows.png'],
         note='subject.stroke 沿客户主体轮廓计算，再接受窗口裁切；mask.stroke 沿固定窗口几何计算。两者共享画笔参数，但轮廓来源独立。',
         example_note='采用案例 38 的 recheck 实测：两张普通照片显式开启矩形窗口，独立主体关闭窗口并沿主体描白边。原始 JSON 与 accepted.json 的框坐标单位不同；最终文件还会移出 placement。',
         sources=['collage/workflow/stages/photo_windows.py', 'collage/prompts/photo_windows.md', 'collage/workflow/window_geometry.py', 'engine/schemas/photo-windows.schema.json', 'engine/scripts/window_contract.py']),
    dict(id='bind', name='Binding', title='匹配客户素材与裁切', question='每个照片槽用哪张客户照片，怎样裁切和对齐？',
         subtitle='一次请求联合完成素材观察、全局匹配和裁切规划；把客户像素绑定到已经识别好的照片槽。',
         output='photos → bindings.json', count='有照片槽时严格 1 次，失败停止',
         inputs=[['图片', '参考图 + 全部素材联系表 + 带 ID 的客户单图。单图最长边不超过 1024 像素。'],
                 ['照片槽', '普通 photo 与客户素材背景均参与。描述与关联来自 structure.json；制作框和角度优先取窗口阶段确认后的 analysis.json，按旋转前制作框计算真实 aspect_ratio。独立绑定缺少 analysis 时沿用结构几何。'],
                 ['主体需求', '从窗口计划推导 requires_segmentable_subject：去背景或主体描边时为 true；关联前景的需求同步给底图。'],
                 ['客户数据', 'instructions 与 catalog。全图归一化时，素材的显式 size 转成 aspect_ratio；不传 mask、subject、edge_gradient 等完整绘制细节。']],
         rules=[['先看联系表，再核对单图', '先比较所有候选，再核对 ID 与细节；依次匹配主体/场景、人数、景别、姿态朝向、构图，之后才考虑色调与留白。'],
                ['主体要求是选材条件', '需要分割时选清晰、轮廓完整的主体。true 不代表实际分割已经执行，也不代表素材题材与姿态一定匹配。'],
                ['裁切与 cover 对齐分开', 'source_crop 是素材原图上的 0～1 裁切区域；crop_center 是裁切后按槽比例 cover 的对齐位置，并非原图人物中心。'],
                ['关联前景与底图一致', 'asset_id、source_crop、crop_center、mirror_x 四项完全一致。独立去背景照片的 source_crop 由动态 Schema 固定为全幅；仅描主体不等于必须全幅。'],
                ['只输出照片绑定', '不输出文字替换或窗口参数；没有完全相同素材时说明近似与不足。当前没有独立 materials 模型阶段。']],
         fields=[['photos', '顶层唯一字段', '数组长度与本次照片槽数量相同；每槽恰好一次。'],
                 ['slot_id', '每项必填', '动态 Schema 枚举本次实际照片槽 ID。'],
                 ['asset_id', '每项必填', '动态 Schema 枚举 catalog 中实际素材 ID，例如 material_00。'],
                 ['source_crop', '每项必填 · 4 个 0～1 数字', '[左,上,右,下]；面积为正。独立去背景主体槽固定 [0,0,1,1]。'],
                 ['crop_center', '每项必填 · 2 个 0～1 数字', '裁切后 cover 的横向/纵向对齐；[0.5,0.5] 居中。'],
                 ['mirror_x', '每项必填 · boolean', '仅确需改变朝向且不损坏文字时水平镜像。'],
                 ['reason', '每项必填 · 非空中文', '说明可见匹配依据、需要时的主体适用性、裁切数值与明显不足。']],
         processing=[['先检查窗口计划', '有照片槽时，窗口计划必须存在、合法且精确覆盖全部照片；否则在发请求之前停止。'],
                     ['一次请求，失败停止', 'single_request=True；不自动重新选材，不进行格式重试或自动修补绑定。无照片槽则本地写空 photos。'],
                     ['校验后落盘', '检查槽覆盖、有效素材、裁切范围、关联参数一致；写入 bindings.json，并由程序补 schema_version 与 texts:[]。后续渲染使用客户像素。']],
         artifacts=['workflow/binding-input.json', 'workflow/binding-check.json', 'bindings.json'],
         note='requires_segmentable_subject = 去背景 OR 主体描边。窗口绘制细节留在本地，binding 只接收与选择素材相关的最小信息。',
         example_note='历史案例 38：18 张素材、2 张联系表，加参考图共 21 张输入图片。绑定格式已接收；既有评估记录指出，photo_3 选中的半身素材不能覆盖参考跪姿的腿部范围，不能据 accepted 推断效果已通过。',
         sources=['collage/workflow/stages/bind.py', 'collage/prompts/bind.md', 'collage/workflow/context.py', 'collage/contracts.py']),
]


def load_example(stage, relative):
    run = CHECKS / relative
    folder = next((run / 'workflow/vlm' / stage).glob('*/accepted.json')).parent
    inp = read(folder / 'input.json')
    raw = read(folder / 'response-0.json')
    receipts = [read(p) for p in (run / 'workflow/vlm/requests').glob('*.json')]
    receipt = next((r for r in receipts if r.get('input_key') == inp['input_key']), {})
    records = {}
    for name in ('input.json', 'request-0.json', 'response-0.json', 'accepted.json', 'prompt-sources.json'):
        records[name] = read(folder / name)
    records['system-0.txt'] = (folder / 'system-0.txt').read_text(encoding='utf-8')
    persisted = {'analyze': 'structure.json', 'photo_windows': 'photo-windows.json', 'bind': 'bindings.json'}[stage]
    records[persisted] = read(run / persisted)
    if stage == 'bind':
        records['binding-input.json'] = read(run / 'workflow/binding-input.json')
    candidates = list(run.rglob('*.png')) + list(run.rglob('*.jpg'))
    if stage == 'bind':
        catalog = read(run / 'prepared/catalog.json')
        candidates += [Path(p) for p in catalog['contact_sheets']]
    by_hash = {sha(p): p for p in candidates}
    pictures = []
    for meta in inp['images']:
        path = by_hash.get(meta['sha256'])
        pictures.append({**meta, 'url': image_url(path) if path else None})
    expected = raw['content']
    if isinstance(expected, str):
        expected = json.loads(expected)
    Draft202012Validator(inp['schema']).validate(expected)
    evidence_files = [{'file': str(p.relative_to(ROOT.parent)).replace('\\', '/'), 'sha256': sha(p)}
                      for p in folder.iterdir() if p.suffix in ('.json', '.txt')]
    return dict(run=str(run), folder=str(folder), relative=str(run.relative_to(ROOT.parent)).replace('\\', '/'),
                records=records, pictures=pictures, model=inp['model'], provider=inp['provider'],
                seconds=receipt.get('seconds'), usage=raw.get('usage', {}), at=receipt.get('at'),
                raw=expected, accepted=records['accepted.json']['answer'], persisted=records[persisted],
                persisted_name=persisted, hashes=evidence_files)


def build_data():
    specs = {
        'analyze': 'analysis-kind-examples-20261023/cases/38',
        'photo_windows': 'photo-windows-v3-20261023/recheck/cases/38',
        'bind': 'binding-compact-20261023/cases/38',
    }
    nodes = deepcopy(NODES)
    for node in nodes:
        stage = node['id']
        node['example'] = load_example(stage, specs[stage])
        node['example_note'] = '历史版本记录（2026-10-23），用于查看当时请求，不代表当前 Prompt 的运行结果。' + node['example_note']
        node['prompts'] = {}
        node['schemas'] = {}
        for mode in ('normalized_1000', 'pixel'):
            prompt = builder.render(stage, mode=mode)
            if stage == 'analyze':
                schema = analyze.schema([1080, 1435], mode)
            elif stage == 'photo_windows':
                coords = ReferenceCoordinates({'reference_size': [1080, 1435]}, mode)
                prompt = coords.prompt(prompt)
                schema = coords.schema(photo_windows.schema())
            else:
                coords = ReferenceCoordinates({'reference_size': [1080, 1435]}, mode, input_only=True)
                prompt = coords.prompt(prompt)
                run = Path(node['example']['run'])
                schema = bind.schema_for(read(run/'workflow/binding-input.json'), read(run/'photo-windows.json'))
            node['prompts'][mode] = str(prompt)
            node['schemas'][mode] = schema
        node['template'] = (ROOT / f'collage/prompts/{stage}.md').read_text(encoding='utf-8')
        node['source_records'] = []
        for file in node['sources']:
            text = (ROOT/file).read_text(encoding='utf-8')
            node['source_records'].append(dict(file=file, sha256=sha(ROOT/file), text=text))
        # An explicitly illustrative format example, separate from historical output.
        if stage in ('analyze', 'photo_windows'):
            node['format_example'] = json.loads(re.search(r'```json\s*([\s\S]*?)```', node['prompts']['normalized_1000']).group(1))
        else:
            node['format_example'] = node['example']['raw']
        Draft202012Validator(node['schemas']['normalized_1000']).validate(node['format_example'])
    sources = sorted({file for n in nodes for file in n['sources']} | {
        'collage/workflow/pipeline.py', 'collage/workflow/options.py', 'collage/prompts/builder.py',
        'collage/vlm/client.py', 'collage/vlm/coordinates.py', 'collage/prompts/codex_system.md', 'collage/workflow/stages/build.py',
        'collage/workflow/stages/deliver.py'})
    evidence = {f: {'sha256': sha(ROOT/f), 'text': (ROOT/f).read_text(encoding='utf-8')} for f in sources}
    snapshot = hashlib.sha256(json.dumps({f:d['sha256'] for f,d in evidence.items()},sort_keys=True).encode()).hexdigest()
    return dict(title='Collage v9 · VLM 工作流详解', generated_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                snapshot=snapshot, nodes=nodes, source_records=evidence,
                scope='当前源码快照 + 已保存分阶段请求；没有重新调用模型，案例不是一次端到端成功运行。')


def overview(data):
    from visual_overview import overview as render_visual_overview
    return render_visual_overview(data)


def main():
    data = build_data()
    svg = overview(data)
    data['overview_svg'] = svg
    (HERE/'workflow-overview.svg').write_text(svg, encoding='utf-8')
    packed = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    template = (HERE/'atlas.template.html').read_text(encoding='utf-8')
    html = template.replace('/* INLINE_STYLE */', (HERE/'atlas.css').read_text(encoding='utf-8'))
    html = html.replace('/* INLINE_DATA */', packed).replace('/* INLINE_SCRIPT */', (HERE/'atlas.js').read_text(encoding='utf-8'))
    (HERE/'index.html').write_text(html, encoding='utf-8')
    (HERE/'workflow-data.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    from build_project_flows import build as build_project_flows
    build_project_flows()
    print(json.dumps({'report': str(HERE/'index.html'), 'bytes': len(html.encode('utf-8')), 'snapshot': data['snapshot'],
                      'images': [len(n['example']['pictures']) for n in data['nodes']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
