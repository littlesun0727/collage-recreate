"""Model-facing reference coordinates; persisted workflow documents stay in pixels.

Only declared global geometry is converted. In particular style.window.bbox,
polygon points, source_crop, stroke/fade ratios and rotations are not global boxes.
"""
from copy import deepcopy
import math

SLOT = '{{REFERENCE_COORDINATES}}'
VERSION = 1
# Deliberately explicit: Qwen2.5-VL and unknown future models must not match this.
NORMALIZED_MODELS = frozenset({'qwen/qwen3.8-flash', 'qwen/qwen3.8-max-0902',
                              'qwen3.8-flash', 'qwen3.8-max-0902', 'qwen3.8-max'})


def resolve_mode(model, setting='auto'):
    if setting not in {'auto', 'pixel', 'normalized_1000'}:
        raise ValueError('Unknown coordinate mode: ' + setting)
    return ('normalized_1000' if model in NORMALIZED_MODELS else 'pixel') if setting == 'auto' else setting


def instructions(mode):
    if mode == 'normalized_1000':
        main = ('本次输入和输出中的全局 bbox、window_bbox、crop_bbox 均使用 0～1000 归一化坐标，'
                '顺序为 [左,上,右,下]。横坐标相对于整张参考图宽度，纵坐标相对于整张参考图高度。')
    elif mode == 'pixel':
        main = ('本次输入和输出中的全局 bbox、window_bbox、crop_bbox 均使用 reference_size 的原图像素坐标，'
                '顺序为 [左,上,右,下]；横坐标不超过图宽，纵坐标不超过图高。')
    else:
        raise ValueError('Unknown coordinate mode: ' + mode)
    return ('【坐标约定】\n' + main + '\n'
            'reference_size 始终保留参考图的实际像素宽高。回复的 coordinate_space 必须与输入一致。\n'
            'style.window.bbox、形状 points、source_crop、crop_center 使用各自定义的局部 0～1 坐标；'
            '字体、线宽、阴影和 photo_slots.width/height 仍用原图像素。旋转角度及描边、渐隐比例不变。')


def _records(document):
    """Yield named global rectangles only; never recursively scale arbitrary bbox keys."""
    if not isinstance(document, dict):
        return
    containers = [('', document)]
    if isinstance(document.get('analysis'), dict):
        containers.append(('analysis.', document['analysis']))
    for prefix, container in containers:
        for field in ('objects', 'text_units'):
            rows = container.get(field, [])
            if not isinstance(rows, list):
                continue
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                for name in ('bbox', 'window_bbox') if field == 'objects' else ('bbox',):
                    if name in row:
                        yield (prefix + field, row.get('id', index), name), row, name
    regions = document.get('crop_regions', [])
    if isinstance(regions, list):
        for index, row in enumerate(regions):
            if isinstance(row, dict) and 'crop_bbox' in row:
                yield ('crop_regions', row.get('photo_id', index), 'crop_bbox'), row, 'crop_bbox'


def _check_box(box, size, label):
    if (not isinstance(box, list) or len(box) != 4 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box)):
        raise ValueError(f'{label}: expected four finite coordinates')
    l, t, r, b = box
    if not (0 <= l < r <= size[0] and 0 <= t < b <= size[1]):
        raise ValueError(f'{label}: expected positive box within [0,0,{size[0]},{size[1]}]')


def _bbox_schemas(schema):
    """Only the global geometry fields in reference/boxes outputs."""
    props = schema.get('properties', {})
    analysis = props.get('analysis', {}).get('properties', {})
    fields = analysis.get('objects', {}).get('items', {}).get('properties', {})
    for name in ('bbox', 'window_bbox'):
        if name in fields:
            yield fields[name]
    units = props.get('text_units', {}).get('items', {}).get('properties', {})
    if 'bbox' in units:
        yield units['bbox']


class ReferenceCoordinates:
    """Adapt one request; same instance decodes replies, retries and recovery candidates."""
    def __init__(self, context, mode):
        self.mode = mode
        self.size = deepcopy(context.get('reference_size'))
        if (not isinstance(self.size, list) or len(self.size) != 2 or
                any(type(n) is not int or n <= 0 for n in self.size)):
            raise ValueError('Reference coordinate requests require actual reference_size')
        self.context = deepcopy(context)
        self.originals = {}
        self.sent = {}
        for key, row, name in _records(self.context):
            _check_box(row[name], self.size, str(key))
            self.originals[key] = deepcopy(row[name])
            if mode == 'normalized_1000':
                row[name] = [round(v / self.size[i % 2] * 1000, 8) for i, v in enumerate(row[name])]
            self.sent[key] = deepcopy(row[name])
        self.context['coordinate_space'] = mode
        # Give the window stage actual aspect ratios; normalized width/height are not aspect ratios.
        if isinstance(context.get('analysis'), dict) and 'photo_slots' not in self.context:
            self.context['photo_slots'] = []
            for item in context['analysis'].get('objects', []):
                if item.get('kind') == 'photo':
                    l, t, r, b = item['bbox']
                    self.context['photo_slots'].append({'slot_id': item['id'], 'width': r-l, 'height': b-t,
                                                       'aspect_ratio': round((r-l)/(b-t), 6)})

    @property
    def metadata(self):
        return {'mode': self.mode, 'canonical_mode': 'pixel', 'reference_size': self.size,
                'scale': 1000 if self.mode == 'normalized_1000' else None, 'adapter_version': VERSION}

    def prompt(self, template):
        if template.count(SLOT) != 1:
            raise ValueError('Reference prompt must contain exactly one coordinate slot')
        return template.replace(SLOT, instructions(self.mode))

    def schema(self, canonical):
        schema = deepcopy(canonical)
        schema['properties']['coordinate_space'] = {'type': 'string', 'const': self.mode}
        schema['required'] = list(schema.get('required', [])) + ['coordinate_space']
        for box in _bbox_schemas(schema):
            box['items'] = {'type': 'number' if self.mode == 'normalized_1000' else 'integer',
                            'minimum': 0, 'maximum': 1000 if self.mode == 'normalized_1000' else max(self.size)}
            box['description'] = ('全局 [left,top,right,bottom]；' +
                                  ('0～1000 归一化坐标。' if self.mode == 'normalized_1000'
                                   else '原图像素；x<=reference_size[0]，y<=reference_size[1]。'))
        analysis = schema['properties'].get('analysis', {}).get('properties', {})
        if 'reference_size' in analysis:
            analysis['reference_size']['const'] = self.size
        return schema

    def decode(self, raw):
        if not isinstance(raw, dict) or raw.get('coordinate_space') != self.mode:
            raise ValueError('coordinate_space must match request: ' + self.mode)
        result = deepcopy(raw)
        result.pop('coordinate_space')
        if isinstance(result.get('analysis'), dict) and result['analysis'].get('reference_size') != self.size:
            raise ValueError('reference_size must preserve the actual image dimensions')
        for key, row, name in _records(result):
            _check_box(row[name], [1000, 1000] if self.mode == 'normalized_1000' else self.size, str(key))
            if self.mode == 'normalized_1000':
                # Preserve untouched rectangles exactly, including immutable window_bbox fields.
                if key in self.sent and row[name] == self.sent[key]:
                    row[name] = deepcopy(self.originals[key])
                else:
                    row[name] = [round(v / 1000 * self.size[i % 2]) for i, v in enumerate(row[name])]
                _check_box(row[name], self.size, str(key) + ' converted pixels')
        return result
