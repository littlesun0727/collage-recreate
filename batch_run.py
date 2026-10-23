"""Submit a reproducible model batch to the local workbench."""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
import uuid
import webbrowser

from collage.config import Config
from collage.paths import OUTPUT_ROOT

IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
MEDIA = IMAGES | {'.mp4', '.mov'}
EFFORTS = ('default', 'off', 'on', 'minimal', 'low', 'medium', 'high', 'xhigh')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('工作台请求不接受重定向')


class Workbench:
    def __init__(self, base):
        url = urlsplit(base)
        if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost'}
                or url.username or url.password or url.path not in {'', '/'} or url.query or url.fragment):
            raise ValueError('--server 必须是本机 HTTP 工作台地址')
        self.base = base.rstrip('/')
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.token = None

    def request(self, route, payload=None, file=None, role=None):
        headers = {}
        data = None
        if payload is not None or file is not None:
            headers.update({'Origin': self.base, 'X-Chat-Token': self.token or ''})
            if file is not None:
                headers.update({'Content-Type': 'application/octet-stream',
                                'Content-Length': str(file.stat().st_size),
                                'X-File-Name': quote(file.name), 'X-Media-Role': role})
                data = file.open('rb')
            else:
                headers['Content-Type'] = 'application/json'
                data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        try:
            with self.opener.open(Request(self.base+route, data=data, headers=headers), timeout=300) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                detail = json.load(exc).get('error', str(exc.code))
            except (ValueError, AttributeError):
                detail = str(exc.code)
            raise ValueError(f'工作台 HTTP {exc.code}: {detail}') from None
        finally:
            if file is not None and data is not None:
                data.close()

    def connect(self, start=True, data=None):
        try:
            config = self.request('/api/chat-config')
        except URLError:
            if not start:
                raise ValueError('工作台未启动，请先运行 launch.py') from None
            port = urlsplit(self.base).port or 80
            command = [sys.executable, '-B', str(Path(__file__).with_name('launch.py')), '--port', str(port)]
            if data:
                command += ['--data', str(Path(data).resolve())]
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError('工作台启动失败：'+(result.stderr or result.stdout)[-700:])
            config = self.request('/api/chat-config')
        if config.get('controller') != 'workflow-v9' or not config.get('batch_enabled'):
            raise ValueError('当前工作台尚未启用批跑，请结束旧服务后重新运行 launch.py')
        self.token = config['token']


def source_files(values, base, extensions, label):
    if not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values):
        raise ValueError(label+'须为文件或目录路径列表')
    found, seen = [], set()
    for value in values:
        path = (base/Path(value)).resolve()
        if path.is_dir():
            candidates = sorted((p for p in path.rglob('*') if p.is_file() and p.suffix.lower() in extensions),
                                key=lambda p: p.as_posix().casefold())
        elif path.is_file() and path.suffix.lower() in extensions:
            candidates = [path]
        else:
            raise ValueError(f'{label}文件不存在或格式不支持：{path}')
        if not candidates:
            raise ValueError(f'{label}目录没有可用文件：{path}')
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            size = resolved.stat().st_size
            if not 0 < size <= 128*1024*1024:
                raise ValueError(f'文件须在 1 字节～128 MB：{resolved}')
            if len(resolved.name) > 180:
                raise ValueError(f'文件名超过 180 字符：{resolved.name}')
            with resolved.open('rb') as stream:
                sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            found.append({'path': str(resolved), 'name': resolved.name, 'bytes': size, 'sha256': sha})
    return found


