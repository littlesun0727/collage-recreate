"""Actual optional PaddleOCR + LaMa integration, not a mocked unit test."""
import sys,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha
from asset_repair import apply_edits,worker
from PIL import Image,ImageDraw,ImageFont
import numpy as np

run=Path(sys.argv[1]);run.mkdir(parents=True,exist_ok=True)
im=Image.new('RGBA',(512,256),(240,231,209,255))
ImageDraw.Draw(im).text((70,90),'DELETE THIS',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',42),fill='black')
im.save(run/'before.png')
plan={'edits':[{'id':'board','operation':'remove_text','bbox':[60,80,390,150],
                'texts':['DELETE THIS'],'backend':'lama','padding':3,'reason':'Integration test of unwanted printed text'}]}
save(run/'repair-input.json',plan)
started=time.monotonic();out,records,errors=apply_edits(run,im,'board',plan);first=time.monotonic()-started
if errors:raise RuntimeError(errors)
out.save(run/'after.png');mask=Image.open(records[0]['mask']['file'])
assert not np.any(np.any(np.asarray(im)!=np.asarray(out),axis=2)&(np.asarray(mask)==0))
assert im.getchannel('A').tobytes()==out.getchannel('A').tobytes()
started=time.monotonic();again,_,errors=apply_edits(run,im,'board',plan);second=time.monotonic()-started
assert not errors and out.tobytes()==again.tobytes()
after_text=worker(run,'ocr',out.convert('RGB'))
save(run/'verification.json',{'actual_paddleocr':True,'actual_lama':True,'errors':errors,
    'mask_outside_unchanged':True,'alpha_unchanged':True,'cache_exact':True,
    'first_seconds':round(first,3),'cached_seconds':round(second,3),'records':records,'after_ocr':after_text})
print(json.dumps(read(run/'verification.json'),ensure_ascii=False))
