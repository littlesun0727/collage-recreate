"""Project task evidence without importing the rendering engine."""
from collage.object_roles import is_customer_photo
import hashlib
import io
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from collage.paths import relocated_path, relocated_values
from .workflow_view import DOCUMENTS, project

COMMAND_LABEL = {'manual-edit':'手动编辑','revision-commit':'对话修改','revision-prepare':'制作修改预览','build': '构建首版', 'render': '合成成图', 'screen': '筛选素材',
                 'apply': '调整排版', 'generate': '素材精修', 'recover': '局部恢复'}


def read(path, default=None):
    for attempt in range(9):
        try:
            return relocated_values(json.loads(Path(relocated_path(path)).read_text(encoding='utf-8-sig')))
        except PermissionError:
            # Windows briefly denies readers while another worker replaces a file.
            if attempt<8:
                time.sleep(min(.005*2**attempt,.15));continue
        except (OSError, ValueError):
            pass
        return {} if default is None else default


def elapsed(at):
    try:
        return max(0, (datetime.now(timezone.utc)-datetime.fromisoformat(at)).total_seconds())
    except (TypeError, ValueError):
        return None


def clean(value):
    if isinstance(value, str):
        return re.sub(r'[A-Za-z]:[\\/][^\s"\n]+', '[本机文件]', value)
    if isinstance(value, list):
        return [clean(x) for x in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k not in {'file', 'source', 'thumbnail', 'content_mask', 'python', 'credentials', 'key_file'}}
    return value


def events(run):
    rows, seen, broken = [], set(), 0
    try:
        # Decode each record separately: an interrupted Chinese character must
        # not hide all the valid records before and after the broken append.
        lines = (run/'events.jsonl').read_bytes().splitlines()
    except OSError:
        lines = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = relocated_values(json.loads(line.decode('utf-8')))
            if not isinstance(row, dict):
                raise ValueError('Not an event')
            identifier = row.get('event_id')
            if identifier and identifier in seen:
                continue
            seen.add(identifier); rows.append(row)
        except ValueError:
            broken += 1
    return rows, broken


