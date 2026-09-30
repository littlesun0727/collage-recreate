"""Fixed font selection and measured text; no per-task drawing scripts."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT_ROOT=Path('C:/Windows/Fonts')
FONTS={'sans':'arial','serif':'times','mono':'cour','hand':'comic','cjk':'msyh','cjk-hand':'simkai'}


def font_file(style,text):
    name=style.get('font','cjk' if any(ord(c)>255 for c in text) else 'sans')
    if any(ord(c)>255 for c in text) and name not in ['cjk','cjk-hand']:name='cjk'
    base=FONTS[name]
    if name=='cjk':name_file='msyhbd.ttc' if style.get('bold') else 'msyh.ttc'
    elif name=='cjk-hand':name_file='simkai.ttf'
    else:
        suffix=('bi' if style.get('bold') and style.get('italic') else 'bd' if style.get('bold') else 'i' if style.get('italic') else '')
        if base=='comic' and suffix=='bi':suffix='z'
        name_file=base+suffix+'.ttf'
    path=FONT_ROOT/name_file
    if not path.exists():raise ValueError(f'Font unavailable: {path}')
    return str(path)


def render_text(text,size,style):
    out=Image.new('RGBA',size);d=ImageDraw.Draw(out)
    path=font_file(style,text);stroke=round(style.get('stroke_width',0));spacing_ratio=style.get('line_spacing',1.05)
    hi=max(1,min(2000,round(style.get('font_size',size[1]))));lo=1;chosen=None
    while lo<=hi:
        px=(lo+hi)//2;f=ImageFont.truetype(path,px);spacing=max(0,round(px*(spacing_ratio-1)))
        bounds=d.multiline_textbbox((0,0),text,font=f,spacing=spacing,stroke_width=stroke)
        if bounds[2]-bounds[0]<=size[0] and bounds[3]-bounds[1]<=size[1]:
            chosen=(f,bounds,spacing,px);lo=px+1
        else:hi=px-1
    if chosen is None:raise ValueError('Text cannot fit even at 1px')
    f,b,spacing,px=chosen;align=style.get('align','center');width=b[2]-b[0];height=b[3]-b[1]
    x=(size[0]-width)/2 if align=='center' else 0 if align=='left' else size[0]-width
    d.multiline_text((x-b[0],(size[1]-height)/2-b[1]),text,font=f,fill=style.get('fill','#171717'),spacing=spacing,align=align,stroke_width=stroke,stroke_fill=style.get('stroke','#FFFFFF'))
    return out,{'font':path,'font_size_px':px,'text':text,'text_rendered':True,'font_match':'approximate'}
