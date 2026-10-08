"""Audit a serial SDK batch from controller timestamps and production evidence."""
import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def read(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {} if default is None else default


def records(path):
    if not path.exists():
        return []
    result = []
    for line in path.read_bytes().splitlines():
        try:
            result.append(json.loads(line.decode('utf-8')))
        except ValueError:
            pass
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def actual_model(thread):
    if not thread:
        return None
    sessions = Path('C:/Users/admin/.codex/sessions')
    for file in sessions.glob('*/*/*/*'+thread+'*.jsonl'):
        for line in file.open(encoding='utf-8'):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get('type') == 'turn_context':
                payload = row.get('payload', {})
                return {'model': payload.get('model'), 'effort': payload.get('effort')}
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--contact-sheets', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = read(root/'manifest.json')
    rows = []
    for sample in manifest['samples']:
        state = read(root/'controller'/sample['task']/'state.json')
        run = root/'tasks'/sample['task']
        result = read(run/'result.json')
        events = records(run/'events.jsonl')
        review = result.get('visual_review', {})
        versions = list((run/'observability/versions').glob('*/manifest.json'))
        versions = [p for p in versions if not p.parent.name.startswith('.')]
        errors = []
        for version in versions:
            for name, artifact in read(version).get('artifacts', {}).items():
                file = version.parent/artifact['path']
                if not file.is_file() or digest(file) != artifact['sha256']:
                    errors.append(version.parent.name+':'+name)
        model = actual_model(state.get('thread_id'))
        verified = bool(result.get('exported') and result.get('customer_photos_verified'))
        for key, file in [('final_sha256','final.png'),('scene_sha256','scene.json')]:
            verified = verified and (run/file).is_file() and digest(run/file) == result.get(key)
        metrics = {name: round(sum(e.get('elapsed_seconds',0) for e in events if e.get('event')==name+'_finished'),3)
                   for name in ['prepare','validate','build','screen','apply','review']}
        rows.append({**sample, 'status':state.get('status','pending'),
                     'started_at':state.get('started_at'), 'finished_at':state.get('finished_at'),
                     'elapsed_seconds':state.get('elapsed_seconds'), 'actual_configuration':model,
                     'model_verified':model=={'model':manifest['model'],'effort':manifest['effort']},
                     'visual_verdict':review.get('verdict','pending'), 'summary':review.get('summary',''),
                     'render_verified':bool(verified), 'versions':len(versions), 'snapshot_errors':errors,
                     'workflow_command_seconds':metrics,
                     'reveal_submissions':sum(e.get('event')=='reveal_request_started' for e in events),
                     'issues':review.get('items',[]), 'run':str(run)})
    overlap = []
    previous = None
    for row in rows:
        if not row['started_at']:
            continue
        if previous and (not previous['finished_at'] or datetime.fromisoformat(row['started_at']) < datetime.fromisoformat(previous['finished_at'])):
            overlap.append([previous['index'],row['index']])
        previous = row
    audit = {'model':manifest['model'],'effort':manifest['effort'],'concurrency':1,
             'samples':len(rows),'completed':sum(r['status']=='completed' for r in rows),
             'overlapping_samples':overlap,'rows':rows}
    (root/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (root/'timings.csv').open('w',encoding='utf-8-sig',newline='') as out:
        fields=['index','name','status','started_at','finished_at','elapsed_seconds','visual_verdict','versions','model_verified','render_verified','reveal_submissions']
        writer=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    lines=['# Codex SDK 串行端到端测试','',f"模型：`{manifest['model']}`，推理档位：`{manifest['effort']}`，并发数：1。",
           '',f"共 {len(rows)} 张；已完整交付 {audit['completed']} 张；检测到样本重叠 {len(overlap)} 处。",
           '', '耗时从 SDK turn 启动到最终响应结束，包含启动、分析、工具、远端等待和复核。命令耗时单独保存在 audit.json，不将总耗时减去命令耗时冒称纯模型思考时间。',
           '', '| 序号 | 样片 | 执行状态 | 总耗时 | 视觉结论 | 版本数 |', '|---|---|---|---|---|---|']
    for row in rows:
        seconds=row['elapsed_seconds']
        duration='—' if seconds is None else f'{int(seconds//60)}分{seconds%60:.1f}秒'
        if row['status']=='running':duration+='（进行中）'
        lines.append(f"| {row['index']} | [{row['name']}](tasks/{row['task']}/previews/comparison.png) | {row['status']} | {duration} | {row['visual_verdict']} | {row['versions']} |")
    lines += ['', '## 每张样片的实际复核','']
    for row in rows:
        lines += [f"### {row['index']:02d} {row['name']}",'',row['summary'] or '尚未登记复核。','']
        lines += [f"- {item.get('id','')}：{item.get('reason','')}" for item in row['issues']]
        lines += ['']
    (root/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    if args.contact_sheets:
        font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',16)
        for offset in range(0,len(rows),4):
            sheet=Image.new('RGB',(1100,1060),'#F4F2EB');draw=ImageDraw.Draw(sheet)
            for j,row in enumerate(rows[offset:offset+4]):
                x=(j%2)*550;y=(j//2)*530
                draw.text((x+12,y+8),f"{row['index']:02d} {row['name']}",font=font,fill='#173C34')
                draw.text((x+12,y+34),f"{row['elapsed_seconds']}s · {row['visual_verdict']}",font=font,fill='#665C50')
                run=Path(row['run'])
                for k,file in enumerate([run/'prepared/reference.png',run/'final.png']):
                    if file.exists():
                        with Image.open(file) as image:
                            image=image.convert('RGB');image.thumbnail((255,450))
                            sheet.paste(image,(x+12+k*272+(255-image.width)//2,y+66+(450-image.height)//2))
            sheet.save(root/f'contact-{offset//4+1:02d}.jpg',quality=93)
    print(json.dumps({k:v for k,v in audit.items() if k!='rows'},ensure_ascii=True))


if __name__=='__main__':
    main()
