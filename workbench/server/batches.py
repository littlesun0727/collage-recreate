"""Named model comparisons on the existing durable import/creation queues."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import uuid

from collage.config import Config
from collage.workflow.options import normalize_options
from .chat import Conflict, now
from .motion import read, reserve_task_paths, save
from .store import clean


class Batches:
    def __init__(self, service):
        self.service = service
        self.root = service.root/'batches'
        # Finish dispatching a committed batch after a server interruption. Existing
        # jobs (including failed ones) are never re-enqueued here.
        for path in sorted(self.root.glob('run-*.json')):
            self.dispatch(read(path))

    def path(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'run-[a-f0-9]{32}', identifier):
            raise ValueError('批跑 ID 无效')
        return self.root/(identifier+'.json')

    def submit(self, payload):
        allowed = {'id', 'upload_id', 'name', 'model_policy', 'width', 'instructions', 'bbox_refinement', 'screen'}
        if set(payload) - allowed or not {'id', 'upload_id', 'model_policy'} <= set(payload):
            raise ValueError('批跑需要 id、upload_id 和 model_policy')
        path = self.path(payload['id'])
        options = normalize_options({'bbox_refinement': payload.get('bbox_refinement', False),
                                     'screen': payload.get('screen', False)})
        signature = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        service = self.service
        with service.guard:
            previous = read(path)
            if previous:
                if previous['request_sha256'] != signature:
                    raise Conflict('该批跑 ID 已用于不同参数，请使用新的 ID')
                self.dispatch(previous)
                return self.public(previous)
            upload_path, upload = service.batch(payload['upload_id'])
            if upload['sealed']:
                raise Conflict('这批输入已提交，请创建新的批跑')
            if not upload.get('multiple_references'):
                raise ValueError('请使用支持多参考图的批跑上传')
            references = [f for f in upload['files'] if f['role'] == 'reference']
            materials = [f for f in upload['files'] if f['role'] == 'material']
            if not references or not materials:
                raise ValueError('批跑需要至少一张参考图和一个客户素材')
            name = payload.get('name', '')
            if not isinstance(name, str) or not name.strip() or len(name) > 120:
                raise ValueError('批跑名称须为 1～120 个字符')
            width, instructions = payload.get('width', 1200), payload.get('instructions', '')
            if type(width) is not int or not 128 <= width <= 4096:
                raise ValueError('输出宽度须为 128～4096')
            if not isinstance(instructions, str) or len(instructions) > 4000:
                raise ValueError('制作要求须在 4000 字以内')
            policy = Config.load().with_policy(payload['model_policy']).model_policy()
            service.creator.preflight(service.creator.configuration(policy))
            roots = [r for r in service.store.roots if not (r/'input.json').exists()]
            if not roots:
                raise ValueError('工作台需要一个任务父目录才能创建批跑')
            # Hash the uploaded snapshot, rather than trusting mutable source paths.
            for item in upload['files']:
                with Path(item['file']).open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                        raise ValueError('已上传的素材发生变化，请重新上传')
            dataset = {'references': [f['sha256'] for f in references],
                       'materials': [f['sha256'] for f in materials]}
            fingerprint = hashlib.sha256(json.dumps(dataset, sort_keys=True).encode()).hexdigest()
            record = {'id': payload['id'], 'name': name.strip(), 'created_at': now(),
                      'request_sha256': signature, 'upload_id': upload['id'],
                      'model_policy': policy, 'width': width, 'instructions': instructions,
                      'workflow_options': options,
                      'dataset_sha256': fingerprint, 'references': references, 'materials': materials,
                      'jobs': []}
            runs = reserve_task_paths(roots[0], len(references))
            for index, (reference, run) in enumerate(zip(references, runs, strict=True), 1):
                suffix = uuid.uuid5(uuid.NAMESPACE_URL, payload['id']+'/'+str(index)).hex
                group = {k: deepcopy(record[k]) for k in ('id', 'name', 'created_at', 'model_policy', 'dataset_sha256')}
                group.update(index=index, total=len(references), reference_name=reference['name'])
                group['workflow_options'] = deepcopy(options)
                record['jobs'].append({'id': 'import-'+suffix, 'kind': 'import',
                    'batch_id': 'batch-'+suffix, 'run': str(run), 'width': width,
                    'instructions': instructions, 'created_at': record['created_at'],
                    'auto_create': True, 'model_policy': deepcopy(policy), 'batch_run': group,
                    'workflow_options': deepcopy(options),
                    'materials_dir': str(upload_path.parent/'materials')})
            # Commit the entire plan before starting any task. Request IDs make a
            # lost HTTP response safe to resume without another paid batch.
            save(path, record)
            self.dispatch(record)
            return self.public(record)

    def dispatch(self, record):
        service = self.service
        with service.guard:
            upload_path, upload = service.batch(record['upload_id'])
            upload.update(sealed=True, batch_run_id=record['id'])
            save(upload_path, upload)
            for job, reference in zip(record['jobs'], record['references'], strict=True):
                child = service.root/'uploads'/job['batch_id']/'batch.json'
                if not child.exists():
                    save(child, {'id': job['batch_id'], 'sealed': True,
                                 'files': [reference, *record['materials']], 'job_id': job['id']})
                if not (service.root/'jobs'/(job['id']+'.json')).exists():
                    service.enqueue(deepcopy(job))

    def public(self, record):
        tasks = self.service.store.tasks()
        task_ids = {str(run): key for key, run in tasks.items()}
        items = []
        for job, reference in zip(record['jobs'], record['references'], strict=True):
            imported = read(self.service.root/'jobs'/(job['id']+'.json'))
            creation = read(self.service.root/'jobs'/('create-'+job['id'].removeprefix('import-')+'.json'))
            status = creation.get('status') or imported.get('status', 'queued')
            phase = 'create' if creation else 'import'
            if not creation and status == 'completed':
                status = 'queued'
            items.append({'index': job['batch_run']['index'], 'name': reference['name'],
                          'reference_sha256': reference['sha256'], 'status': status, 'phase': phase,
                          'task_id': task_ids.get(job['run']), 'error': clean(creation.get('error') or imported.get('error')),
                          'visual_verdict': creation.get('visual_verdict'),
                          'elapsed_seconds': creation.get('elapsed_seconds')})
        counts = {s: sum(item['status'] == s for item in items) for s in ('queued', 'running', 'completed', 'failed')}
        status = 'running' if counts['running'] else 'queued' if counts['queued'] else 'failed' if counts['failed'] else 'completed'
        public = {k: deepcopy(record[k]) for k in ('id', 'name', 'created_at', 'model_policy', 'dataset_sha256', 'width', 'instructions')}
        public.update(status=status, counts=counts, total=len(items), items=items,
                      workflow_options=normalize_options(record.get('workflow_options')),
                      materials=[{k: f[k] for k in ('name', 'sha256', 'bytes')} for f in record['materials']])
        return public

    def get(self, identifier):
        record = read(self.path(identifier))
        if not record:
            raise KeyError('Batch')
        return self.public(record)

    def listing(self):
        records = [read(path) for path in self.root.glob('run-*.json')]
        return [self.public(r) for r in sorted(records, key=lambda r: r['created_at'], reverse=True)]
