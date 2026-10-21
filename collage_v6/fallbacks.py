"""Deterministic recovery only; never invent model observations or image content."""
from copy import deepcopy
import math

from jsonschema import Draft202012Validator, ValidationError

from . import contracts
from .storage import read


def executable_bundle(value, run):
    contracts.validate_bundle(value, run, check_text_units=False)


def recover_bundle(raw, run, reference_only=False):
    """Recover executable geometry while treating auxiliary text inventory as advisory."""
    if not isinstance(raw, dict):
        raise ValueError('Expected an analysis bundle')
    keys = ['analysis', 'text_units'] if reference_only else ['analysis', 'bindings', 'text_units']
    value = {k: deepcopy(raw[k]) for k in keys if k in raw}
    value.setdefault('text_units', [])
    notes = []
    def colors(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if key in {'fill', 'stroke', 'color', 'outline_color'} and item == 'transparent':
                    node[key] = '#00000000'
                    notes.append('透明颜色已转换为等价的 RGBA 数值')
                else:
                    colors(item)
        elif isinstance(node, list):
            for item in node:
                colors(item)
    colors(value)
    try:
        Draft202012Validator(contracts.TEXT_UNITS).validate(value['text_units'])
        contracts.validate_text_units(value['text_units'], value['analysis'])
    except (ValidationError, ValueError, KeyError, TypeError):
        value['text_units'] = []
        notes.append('文字归属清单不可执行，已跳过辅助清单；原分析中的文字与装饰对象仍保留，提取装饰禁止改为空白底板')
    # No guessed IDs, bindings, bounds, ownership or grouping. These must still execute safely.
    Draft202012Validator(contracts.REFERENCE if reference_only else contracts.BUNDLE).validate(value)
    if reference_only:
        contracts.validate_reference(value, run, check_text_units=False)
    else:
        executable_bundle(value, run)
    if not reference_only and not any(o['kind'] == 'photo' for o in value['analysis']['objects']):
        raise ValueError('Analysis has no customer photo slots')
    return value, list(dict.fromkeys(notes))


def simple_reference(run):
    """An explicit neutral fallback; never claim it was observed in the reference."""
    w, h = read(run/'input.json')['reference_size']
    return {'analysis': {'schema_version': 'collage-analysis-v1', 'reference_size': [w, h],
                        'objects': [{'id': 'draft_photo', 'kind': 'photo', 'method': 'local',
                                     'mode': 'cover', 'bbox': [0, 0, w, h], 'label': '简化草稿照片',
                                     'description': '参考分析失败后的单图兜底槽位，非参考观察结果；选择清晰且主体完整的素材'}],
                        'layer_order': ['draft_photo']}, 'text_units': []}


def simple_bindings(run, analysis):
    """Preserve reference geometry if selection fails; report the nonsemantic fallback."""
    assets = read(run/'prepared/catalog.json')['assets']
    used = set()
    photos = []
    for slot in analysis['objects']:
        if slot['kind'] != 'photo':
            continue
        if not assets:
            raise ValueError('没有可用的客户素材，无法绑定照片')
        l, t, r, b = slot['bbox']
        ratio = (r-l)/(b-t)
        asset = min(assets, key=lambda a: (a['id'] in used, abs(math.log((a['size'][0]/a['size'][1])/ratio))))
        used.add(asset['id'])
        photos.append({'slot_id': slot['id'], 'asset_id': asset['id'],
                       'reason': 'VLM 绑定失败；仅按未使用优先及宽高比兜底，未经语义匹配确认'})
    value = {'schema_version': 'collage-bindings-v1', 'photos': photos, 'texts': []}
    contracts.validate_bindings(value, analysis, run)
    return value


def simple_bundle(run):
    """A visible, editable fallback made only from verified customer pixels."""
    from common import verify_source
    from PIL import Image
    inp = read(run/'input.json')
    assets = read(run/'prepared/catalog.json')['assets']
    available = []
    for asset in assets:
        # Integrity mismatches are not quality failures and must not be ignored.
        path = verify_source(asset)
        try:
            with Image.open(path) as image:
                image.verify()
        except OSError:
            continue
        available.append(asset)
    if not available:
        raise ValueError('没有可用的客户素材，无法生成简化拼图')
    available = available[:6]
    w, h = inp['reference_size']
    cols = 1 if len(available) == 1 else 2
    rows = math.ceil(len(available)/cols)
    gap = max(1, round(min(w, h)*.025))
    objects = [{'id': 'draft_background', 'kind': 'background', 'background_type': 'simple_design',
                'method': 'local', 'bbox': [0, 0, w, h], 'label': '简化草稿底色',
                'description': '分析不可用时的中性设计底图', 'style': {'fill': '#F4F1EB'}}]
    photos = []
    for i, asset in enumerate(available):
        col, row = i % cols, i // cols
        oid = f'draft_photo_{i+1}'
        bbox = [round(col*w/cols)+gap, round(row*h/rows)+gap,
                round((col+1)*w/cols)-gap, round((row+1)*h/rows)-gap]
        objects.append({'id': oid, 'kind': 'photo', 'method': 'local', 'mode': 'cover',
                        'bbox': bbox, 'label': '客户照片', 'description': '简化拼图中的真实客户素材'})
        photos.append({'slot_id': oid, 'asset_id': asset['id'], 'reason': '简化草稿按素材目录顺序绑定'})
    bundle = {'analysis': {'schema_version': 'collage-analysis-v1', 'reference_size': [w, h],
                           'objects': objects, 'layer_order': [o['id'] for o in objects]},
              'bindings': {'schema_version': 'collage-bindings-v1', 'photos': photos, 'texts': []},
              'text_units': []}
    Draft202012Validator(contracts.BUNDLE).validate(bundle)
    contracts.validate_bundle(bundle, run)
    return bundle
