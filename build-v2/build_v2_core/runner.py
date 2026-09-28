"""Bounded task execution with an actual container boundary. Fail closed by default."""
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from .common import BuildError, ROOT, relative_file, sha
from .store import atomic, commit, load, locked
from .workflow import export, refresh, task_for
from .review_budget import ensure_remaining


def capability():
    docker = shutil.which('docker')
    return {'docker': docker, 'default_backend': 'docker', 'host_subprocess_is_sandbox': False,
            'requirements': ['preinstalled image with dependencies', 'no network', 'read-only input mounts',
                             'task-only output mount', 'CPU/memory/PID limits', 'wall-clock timeout'],
            'available': bool(docker)}


def run_script(root, ident, script, *, timeout=120, image=None, trusted_host=False, approval_reason=None):
    with locked(root) as root:
        state = load(root); refresh(root, state); row = task_for(state, ident)
        if row['status'] in ('accepted', 'blocked', 'deferred'):
            raise BuildError('execution_state', 'Task is accepted or blocked')
        if row['status'] == 'stale':
            local_prefix = 'dependency_changed:work/' + ident + '/'
            reasons = [reason.replace('\\', '/') for reason in row.get('stale_reasons', [])]
            if not reasons or any(not reason.startswith(local_prefix) and reason != 'runtime_dependency_changed' for reason in reasons):
                raise BuildError('execution_state', 'Plan/input/snapshot changed; explicitly revise or resolve evidence first')

        if trusted_host and not approval_reason:
            raise BuildError('execution_approval', 'Trusted host execution requires an explicit user-approved reason')
        ensure_remaining(state, ident)
        attempts = [e for e in state['events'] if e['event']=='script_started' and e['details'].get('task')==ident]
        failures = [e for e in state['events'] if e['event']=='script_finished' and e['details'].get('task')==ident
                    and e['details'].get('status') in ('failed', 'timeout')]
        if len(failures) >= state['limits']['max_attempts']: raise BuildError('attempt_limit', 'Script technical failure budget exhausted')
        elapsed = sum(e['details'].get('elapsed_seconds', 0) for e in state['events'] if e['event']=='script_finished' and e['details'].get('task')==ident)
        remaining = state['limits']['max_seconds'] - elapsed
        if remaining <= 0: raise BuildError('time_limit', 'Script runtime budget exhausted')
        timeout = min(timeout, remaining)
        workspace = root/'work'/ident; workspace.mkdir(parents=True, exist_ok=True)
        source = relative_file(workspace, script)
        attempt = root/'execution'/uuid.uuid4().hex; attempt.mkdir(parents=True)
        package = row['package'].copy(); package['input_root'] = str(root/state['snapshot'])
        atomic(workspace/'task.json', package)
        environment = {key: os.environ[key] for key in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','LANG') if key in os.environ}
        name = 'build-v2-'+uuid.uuid4().hex
        if trusted_host:
            import sys
            environment['PYTHONPATH'] = str(ROOT)
            command = [sys.executable, '-B', '-X', 'utf8', str(source), '--task', str(workspace/'task.json'), '--output', str(workspace)]
            boundary = 'trusted-host-user-approved-NOT-SANDBOXED'
        else:
            docker = shutil.which('docker')
            if not docker or not image:
                raise BuildError('sandbox_unavailable', 'No verified Docker backend/image; cannot run arbitrary task code. Explicitly agree an execution environment first.')
            inspected = subprocess.run([docker, 'image', 'inspect', image], capture_output=True, timeout=20)
            if inspected.returncode: raise BuildError('sandbox_image', 'Sandbox image must already exist; no implicit pull/install')
            package['input_root'] = '/inputs'; atomic(workspace/'task.json', package)
            command = [docker, 'run', '--rm', '--pull=never', '--name', name, '--network=none', '--read-only',
                       '--cap-drop=ALL', '--security-opt=no-new-privileges', '--memory=1g', '--cpus=2', '--pids-limit=64',
                       '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m', '--mount', f'type=bind,src={ROOT},dst=/tools,readonly',
                       '--mount', f'type=bind,src={root/state["snapshot"]},dst=/inputs,readonly',
                       '--mount', f'type=bind,src={workspace},dst=/work', '-e', 'PYTHONPATH=/tools', '-w', '/work',
                       image, 'python', '-B', '/work/'+script.replace('\\','/'), '--task', '/work/task.json', '--output', '/work']
            boundary = 'docker-no-network-readonly-inputs-resource-limits'
        receipt = {'task': ident, 'script_sha256': sha(source), 'backend': boundary, 'approval_reason': approval_reason,
                   'timeout_seconds': timeout, 'status': 'running',
                   'stdout_path': str(attempt/'stdout.txt'), 'stderr_path': str(attempt/'stderr.txt'),
                   'attempt': len(attempts)+1, 'technical_failures_remaining': state['limits']['max_attempts']-len(failures)}
        row['status'] = 'pending'; row['review'] = None
        commit(root, state, 'script_started', receipt); atomic(attempt/'receipt.json', receipt)
        start = time.monotonic()
        try:
            with (attempt/'stdout.txt').open('wb') as out, (attempt/'stderr.txt').open('wb') as err:
                process = subprocess.run(command, cwd=workspace, env=environment, stdout=out, stderr=err,
                                         timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            receipt.update(status='completed' if process.returncode==0 else 'failed', exit_code=process.returncode)
        except subprocess.TimeoutExpired:
            receipt.update(status='timeout')
        finally:
            if not trusted_host:
                subprocess.run([docker, 'rm', '-f', name], capture_output=True, timeout=20)
            receipt['elapsed_seconds'] = time.monotonic()-start
            if receipt['status'] in ('failed', 'timeout'):
                with (attempt/'stderr.txt').open('rb') as stream:
                    stream.seek(max(0, stream.seek(0, 2)-4096))
                    receipt['stderr_tail'] = stream.read().decode('utf-8', errors='replace')
            atomic(attempt/'receipt.json', receipt); commit(root, state, 'script_finished', receipt); export(root, state)
        return receipt