def plan(args):
    settings = {}
    base = Path.cwd()
    if args.config:
        config_path = Path(args.config).resolve()
        settings = json.loads(config_path.read_text(encoding='utf-8-sig'))
        base = config_path.parent
        allowed = {'name', 'provider', 'model', 'reasoning', 'reasoning_budget', 'max_tokens',
                   'coordinate_mode', 'response_format', 'references', 'materials', 'width',
                   'instructions', 'model_policy', 'bbox_refinement', 'screen', 'json_schema'}
        if not isinstance(settings, dict) or set(settings)-allowed:
            raise ValueError('批跑配置包含未知字段')
    def option(key, default=None):
        value = getattr(args, key, None)
        return value if value is not None else settings.get(key, default)
    schema_enabled = option('json_schema')
    if (schema_enabled is not None or 'json_schema' in settings) and type(schema_enabled) is not bool:
        raise ValueError('json_schema 必须为 true 或 false')
    output_override = ('json_schema' if schema_enabled else 'json_object') if schema_enabled is not None else None
    from collage.workflow.options import normalize_options
    workflow_options = normalize_options({'bbox_refinement': option('bbox_refinement', False),
                                          'screen': option('screen', False)})
    if 'model_policy' in settings:
        if any(getattr(args, k, None) is not None for k in ('model', 'provider', 'reasoning', 'reasoning_budget')):
            raise ValueError('分阶段 model_policy 不能与命令行单模型参数混用')
        policy = deepcopy(settings['model_policy'])
        if not isinstance(policy, dict) or set(policy) != {'analyze', 'subsequent'}:
            raise ValueError('模型配置必须包含 analyze 和 subsequent')
        for entry in policy.values():
            if not isinstance(entry, dict):
                raise ValueError('阶段模型配置字段无效')
            if output_override is not None:
                entry['response_format'] = output_override
            else:
                entry.setdefault('response_format', settings.get('response_format', Config.response_format))
        policy = Config.load().with_policy(policy).model_policy()
        if args.coordinate_mode is not None:
            for entry in policy.values():
                entry['coordinate_mode'] = args.coordinate_mode
    else:
        model = option('model')
        if not model:
            raise ValueError('请通过 --model 或配置文件指定 VLM 模型 ID')
        effort = option('reasoning', 'default')
        budget = option('reasoning_budget')
        if effort not in EFFORTS:
            raise ValueError('推理强度无效')
        if budget is not None and effort not in {'on', 'default'}:
            raise ValueError('推理 token 预算不能与 off 或推理档位同时使用')
        config = Config(provider=option('provider', 'openrouter'), model=model,
                        reasoning_enabled=True if budget or effort not in {'default', 'off'} else False if effort == 'off' else None,
                        reasoning_effort=effort if effort not in {'default', 'off', 'on'} else None,
                        reasoning_max_tokens=budget, max_tokens=option('max_tokens', 16000),
                        coordinate_mode=option('coordinate_mode', 'normalized_1000'),
                        response_format=output_override if output_override is not None else option('response_format', Config.response_format))
        policy = config.with_policy(config.model_policy()).model_policy()
    references = source_files(option('references'), Path.cwd() if args.references else base, IMAGES, '参考图')
    materials = source_files(option('materials'), Path.cwd() if args.materials else base, MEDIA, '客户素材')
    if len(references)+len(materials) > 100 or sum(f['bytes'] for f in references+materials) > 1024**3:
        raise ValueError('每批最多 100 个输入文件，总计 1 GB')
    width, instructions = option('width', 1200), option('instructions', '')
    if type(width) is not int or not 128 <= width <= 4096 or not isinstance(instructions, str) or len(instructions) > 4000:
        raise ValueError('宽度须为 128～4096，制作要求须在 4000 字以内')
    entry = policy['subsequent']
    label = entry['reasoning'].get('effort') or ('off' if entry['reasoning']['enabled'] is False else 'thinking' if entry['reasoning']['enabled'] else 'default')
    name = option('name', f"{entry['model']} · {label} · {datetime.now():%m-%d %H:%M:%S}")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError('批跑名称须为 1～120 个字符，请用 --name 指定短名称')
    return {'name': name, 'model_policy': policy, 'references': references, 'materials': materials,
            'width': width, 'instructions': instructions, **workflow_options}


