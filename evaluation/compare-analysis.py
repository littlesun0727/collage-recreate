"""Compare frozen model analyses; do not author or repair any image-specific boxes."""
import argparse
import json
from pathlib import Path
from statistics import median
from datetime import datetime, timezone


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def metrics(cases):
    finished = [c for c in cases if c['critic_status'] == 'completed']
    return {
        'cases': len(cases),
        'analysts_completed': sum(c['analyst_status'] == 'completed' for c in cases),
        'critics_completed': len(finished),
        'objects': sum(c['object_count'] for c in cases),
        'critic_pass': sum(c['critic_verdict'] == 'pass' for c in finished),
        'needs_changes': sum(c['critic_verdict'] == 'needs_changes' for c in finished),
        'images_with_major': sum(c['major_count'] > 0 for c in finished),
        'major': sum(c['major_count'] for c in finished),
        'minor': sum(len(c['errors']) - c['major_count'] for c in finished),
        'analyst_seconds_median': median(c['analyst_seconds'] for c in cases if c['analyst_seconds']) if any(c['analyst_seconds'] for c in cases) else None,
        'critic_seconds_median': median(c['critic_seconds'] for c in finished) if finished else None,
        'resized_first_inputs': sum(c['image_was_resized'] is True for c in cases),
        'missing_first_inputs': [c['name'] for c in cases if c['actual_first_visual_size'] is None],
        'self_pass_critic_needs_changes': [c['name'] for c in finished if c['self_verdict'] == 'pass' and c['critic_verdict'] == 'needs_changes'],
        'integrity_flags': [{'name': c['name'], 'flags': c['flags']} for c in cases if c['flags']],
        'model_contexts': sorted(set((e.get('model'), e.get('effort')) for c in cases for key in ['analyst_evidence', 'critic_evidence'] for e in c[key]['model_contexts'])),
        'missing_model_evidence': [c['name'] for c in finished if any(not c[k]['model_contexts'] for k in ['analyst_evidence', 'critic_evidence'])],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('summary'); p.add_argument('--before', required=True); p.add_argument('--after', required=True)
    args = p.parse_args(); out = Path(args.summary).parent
    cases = read(args.summary)['cases']
    before = [c for c in cases if Path(c['root']).resolve() == Path(args.before).resolve()]
    after = [c for c in cases if Path(c['root']).resolve() == Path(args.after).resolve()]
    old = {c['name']: c for c in before}; new = {c['name']: c for c in after}
    pairs = []
    for name in sorted(old.keys() | new.keys()):
        b, a = old.get(name), new.get(name)
        pair = {'name': name, 'before': b, 'after': a}
        if b and a:
            bi = read(Path(b['root']) / name / 'analysis/input.json')
            ai = read(Path(a['root']) / name / 'analysis/input.json')
            pair['same_prepared_reference_sha256'] = bi['reference']['sha256'] == ai['reference']['sha256']
        pairs.append(pair)
    result = {'before': metrics(before), 'after': metrics(after), 'pairs': pairs}
    (out / 'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 原尺寸传图修复：对照结果', '', '分析与独立复核均为 Codex Agent SDK + gpt-5.6-sol / medium。首次 JSON 冻结，开发主控不提供框或逐图答案。独立复核沿用原基线输入方式与规则。', '', '| 指标 | 修复前 | 修复后 |', '|---|---:|---:|']
    for label, key in [('分析完成', 'analysts_completed'), ('独立复核完成', 'critics_completed'), ('已列对象数', 'objects'), ('复核未提出坐标修改', 'critic_pass'), ('复核提出修改', 'needs_changes'), ('含重大问题的图片', 'images_with_major'), ('重大问题项数', 'major'), ('轻微问题项数', 'minor'), ('首张视觉输入尺寸不一致', 'resized_first_inputs'), ('分析含自检中位秒数', 'analyst_seconds_median'), ('独立复核中位秒数', 'critic_seconds_median')]:
        lines.append(f"| {label} | {result['before'][key]} | {result['after'][key]} |")
    lines += ['', '以上只统计已经完成的对应阶段；未完成不得按0问题解释。模型复核不是人工真值，pass不代表元素完整或成品质量通过。两轮对象拆分和模型判断可能不同；单轮差异不能当作稳定准确率提升，也不能把所有变化归因于尺寸。', '', '| 图片 | 前→后对象数 | 前→后重大问题 | 前→后全部问题 | 修复后首张实际输入 | 修复后复核 |', '|---|---|---|---|---|---|']
    for pair in pairs:
        b, a = pair['before'], pair['after']; name = pair['name']
        def cell(c, key): return c[key] if c else '—'
        def errs(c): return len(c['errors']) if c and c['critic_status'] == 'completed' else '待完成'
        lines.append(f"| {name} | {cell(b, 'object_count')}→{cell(a, 'object_count')} | {cell(b, 'major_count')}→{cell(a, 'major_count') if a and a['critic_status']=='completed' else '待完成'} | {errs(b)}→{errs(a)} | {cell(a, 'actual_first_visual_size')} | {cell(a, 'critic_verdict')} |")
    (out / 'COMPARISON.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    def visual(c):
        if not c: return None
        keys = ['analyst_status', 'critic_status', 'object_count', 'analysis_size', 'actual_first_visual_size', 'analyst_seconds', 'critic_seconds', 'self_verdict', 'self_issues', 'critic_verdict', 'major_count', 'errors', 'pattern']
        row = {key: c[key] for key in keys}; run = Path(c['root']) / c['name'] / 'analysis'
        for key, path in [('boxes_url', Path(c['boxes'])), ('reference_url', run/'prepared/reference.png'), ('analysis_url', run/'analysis-first.json'), ('review_url', run.parent/'independent-review.json')]:
            row[key] = path.resolve().as_uri() if path.exists() else None
        return row
    view = {key: result[key] for key in ['before', 'after']}
    view['generated_at'] = datetime.now(timezone.utc).isoformat()
    view['pairs'] = [{'name': pair['name'], 'before': visual(pair['before']), 'after': visual(pair['after'])} for pair in pairs]
    template = Path(__file__).with_name('analysis-gallery.html').read_text(encoding='utf-8')
    (out / 'comparison.html').write_text(template.replace('__REPORT_DATA__', json.dumps(view, ensure_ascii=False).replace('<', '\\u003c')), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ['before', 'after']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
