"""Optional external OpenClaw launcher; existing Codex/OpenClaw sessions run the skill directly."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
MODEL = 'bailian-token-plan/qwen3.8-max'
KEY_ENV = 'BAILIAN_TOKEN_PLAN_API_KEY'


def controller_env(source, environ):
    env = dict(environ)
    key = env.get(KEY_ENV, '').strip()
    if not key:
        # Read only the selected credential; never copy the host configuration.
        config = json.loads(source.read_text(encoding='utf-8-sig'))
        provider = config.get('models', {}).get('providers', {}).get('bailian-token-plan', {})
        key = provider.get('apiKey')
        if not isinstance(key, str):
            raise ValueError('Set BAILIAN_TOKEN_PLAN_API_KEY or configure bailian-token-plan.apiKey.')
        match = re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', key)
        if match:
            key = env.get(match.group(1), '')
        key = key.strip()
        if not key:
            raise ValueError('Token Plan credential is empty or its environment reference is unset.')
    env[KEY_ENV] = key
    env['OPENCLAW_CONFIG_PATH'] = str(ROOT / 'openclaw.json')
    env['OPENCLAW_CONFIG_READONLY'] = '1'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    # Let agent exec own fresh state rather than inherit another running gateway.
    env.pop('OPENCLAW_STATE_DIR', None)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Validate configuration locally; no model call')
    mode.add_argument('--message-file', type=Path, help='Task file including the absolute skill path')
    parser.add_argument('--cwd', type=Path, default=Path.cwd())
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--credentials-config', type=Path, default=Path.home()/'.openclaw/openclaw.json')
    parser.add_argument('--openclaw-entry', type=Path,
                        default=Path(os.environ.get('APPDATA', str(Path.home())))/'npm/node_modules/openclaw/openclaw.mjs')
    args = parser.parse_args()
    try:
        node = shutil.which('node')
        if not node or not args.openclaw_entry.is_file():
            raise ValueError('Node.js and OpenClaw are required; use --openclaw-entry for a non-default install.')
        env = controller_env(args.credentials_config, os.environ)
        command = [node, str(args.openclaw_entry.resolve())]
        if args.check:
            command += ['config', 'validate', '--json']
        else:
            if not args.message_file.is_file() or not args.cwd.is_dir():
                raise ValueError('Task file and working directory must exist.')
            command += ['agent', 'exec', '--config', str(ROOT/'openclaw.json'),
                        '--cwd', str(args.cwd.resolve()), '--model', MODEL, '--thinking', 'high',
                        '--code-mode', 'direct', '--timeout', '7200',
                        '--message-file', str(args.message_file.resolve()), '--json']
            if args.state_dir:
                if not args.state_dir.is_dir():
                    raise ValueError('--state-dir must be an existing, unused directory.')
                command += ['--state-dir', str(args.state_dir.resolve())]
        return subprocess.run(command, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode
    except (OSError, ValueError):
        # Do not echo malformed configuration or credential-bearing exception text.
        print('Controller setup failed: check paths and the Token Plan credential source.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
