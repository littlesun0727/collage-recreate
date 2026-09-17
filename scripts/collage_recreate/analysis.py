"""Business draft validation and review previews; no model calls or asset generation."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Annotated, Literal

from PIL import ImageColor, ImageDraw, ImageFont
from pydantic import Field, model_validator

from .core import file_hash, load_image, parse, png_bytes, read_json, require
from .models import ID, Strict

Words = Annotated[str, Field(min_length=1, pattern=r"\S")]
Rect = Annotated[list[int], Field(min_length=4, max_length=4)]


class Element(Strict):
    id: ID
    label: Words
    source_rect: Rect
    review_notes: str = ""
    locate_colors: Annotated[list[str], Field(max_length=8)] = Field(default_factory=list)

    @model_validator(mode="after")
    def colors(self):
        for color in self.locate_colors:
            ImageColor.getrgb(color)
        return self


class Slot(Element):
    mode: Literal["photo", "photo_feather", "cutout", "unknown"]
    upload_hint: str = ""


class Text(Element):
    default_text: str | None
    style_brief: str = ""


class Attachment(Strict):
    slot_id: ID
    position: Literal["above", "below"]


class Shape(Strict):
    kind: Literal["rectangle", "rounded_rectangle", "ellipse", "dashed_rectangle"]
    fill: str | None = None
    outline: str | None
    width: Annotated[int, Field(ge=0, le=1024)]
    radius: Annotated[float, Field(ge=0, le=8192)] | None = None
    dash: Annotated[int, Field(ge=1, le=8192)] | None = None
    gap: Annotated[int, Field(ge=0, le=8192)] | None = None

    @model_validator(mode="after")
    def appearance(self):
        for color in (self.fill, self.outline):
            if color is not None:
                ImageColor.getcolor(color, "RGBA")
        if self.kind == "rounded_rectangle" and self.radius is None:
            raise ValueError("rounded_rectangle requires radius")
        if self.kind == "dashed_rectangle" and (self.dash is None or self.gap is None):
            raise ValueError("dashed_rectangle requires dash and gap")
        if self.kind != "rounded_rectangle" and self.radius is not None:
            raise ValueError("radius only applies to rounded_rectangle")
        if self.kind != "dashed_rectangle" and (self.dash is not None or self.gap is not None):
            raise ValueError("dash/gap only apply to dashed_rectangle")
        return self


class Overlay(Element):
    action: Literal["basic_shape", "reference_generate"]
    generation_brief: str
    requires_exact_content: bool
    review_notes: str
    attachment: Attachment | None
    text_content: str | None = None
    shape: Shape | None = None

    @model_validator(mode="after")
    def method(self):
        if self.action == "basic_shape" and self.shape is None:
            raise ValueError("basic_shape requires shape")
        if self.action == "basic_shape" and self.text_content:
            raise ValueError("basic_shape describes one geometric shape, not embedded text; use texts or reference_generate for a complete fixed decoration")
        if self.action == "reference_generate":
            if self.shape is not None:
                raise ValueError("reference_generate has no shape")
            if not self.generation_brief.strip():
                raise ValueError("reference_generate needs an appearance brief")
        return self


class FixedBackground(Strict):
    mode: Literal["fixed"]
    kind: Literal["photo", "texture", "solid", "unknown"]
    background_brief: Words
    review_notes: str = ""
    preserve_reason: str | None = None

    @model_validator(mode="after")
    def photo_requires_explicit_preservation(self):
        if self.kind == "photo" and not (self.preserve_reason or "").strip():
            raise ValueError("Photographic backgrounds use a customer slot by default; fixed photo needs preserve_reason")
        return self


class SlotBackground(Strict):
    mode: Literal["slot"]
    kind: Literal["photo", "texture", "solid", "unknown"]
    slot_id: ID
    review_notes: str


class Layer(Strict):
    type: Literal["background", "slot", "text", "overlay"]
    id: ID | None = None

    @model_validator(mode="after")
    def reference(self):
        if (self.type == "background") != (self.id is None):
            raise ValueError("background has no id; slot/text/overlay require id")
        return self


class Draft(Strict):
    background: FixedBackground | SlotBackground
    slots: list[Slot]
    texts: list[Text]
    overlays: list[Overlay]
    layer_order: Annotated[list[Layer], Field(min_length=1)]
    questions: list[Words]


def _bounds(rect, size, location):
    x, y, width, height = rect
    require(0 <= x and 0 <= y and width > 0 and height > 0
            and x + width <= size[0] and y + height <= size[1],
            "RECT_INVALID", f"{location}: use [x, y, width, height] inside {size}")


def _independent_layers(draft):
    """Attachment metadata owns placement, including for already expanded input."""
    attached = {o.id for o in draft.overlays if o.attachment is not None}
    return [layer for layer in draft.layer_order
            if not (layer.type == "overlay" and layer.id in attached)]


def expanded_layers(draft):
    """Rebuild attached layers once, in overlays array order on each photo side."""
    result = []
    for layer in _independent_layers(draft):
        if layer.type == "slot":
            result.extend({"type": "overlay", "id": o.id} for o in draft.overlays
                          if o.attachment and o.attachment.slot_id == layer.id and o.attachment.position == "below")
        result.append(layer.model_dump(exclude_none=True))
        if layer.type == "slot":
            result.extend({"type": "overlay", "id": o.id} for o in draft.overlays
                          if o.attachment and o.attachment.slot_id == layer.id and o.attachment.position == "above")
    return result


def validate_draft(input_path, reference):
    draft = parse(Draft, read_json(input_path))
    source = load_image(Path(reference))
    items = [*draft.slots, *draft.texts, *draft.overlays]
    ids = [item.id for item in items]
    require(len(ids) == len(set(ids)), "ID_DUPLICATE", "IDs must be unique across slots/texts/overlays")
    slots = {s.id: s for s in draft.slots}
    for item in items:
        _bounds(item.source_rect, source.size, f"{item.id}.source_rect")
    background_id = draft.background.slot_id if isinstance(draft.background, SlotBackground) else None
    if background_id is not None:
        slot = slots.get(background_id)
        require(slot is not None and slot.mode == "photo"
                and slot.source_rect == [0, 0, *source.size],
                "BACKGROUND_INVALID", "Background slot must be an image/photo filling the canvas")
    for overlay in draft.overlays:
        if overlay.attachment:
            parent = slots.get(overlay.attachment.slot_id)
            require(parent is not None and parent.id != background_id,
                    "ATTACHMENT_INVALID", f"{overlay.id}: attach only to an existing non-background image slot")
    expected = {("slot", s.id) for s in draft.slots} | {("text", t.id) for t in draft.texts} | {
        ("overlay", o.id) for o in draft.overlays if o.attachment is None}
    if background_id is None:
        expected.add(("background", None))
    actual = [(layer.type, layer.id) for layer in _independent_layers(draft)]
    require(len(actual) == len(set(actual)), "LAYER_ORDER_INVALID", "layer_order contains duplicates")
    require(set(actual) == expected, "LAYER_ORDER_INVALID",
            "layer_order mismatch; missing=" + str(sorted(expected-set(actual), key=str)) +
            "; unexpected=" + str(sorted(set(actual)-expected, key=str)) +
            ". List every image slot, text and independent overlay exactly once.")
    bottom = ("slot", background_id) if background_id is not None else ("background", None)
    require(actual[0] == bottom, "BACKGROUND_INVALID", "Background must be the bottom layer")
    draft.layer_order = [Layer.model_validate(layer) for layer in expanded_layers(draft)]
    return draft, source


def _result(input_path, reference, draft, source):
    return {
        "ok": True, "draft": str(Path(input_path).resolve()), "draft_sha256": file_hash(input_path),
        "source": {"path": str(Path(reference).resolve()), "sha256": file_hash(reference),
                   "width": source.width, "height": source.height},
        "counts": {"slots": len(draft.slots), "texts": len(draft.texts), "overlays": len(draft.overlays), "questions": len(draft.questions)},
        "expanded_layer_order": expanded_layers(draft),
        "structural_status": "valid", "visual_status": "unreviewed",
    }


def check_draft(input_path, reference):
    draft, source = validate_draft(input_path, reference)
    return _result(input_path, reference, draft, source)


PALETTE = ["#e11d48", "#0891b2", "#7c3aed", "#d97706", "#059669", "#c026d3"]


def numbered_preview(draft, source):
    image = source.copy()
    draw = ImageDraw.Draw(image)
    size = max(12, min(28, round(min(source.size) / 45)))
    font = ImageFont.truetype(str(Path(__file__).resolve().parents[2] / "assets/fonts/DejaVuSans.ttf"), size)
    stroke = max(1, round(min(source.size) / 400))
    for index, item in enumerate([*draft.slots, *draft.texts, *draft.overlays]):
        left, top, width, height = item.source_rect
        color = PALETTE[index % len(PALETTE)]
        draw.rectangle((left, top, left + width - 1, top + height - 1), outline=color, width=stroke)
        extent = draw.textbbox((0, 0), str(index + 1), font=font)
        w, h = extent[2] - extent[0] + 8, extent[3] - extent[1] + 6
        x, y = max(0, min(left, image.width - w)), max(0, min(top, image.height - h))
        draw.rectangle((x, y, min(image.width - 1, x + w), min(image.height - 1, y + h)), fill=color)
        draw.text((x + 4, y + 3 - extent[1]), str(index + 1), font=font, fill="white")
    return image


def preview_draft(input_path, reference, output):
    draft, source = validate_draft(input_path, reference)
    output = Path(output).resolve()
    require(not output.exists(), "OUTPUT_EXISTS", "Use a new review directory; existing previews are preserved")
    result = _result(input_path, reference, draft, source)
    data = {"draft": draft.model_dump(), "source": result["source"], "expanded_layer_order": result["expanded_layer_order"]}
    payload = json.dumps(data, ensure_ascii=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    uri = "data:image/png;base64," + base64.b64encode(png_bytes(source)).decode("ascii")
    # Untrusted JSON goes last, so user text cannot become a template substitution.
    html = HTML.replace("__SOURCE__", uri).replace("__HASH__", result["draft_sha256"]).replace("__PAYLOAD__", payload)
    numbered = png_bytes(numbered_preview(draft, source))
    manifest = {**result, "element_numbers": {str(i + 1): e.id for i, e in enumerate([*draft.slots, *draft.texts, *draft.overlays])}}
    output.mkdir(parents=True, exist_ok=False)
    (output / "numbered.png").write_bytes(numbered)
    (output / "index.html").write_text(html, encoding="utf-8")
    (output / "validation.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**result, "preview": str(output / "index.html"), "numbered": str(output / "numbered.png"),
            "validation": str(output / "validation.json")}


HTML = r"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>拼贴制作草案</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:#172033;font:15px/1.65 system-ui,"Microsoft YaHei",sans-serif}
header{padding:20px 28px;background:white;border-bottom:1px solid #dce2ea}h1{font-size:23px;margin:0 0 5px}p{margin:6px 0}
main{display:grid;grid-template-columns:minmax(320px,1.05fr) minmax(370px,1fr);gap:20px;padding:20px}
figure{margin:0;position:sticky;top:12px;align-self:start;background:#e2e8f0;border-radius:10px;overflow:hidden}
svg{display:block;width:100%;max-height:88vh}aside{min-width:0}section{background:white;padding:17px;margin:0 0 14px;border:1px solid #dce2ea;border-radius:10px}
h2{margin:0 0 10px;font-size:18px}button,select{font:inherit;padding:5px 9px;border:1px solid #cbd5e1;border-radius:5px;background:white;cursor:pointer;max-width:100%;overflow-wrap:anywhere}
button:hover,button[aria-pressed="true"]{background:#e0e7ff}#elements{display:flex;gap:5px;flex-wrap:wrap;max-height:230px;overflow:auto;margin-top:10px}
.muted{color:#5d6a7c;font-size:13px}.badge{display:inline-block;border-radius:4px;background:#fef3c7;padding:2px 8px;font-size:13px}
#detail,#background{white-space:pre-wrap;overflow-wrap:anywhere}#layers{max-height:330px;overflow:auto;padding-left:24px}
#layers li{margin:5px 0}.q{border-left:3px solid #d97706;padding-left:10px;margin:10px 0;overflow-wrap:anywhere}
details{margin-top:10px}code{overflow-wrap:anywhere;font-size:11px}
rect.box{fill:transparent;stroke-width:2;vector-effect:non-scaling-stroke;cursor:pointer}rect.box.active{stroke-width:4;fill:#facc1522}
@media(max-width:840px){main{display:block;padding:10px}figure{position:relative;top:0;margin-bottom:15px}svg{max-height:65vh}}
</style>
<header><h1>拼贴制作草案</h1><span class="badge">结构校验通过 · 视觉待核对</span>
<p>点选图片、文字或装饰，核对原图范围、制作说明及照片归属。这里显示原图标注，尚未重建素材或合成成图。</p><p id="summary" class="muted"></p></header>
<main><figure><svg id="scene" xmlns="http://www.w3.org/2000/svg"></svg></figure><aside>
<section><h2>背景</h2><p id="background"></p></section>
<section><h2>可替换内容与重建元素</h2><label>类型 <select id="kind"><option value="">全部</option><option value="slot">图片</option><option value="text">文字</option><option value="overlay">装饰</option></select></label>
<label><input type="checkbox" id="boxes" checked>显示全部框</label> <button id="reset">清除选中</button>
<div id="elements"></div><hr><div id="detail">选择一个项目查看范围、准确文字或制作说明。照片及附属装饰会一起高亮。</div></section>
<section><h2>图层顺序（从底到顶）</h2><p class="muted">已展开照片下方 / 上方的附属装饰；同侧按装饰列表顺序。</p><ol id="layers"></ol></section>
<section><h2>待确认</h2><div id="questions"></div></section>
<section class="muted">本页与编号图由 draft.json 派生；修改草案后重新生成。范围框不能证明素材边缘、文字或遮挡判断正确。
<details><summary>对应数据</summary>draft.json SHA-256：<code>__HASH__</code></details></section>
</aside></main>
<script type="application/json" id="data">__PAYLOAD__</script>
<script>
const data=JSON.parse(document.getElementById('data').textContent),a=data.draft,$=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
const palette=['#e11d48','#0891b2','#7c3aed','#d97706','#059669','#c026d3'];
const items=[...a.slots.map(e=>({...e,category:'slot'})),...a.texts.map(e=>({...e,category:'text'})),...a.overlays.map(e=>({...e,category:'overlay'}))].map((e,i)=>({...e,n:i+1}));
const byId=new Map(items.map(e=>[e.id,e])),svg=$('scene'),w=data.source.width,h=data.source.height;
svg.setAttribute('viewBox','0 0 '+w+' '+h);svg.setAttribute('role','img');svg.setAttribute('aria-label','参考图及草案定位框');
function node(tag,attrs={},text){const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n}
svg.append(node('image',{href:'__SOURCE__',width:w,height:h}));const layer=node('g');svg.append(layer);
$('summary').textContent=w+' × '+h+' 原图 · '+a.slots.length+' 个图片槽 · '+a.texts.length+' 段文字 · '+a.overlays.length+' 个装饰 · '+a.questions.length+' 个待确认项';
$('background').textContent=(a.background.mode==='slot'?'客户照片满铺：'+byId.get(a.background.slot_id).label:'固定底板：'+a.background.background_brief)+'\n'+a.background.review_notes;
let selected=new Set();
function redraw(){layer.replaceChildren();for(const e of items){if($('kind').value&&e.category!==$('kind').value&&!selected.has(e.id))continue;if(!$('boxes').checked&&!selected.has(e.id))continue;
const[x,y,rw,rh]=e.source_rect,c=palette[(e.n-1)%palette.length],g=node('g'),rect=node('rect',{x,y,width:rw,height:rh,stroke:c,class:'box'+(selected.has(e.id)?' active':'')});
g.append(rect);const fs=Math.max(12,Math.min(28,Math.min(w,h)/45)),tw=(String(e.n).length*.65+1)*fs,lx=Math.max(0,Math.min(x,w-tw)),ly=Math.max(0,Math.min(y,h-fs*1.5));
g.append(node('rect',{x:lx,y:ly,width:tw,height:fs*1.5,fill:c}),node('text',{x:lx+fs*.35,y:ly+fs*1.08,fill:'white','font-size':fs,'font-family':'sans-serif'},String(e.n)));
g.onclick=()=>select(e.id);layer.append(g)}
for(const b of $('elements').children)b.setAttribute('aria-pressed',selected.has(b.dataset.id))}
function select(id){const e=byId.get(id);if(!e)return;const parent=e.attachment?.slot_id??(e.category==='slot'?e.id:null);
selected=new Set([id,...(parent?[parent,...a.overlays.filter(o=>o.attachment?.slot_id===parent).map(o=>o.id)]:[])]);
let text=e.n+'. '+e.label+' ('+e.id+')\n原图 [x,y,宽,高]：'+e.source_rect.join(', ');
if(e.category==='slot'){text+='\n类型：'+({photo:'照片',photo_feather:'柔边照片',cutout:'抠图',unknown:'待确认'}[e.mode])+'\n替换提示：'+e.upload_hint}
else if(e.category==='text'){text+='\n可编辑文字：'+(e.default_text??'未确定')+'\n样式：'+e.style_brief}
else{text+='\n制作方式：'+(e.action==='basic_shape'?'确定性图形':'参考生成独立素材')+'\n归属：'+(e.attachment?e.attachment.slot_id+' 的'+(e.attachment.position==='above'?'上方':'下方'):'独立元素')+'\n'+e.generation_brief;if(e.shape)text+='\n图形：'+JSON.stringify(e.shape);if(e.text_content!==null)text+='\n文字：'+e.text_content;text+='\n内容必须准确：'+(e.requires_exact_content?'是':'否')}
$('detail').textContent=text+'\n复核说明：'+e.review_notes;redraw()}
function buttons(){ $('elements').replaceChildren();for(const e of items){if($('kind').value&&e.category!==$('kind').value)continue;const b=document.createElement('button');b.dataset.id=e.id;b.textContent=e.n+'. '+e.label;b.onclick=()=>select(e.id);$('elements').append(b)}redraw()}
for(const l of data.expanded_layer_order){const li=document.createElement('li');if(l.type==='background')li.textContent='固定底板';else{const e=byId.get(l.id),b=document.createElement('button');b.dataset.id=e.id;b.textContent=e.n+'. '+e.label+(e.attachment?'（附属 '+e.attachment.slot_id+'）':'');b.onclick=()=>select(e.id);li.append(b)}$('layers').append(li)}
for(const q of a.questions){const p=document.createElement('p');p.className='q';p.textContent=q;$('questions').append(p)}
if(!a.questions.length)$('questions').textContent='无待确认项（仍需核对原图）。';
$('kind').onchange=()=>{selected.clear();buttons()};$('boxes').onchange=redraw;$('reset').onclick=()=>{selected.clear();$('detail').textContent='选择一个项目查看详情。';redraw()};buttons();
</script></html>
"""
