"""Durable, finite first-image workflow. The VLM never chooses executable commands."""
import os
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import sys
import time

from . import contracts, prompts
from .client import Client, ModelError
from .fallbacks import simple_bundle, simple_bindings
from .config import Config, ENGINE, ROOT
from .storage import digest, exclusive, file_sha, now, read, save


class CommandError(RuntimeError):
    """An engine operation failed; distinct from a process interruption."""


class Runner:
    def __init__(self, run, control=None, config=None, client=None, attempt=1, reveal=True):
        self.run = Path(run).resolve()
        self.control = Path(control).resolve() if control else self.run/'workflow'
        self.config = config or Config.load().for_run(self.run)
        self.client = client or Client(self.control/'vlm', self.config)
        self.attempt = attempt
        self.reveal = reveal
        self.state = read(self.control/'state.json')
        self.state.setdefault('steps', {})
        self.state.setdefault('started_at', now())
        self.started = time.monotonic()

    def warn(self, code, stage, message):
        entry = {'code': code, 'stage': stage, 'message': str(message)[:1500]}
        warnings = self.state.setdefault('warnings', [])
        if entry not in warnings:
            warnings.append(entry)
        save(self.control/'warnings.json', warnings)
        self.sync()

    def candidates(self, stage):
        return self.client.candidates(stage) if hasattr(self.client, 'candidates') else []

    def write_bundle(self, bundle):
        save(self.run/'analysis.json', bundle['analysis'])
        save(self.run/'bindings.json', bundle['bindings'])
        save(self.run/'text-units.json', bundle['text_units'])

    def write_reference(self, value):
        save(self.run/'analysis.json', value['analysis'])
        save(self.run/'text-units.json', value['text_units'])
        if 'photo_windows' in value:
            save(self.run/'photo-windows.json', value['photo_windows'])
        self.command('validate', '--analysis-only')

    def draft(self, reason):
        bundle = simple_bundle(self.run)
        self.state['draft_kind'] = 'simple_layout'
        self.warn('simple_layout', self.state.get('current_step', 'analyze'), reason)
        self.write_bundle(bundle)
        if (self.run/'photo-windows.json').exists():
            from photo_windows import defaults
            save(self.run/'photo-windows.json', defaults(bundle['analysis']))
        self.command('validate')

    def optional(self, stage, function, default=None):
        try:
            return function()
        except (ModelError, CommandError) as exc:
            self.warn('optional_stage_failed', stage, str(exc))
            if stage == 'screen':
                self.progress(4, 'skipped', '素材筛选未完成，保留当前成图并进入交付检查')
            return default

    def sync(self):
        self.state.update(model=self.config.model, controller='workflow-v6', attempt=self.attempt,
                          model_policy=self.config.model_policy(),
                          updated_at=now(), usage=self.client.usage(), worker_pid=os.getpid())
        save(self.control/'state.json', self.state)
        timing = {k: self.state[k] for k in ['model', 'model_policy', 'controller', 'status', 'started_at', 'updated_at', 'usage']}
        timing.update(elapsed_seconds=round(time.time()-__import__('datetime').datetime.fromisoformat(self.state['started_at']).timestamp(), 3))
        if self.state.get('finished_at'):
            timing['finished_at'] = self.state['finished_at']
        save(self.run/'workflow-timing.json', timing)

    def command(self, command, *args):
        allowed = {'validate', 'build', 'render', 'screen', 'review', 'progress'}
        if command not in allowed:
            raise ValueError('Unsupported workflow operation')
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', COLLAGE_CONTROLLER='workflow-v6',
                   COLLAGE_BEST_EFFORT='1')
        # Remove only a stale lock owned by this workflow request, never an active editor's lock.
        lock = self.run/'.write.lock'
        owner = read(lock)
        if owner and owner.get('request_id') == os.getenv('COLLAGE_REQUEST_ID', 'v6-cli'):
            from workbench.server.chat import process_alive
            if not process_alive(owner.get('pid')):
                lock.unlink(missing_ok=True)
        env.setdefault('COLLAGE_REQUEST_ID', 'v6-cli')
        try:
            result = subprocess.run([sys.executable, '-B', str(ENGINE/'workflow.py'), command,
                                 '--run', str(self.run), *map(str, args)], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=1200)
        except subprocess.TimeoutExpired:
            raise CommandError(f'{command} 超时，已停止本次本地操作') from None
        logs = self.control/'commands'; logs.mkdir(parents=True, exist_ok=True)
        with (logs/(command+'.log')).open('ab') as handle:
            handle.write(result.stdout)
        if result.returncode:
            output = result.stdout.decode('utf-8', errors='replace')
            try:
                import json
                output = json.loads(output)['error']
            except (ValueError, KeyError):
                output = output[-1000:]
            raise CommandError(f'{command} 失败：{output[:1500]}')

    def progress(self, stage, status, summary):
        self.command('progress', '--stage', stage, '--status', status, '--summary', summary)

    def step(self, name, function):
        existing = self.state['steps'].get(name, {})
        if existing.get('status') == 'complete':
            return existing.get('value')
        self.state['current_step'] = name
        self.state['steps'][name] = {'status': 'running', 'started_at': existing.get('started_at', now())}
        self.sync()
        try:
            value = function()
        except (Exception, KeyboardInterrupt) as exc:
            if name == 'analyze':
                self.state['steps'][name].update(status='failed', finished_at=now(), error=str(exc)[:1500])
                self.sync()
                self.progress(2, 'failed', str(exc)[:1500])
            raise
        self.state['steps'][name].update(status='complete', finished_at=now(), value=value)
        result = read(self.run/'result.json')
        if result.get('render_id'):
            self.state['last_render_id'] = result['render_id']
        self.sync()
        return value

    def restore_mutation(self, folder, manifest):
        for rel, existed in manifest['files'].items():
            target = (self.run/rel).resolve()
            if not target.is_relative_to(self.run):
                raise ValueError('Unsafe transaction path')
            if existed:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(folder/'before'/rel, target)
            else:
                target.unlink(missing_ok=True)

    def rollback_legacy_adjustment(self):
        """Resume an old interrupted adjustment by keeping its last usable image."""
        folder = self.control/'transactions/apply'
        manifest = read(folder/'manifest.json')
        if manifest and not manifest.get('committed'):
            self.restore_mutation(folder, manifest)
            manifest.update(committed=True, rolled_back=True)
            save(folder/'manifest.json', manifest)
            self.state['last_render_id'] = read(self.run/'result.json').get('render_id')
            self.state['steps']['apply'] = {'status': 'skipped', 'value': False}
            self.warn('legacy_adjustment_rolled_back', 'register', '旧流程微调曾中断，已恢复微调前成图并直接交付首版')

    def mutate(self, name, command, plan):
        """Rollback an interrupted local mutation before replay. Never rolls back remote extraction."""
        folder = self.control/'transactions'/name
        manifest_path = folder/'manifest.json'
        manifest = read(manifest_path)
        targets = ['scene.json', 'result.json', 'final.png', 'screening.json', 'assets/gate/report.json']
        # A failed render can overwrite tile files before it publishes final.png.
        # Snapshot the current render's resources too, so rollback remains verifiable.
        for item in read(self.run/'result.json').get('resources', []):
            for source in [item.get('file'), item.get('metadata', {}).get('content_mask')]:
                if source and Path(source).resolve().is_relative_to(self.run):
                    targets.append(str(Path(source).resolve().relative_to(self.run)))
                    receipt = Path(source).with_suffix('.json')
                    if receipt.is_file():
                        targets.append(str(receipt.resolve().relative_to(self.run)))
        targets += [str(p.relative_to(self.run)) for p in (self.run/'previews').rglob('*') if p.is_file()]
        if manifest:
            if manifest['plan_key'] != digest(plan):
                raise ValueError('Mutation plan changed during recovery')
            if manifest.get('committed'):
                return not manifest.get('rolled_back', False)
            self.restore_mutation(folder, manifest)
        else:
            files = {}
            for rel in targets:
                source = self.run/rel; files[rel] = source.is_file()
                if files[rel]:
                    target = folder/'before'/rel; target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            manifest = {'plan_key': digest(plan), 'files': files, 'committed': False}
            save(manifest_path, manifest)
        path = folder/'plan.json'; save(path, plan)
        try:
            self.command(command, '--file', path)
        except CommandError as exc:
            self.restore_mutation(folder, manifest)
            manifest.update(committed=True, rolled_back=True, error=str(exc))
            save(manifest_path, manifest)
            self.warn('mutation_rolled_back', name, '修改失败，已保留上一张可用成图：' + str(exc))
            return False
        manifest.update(committed=True, render_id=read(self.run/'result.json')['render_id'])
        save(manifest_path, manifest)
        return True

    def input_context(self):
        inp = read(self.run/'input.json'); catalog = read(self.run/'prepared/catalog.json')
        return {'reference_size': inp['reference_size'], 'instructions': inp.get('instructions', ''),
                'catalog': [{k: a[k] for k in ['id', 'size']} for a in catalog['assets']]}

    def analysis_images(self):
        return [('参考图', self.run/'prepared/reference.png')]

    def reference_context(self):
        return {'reference_size': read(self.run/'input.json')['reference_size']}

    def material_images(self):
        from common import verify_source
        from PIL import Image, ImageOps
        catalog = read(self.run/'prepared/catalog.json')
        images = [
            (f'客户素材联系表 {i+1}', Path(path)) for i, path in enumerate(catalog['contact_sheets'])]
        folder = self.run/'prepared/vlm-materials'
        folder.mkdir(exist_ok=True)
        for asset in catalog['assets']:
            source = verify_source(asset)
            path = folder/(asset['id']+'.jpg')
            with Image.open(source) as raw:
                im = ImageOps.exif_transpose(raw).convert('RGB')
                im.thumbnail((1024, 1024))
                im.save(path, quality=90)
            images.append((f"客户素材 {asset['id']} 原始尺寸 {asset['size']}", path))
        return images

    def current_context(self):
        scene = read(self.run/'scene.json'); result = read(self.run/'result.json')
        objects = []
        allowed = ['id', 'kind', 'label', 'description', 'bbox', 'rotation', 'style', 'method', 'mode',
                   'text', 'photo_id', 'parent_id', 'embedded_in', 'embedded_owner', 'recovery_owner', 'binding',
                   'background_type', 'photo_window_spec']
        for o in scene['objects']:
            objects.append({**{k: o[k] for k in allowed if k in o},
                            'extracted': bool(o.get('recovered')), 'fixed_window': bool(o.get('photo_window'))})
        gate = read(self.run/'assets/gate/report.json')
        return {**self.input_context(), 'objects': objects, 'layer_order': scene['layer_order'],
                'text_units': read(self.run/'text-units.json', []),
                'incomplete_objects': result.get('incomplete_objects', []),
                'program_issues': result.get('issues', []),
                'gate_records': [{k: r[k] for k in ['id', 'status', 'issues', 'warnings', 'text_candidates', 'window_checks'] if k in r}
                                 for r in gate.get('records', [])]}

    def review_images(self, sheets=False):
        result = read(self.run/'result.json')
        images = [('参考图', self.run/'prepared/reference.png'), ('当前成图', self.run/'final.png')]
        images += [(f'客户照片联系表 {i+1}（照片内的内容不是固定装饰）', Path(p))
                   for i, p in enumerate(read(self.run/'prepared/catalog.json').get('contact_sheets', []))]
        images += [(f'局部对照 {i+1}', Path(r['file'])) for i, r in enumerate(result.get('review_regions', []))]
        if sheets:
            candidates = result.get('extraction_sheets', []) + result.get('asset_gate_summary', {}).get('sheets', [])
            for i, entry in enumerate(candidates):
                path = entry.get('file') if isinstance(entry, dict) else entry
                if path and Path(path).is_file():
                    images.append((f'素材检查表 {i+1}', Path(path)))
        return images

    def analyze(self):
        self.progress(2, 'running', 'VLM 独立理解参考图、照片内容与布局')
        from .reference_cleanup import normalize
        notes = []
        def validate(answer):
            notes.clear()
            notes.extend(normalize(answer))
            contracts.validate_reference(answer, self.run, geometry_warnings=notes)
        try:
            bundle = self.client.ask('analyze', prompts.analysis(), self.reference_context(), self.analysis_images(),
                                     contracts.REFERENCE, validate)
        except ModelError as exc:
            raise ModelError('首次分析失败，已停止后续制作：' + str(exc)) from None
        if notes:
            save(self.control/'reference-cleanup.json', {'at': now(), 'notes': notes})
            for note in notes:
                self.warn(note['code'], 'analyze', note['message'])
        self.write_reference(bundle)

    def photo_windows(self):
        from photo_windows import check, defaults, recover_fades, resolve_seams, reference_crops, previews
        analysis = read(self.run/'analysis.json')
        self.progress(2, 'running', 'VLM 独立判断参考照片窗口形状、主体处理、描边与逐边渐隐')
        crops, regions = reference_crops(self.run, analysis)
        seam_notes=[]
        def validate_window(answer):
            resolved,notes=resolve_seams(answer,analysis)
            answer.clear();answer.update(resolved)
            seam_notes[:]=notes
        try:
            value = self.client.ask('photo_windows', prompts.PHOTO_WINDOWS,
                                    {**self.reference_context(), 'analysis': analysis, 'crop_regions': regions},
                                    self.analysis_images()+crops, contracts.PHOTO_WINDOWS,
                                    validate_window) if crops else defaults(analysis)
        except ModelError as exc:
            from jsonschema import ValidationError
            value = None
            for candidate in self.candidates('photo_windows'):
                try:
                    value, notes = recover_fades(candidate, analysis)
                except (ValueError, TypeError, KeyError, ValidationError):
                    continue
                save(self.control/'recovered-photo-windows.json', value)
                self.warn('photo_windows_recovered', 'photo_windows',
                          '保留完整视觉回复，仅收缩无下层覆盖的逐边渐隐；'+'；'.join(notes))
                break
            if value is None:
                value = defaults(analysis)
                self.warn('photo_windows_fallback', 'photo_windows',
                          '窗口视觉判断未完成，临时沿用明确参数；不从旧 feather 猜测四边渐隐：' + str(exc))
        for note in seam_notes:self.warn('seam_adjusted','photo_windows',note)
        save(self.run/'photo-windows.json', value)
        previews(self.run, analysis, value)

    def boxes(self):
        from .reference_cleanup import normalize_boxes
        notes = []
        bundle = {'analysis': read(self.run/'analysis.json'),
                  'text_units': read(self.run/'text-units.json', [])}
        images = self.analysis_images()+[('分析叠框', self.run/'previews/analysis-boxes.png')]
        if (self.run/'photo-windows.json').exists():
            # The VLM carries windows through unchanged; validation reconciles
            # fade coverage deterministically after its geometry corrections.
            bundle['photo_windows'] = read(self.run/'photo-windows.json')
        def validate(answer):
            notes.clear()
            if answer['decision'] == 'replace':notes.extend(normalize_boxes(answer, bundle))
            contracts.validate_box_decision(answer, bundle, self.run, geometry_warnings=notes)
        answer = self.client.ask('boxes', prompts.BOXES, {**self.reference_context(), **bundle},
                                 images,
                                 contracts.BOXES, validate)
        # The corrected bundle is the stage output; persist before mutating inputs.
        save(self.control/'boxes-decision.json', answer)
        if notes:
            save(self.control/'boxes-cleanup.json', {'at': now(), 'notes': notes})
            for note in notes:self.warn(note['code'], 'boxes', note['message'])
        return answer

    def apply_boxes(self, answer):
        if answer['decision'] == 'replace':
            current = {'analysis': read(self.run/'analysis.json'),
                       'text_units': read(self.run/'text-units.json', [])}
            if (self.run/'photo-windows.json').exists():
                current['photo_windows'] = read(self.run/'photo-windows.json')
            contracts.validate_box_decision(answer, current, self.run, geometry_warnings=[])
            self.write_reference(answer)
            if 'photo_windows' in answer:
                from photo_windows import previews
                previews(self.run, answer['analysis'], answer['photo_windows'])
        self.progress(2, 'running', '参考分析及叠框已完成，准备理解客户素材')

    def understand_materials(self):
        self.progress(2, 'running', 'VLM 整体浏览客户素材并逐张记录内容、构图与裁切限制')
        catalog = read(self.run/'prepared/catalog.json')
        try:
            value = self.client.ask('materials', prompts.MATERIALS,
                                    {'catalog': self.input_context()['catalog']}, self.material_images(),
                                    contracts.MATERIALS, lambda a: contracts.validate_materials(a, catalog))
        except ModelError as exc:
            self.warn('materials_unavailable', 'materials', '独立素材理解失败，绑定调用将重新观察全部素材：' + str(exc))
            value = {'status': 'unavailable', 'summary': '未获得有效素材理解，不能视作已观察', 'assets': []}
        save(self.run/'material-inventory.json', value)

    def bind(self):
        self.progress(2, 'running', 'VLM 根据参考 photo 需求和全部素材理解选择照片及裁切')
        analysis = read(self.run/'analysis.json')
        slots = []
        for photo in analysis['objects']:
            if photo['kind'] != 'photo':
                continue
            l, t, r, b = photo['bbox']
            slots.append({'slot_id': photo['id'], 'width': r-l, 'height': b-t,
                          'aspect_ratio': round((r-l)/(b-t), 4)})
        context = {**self.input_context(), 'analysis': analysis,
                   'photo_slots': slots,
                   'material_inventory': read(self.run/'material-inventory.json')}
        if (self.run/'photo-windows.json').exists():
            context['photo_windows'] = read(self.run/'photo-windows.json')
        try:
            value = self.client.ask('bind', prompts.binding(), context,
                                    self.analysis_images()+self.material_images(), contracts.BINDING_PLAN,
                                    lambda a: contracts.validate_bindings(a, analysis, self.run))
        except ModelError as exc:
            self.warn('binding_fallback', 'bind', '素材绑定失败，保留参考布局并使用明确标记的比例兜底：' + str(exc))
            value = simple_bindings(self.run, analysis)
        # This stage sees original RGB, not the eventual segmentation bounds.
        # A crop guessed in that unseen coordinate space can remove a whole face.
        windows = {p['photo_id']: p for p in context.get('photo_windows', {}).get('photos', [])}
        photos = {p['id']: p for p in analysis['objects'] if p['kind'] == 'photo'}
        value = deepcopy(value)
        for binding in value['photos']:
            oid = binding['slot_id']
            mode = windows.get(oid, {}).get('subject', {}).get('mode', photos[oid].get('mode'))
            if mode == 'cutout' and not windows.get(oid,{}).get('linked_to') and binding.get('source_crop', [0,0,1,1]) != [0,0,1,1]:
                before = binding['source_crop']
                binding['source_crop'] = [0,0,1,1]
                note = f'{oid}：绑定阶段未见实际抠图边界，撤销猜测裁剪 {before}，完整保留抠出主体'
                binding['reason'] += '；程序修正：'+note
                self.warn('cutout_crop_preserved', 'bind', note)
        from photo_links import sync_bindings
        for note in sync_bindings(value,context.get('photo_windows',{})):
            self.warn('linked_photo_binding','bind',note)
        save(self.run/'bindings.json', value)
        self.command('validate')
        self.progress(2, 'complete', '参考分析与独立素材绑定均已保存并通过程序校验，进入制作')

    def build(self):
        self.progress(3, 'running', '按固定计划提取装饰并本地合成')
        needed = any(o.get('method') == 'extract' for o in read(self.run/'analysis.json')['objects'])
        try:
            if needed and self.reveal:
                self.command('build', '--reveal', '--reveal-layout', 'full', '--reveal-key-file', self.config.reveal_key_file)
            else:
                self.command('build', '--no-reveal')
        except CommandError as exc:
            self.warn('build_fallback', 'build', '制作失败，尝试保留布局的本地合成：' + str(exc))
            try:
                self.command('build', '--no-reveal')
            except CommandError as local_exc:
                self.draft('布局无法完成渲染，改为简化拼图：' + str(local_exc))
                self.command('build', '--no-reveal')
        for item in read(self.run/'result.json').get('resources', []):
            if item.get('metadata', {}).get('fallback'):
                self.warn('asset_fallback', 'build', item['id'] + '：' + item['metadata'].get('note', '素材已降级'))
        self.progress(3, 'complete', '首版已合成，进入素材筛选')

    def screen(self):
        result = read(self.run/'result.json')
        records = read(self.run/'assets/gate/report.json').get('records', [])
        if not records:
            self.progress(4, 'skipped', '无远端提取素材，跳过筛选')
            return
        context = self.current_context()
        gate_ids = {r['id'] for r in records}
        screenable = sorted(o['id'] for o in context['objects'] if o['kind'] == 'frame'
                            and o['id'] in gate_ids)
        if not screenable:
            self.progress(4, 'skipped', '没有可由素材筛选节点处理的对象')
            return
        context['screenable_ids'] = screenable
        schema = deepcopy(contracts.SCREEN)
        schema['properties']['decisions']['items']['properties']['id'] = {'enum': screenable}
        self.progress(4, 'running', '仅检查相框原照片残留；其他装饰默认保留')
        def validate(answer):
            from screening import screen, retain_uncertain_frames
            answer, _ = retain_uncertain_frames(answer, records)
            fixed_text = {u['carrier_id'] for u in read(self.run/'text-units.json', [])
                          if u['treatment'] == 'fixed_artwork'}
            if self.state.get('text_inventory_advisory'):
                fixed_text.update(o['id'] for o in context['objects'] if o.get('method') == 'extract')
            if any(d['id'] in fixed_text and d['action'] == 'draw' for d in answer['decisions']):
                raise ValueError('完整文字贴纸不能降级为空白纸或通用字体；请保留可用候选或报告缺口')
            plan = {'render_id': result['render_id'], **answer}
            path = self.control/'screen-check.json'; save(path, plan)
            screen(self.run, path, dry_run=True)
        answer = self.client.ask('screen', prompts.SCREEN, context, self.review_images(True), schema, validate)
        from screening import retain_uncertain_frames
        answer, notes = retain_uncertain_frames(answer, records)
        for note in notes:self.warn('frame_retained_or_fallback', 'screen', note)
        return {'render_id': result['render_id'], **answer}

    def register(self):
        """Validate and deliver the first image without claiming a visual review."""
        self.progress(5, 'running', '检查首版文件与客户照片来源')
        result = read(self.run/'result.json')
        issues = {}
        for oid in result.get('incomplete_objects', []):
            issues.setdefault(oid, '程序检测到素材缺失、占位或照片不可见')
        if not result.get('customer_photos_verified'):
            raise ValueError('客户照片来源校验失败，禁止交付')
        warnings = self.state.get('warnings', [])
        suffix = '；简化拼图草稿，尚未复刻参考布局。' if self.state.get('draft_kind') else ''
        if issues:
            suffix += f'；程序检测到 {len(issues)} 个未完成对象。'
        if warnings:
            suffix += f'；流程保留 {len(warnings)} 项告警，待优化。'
        review = {'schema_version': 'collage-review-v1', 'render_id': result['render_id'],
                  'checked_entire_composition': False, 'verdict': 'needs_changes',
                  'summary': '首版已生成，未做整图视觉复核；可在画布继续编辑。' + suffix,
                  'items': [{'id': oid, 'action': 'unresolved', 'reason': reason} for oid, reason in issues.items()]}
        save(self.run/'review.json', review)
        # This engine command only checks files and registers the delivery record; no VLM call.
        self.command('review', '--file', self.run/'review.json')
        result = read(self.run/'result.json')
        category = 'simple_draft' if self.state.get('draft_kind') else 'with_issues' if issues or warnings else 'unreviewed'
        result['status'] = 'preview' if category == 'unreviewed' else 'needs_changes'
        result['workflow_delivery'] = {'mode': 'best_effort', 'render_id': result['render_id'],
            'category': category, 'review_status': 'skipped', 'warnings': warnings, 'draft_kind': self.state.get('draft_kind')}
        save(self.run/'result.json', result)
        self.progress(5, 'complete', review['summary'])
        self.progress(6, 'complete', '首版已交付，未做整图视觉复核；可进入画布编辑')

    def execute(self):
        with exclusive(self.run/'.v6-runner.lock'):
            policy = self.config.model_policy()
            if not self.config.policy_matches(self.state.get('model_policy')):
                raise ValueError('阶段模型配置已改变，请使用原配置接续或创建新任务')
            snapshot = read(self.run/'workflow-models.json')
            if snapshot and not self.config.policy_matches(snapshot):
                raise ValueError('任务模型快照与运行配置不一致，请创建新任务')
            if not snapshot:
                save(self.run/'workflow-models.json', policy)
            identity = digest({'input': file_sha(self.run/'input.json'), 'catalog': file_sha(self.run/'prepared/catalog.json'),
                               'model': self.config.model, 'reveal': self.reveal})
            if self.state.get('identity') not in {None, identity}:
                raise ValueError('任务输入、模型或提取模式已改变，请创建新任务')
            previous = read(self.run/'result.json')
            interrupted = any(s.get('status') == 'running' for s in self.state['steps'].values())
            if self.state.get('last_render_id') and previous.get('render_id') != self.state['last_render_id'] and not interrupted:
                raise ValueError('当前成图已被后续编辑改变，不能续跑首版')
            if self.state.get('status') == 'finished':
                if previous.get('final_sha256') != file_sha(self.run/'final.png') or previous.get('scene_sha256') != file_sha(self.run/'scene.json'):
                    raise ValueError('交付文件已改变')
                self.sync()
                return previous
            self.state.update(identity=identity, status='running', error=None)
            self.state.pop('finished_at', None)
            self.sync()
            try:
                self.rollback_legacy_adjustment()
                self.progress(1, 'complete', '输入素材已准备完成')
                self.step('analyze', self.analyze)
                if self.state['steps'].get('build', {}).get('status') != 'complete':
                    if not self.state.get('photo_windows_version'):
                        # Adopt the new stage only before build. Re-audit any previous boxes
                        # and binding against the new windows; material observations remain valid.
                        for name in ('boxes', 'boxes_apply', 'bind'):
                            self.state['steps'].pop(name, None)
                        self.state['photo_windows_version'] = 1
                        self.sync()
                    self.step('photo_windows', self.photo_windows)
                boxes = self.step('boxes', lambda: self.optional('boxes', self.boxes,
                    {'decision': 'keep', 'summary': '叠框复核未完成，沿用已校验的分析'}))
                self.step('boxes_apply', lambda: self.apply_boxes(boxes))
                # Legacy tasks with an existing built image must retain the bindings
                # that produced that image. Unbuilt tasks can adopt the new stages.
                if self.state['steps'].get('build', {}).get('status') != 'complete':
                    self.step('materials', self.understand_materials)
                    self.step('bind', self.bind)
                self.step('build', self.build)
                screening = self.step('screen', lambda: self.optional('screen', self.screen))
                if screening and screening['decisions']:
                    self.step('screen_apply', lambda: self.mutate('screen', 'screen', screening))
                if screening:
                    self.progress(4, 'complete', '素材筛选完成')
                self.step('register', self.register)
                self.state.update(status='finished', finished_at=now())
                self.sync()
                return read(self.run/'result.json')
            except (Exception, KeyboardInterrupt) as exc:
                self.state.update(status='failed', error=str(exc)[:1500], finished_at=now())
                self.sync()
                raise