class Store:
    def __init__(self, roots, listing_filter=None):
        self.roots = [Path(p).resolve() for p in roots]
        self.listing_filter = Path(listing_filter) if listing_filter else None
        self._artifacts = {}
        self._lock = threading.RLock()

    def tasks(self):
        tasks = {}
        for root in self.roots:
            candidates = [root] if (root/'input.json').exists() else sorted(root.glob('*/input.json'))
            for candidate in candidates:
                run = candidate if candidate == root else candidate.parent
                run = run.resolve()
                if not run.is_relative_to(root):
                    continue
                if not (run/'input.json').exists():
                    continue
                key = hashlib.sha256(str(run).encode()).hexdigest()[:16]
                portable = read(run/'portable-task.json').get('id')
                if isinstance(portable,str) and re.fullmatch(r'[0-9a-f]{16}',portable):
                    key = portable
                if key in tasks and tasks[key] != run:
                    raise ValueError('Duplicate portable task identifier')
                tasks[key] = run
        return tasks

    def image(self, task, run, path, crop=None):
        if not path:
            return None
        p = Path(path)
        p = (run/p).resolve() if not p.is_absolute() else p.resolve()
        if not p.is_relative_to(run.resolve()) or not p.is_file() or p.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
            return None
        stat = p.stat()
        signature = json.dumps([str(p), crop, stat.st_size, stat.st_mtime_ns])
        token = hashlib.sha256(signature.encode()).hexdigest()[:24]
        with self._lock:
            self._artifacts[(task, token)] = (p, crop, stat.st_size, stat.st_mtime_ns)
        return f'/api/tasks/{task}/images/{token}'

    def artifact(self, task, token):
        with self._lock:
            entry = self._artifacts.get((task, token))
        if not entry:
            raise KeyError('Unknown image')
        p, crop, size, modified = entry
        stat = p.stat()
        if stat.st_size != size or stat.st_mtime_ns != modified:
            raise KeyError('Image changed; refresh task')
        if crop:
            with Image.open(p) as im:
                im = im.crop(crop); im.thumbnail((900, 900))
                buffer = io.BytesIO(); im.save(buffer, 'PNG')
                return buffer.getvalue(), 'image/png'
        return p.read_bytes(), {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}.get(p.suffix.lower(), 'image/png')

    def product_document(self, task, name):
        run = self.tasks().get(task)
        if run is None or name not in DOCUMENTS:
            raise KeyError('Unknown document')
        path = (run/name).resolve()
        if not path.is_relative_to(run):
            raise KeyError('Document outside task')
        return path.read_bytes()

    def customer_image(self, key, run, asset):
        # Binding requests also keep a task-local copy when the catalog shares
        # thumbnails with another task. Image access stays inside this task.
        return (self.image(key, run, asset.get('thumbnail')) or
                self.image(key, run, run/'prepared/vlm-materials'/f"{asset.get('id')}.jpg"))

    def listing(self):
        result = []
        visible = None
        if self.listing_filter and self.listing_filter.exists():
            identifiers = read(self.listing_filter).get('task_ids')
            if (not isinstance(identifiers, list)
                    or any(not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{16}', key)
                           for key in identifiers)
                    or len(set(identifiers)) != len(identifiers)):
                raise ValueError('Invalid visible task list')
            visible = set(identifiers)
        for key, run in self.tasks().items():
            batch = read(run/'batch-run.json')
            if visible is not None and key not in visible and not batch:
                continue
            inp = read(run/'input.json'); outcome = read(run/'result.json')
            result.append({'id': key, 'name': batch.get('reference_name') or run.name, 'created_at': inp.get('created_at'),
                           'batch': batch or None,
                           'quality': outcome.get('status', 'pending'),
                           'thumbnail': self.image(key, run, run/'final.png') or self.image(key, run, run/'prepared/reference.png')})
        return sorted(result, key=lambda r: r.get('created_at') or '', reverse=True)

    def document(self, key):
        run = self.tasks().get(key)
        if run is None:
            raise KeyError('Unknown task')
        inp = read(run/'input.json'); result = read(run/'result.json')
        analysis = read(run/'analysis.json'); scene = read(run/'scene.json')
        if not analysis:
            # Stage-only evaluations can retain the public structure without an
            # engine scene. Convert coordinates for display, without compiling it.
            structure = read(run/'structure.json')
            mode = structure.get('coordinate_mode') or read(run/'workflow-models.json').get('analyze', {}).get('coordinate_mode')
            width, height = inp.get('reference_size', [1000, 1000])
            objects = []
            for obj in structure.get('objects', []):
                box = obj.get('bbox')
                if box and mode == 'normalized_1000':
                    box = [round(v * (width if i % 2 == 0 else height) / 1000) for i, v in enumerate(box)]
                objects.append({**obj, 'bbox': box, 'label': obj.get('label') or obj['id'],
                                'method': 'extract' if obj['kind'] in {'overlay', 'frame'} or
                                (obj['kind'] == 'background' and not obj.get('color')) else 'local'})
            analysis = {'objects': objects}
        catalog = read(run/'prepared/catalog.json'); rows, broken = events(run)
        observed = any(r.get('schema_version') == 'collage-events/1' for r in rows)
        current_version = None; reviews = {}; latest_checkpoint = {}
        material_round = None; materials = {}; groups = {}; extracted = {}; last_command = None
        for row in rows:
            event = row.get('event', ''); at = row.get('at')
            if event == 'build_started':
                last_command = row
                groups = {}; extracted = {}; materials = {}; material_round = None
            if event in {'build_finished', 'build_failed'} and last_command:
                last_command = {**last_command, 'status': event.removeprefix('build_')}
            if event in {'agent_stage', 'workflow_stage'}:
                if row.get('stage') == 2 and row.get('artifacts'):
                    latest_checkpoint = row['artifacts']
            if event == 'materials_started':
                material_round = row.get('material_attempt'); materials = {}
            if event == 'material_extracted':
                extracted[row['object_id']] = row
            if event in {'material_started', 'material_finished'} and row.get('material_attempt') == material_round:
                materials[row['object_id']] = row
            if event == 'reveal_request_started':
                groups[row.get('task', '')] = {'status': 'waiting', 'ids': row.get('targets', []), 'at': at}
            if event == 'reveal_group_finished':
                groups[row.get('task', '')] = {'status': 'failed' if row.get('error') else 'complete',
                                               'ids': row.get('requested_ids', []), 'at': at,
                                               'error': clean(row.get('error', '')),
                                               'available_ids': row.get('available_ids', []),
                                               'missing_ids': row.get('missing_ids', row.get('requested_ids', [])) if row.get('error') else [],
                                               'elapsed_seconds': row.get('elapsed_seconds'), 'cache_hit': row.get('cache_hit', False)}
            if event == 'version_published':
                current_version = row.get('version_id'); reviews.pop(current_version, None)
            if event == 'review_registered' and current_version:
                reviews[current_version] = clean(row.get('review', {}))
        heartbeat = read(run/'observability/heartbeat.json')
        age = elapsed(heartbeat.get('at'))
        live = bool(heartbeat.get('active') and age is not None and age < 15)
        versions = []
        for path in sorted((run/'observability/versions').glob('*/manifest.json')):
            if path.parent.name.startswith('.'):
                continue
            m = read(path)
            if not m.get('version_id') or not all((path.parent/m.get('artifacts', {}).get(n, {}).get('path', '!missing')).is_file() for n in ('final.png', 'scene.json', 'result.json')):
                continue
            version_scene = read(path.parent/'scene.json'); vr = read(path.parent/'result.json')
            arts = {name: self.image(key, run, path.parent/a['path']) for name, a in m.get('artifacts', {}).items()}
            vmaterials = []
            vresources = {r['id']: r for r in vr.get('resources', [])}
            for obj in version_scene.get('objects', []):
                oid = obj['id']; resource = vresources.get(oid, {})
                vmaterials.append({'id': oid, 'label': obj.get('label', oid), 'kind': obj['kind'],
                                   'background_type': obj.get('background_type'),
                                   'bbox': obj.get('bbox'), 'method': '程序相框' if resource.get('metadata',{}).get('method')=='local_frame' else '客户照片' if is_customer_photo(obj) else '参考提取' if obj.get('method')=='extract' else '本地绘制',
                                   'status': 'unresolved' if resource.get('quality') in {'placeholder','unresolved'} else 'complete',
                                   'quality': resource.get('quality'), 'cache_hit': False,
                                   'images': {'reference': self.image(key, run, run/'prepared/reference.png', obj.get('bbox')), 'final': arts.get('material:'+oid), 'raw': arts.get('raw:'+oid), 'processed': arts.get('processed:'+oid)},
                                   'decision': clean(obj.get('gate', {})), 'note': clean(resource.get('metadata', {}).get('note', ''))})
            versions.append({'id': m['version_id'], 'parent_id': m.get('parent_version_id'), 'render_id': m['render_id'],
                             'command': m.get('command'), 'materials': vmaterials,
                             'label': '首版' if not versions else COMMAND_LABEL.get(m.get('command'), '成图更新'),
                             'at': m.get('at'), 'image': arts.get('final.png'), 'artifacts': arts,
                             'changes': clean(m.get('changes', [])), 'intent': clean(m.get('intent', {})),
                             'review': reviews.get(m['version_id']), 'objects': clean(version_scene.get('objects', [])),
                             'issues': clean(vr.get('issues', [])), 'incomplete_objects': vr.get('incomplete_objects', [])})
        latest = versions[-1] if versions else None
        objects = scene.get('objects') or analysis.get('objects', [])
        bindings = {b['slot_id']: b for b in read(run/'bindings.json').get('photos', [])}
        assets = {a['id']: a for a in catalog.get('assets', [])}
        resources = {a['id']: a for a in result.get('resources', [])}
        cards = []
        rebuilding = bool(last_command and last_command.get('status') != 'finished')
        for obj in objects:
            oid = obj['id']; row = materials.get(oid, {})
            state = 'pending'; quality = row.get('quality'); output = None
            if row.get('event') == 'material_started':
                state = 'running'
            elif row.get('event') == 'material_finished':
                state = 'unresolved' if quality in {'placeholder', 'unresolved'} else 'complete'
                output = self.image(key, run, row.get('file'))
            elif oid in resources and not rebuilding:
                state = 'unresolved' if resources[oid].get('quality') in {'placeholder', 'unresolved'} else 'complete'
                output = self.image(key, run, resources[oid].get('file'))
            if rebuilding and row.get('attempt_id') != last_command.get('attempt_id'):
                state = 'pending'; output = None
            source = assets.get(bindings.get(oid, {}).get('asset_id'), {})
            method = '客户照片' if is_customer_photo(obj) else '参考提取' if obj.get('method') == 'extract' else '本地绘制'
            metadata = resources.get(oid, {}).get('metadata', {})
            if metadata.get('method')=='local_frame':method='程序相框'
            images = {'reference': self.image(key, run, run/'prepared/reference.png', obj.get('bbox')),
                      'customer': self.customer_image(key, run, source), 'final': output}
            if oid in extracted:
                images['raw'] = self.image(key, run, extracted[oid].get('raw_file'))
                images['processed'] = self.image(key, run, extracted[oid].get('processed_file'))
                if state == 'pending':
                    state = 'waiting'
            if output and isinstance(metadata.get('foreground'), str):
                images['processed'] = self.image(key, run, metadata['foreground'])
            failure = next((g for g in reversed(list(groups.values())) if g.get('status') == 'failed'
                            and oid in g.get('missing_ids', [])
                            and (not rebuilding or g.get('at', '') >= last_command.get('at', ''))), None)
            note = metadata.get('note', '')
            if failure and state == 'unresolved':
                state = 'failed'
                note = failure['error'] or '提取或下载失败，未采用该素材'
            cards.append({'id': oid, 'label': obj.get('label', oid), 'kind': obj['kind'], 'bbox': obj.get('bbox'),
                          'background_type': obj.get('background_type'),
                          'binding': bindings.get(oid),
                          'method': method, 'status': state, 'quality': quality, 'cache_hit': row.get('cache_hit', False),
                          'images': images, 'decision': clean(obj.get('gate', {})), 'note': clean(note)})
        if rebuilding:
            groups = {k: v for k, v in groups.items() if v.get('at', '') >= last_command.get('at', '')}
        reference = self.image(key, run, run/'prepared/reference.png')
        boxes = self.image(key, run, latest_checkpoint.get('previews/analysis-boxes.png') or run/'previews/analysis-boxes.png')
        reference_analysis = read(run/latest_checkpoint['analysis.json']) if latest_checkpoint.get('analysis.json') else analysis
        stages, current, history, workflow = project(run, read, rows, cards, list(groups.values()))
        for stage in stages:
            for artifact in stage['artifacts']:
                path = artifact.pop('path')
                artifact['url'] = (self.image(key, run, path) if artifact['kind'] == 'image'
                                   else f'/api/tasks/{key}/documents/{path}' if artifact['exists'] else None)
        active = stages[current-1]
        workflow_age = elapsed(workflow.get('updated_at'))
        live = live or (workflow.get('status') == 'running' and workflow_age is not None and workflow_age < 15)
        if workflow.get('status') == 'running' and workflow.get('worker_pid'):
            from .chat import process_alive
            live = process_alive(workflow['worker_pid'])
        execution = ('正在执行' if live else '执行未完成' if active['status'] == 'failed'
                     else '首版已生成' if stages[4]['status'] == 'complete' else '等待下一步')
        if not live and active['status'] == 'running':
            execution = '等待执行端更新'
        timing = read(run/'workflow-timing.json') or read(run/'sdk-timing.json')
        timing = {k: timing[k] for k in ('model','model_policy','effort','controller','usage','started_at','finished_at','elapsed_seconds','status') if k in timing}
        batch = read(run/'batch-run.json')
        return clean({'id': key, 'name': batch.get('reference_name') or run.name, 'batch': batch or None,
                      'created_at': inp.get('created_at'), 'reference_size': inp.get('reference_size'),
                      'sdk_timing': timing,
                      'reference_objects': reference_analysis.get('objects', []),
                      'instructions': inp.get('instructions', ''), 'historical': not observed, 'current_stage': current,
                      'stages': stages, 'execution': execution, 'live': live, 'heartbeat_at': heartbeat.get('at'),
                      'history': history[-160:], 'reference': reference, 'boxes': boxes,
                      'windows': self.image(key, run, run/'previews/photo-windows.png'),
                      'catalog': [{'id': a['id'], 'image': self.customer_image(key, run, a)} for a in catalog.get('assets', [])],
                      'current_image': latest['image'] if latest else self.image(key, run, run/'final.png'),
                      'first_image': self.image(key, run, run/'previews/first.png'),
                      'versions': versions, 'materials': cards, 'groups': list(groups.values()),
                      'quality': result.get('status', 'pending'), 'issues': clean(result.get('issues', [])),
                      'review': clean(result.get('visual_review')), 'recording_incomplete': bool(broken or (run/'observability/warning.json').exists())})