def parser():
    p = argparse.ArgumentParser(description='多参考图 + 固定客户素材 + 指定模型的工作台批跑')
    p.add_argument('--config', help='JSON 配置文件；文件内相对路径以配置所在目录为准')
    p.add_argument('--model', help='供应商的准确 VLM 模型 ID；应用于全部 VLM 阶段')
    p.add_argument('--provider', choices=['openrouter', 'yibu', 'codex'])
    p.add_argument('--reasoning', choices=EFFORTS)
    p.add_argument('--reasoning-budget', type=int, help='thinking token 预算，不能与推理档位同时设置')
    p.add_argument('--coordinate-mode', choices=['normalized_1000', 'pixel', 'auto'],
                   help='bbox 坐标模式：0～1000 归一化 / 原图像素 / 按模型自动；覆盖配置中的首次分析与后续 VLM 坐标模式，未配置时默认 normalized_1000')
    p.add_argument('--bbox-refinement', action=argparse.BooleanOptionalAction, default=None,
                   help='开启第二步 bbox 校准；默认关闭；--no-bbox-refinement 覆盖配置为关闭')
    p.add_argument('--screen', action=argparse.BooleanOptionalAction, default=None,
                   help='开启提取相框审核；默认关闭；--no-screen 覆盖配置为关闭')
    p.add_argument('--json-schema', action=argparse.BooleanOptionalAction, default=None,
                   help='显式开启接口 JSON Schema；默认使用 json_object，Schema 仅放在提示词中；统一覆盖首次分析与后续 VLM，保留本地校验')
    p.add_argument('--references', nargs='+', help='参考图文件或目录列表，按给定顺序制作')
    p.add_argument('--materials', nargs='+', help='每张参考图共用的客户素材文件或目录列表')
    p.add_argument('--name', help='前端批次名称')
    p.add_argument('--width', type=int)
    p.add_argument('--instructions')
    p.add_argument('--server', default='http://127.0.0.1:8809')
    p.add_argument('--data', help='自动启动工作台时使用的独立数据目录；切换端口时同时指定')
    p.add_argument('--dry-run', action='store_true', help='仅检查输入和模型参数，输出清单，不提交制作')
    p.add_argument('--no-start', action='store_true', help='工作台未启动时直接报错')
    p.add_argument('--open', action='store_true', help='提交后打开该批次的前端页面')
    p.add_argument('--wait', action='store_true', help='提交后等待全批结束，并返回失败退出码')
    p.add_argument('--resume', help='读取之前的提交回执，安全续接同一次提交；不重新制作失败项')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    receipt_path = None
    try:
        if args.resume:
            if args.dry_run or args.config or args.model or args.provider or args.reasoning or args.reasoning_budget is not None or args.coordinate_mode is not None or args.bbox_refinement is not None or args.screen is not None or args.json_schema is not None or args.references or args.materials or args.name or args.width or args.instructions:
                raise ValueError('--resume 不接受新的模型、输入或制作参数')
            receipt_path = Path(args.resume).resolve()
            receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
            client = Workbench(receipt['server'])
            payload = receipt['payload']
            client.connect(start=not args.no_start, data=receipt.get('data'))
        else:
            batch = plan(args)
            if args.dry_run:
                print(json.dumps(batch, ensure_ascii=False, indent=2))
                return 0
            client = Workbench(args.server)
            client.connect(start=not args.no_start, data=args.data)
            upload = client.request('/api/uploads', {'multiple_references': True})
            inputs = [('reference', f) for f in batch['references']] + [('material', f) for f in batch['materials']]
            for index, (role, item) in enumerate(inputs, 1):
                uploaded = client.request(f"/api/uploads/{upload['id']}/files", file=Path(item['path']), role=role)
                if uploaded['sha256'] != item['sha256'] or uploaded['bytes'] != item['bytes']:
                    raise ValueError('上传期间输入发生变化，未提交制作：'+item['name'])
                print(f"已冻结输入 {index}/{len(inputs)}：{item['name']}", flush=True)
            identifier = 'run-'+uuid.uuid4().hex
            payload = {k: batch[k] for k in ('name', 'model_policy', 'width', 'instructions', 'bbox_refinement', 'screen')}
            payload.update(id=identifier, upload_id=upload['id'])
            receipt = {'server': client.base, 'data': str(Path(args.data).resolve()) if args.data else None,
                       'payload': payload, 'inputs': batch,
                       'url': client.base+'/?batch='+identifier}
            receipt_path = OUTPUT_ROOT/'batch-receipts'/(identifier+'.json')
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print('提交回执：'+str(receipt_path), flush=True)
        batch = client.request('/api/batches', payload)
        url = client.base+'/?batch='+batch['id']
        print(f"批跑已提交：{batch['name']}（{batch['total']} 张参考图）\n工作台：{url}", flush=True)
        if args.open:
            webbrowser.open(url)
        while args.wait:
            counts = batch['counts']
            print(f"完成 {counts['completed']}/{batch['total']}，失败 {counts['failed']}，执行中 {counts['running']}，排队 {counts['queued']}", flush=True)
            if batch['status'] in {'completed', 'failed'}:
                return 1 if counts['failed'] else 0
            time.sleep(5)
            batch = client.request('/api/batches/'+batch['id'])
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print('批跑未完成：'+str(exc), file=sys.stderr)
        if receipt_path:
            print(f'可用 --resume "{receipt_path}" 查询或续接原提交，不会重复新建批次。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
