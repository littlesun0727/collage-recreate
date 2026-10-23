"""阶段 2：对象清单 + 参考图/裁片 -> 照片摆放、窗口形状、主体处理和效果。"""

from copy import deepcopy
from collage import contracts
from collage import prompts
from collage.vlm.client import ModelError
from collage.storage import read, save


VERSION = 'window-drawing-v9-3'


class StructureRepairRequired(RuntimeError):
    """Stop when photo/frame structure cannot express the observed windows."""


def input_analysis(analysis):
    """Only photo/frame design data crosses this model boundary."""
    fields = ('id', 'kind', 'bbox', 'description', 'photo_id', 'linked_to', 'parent_id')
    rows = [{k: deepcopy(o[k]) for k in fields if k in o}
            for o in analysis['objects'] if o['kind'] in {'photo', 'frame'}]
    ids = {o['id'] for o in rows}
    for row in rows:
        if row.get('parent_id') not in ids:
            row.pop('parent_id', None)
    return {'objects': rows, 'layer_order': [oid for oid in analysis['layer_order'] if oid in ids]}


def execute(ctx):
    from photo_windows import defaults, reference_crops, previews
    current = {'analysis': read(ctx.run/'analysis.json'), 'text_units': read(ctx.run/'text-units.json', [])}
    crops, regions = reference_crops(ctx.run, current['analysis'])
    def validate(answer):
        # A valid report of missing structure is useful evidence, not malformed JSON.
        if not answer.get('structure_issues'):
            compile_response(answer, current, ctx.run)
    evidence = {'version': VERSION}
    try:
        if crops:
            answer = ctx.client.ask('photo_windows', prompts.render('photo_windows'),
                {**ctx.reference_context(), 'analysis': input_analysis(current['analysis']), 'crop_regions': regions},
                ctx.analysis_images()+crops, schema(), validate)
            if answer.get('structure_issues'):
                evidence.update(status='structure_repair_required', answer=answer,
                                structure_issues=answer['structure_issues'])
                save(ctx.control/'window-geometry.json', evidence)
                ctx.warn('window_structure_unresolved', 'photo_windows', '；'.join(answer['structure_issues']))
                raise StructureRepairRequired('照片窗口结构需要修正：' + '；'.join(answer['structure_issues']))
            value, plan, notes = compile_response(answer, current, ctx.run)
            evidence.update(status='accepted', answer=answer, notes=notes)
            ctx.write_reference(value)
        else:
            value, plan, notes = current, defaults(current['analysis']), []
            evidence.update(status='no_photos', notes=[])
    except ModelError as exc:
        value, plan, notes = current, defaults(current['analysis']), []
        evidence.update(status='fallback', error=str(exc), notes=[])
        ctx.warn('window_geometry_unresolved', 'photo_windows',
                    '窗口绘制参数未确认，临时保留第一步水平范围与明确关联；描边和渐变不猜参数：' + str(exc))
    assessed = evidence['status'] == 'accepted'
    evidence['drawing_review'] = drawing_review(plan, assessed=assessed)
    pending = [row for row in evidence['drawing_review'] if row['status'] == 'needs_visual_review']
    if pending:
        evidence['status'] = 'needs_visual_review'
        for row in pending:
            ctx.warn('window_boundary_review', 'photo_windows', row['photo_id'] + '：' + row['review_reason'])
    initial = {o['id']: o for o in current['analysis']['objects']}
    evidence['link_review'] = [
        {'photo_id': p['photo_id'], 'initial_linked_to': initial[p['photo_id']].get('linked_to'),
         'applied_linked_to': p.get('linked_to'),
         'status': 'assessed' if assessed else 'unresolved'}
        for p in plan['photos'] if p.get('linked_to') or initial[p['photo_id']].get('linked_to')]
    save(ctx.control/'window-geometry.json', evidence)
    for note in notes:
        ctx.warn('seam_adjusted', 'photo_windows', note)
    save(ctx.run/'photo-windows.json', plan)
    previews(ctx.run, value['analysis'], plan)


