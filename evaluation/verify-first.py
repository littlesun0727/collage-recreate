"""Mechanical checks only; visual judgments remain those of the SDK sessions."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'scripts'))
from validate import analysis_check, bindings_check, review_check


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True)
    p.add_argument('--report', required=True)
    a = p.parse_args()
    root, report = Path(a.root), Path(a.report)
    manifest = read(root/'manifest.json')
    summaries = {c['name']: c for c in read(report/'summary.json')['cases']}
    material_roots = [Path(x).resolve() for x in manifest['materials']]
    changes = [f for f, h in manifest['skill_files'].items() if sha(Path(manifest['skill'])/f) != h]
    cases = []
    for name in manifest['names']:
        stem = Path(name).stem
        case, run = root/stem, root/stem/'run'
        state_path = case/'controller/state.json'
        if not state_path.exists():
            cases.append({'name': stem, 'status': 'queued'})
            continue
        state = read(state_path)
        summary = summaries.get(stem, {})
        checks = {'one_sdk_run': state.get('sdk_run_calls') == 1,
                  'actual_model_matches': bool(summary.get('model_evidence')) and all(x == {'model':manifest['model'],'effort':manifest['effort']} for x in summary['model_evidence'])}
        for kind, check in [
            ('analysis',lambda:analysis_check(read(run/'analysis.json'))),
            ('bindings',lambda:bindings_check(read(run/'bindings.json'),read(run/'analysis.json'),read(run/'prepared/catalog.json'))),
            ('review',lambda:review_check(read(run/'review.json'),read(run/'scene.json'))),
        ]:
            if (run/f'{kind}.json').exists():
                try:
                    check()
                    checks[f'{kind}_schema_and_semantics'] = True
                except (ValueError, KeyError) as exc:
                    checks[f'{kind}_schema_and_semantics'] = False
                    checks[f'{kind}_validation_error'] = str(exc)
        if (run/'prepared/reference.png').exists():
            with Image.open(Path(manifest['reference_dir'])/name) as im: original_size = im.size
            with Image.open(run/'prepared/reference.png') as im: prepared_size = im.size
            checks['prepared_size_unchanged'] = original_size == prepared_size
        if (run/'result.json').exists():
            result = read(run/'result.json')
            photos = [r for r in result.get('resources',[]) if r['kind']=='photo']
            checks['customer_files_verified'] = bool(photos) and all(
                any(Path(r['source']['file']).resolve().is_relative_to(d) for d in material_roots)
                and sha(Path(r['source']['file'])) == r['source']['sha256'] for r in photos)
            visibility = result.get('photo_visible_fractions',{})
            checks['all_photos_visible'] = bool(photos) and len(visibility) == len(photos) and all(v >= .005 for v in visibility.values())
            checks['review_registered'] = bool(result.get('visual_review'))
            checks['first_exists'] = (run/'previews/first.png').exists()
            if checks['first_exists']:
                checks['first_equals_final'] = sha(run/'previews/first.png') == sha(run/'final.png')
                checks['first_scene_equals_scene'] = read(run/'previews/first-scene.json') == read(run/'scene.json')
                checks['final_hash_matches'] = sha(run/'final.png') == result.get('final_sha256')
            if (run/'review.json').exists():
                review = read(run/'review.json')
                checks['review_hashes_match'] = review['scene_sha256'] == sha(run/'scene.json') and review['final_sha256'] == sha(run/'final.png')
                checks['assessment_matches_review'] = summary.get('visual_verdict') == review['verdict']
            checks['comparison_view_requested'] = summary.get('comparison_open_requested',False)
            checks['single_successful_build'] = summary.get('build_count') == 1
        cases.append({'name':stem,'status':state['status'],'thread_id':state.get('thread_id'),'checks':checks})
    threads = [c['thread_id'] for c in cases if c.get('thread_id')]
    data = {'skill_files_changed':changes,'thread_count':len(threads),'unique_threads':len(set(threads)),
            'complete':len(cases)==len(manifest['names']) and all(c['status']=='completed' for c in cases),
            'cases':cases}
    data['failed_checks'] = [{'name':c['name'],'check':k} for c in cases for k,v in c.get('checks',{}).items() if not v]
    (report/'verification.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in data.items() if k!='cases'},ensure_ascii=False))


if __name__=='__main__':main()
