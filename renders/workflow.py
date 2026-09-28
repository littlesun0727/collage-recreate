"""Assemble build assets and customer slots; cutouts use local BiRefNet."""
import argparse
import copy
import json
import subprocess
import sys
sys.dont_write_bytecode = True
from pathlib import Path

from PIL import Image
from compositor import box_size, compose, digest, numbers, open_pinned, pin, read_json, write_json
from cutout import DEFAULT_OUTLINE, DEFAULT_COLOR


def object_key(item):
    return 'background' if item['type'] == 'background' else item['type'] + ':' + item['id']


def asset_fit(asset, cutout_member_keys):
    if any(key in cutout_member_keys for key in asset['member_keys']):
        return 'contain', True
    if asset.get('paint_box') or asset.get('method') in ('draw', 'compose'):
        return 'stretch', False
    return 'contain', False


def customer_cutout(source, output, model, python=None, cache=None,
                    outline_width=DEFAULT_OUTLINE, outline_color=DEFAULT_COLOR):
    if not model or not Path(model).is_file():
        raise ValueError('Cutout slots require --cutout-model pointing to local BiRefNet FP32 ONNX weights')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(python or sys.executable), '-B', '-X', 'utf8', str(Path(__file__).with_name('cutout.py')),
               '--source', source['file'], '--model', str(Path(model).resolve()), '--output', str(output),
               '--outline-width', str(outline_width), '--outline-color', outline_color]
    if cache:
        command += ['--cache', str(Path(cache).resolve())]
    with output.with_suffix('.stdout.txt').open('wb') as stdout, output.with_suffix('.stderr.txt').open('wb') as stderr:
        result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=300,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('BiRefNet cutout failed; inspect ' + str(output.with_suffix('.stderr.txt')))
    receipt = read_json(output / 'cutout.json')
    if receipt['segmentation']['source']['sha256'] != source['sha256']:
        raise ValueError('Cutout source does not match the bound customer photo')
    pin(receipt['asset']['file'], receipt['asset']['sha256'])
    return receipt


