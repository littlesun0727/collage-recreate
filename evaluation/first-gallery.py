"""Show frozen first renders and evaluator statements, never substitute new renders."""
import argparse
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from statistics import median


def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def uri(p): return Path(p).resolve().as_uri() if Path(p).is_file() else None


def main():
    p = argparse.ArgumentParser(); p.add_argument('--root', required=True); p.add_argument('--report', required=True); p.add_argument('--analysis-baseline');p.add_argument('--baseline-first');p.add_argument('--title',default='新合并规则，第一轮制作结果')
    a = p.parse_args(); root = Path(a.root); report = Path(a.report); manifest = read(root/'manifest.json')
    cases = {c['name']: c for c in read(report/'summary.json')['cases']}; baseline = {}
    if a.analysis_baseline:
        baseline = {c['name']: c['object_count'] for c in read(a.analysis_baseline)['cases'] if c.get('image_transport') == 'tool-original-v1'}
    previous={c['name']:c for c in read(a.baseline_first)['cases']} if a.baseline_first else {}
    if previous:baseline={k:c['object_count'] for k,c in previous.items()}
    items = []
    for name in manifest['names']:
        stem = Path(name).stem; c = cases.get(stem, {'name': stem, 'status': 'queued'}); run = root/stem/'run'
        row = dict(c)
        row['reference_url'] = uri(run/'prepared/reference.png') or uri(Path(manifest['reference_dir'])/name)
        for key, file in [('first_url',run/'previews/first.png'),('comparison_url',run/'previews/comparison.png'),('boxes_url',run/'previews/analysis-boxes.png'),('analysis_url',run/'analysis.json'),('bindings_url',run/'bindings.json'),('review_url',run/'review.json')]: row[key] = uri(file)
        row['extraction_urls'] = [u for path in c.get('extraction_sheets',[]) if (u:=uri(path))]
        row['baseline_objects'] = baseline.get(stem)
        old=previous.get(stem,{})
        row['previous_first_url']=uri(old['first_preview']) if old.get('first_preview') else None
        row['previous_first_seconds']=old.get('first_preview_seconds')
        items.append(row)
    times = [c['first_preview_seconds'] for c in items if c.get('first_preview_seconds') is not None]
    data = {'generated_at': datetime.now(timezone.utc).isoformat(), 'items': items, 'model': manifest['model'], 'effort': manifest['effort'], 'completed': sum(c['status']=='completed' for c in items), 'rendered': sum(bool(c['first_url']) for c in items), 'first_median': median(times) if times else None}
    data.update(title=a.title,baseline_label='上一轮首版对象' if previous else '上一轮仅分析对象',has_previous=bool(previous))
    template = Path(__file__).with_name('first-gallery.html').read_text(encoding='utf-8')
    common = Path(__file__).with_name('analysis-gallery.html').read_text(encoding='utf-8')
    css = re.search(r'<style>(.*?)</style>',common,re.S).group(1)
    html = template.replace('__COMMON_CSS__',css).replace('__REPORT_DATA__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c'))
    (report/'gallery.html').write_text(html,encoding='utf-8')
    (report/'gallery-data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'cases':len(items),'rendered':data['rendered'],'completed':data['completed'],'html':str(report/'gallery.html')},ensure_ascii=False))


if __name__ == '__main__': main()
