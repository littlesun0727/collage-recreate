"""Five workbench nodes, projected from workflow state and saved products.

Engine progress numbers belong to the old execution protocol. Never use them as
UI node numbers: analysis, window geometry and binding are separate here.
"""

NODES = (
    ('prepare', '准备素材', '参考图与客户素材'),
    ('analyze', '分析素材', 'structure.json · analysis-boxes.png'),
    ('photo_windows', '判断窗口蒙版', 'photo-windows.png'),
    ('materials', '素材绑定与提取', 'bindings.json · 360 提取结果'),
    ('compose', '首版成图', 'first.png'),
)
DOCUMENTS = frozenset({'structure.json', 'photo-windows.json', 'bindings.json'})


def project(run, read, rows, materials, groups):
    state = read(run/'workflow-progress.json') or read(run/'workflow/state.json')
    steps = state.get('steps', {})
    products = [
        [('prepared/reference.png', 'image')],
        [('structure.json', 'json'), ('previews/analysis-boxes.png', 'image')],
        [('previews/photo-windows.png', 'image'), ('photo-windows.json', 'json')],
        [('bindings.json', 'json')],
        [('previews/first.png', 'image')],
    ]
    stages = []
    for index, (key, label, description) in enumerate(NODES):
        artifacts = [{'name': path.rsplit('/', 1)[-1], 'path': path, 'kind': kind,
                      'exists': (run/path).is_file()} for path, kind in products[index]]
        available = any(a['exists'] for a in artifacts)
        stages.append({'id': index+1, 'key': key, 'label': label,
                       'description': description, 'artifacts': artifacts,
                       'status': 'saved' if available else 'pending', 'at': None,
                       'summary': '已留存的产物可点击查看；未生成的文件单独标出。' if available else '等待此步骤生成产物。'})
    if stages[0]['status'] == 'saved':
        stages[0].update(status='complete', summary='参考图和客户素材已准备。')

    for name, index in [('analyze', 1), ('photo_windows', 2)]:
        step = steps.get(name, {})
        if step:
            status = step['status']
            if status == 'complete' and stages[index]['status'] == 'pending':
                status = 'saved'  # Older runs may not retain every named artifact.
            stages[index].update(status=status, started_at=step.get('started_at'),
                at=step.get('finished_at') or step.get('started_at'),
                summary=step.get('error') or ('正在生成本步骤产物。' if status == 'running'
                                            else '本步骤已完成，已留存的产物可直接查看。'))

    binding = steps.get('bind', {})
    build = steps.get('build', {})
    # materials_started is emitted by the renderer, after Reveal extraction.
    build_start = build.get('started_at', '')
    composing = any(r.get('event') == 'materials_started' and r.get('at', '') >= build_start for r in rows)
    binding_saved = (run/'bindings.json').is_file()
    final_saved = (run/'previews/first.png').is_file()
    extracted = [m for m in materials if m['method'] == '参考提取']
    missing = sum(m['status'] in {'failed', 'unresolved'} for m in extracted)
    finished_build = build.get('status') == 'complete' or final_saved
    if binding_saved:
        stages[3].update(status='complete' if finished_build or composing else 'saved',
            summary=f'照片绑定已保存；360 提取对象 {len(extracted)} 件。'
                    + (f'其中 {missing} 件缺失或失败，可查看已返回结果。' if missing else ''))
    if binding.get('status') in {'running', 'failed'}:
        stages[3].update(status=binding['status'],
            summary=binding.get('error') or '正在选择客户照片并确定裁切。')
    elif build.get('status') == 'running' and not composing and not finished_build:
        stages[3].update(status='running', summary=f'正在提取固定素材；360 已返回 {sum(g["status"] == "complete" for g in groups)} / {len(groups)} 组。')
    if final_saved:
        stages[4].update(status='complete', summary='首版已生成，可与参考图对照。')
    elif composing:
        stages[4].update(status='running', summary='正在处理客户照片并合成首版。')
    for index, step in [(3, binding), (4, build)]:
        stages[index].update(started_at=step.get('started_at'), at=step.get('finished_at') or step.get('started_at'))

    target = {'analyze': 2, 'photo_windows': 3, 'boxes': 3, 'boxes_apply': 3,
              'bind': 4, 'build': 5 if composing else 4, 'screen': 5,
              'screen_apply': 5, 'register': 5}.get(state.get('current_step'))
    if state.get('status') == 'failed' and target:
        stages[target-1].update(status='failed', summary=state.get('error') or '此步骤未完成，已保存产物仍可查看。')
    active = [s['id'] for s in stages if s['status'] in {'running', 'failed'}]
    available = [s['id'] for s in stages if s['status'] in {'complete', 'saved'}]
    current = active[-1] if active else max(available, default=1)
    history = []
    for name, index in [('analyze', 2), ('photo_windows', 3), ('bind', 4), ('build', 5)]:
        step = steps.get(name, {})
        if step:
            history.append({'stage': index, 'status': step['status'],
                'at': step.get('finished_at') or step.get('started_at'),
                'summary': stages[index-1]['label']+' · '+(step.get('error') or
                    {'complete': '产物已保存', 'running': '执行中', 'failed': '未完成'}.get(step['status'], step['status']))})
    return stages, current, history, state
