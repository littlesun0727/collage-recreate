"""Project task evidence without importing or executing the production skill."""
import hashlib
import io
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

LABELS = ['准备素材', '分析布局', '构建首版', '筛选素材', '整图复核', '交付结果']
COMMAND_STAGE = {'revision-recover':5,'revision-prepare':5,'revision-commit':5,'prepare': 1, 'validate': 2, 'reveal-plan': 2, 'build': 3, 'render': 3,
                 'screen': 4, 'apply': 5, 'review': 5, 'recover': 5, 'generate': 5}
COMMAND_LABEL = {'manual-edit':'手动编辑','revision-commit':'对话修改','revision-prepare':'制作修改预览','build': '构建首版', 'render': '合成成图', 'screen': '筛选素材',
                 'apply': '调整排版', 'generate': '素材精修', 'recover': '局部恢复'}


def read(path, default=None):
    for attempt in range(9):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8-sig'))
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
            row = json.loads(line.decode('utf-8'))
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
    def __init__(self, roots):
        self.roots = [Path(p).resolve() for p in roots]
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

    def listing(self):
        result = []
        for key, run in self.tasks().items():
            inp = read(run/'input.json'); outcome = read(run/'result.json')
            result.append({'id': key, 'name': run.name, 'created_at': inp.get('created_at'),
                           'quality': outcome.get('status', 'pending'),
                           'thumbnail': self.image(key, run, run/'final.png') or self.image(key, run, run/'prepared/reference.png')})
        return sorted(result, key=lambda r: r.get('created_at') or '', reverse=True)

    def document(self, key):
        run = self.tasks().get(key)
        if run is None:
            raise KeyError('Unknown task')
        inp = read(run/'input.json'); result = read(run/'result.json')
        analysis = read(run/'analysis.json'); scene = read(run/'scene.json')
        catalog = read(run/'prepared/catalog.json'); rows, broken = events(run)
        observed = any(r.get('schema_version') == 'collage-events/1' for r in rows)
        stages = [{'id': i+1, 'label': label, 'status': 'pending', 'summary': '', 'at': None}
                  for i, label in enumerate(LABELS)]
        current = 1; history = []; current_version = None; reviews = {}; latest_checkpoint = {}
        material_round = None; materials = {}; groups = {}; extracted = {}; last_command = None
        for row in rows:
            event = row.get('event', ''); at = row.get('at')
            command = next((c for c in COMMAND_STAGE if event in {c+'_started', c+'_finished', c+'_failed'}), None)
            if command and row.get('schema_version'):
                stage = COMMAND_STAGE[command]; current = stage
                suffix = event.rsplit('_', 1)[-1]
                state = {'started': 'running', 'finished': 'complete', 'failed': 'failed'}[suffix]
                if command in {'validate', 'reveal-plan'} and suffix == 'finished':
                    state = 'waiting'
                if command == 'apply' and suffix == 'finished':
                    state = 'waiting'
                summary = {'started': '正在执行', 'finished': '执行完成', 'failed': '执行失败'}[suffix]
                if command == 'validate' and suffix == 'finished':
                    summary = '数据校验完成，等待叠框看图检查'
                if command == 'apply' and suffix == 'finished':
                    summary = '排版调整完成，等待复核新图'
                stages[stage-1].update(status=state, at=at, summary=clean(row.get('error', summary)))
                if suffix == 'started':
                    stages[stage-1]['started_at'] = at
                    last_command = row
                    if command == 'build':
                        groups = {}; extracted = {}; materials = {}; material_round = None
                history.append({'at': at, 'stage': stage, 'status': state,
                                'summary': COMMAND_LABEL.get(command, LABELS[stage-1])+' · '+stages[stage-1]['summary']})
            if event == 'agent_stage':
                stage = row.get('stage', 0)
                if not isinstance(stage, int) or not 1 <= stage <= 6:
                    continue
                current = stage
                stages[stage-1].update(status=row['status'], summary=clean(row.get('summary', '')), at=at)
                if row['status'] == 'running':
                    stages[stage-1]['started_at'] = at
                history.append({k: row.get(k) for k in ('at', 'stage', 'status', 'summary')})
                if stage == 2 and row.get('artifacts'):
                    latest_checkpoint = row['artifacts']
                if row.get('analysis_only') and row['status'] == 'complete':
                    for s in stages[2:5]:
                        s.update(status='skipped', summary='仅分析任务，不适用')
                    if stage == 2:
                        stages[5].update(status='complete', summary='分析产物已交付', at=at); current = 6
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
        if not observed:
            stages[0].update(status='complete', summary='历史任务，已有输入')
            for i, exists in [(1, bool(analysis)), (2, bool(result.get('exported')))]:
                if exists:
                    stages[i].update(status='saved', summary='已有产物，未留存完整阶段记录'); current = i+1
        heartbeat = read(run/'observability/heartbeat.json')
        age = elapsed(heartbeat.get('at'))
        live = bool(heartbeat.get('active') and age is not None and age < 15)
        active_stage = stages[current-1]
        execution = '正在执行' if live else '已结束' if current == 6 and active_stage['status'] == 'complete' else '等待下一步'
        if not live and active_stage['status'] in {'running', 'waiting'}:
            execution = '状态待确认' if heartbeat.get('active') else '等待执行端更新'
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
                                   'bbox': obj.get('bbox'), 'method': '客户照片' if obj['kind']=='photo' else '参考提取' if obj.get('method')=='extract' else '本地绘制',
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
        rebuilding = bool(last_command and last_command.get('command') == 'build' and stages[2]['status'] in {'running', 'failed'})
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
            method = '客户照片' if obj['kind'] == 'photo' else '参考提取' if obj.get('method') == 'extract' else '本地绘制'
            metadata = resources.get(oid, {}).get('metadata', {})
            images = {'reference': self.image(key, run, run/'prepared/reference.png', obj.get('bbox')),
                      'customer': self.image(key, run, source.get('thumbnail')), 'final': output}
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
                          'method': method, 'status': state, 'quality': quality, 'cache_hit': row.get('cache_hit', False),
                          'images': images, 'decision': clean(obj.get('gate', {})), 'note': clean(note)})
        if rebuilding:
            groups = {k: v for k, v in groups.items() if v.get('at', '') >= last_command.get('at', '')}
        reference = self.image(key, run, run/'prepared/reference.png')
        boxes = self.image(key, run, latest_checkpoint.get('previews/analysis-boxes.png') or run/'previews/analysis-boxes.png')
        reference_analysis = read(run/latest_checkpoint['analysis.json']) if latest_checkpoint.get('analysis.json') else analysis
        timing = read(run/'sdk-timing.json')
        timing = {k: timing[k] for k in ('model','effort','started_at','finished_at','elapsed_seconds','status') if k in timing}
        return clean({'id': key, 'name': run.name, 'created_at': inp.get('created_at'), 'reference_size': inp.get('reference_size'),
                      'sdk_timing': timing,
                      'reference_objects': reference_analysis.get('objects', []),
                      'instructions': inp.get('instructions', ''), 'historical': not observed, 'current_stage': current,
                      'stages': stages, 'execution': execution, 'live': live, 'heartbeat_at': heartbeat.get('at'),
                      'history': history[-160:], 'reference': reference, 'boxes': boxes,
                      'catalog': [{'id': a['id'], 'image': self.image(key, run, a.get('thumbnail'))} for a in catalog.get('assets', [])],
                      'current_image': latest['image'] if latest else self.image(key, run, run/'final.png'),
                      'first_image': self.image(key, run, run/'previews/first.png'),
                      'versions': versions, 'materials': cards, 'groups': list(groups.values()),
                      'quality': result.get('status', 'pending'), 'issues': clean(result.get('issues', [])),
                      'review': clean(result.get('visual_review')), 'recording_incomplete': bool(broken or (run/'observability/warning.json').exists())})