def prepare(build, output, deliveries, cutout_model=None, cutout_python=None,
            outline_width=DEFAULT_OUTLINE, outline_color=DEFAULT_COLOR, cutout_cache=None):
    build, output = Path(build).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('prepare requires a new output directory')
    if output == build or build in output.parents:
        raise ValueError('Keep renders outside the build directory')
    state = read_json(build / 'state.json')
    catalog = read_json(build / 'assets.json')
    if catalog['schema_version'] != 'build-unit-assets-v1':
        raise ValueError('Expected build-unit-assets-v1')
    if catalog['revision'] != state['revision']:
        raise ValueError('Build catalog revision differs from state')
    inputs = state['input']
    provenance = [pin(build / 'assets.json'), pin(build / 'state.json')]
    for name in ('draft', 'reference', 'bindings'):
        provenance.append(pin(inputs[name + '_path'], inputs[name + '_sha256']))
    order = {object_key(item): i for i, item in enumerate(state['draft']['layer_order'])}
    cutout_member_keys = {'slot:' + item['id'] for item in state['draft']['slots'] if item['mode'] == 'cutout'}
    metadata = {}
    for task in state['tasks'].values():
        for obj in task['package']['objects']:
            metadata[obj['key']] = obj
    chosen = {}
    for asset in catalog['assets']:
        if asset['key'] in chosen:
            raise ValueError('Duplicate asset key: ' + asset['key'])
        chosen[asset['key']] = (asset, build, False)
    unresolved = []
    for task in state['tasks'].values():
        if task['status'] != 'accepted' and task.get('review'):
            unresolved.extend(task['review'].get('effective_issues', []))
    unresolved = list(dict.fromkeys(unresolved))
    overridden = set()
    for delivery_path in deliveries:
        path = Path(delivery_path).resolve()
        delivery = read_json(path)
        task = state['tasks'][delivery['task_id']]['package']
        if delivery.get('schema_version') != 'build-delivery-v2' or delivery['task_revision'] != task['revision']:
            raise ValueError('Delivery task revision/schema mismatch: ' + str(path))
        provenance.append(pin(path))
        unresolved.extend(delivery.get('unresolved', []))
        unresolved.extend('Source approximation: ' + text for text in delivery.get('approximations', []))
        for item in delivery['outputs']:
            key = item['key']
            if key not in task['owned_keys'] or key in overridden:
                raise ValueError('Unowned or duplicate override: ' + key)
            overridden.add(key)
            obj = metadata[key]
            asset = {**item, 'member_keys': obj['member_keys'], 'layer_index': obj['layer_index'],
                     'method': obj['plan']['method'], 'status': 'unreviewed_override'}
            chosen[key] = (asset, path.parent, True)
    layers, issues = [], []
    for key, (asset, root, override) in chosen.items():
        # Old runs may already contain an independently baked cutout. Recreate
        # that slot from its bound source through the same renders entrance.
        cutout_members = set(asset['member_keys']) & cutout_member_keys
        if cutout_members:
            if set(asset['member_keys']) != cutout_members:
                raise ValueError('Legacy cutout is flattened with other members; split this unit before rendering: ' + key)
            unresolved.append('Legacy build cutout replaced from bound customer source: ' + key)
            continue
        source_path = (root / asset['file']).resolve()
        if not source_path.is_file():
            issues.append('Missing asset file: ' + str(source_path))
            continue
        source = pin(source_path, asset.get('sha256'))
        box = asset.get('paint_box', asset['target_box'])
        box_size(box)
        obj = metadata.get(key, {})
        if obj.get('interleaved_context_keys'):
            unresolved.append('Flattened unit has interleaved layers; inspect occlusion: ' + key)
        with Image.open(source_path) as im:
            if list(im.size) != asset['raster_size']:
                raise ValueError('Asset raster_size mismatch: ' + key)
        # A cutout stays proportional even when it is packaged in a compose unit.
        fit, contains_cutout = asset_fit(asset, cutout_member_keys)
        layers.append({'key': key, 'kind': 'asset', 'member_keys': asset['member_keys'],
                       'source': source, 'source_status': asset.get('status', 'unknown'),
                       'source_override': override, 'original_box': box, 'box': box,
                       'layer_index': asset['layer_index'], 'fit': fit,
                       'contains_cutout': contains_cutout, 'rotation': 0})
    bindings = read_json(inputs['bindings_path'])
    bound = {item['slot_id']: item['asset_id'] for item in bindings['bindings']}
    customer_assets = {item['asset_id']: item for item in bindings['assets']}
    runtime_slots = list(catalog['passthrough'])
    known_slots = {item['key'] for item in runtime_slots}
    # Compatibility with builds created before cutout moved to passthrough.
    cw, ch = inputs['canvas_size']
    for slot in state['draft']['slots']:
        key = 'slot:' + slot['id']
        if key in cutout_member_keys and key not in known_slots:
            runtime_slots.append({'key': key, 'id': slot['id'], 'draft': slot,
                                  'target_box': [(v*d+500)//1000 for v,d in zip(slot['source_bbox_1000'],(cw,ch,cw,ch))],
                                  'source_asset': customer_assets.get(bound.get(slot['id']))})
    if any(item['draft']['mode'] == 'cutout' and item.get('source_asset') for item in runtime_slots):
        if not cutout_model or not Path(cutout_model).is_file():
            raise ValueError('Cutout slots require --cutout-model (BiRefNet FP32 ONNX)')
    for item in runtime_slots:
        source = item.get('source_asset')
        if not source or bound.get(item['id']) != source['asset_id']:
            issues.append('Missing or inconsistent photo binding: ' + item['key'])
            continue
        if not Path(source['path']).is_file():
            issues.append('Missing photo file: ' + source['path'])
            continue
        if item['draft']['mode'] == 'cutout':
            original = pin(source['path'], source['sha256'])
            cutout = customer_cutout(original, output / 'cutouts' / item['id'], cutout_model,
                                     cutout_python, cutout_cache, outline_width, outline_color)
            receipt_pin = pin(output / 'cutouts' / item['id'] / 'cutout.json')
            provenance.extend([original, receipt_pin])
            layers.append({'key': item['key'], 'kind': 'cutout', 'member_keys': [item['key']],
                           'source': cutout['asset'], 'source_status': 'bound', 'cutout_receipt': receipt_pin,
                           'original_box': item['target_box'], 'box': item['target_box'],
                           'layer_index': order[item['key']], 'fit': 'contain', 'contains_cutout': True, 'rotation': 0})
            continue
        layers.append({'key': item['key'], 'kind': 'photo', 'member_keys': [item['key']],
                       'source': pin(source['path'], source['sha256']), 'source_status': 'bound',
                       'original_box': item['target_box'], 'box': item['target_box'],
                       'layer_index': order[item['key']], 'fit': 'cover', 'crop_center': [0.5, 0.5], 'rotation': 0})
    members = [key for layer in layers for key in layer['member_keys']]
    if len(set(members)) != len(members):
        raise ValueError('Multiple units own the same draft object')
    relationships = [{'key': 'overlay:' + item['id'], 'attachment': item['attachment']}
                     for item in state['draft']['overlays'] if item.get('attachment')]
    layout = {'schema_version': 'collage-renders-v1', 'canvas_size': inputs['canvas_size'],
              'reference': pin(inputs['reference_path'], inputs['reference_sha256']),
              'provenance': provenance, 'expected_members': list(order), 'relationships': relationships,
              'layers': layers, 'source_issues': issues, 'source_notes': unresolved,
              'adjustments': [], 'policy': 'local customer cutouts via BiRefNet; local composition; no image generation'}
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'layout.json', layout)
    return {'layout': str(output / 'layout.json'), 'layers': len(layers)}


