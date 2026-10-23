"""Local model preferences and read-only provider catalogs; no inference on save."""
from dataclasses import replace
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

from collage.vlm.client import NoRedirect
from collage.config import Config
from collage.vlm.providers import ENDPOINTS, capabilities
from collage.storage import read, save


class ModelSettings:
    def __init__(self, root):
        self.path = Path(root)/'model-settings.json'
        self.lock = threading.RLock()
        self.cache = {}

    def config(self, policy=None):
        base = Config.load()
        if policy is not None:
            return base.with_policy(policy)
        policy = read(self.path)
        if not policy:
            policy = base.model_policy()
            for stage in policy.values():
                stage['coordinate_mode'] = 'normalized_1000'
        return base.with_policy(policy)

    def public(self):
        credentials = {}
        for provider in ENDPOINTS:
            try:
                replace(Config.load(), provider=provider).check_ready()
                credentials[provider] = True
            except ValueError:
                credentials[provider] = False
        return {'policy': self.config().model_policy(), 'credentials': credentials,
                'providers': [{'id': 'openrouter', 'name': 'OpenRouter'}, {'id': 'yibu', 'name': 'yibuapi'},
                              {'id': 'codex', 'name': 'Codex SDK'}]}

    def update(self, policy):
        config = Config.load().with_policy(policy)
        config.check_keys()
        with self.lock:
            save(self.path, config.model_policy())
        return self.public()

    def catalog(self, provider):
        if provider not in ENDPOINTS:
            raise ValueError('未知供应商')
        if provider == 'codex':
            return {'provider': provider, 'models': [
                {'id': model, 'vision': True, 'capabilities': capabilities(provider, model)}
                for model in ('gpt-6.1-sol', 'gpt-6-luna')],
                'note': '内置推荐模型；账号是否有权限以实际调用为准，可手动输入其他 Codex GPT 模型 ID。'}
        with self.lock:
            cached = self.cache.get(provider)
            if cached and time.monotonic()-cached[0] < 300:
                return cached[1]
        config = replace(Config.load(), provider=provider)
        key = config.api_key()
        request = Request(ENDPOINTS[provider].replace('/chat/completions', '/models'),
                          headers={'Authorization': 'Bearer '+key, 'Accept': 'application/json'})
        handlers = [NoRedirect()] + ([ProxyHandler({})] if provider == 'yibu' else [])
        try:
            with build_opener(*handlers).open(request, timeout=30) as response:
                body = response.read(8*1024*1024+1)
                if len(body) > 8*1024*1024:
                    raise ValueError('模型列表过大')
                raw = json.loads(body)
                if not isinstance(raw, dict) or not isinstance(raw.get('data'), list):
                    raise ValueError('模型列表结构无效')
        except HTTPError as exc:
            raise ValueError(f'{provider} 模型列表 HTTP {exc.code}') from None
        except (URLError, OSError, ValueError):
            raise ValueError(f'{provider} 模型列表读取失败，可手动输入模型 ID') from None
        models = []
        for item in raw.get('data', []):
            if not isinstance(item, dict) or not isinstance(item.get('id'), str):
                continue
            # OpenRouter publishes actual modalities. Yibu publishes endpoint types only.
            inputs = (item.get('architecture') or {}).get('input_modalities')
            endpoints = item.get('supported_endpoint_types')
            if inputs is not None and 'image' not in inputs:
                continue
            if endpoints and 'openai' not in endpoints:
                continue
            models.append({'id': item['id'], 'vision': True if inputs and 'image' in inputs else None,
                           'capabilities': capabilities(provider, item['id'])})
        value = {'provider': provider, 'models': sorted(models, key=lambda m: m['id']),
                 'note': 'OpenRouter 列表按图片输入筛选；yibu 列表按 Chat 接口筛选，识图能力需实测。'}
        with self.lock:
            self.cache[provider] = (time.monotonic(), value)
        return value
