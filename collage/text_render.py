"""Fit locally rendered, glyph-checked handwritten runs to an analysis text box."""
import os
import unicodedata
import regex
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

FONT_ROOT = Path(os.environ.get("COLLAGE_FONT_ROOT","C:/Windows/Fonts"))
BUNDLED = Path(__file__).resolve().parents[1]/"assets/fonts"
HANDWRITTEN_LETTERING = regex.compile(
    r"(?<!非|不是|并非|没有|不含|不用)(?:(?:手写|手绘|连笔)(?:的)?"
    r"(?:字体|字形|体|小字|字|文字|英文|中文|汉字|数字|标题|说明)|签名字样)"
)


@lru_cache(maxsize=20)
def glyphs(path):
    if not path.is_file():
        return frozenset()
    with TTFont(path, fontNumber=0, lazy=True) as font:
        return frozenset(font.getBestCmap())


def script(char):
    name = unicodedata.name(char,"")
    if "HANGUL" in name:
        return "ko"
    if any(tag in name for tag in ("CJK", "IDEOGRAPH", "BOPOMOFO")):
        return "zh"
    if char.isalpha() or char.isdigit():
        return "en"
    return None


def font_runs(line, style, skipped=None, *, handwritten=False):
    if handwritten:
        english = "segoeprb.ttf" if style.get("bold") else "segoepr.ttf"
    else:
        english = "timesbd.ttf" if style.get("bold") else "times.ttf"
    fonts = {"zh": BUNDLED/("Yozai-Regular.ttf" if handwritten else "SourceHanSerifCN-Medium.otf"),
             "ko": BUNDLED/"NanumPenScript-Regular.ttf", "en": FONT_ROOT/english}
    language = next((script(c) for c in line if script(c)), "en")
    runs = []
    for cluster in regex.findall(r"\X", line):
        # These text layers are drawn in a single ink color.
        char = cluster.replace("\ufe0e", "").replace("\ufe0f", "")
        language = (script(char[0]) if char else None) or language
        choices = [fonts[language],FONT_ROOT/"malgun.ttf" if language=="ko" else FONT_ROOT/"msyh.ttc",
                   FONT_ROOT/"arial.ttf",FONT_ROOT/"malgun.ttf",*fonts.values(),
                   FONT_ROOT/"seguisym.ttf",FONT_ROOT/"seguiemj.ttf"]
        path = next((p for p in choices if char and all(ord(c) in glyphs(p) for c in char)), None)
        if path is None:
            if skipped is not None:
                skipped.append(cluster)
            continue
        if runs and runs[-1][0] == path:
            runs[-1] = (path,runs[-1][1]+char)
        else:
            runs.append((path,char))
    return runs


@lru_cache(maxsize=256)
def font_at(path, size):
    return ImageFont.truetype(str(path),size)


def render_text(text,size,style,description=None):
    skipped = []
    handwritten = bool(HANDWRITTEN_LETTERING.search("".join((description or "").split())))
    lines = [font_runs(line,style,skipped,handwritten=handwritten) for line in text.replace('\r\n','\n').replace('\r','\n').expandtabs(4).split('\n')]
    paths = list(dict.fromkeys(p for line in lines for p,_ in line))
    if not paths:
        return Image.new('RGBA',size), {'text':text,'text_rendered':False,'skipped_glyphs':skipped}
    align = style.get('align','center')
    stroke = round(style.get('stroke_width',0))
    def layout(px):
        fonts = {p:font_at(p,px) for p in paths}
        advance = max(sum(f.getmetrics()) for f in fonts.values())*style.get('line_spacing',1)
        widths=[]; lefts=[]; tops=[]; bottoms=[]
        for i,line in enumerate(lines):
            cursor=0; left=right=0; top=bottom=0
            for path,chunk in line:
                f=fonts[path]
                l,t,r,b=f.getbbox(chunk,anchor='ls',stroke_width=stroke)
                left=min(left,cursor+l);right=max(right,cursor+r)
                top=min(top,t);bottom=max(bottom,b)
                cursor+=f.getlength(chunk)
            lefts.append(left);widths.append(right-left);tops.append(top);bottoms.append(bottom)
        if len(lines)>1 and 'line_spacing' not in style:
            advance=max(advance,(size[1]-(max(bottoms)-min(tops)))/(len(lines)-1))
        top=min(t+i*advance for i,t in enumerate(tops))
        height=max(b+i*advance for i,b in enumerate(bottoms))-top
        return fonts,advance,lefts,widths,top,height
    lo,hi=1,max(1,min(2000,round(style.get('font_size') or 2000)))
    chosen=None
    while lo<=hi:
        px=(lo+hi)//2
        candidate=layout(px)
        if max(candidate[3])<=size[0] and candidate[5]<=size[1]+1e-7:
            chosen=(px,candidate);lo=px+1
        else:
            hi=px-1
    if chosen is None:
        raise ValueError("Text cannot fit even at 1px")
    px,(fonts,advance,lefts,widths,top,height)=chosen
    out=Image.new("RGBA",size);draw=ImageDraw.Draw(out)
    for i,line in enumerate(lines):
        spare=size[0]-widths[i]
        x=(spare/2 if align=='center' else spare if align=='right' else 0)-lefts[i]
        y=(size[1]-height)/2-top+i*advance
        for path,chunk in line:
            draw.text((x,y),chunk,font=fonts[path],anchor='ls',fill=style.get('fill','#171717'),
                      stroke_width=stroke,stroke_fill=style.get('stroke','#FFFFFF'))
            x+=fonts[path].getlength(chunk)
    return out,{'font':str(paths[0]),'fonts':[p.name for p in paths],'font_size_px':px,
                'line_count':len(lines),'align':align,'text':text,'text_rendered':True,'skipped_glyphs':skipped}