def render(path):
    path = Path(path).resolve()
    layout = read_json(path)
    if layout.get('schema_version') != 'collage-renders-v1':
        raise ValueError('Unsupported layout schema')
    root = path.parent
    targets = [root / name for name in ('final.png', 'comparison.png', 'result.json')]
    protected = [Path(item['file']).resolve() for item in layout['provenance']]
    protected += [Path(layer['source']['file']).resolve() for layer in layout['layers']]
    protected += [Path(layer['mask']['file']).resolve() for layer in layout['layers'] if layer.get('mask')]
    protected += [Path(layout['reference']['file']).resolve(), path]
    if any(target in protected for target in targets):
        raise ValueError('Output would overwrite an input')
    keys = [layer['key'] for layer in layout['layers']]
    members = [member for layer in layout['layers'] for member in layer['member_keys']]
    if len(set(keys)) != len(keys) or len(set(members)) != len(members):
        raise ValueError('Duplicate layer/member ownership')
    reference = open_pinned(layout['reference'])
    canvas, rendered = compose(layout)
    missing = sorted(set(layout['expected_members']) - set(members))
    issues = list(layout['source_issues'])
    if missing:
        issues.append('Missing draft objects: ' + ', '.join(missing))
    unaccepted = [layer['key'] for layer in layout['layers']
                  if layer['kind'] == 'asset' and layer['source_status'] != 'accepted']
    if unaccepted:
        issues.append('Some source assets have not passed build acceptance')
    comparison = Image.new('RGBA', (canvas.width * 2, canvas.height), 'white')
    comparison.alpha_composite(reference.resize(canvas.size, Image.Resampling.LANCZOS), (0, 0))
    comparison.alpha_composite(canvas, (canvas.width, 0))
    for file, image in ((targets[0], canvas), (targets[1], comparison)):
        temp = file.with_suffix('.tmp')
        image.save(temp, format='PNG')
        temp.replace(file)
    result = {'schema_version': 'collage-renders-result-v1', 'exported': True,
              'coverage_complete': not missing and not layout['source_issues'],
              'source_assets_accepted': not unaccepted, 'renders_verified': False,
              'layout_sha256': digest(path), 'final_sha256': digest(targets[0]),
              'rendered_layers': rendered, 'missing_members': missing, 'unaccepted_assets': unaccepted,
              'issues': issues, 'source_notes': layout['source_notes'],
              'files': {'final': str(targets[0]), 'comparison': str(targets[1]), 'layout': str(path)},
              'visual_review': None}
    write_json(targets[2], result)
    return result


