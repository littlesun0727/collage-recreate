"""Explicit production tool calls; never invoked by planning or submission."""
from pathlib import Path
import time
import uuid
from .common import BuildError, read_json, sha
from .drawing import draw_resource, feather_resource
from .recipes import validate_item
from .script_api import render_text
from .store import atomic, commit, load, locked
from .workflow import export, refresh, task_for
from .review_budget import ensure_remaining


def component_entry(obj, requested_members):
    """Return the selected compose members with their own reference footprint."""
    from copy import deepcopy
    from .content_scope import allowed_text

    chosen = set(requested_members)
    if obj['plan']['method'] != 'compose' or not chosen or not chosen <= set(obj.get('member_keys', [])):
        raise BuildError('component_scope', '--members requires members owned by a compose unit')
    entry = deepcopy(obj)
    entry['members'] = [member for member in entry['members'] if member['key'] in chosen]

    def bounds(field):
        return [min(member[field][0] for member in entry['members']),
                min(member[field][1] for member in entry['members']),
                max(member[field][2] for member in entry['members']),
                max(member[field][3] for member in entry['members'])]

    # --members narrows both the prompt and the image sent to the generator.
    # The union is intentional when a generated component has multiple members.
    entry['reference_box'] = bounds('reference_box')
    entry['target_box'] = bounds('target_box')
    left, top, right, bottom = entry['target_box']
    entry['size'] = [right - left, bottom - top]
    entry['plan']['method'] = 'generate'
    entry['content_scope']['allowed_text'] = [text for member in entry['members'] for text in allowed_text(member)]
    entry['content_scope']['keep'] = 'Only these component members: ' + ', '.join(requested_members)
    return entry


def produce(args):
    with locked(args.run) as root:
        state = load(root); refresh(root, state); row = task_for(state, args.task)
        if row['status'] in ('accepted','blocked','stale','deferred'): raise BuildError('tool_state', 'Task must be current and unfinished')
        ensure_remaining(state, args.task)
        obj = next((o for o in row['package']['objects'] if o['key']==args.key), None)
        if not obj: raise BuildError('ownership', 'Object does not belong to task')
        if getattr(args,'members',None):
            obj = component_entry(obj, args.members)
        method = obj['plan']['method']; params = read_json(args.parameters) if args.parameters else obj['plan']['intent']
        if method=='generate' and not args.allow_remote: raise BuildError('remote_authorization', 'Generate requires explicit --allow-remote')
        if method=='generate' and getattr(args,'image_provider','yibu')!='yibu':
            raise BuildError('provider_endpoint', 'Internal image generation uses yibu through the audit gateway only')
        if method=='cutout': raise BuildError('cutout_in_renders', 'Customer cutout moved to renders/cutout.py (BiRefNet); replan cutout slots as passthrough')
        count = sum(e['event']=='tool_started' and e['details'].get('key')==args.key for e in state['events'])
        if count >= state['limits']['max_attempts']: raise BuildError('attempt_limit', 'Tool attempt budget exhausted')
        workspace = root/'work'/args.task; workspace.mkdir(parents=True, exist_ok=True)
        folder = workspace/('tool-'+uuid.uuid4().hex)
        inputs = {'draft': state['draft'], 'entries': state['all_entries'], 'passthrough': state['passthrough']}
        from .common import oriented
        inputs['reference'] = oriented(root/state['snapshot']/'inputs/reference.png')
        start = time.monotonic(); commit(root, state, 'tool_started', {'key': args.key, 'method': method})
        try:
            if method=='text':
                folder.mkdir()
                font_id = params.pop('font_id', obj['plan']['intent'].get('font_id'))
                image, metadata = render_text(obj['size'], obj['draft']['default_text'], row['package']['fonts'][font_id], **params)
                masks = []
            else:
                validate_item({'key': obj['key'], 'method': method, 'reason': 'explicit production tool', 'recipe': params,
                               'approximations': []}, obj, row['package']['fonts'], inputs)
                if method=='draw': folder.mkdir(); image, masks, metadata = draw_resource(obj['size'], params)
                elif method=='feather':
                    folder.mkdir(); image, mask, metadata = feather_resource(obj['size'], params)
                    mask.save(folder/'feather-mask.png'); masks = []
                elif method=='generate':
                    from .yibu import generate_yibu
                    image,masks,metadata=generate_yibu(obj,params,inputs,folder,args)
                else: raise BuildError('method', 'Unsupported tool')
            image.save(folder/'asset.png')
            output = {'key': obj['key'], 'file': str((folder/'asset.png').relative_to(workspace)).replace('\\','/'),
                      'raster_size': list(image.size), 'target_box': obj['target_box']}
            if method=='text': output['text'] = metadata
            if method=='generate': output['tool_receipt']=str((folder/'tool-result.json').relative_to(workspace)).replace('\\\\','/')
            receipt = {'status': 'produced_unreviewed', 'output': output, 'parameters': params, 'metadata': metadata,
                       'sha256': sha(folder/'asset.png'), 'elapsed_seconds': time.monotonic()-start}
            atomic(folder/'tool-result.json', receipt)
        except Exception as exc:
            commit(root, state, 'tool_failed', {'key': args.key, 'code': getattr(exc, 'code', 'tool_failed'),
                                               'elapsed_seconds': time.monotonic()-start}); export(root, state); raise
        commit(root, state, 'tool_finished', {'key': args.key, 'elapsed_seconds': receipt['elapsed_seconds'], 'method': method})
        export(root, state); return receipt
