"""Pixel/hash regression on existing tasks without writing to those tasks."""
import argparse
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skill/scripts'))
from common import sha,save
from manual_edit import copy_run
from render import render


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--sources',nargs='+',required=True)
    parser.add_argument('--out',required=True);args=parser.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True);records=[]
    for index,source in enumerate(args.sources):
        source=Path(source)
        before={str(p.relative_to(source)):sha(p) for p in source.rglob('*') if p.is_file()}
        candidate=out/f'case-{index+1}';copy_run(source,candidate)
        start=time.monotonic();render(candidate,publish_result=False)
        after={str(p.relative_to(source)):sha(p) for p in source.rglob('*') if p.is_file()}
        row={'source':str(source),'candidate':str(candidate),'elapsed_seconds':round(time.monotonic()-start,3),
             'same_png_sha256':sha(source/'final.png')==sha(candidate/'final.png'),
             'source_unchanged':before==after,'source_file_count':len(before),'sha256':sha(candidate/'final.png')}
        records.append(row);assert row['same_png_sha256'] and row['source_unchanged'],row
    save(out/'report.json',{'passed':True,'cases':records})
    print(records)


if __name__=='__main__':main()