def adjust(path, patch_path):
    path = Path(path).resolve()
    layout = read_json(path)
    patch = read_json(patch_path)
    if not patch.get('reason'):
        raise ValueError('Adjustment requires a reason')
    by_key = {layer['key']: layer for layer in layout['layers']}
    allowed = {'box', 'fit', 'crop_center', 'rotation', 'mask', 'layer_index', 'source_crop', 'mirror_x'}
    for change in patch['changes']:
        layer = by_key[change['key']]
        if set(change) - allowed - {'key'}:
            raise ValueError('Only local geometry/mask changes are allowed')
        before = copy.deepcopy(layer)
        layer.update({key: value for key, value in change.items() if key != 'key'})
        if layer.get('mask'):
            mask = layer['mask']
            layer['mask'] = {**mask, **pin(Path(patch_path).resolve().parent / mask['file'], mask.get('sha256'))}
        layout['adjustments'].append({'key': layer['key'], 'reason': patch['reason'],
                                      'before': before, 'after': copy.deepcopy(layer)})
    compose(layout)  # Validate all inputs and geometry before committing.
    write_json(path, layout)
    return render(path)


def review(path, verdict, note):
    path = Path(path).resolve()
    result_path = path.parent / 'result.json'
    result = read_json(result_path)
    if result['layout_sha256'] != digest(path) or result['final_sha256'] != digest(path.parent / 'final.png'):
        raise ValueError('Render changed; render again before review')
    layout = read_json(path)
    for layer in layout['layers']:
        pin(layer['source']['file'], layer['source']['sha256'])
        if layer.get('mask'):
            pin(layer['mask']['file'], layer['mask']['sha256'])
    pin(layout['reference']['file'], layout['reference']['sha256'])
    result['visual_review'] = {'verdict': verdict, 'note': note, 'final_sha256': result['final_sha256']}
    result['renders_verified'] = verdict == 'pass' and result['coverage_complete'] and result['source_assets_accepted']
    write_json(result_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('prepare')
    init.add_argument('--build', required=True)
    init.add_argument('--output', required=True)
    init.add_argument('--delivery', action='append', default=[], help='Explicit work delivery override; remains unaccepted')
    init.add_argument('--cutout-model', type=Path, help='Local BiRefNet FP32 ONNX weights; required for cutout slots')
    init.add_argument('--cutout-python', type=Path, help='Optional Python environment with ONNX Runtime and OpenCV')
    init.add_argument('--cutout-cache', type=Path, help='Reuse source/model masks across render attempts')
    init.add_argument('--outline-width', default=DEFAULT_OUTLINE, help='Native cutout pixels or percent of subject short side; default 2%%')
    init.add_argument('--outline-color', default=DEFAULT_COLOR)
    for command in ('render', 'adjust', 'review'):
        sub = commands.add_parser(command)
        sub.add_argument('--layout', required=True)
        if command == 'adjust':
            sub.add_argument('--patch', required=True)
        if command == 'review':
            sub.add_argument('--verdict', choices=['pass', 'needs_changes'], required=True)
            sub.add_argument('--note', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.build, args.output, args.delivery, args.cutout_model, args.cutout_python,
                             args.outline_width, args.outline_color, args.cutout_cache)
        elif args.command == 'render':
            result = render(args.layout)
        elif args.command == 'adjust':
            result = adjust(args.layout, args.patch)
        else:
            result = review(args.layout, args.verdict, args.note)
        print(json.dumps(result, ensure_ascii=True, indent=2))
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