def schema():
    from window_contract import SCHEMA
    result = deepcopy(SCHEMA)
    row = result['properties']['photos']['items']
    row['properties']['placement'] = contracts.obj({
        'bbox': deepcopy(contracts.ANALYSIS['properties']['objects']['items']['properties']['bbox']),
        'rotation': {'type': 'number', 'minimum': -180, 'maximum': 180}})
    row['required'].append('placement')
    result['properties']['structure_issues'] = {'type': 'array', 'maxItems': 12,
        'items': {'type': 'string', 'minLength': 1, 'maxLength': 500},
        'description': '仅输入 photo/frame 明确不存在、重复，或缺少必要前景层/关联错误使效果无法表达时填写，说明相关 ID 与可见依据；不校验前后层级，不报告局部参数不确定。正常时省略或为空；非空时 photos 可为空。'}
    result['properties']['frame_fallbacks'] = {'type': 'array', 'items': contracts.obj({
        'frame_id': contracts.IDENTIFIER,
        'fill': {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$'},
        'radius': {'type': 'number', 'minimum': 0, 'maximum': .5}}, ['frame_id', 'fill'])}
    return result


def compile_response(answer, current, run=None):
    from photo_windows import resolve_seams
    from window_contract import execution_spec
    value = deepcopy(current)
    plan = deepcopy(answer)
    if plan.pop('structure_issues', []):
        raise StructureRepairRequired('Window assessment reported an incorrect photo/frame structure')
    objects = {o['id']: o for o in value['analysis']['objects']}
    expected = {oid for oid, o in objects.items() if o['kind'] == 'photo'}
    ids = [p['photo_id'] for p in plan['photos']]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError('Window plan must include every photo exactly once')
    for spec in plan['photos']:
        if not isinstance(spec.get('reason'), str) or not spec['reason'].strip():
            raise ValueError(spec['photo_id'] + ': window reason must explain visible drawing parameters')
        placement = spec.pop('placement')
        item = objects[spec['photo_id']]
        if item.get('linked_to'):
            spec.setdefault('linked_to', item['linked_to'])
        if spec.get('linked_to') and execution_spec(spec)['shape']['type'] != 'rectangle':
            raise ValueError(spec['photo_id'] + ': linked foreground shape must be rectangle; '
                             'the subject segmentation supplies the silhouette, not a polygon traced from the reference')
        old = item['bbox']; new = placement['bbox']
        if min(old[2], new[2]) <= max(old[0], new[0]) or min(old[3], new[3]) <= max(old[1], new[1]):
            raise ValueError(item['id'] + ': photo placement must overlap its observed location')
        item.update(bbox=new, rotation=placement['rotation'])
    seen = set()
    for fallback in plan.pop('frame_fallbacks', []):
        oid = fallback['frame_id']
        if oid in seen or oid not in objects or objects[oid]['kind'] != 'frame':
            raise ValueError('frame_fallbacks must name distinct existing frames')
        seen.add(oid)
        objects[oid]['style'] = {k: v for k, v in fallback.items() if k != 'frame_id'}
    if run is not None:
        contracts.validate_reference(value, run, geometry_warnings=[])
    plan, notes = resolve_seams(plan, value['analysis'])
    return value, plan, notes


def drawing_review(plan, *, assessed):
    rows = []
    for spec in plan['photos']:
        row = {**spec, 'status': 'assessed' if assessed else 'unresolved'}
        mask = spec['mask']
        if (assessed and not spec['subject']['preserve_background'] and mask['enabled']
                and mask['shape']['type'] == 'polygon' and mask['stroke']['enabled']):
            row.update(status='needs_visual_review',
                       review_reason='去背景主体叠加多边形窗口描边：需确认存在独立固定窗口，不能以参考主体轮廓代替客户分割；参数暂不改写。')
        rows.append(row)
    return rows


