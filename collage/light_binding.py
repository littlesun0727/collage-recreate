"""Contact-sheet classification and deterministic photo slot assignment."""
import math

from jsonschema import Draft202012Validator
from PIL import Image, ImageDraw, ImageFont

from . import binding, contracts
from .storage import save
from .timings import log


def classify(run, catalog):
    from .vlm import ask
    sheets = []
    folder = run/"contact-sheets"
    folder.mkdir(exist_ok=True)
    for start in range(0, len(catalog), 9):
        group = catalog[start:start+9]
        cols = math.ceil(math.sqrt(len(group)))
        sheet = Image.new("RGB", (320*cols, 350*math.ceil(len(group)/cols)), "white")
        draw = ImageDraw.Draw(sheet)
        for index, asset in enumerate(group):
            x, y = index%cols*320, index//cols*350
            with Image.open(run/"prepared"/asset["binding_preview"]) as image:
                image.thumbnail((320,320), Image.Resampling.LANCZOS)
                sheet.paste(image,(x+(320-image.width)//2,y+(320-image.height)//2))
            draw.text((x+8,y+324),asset["id"],font=ImageFont.load_default(size=18),fill="black")
        path = folder/f"sheet-{len(sheets)+1:02d}.jpg"
        sheet.save(path,quality=88)
        sheets.append((path.stem,path))
    ids = [asset["id"] for asset in catalog]
    item = contracts.obj({"id":{"enum":ids},"category":{"enum":contracts.SUBJECT_TYPES},
                          "cutout_ok":{"type":"boolean"},
                          "focus_point":{"type":"array","minItems":2,"maxItems":2,
                                         "items":{"type":"number","minimum":0,"maximum":1}}})
    schema = contracts.obj({"materials":{"type":"array","items":item,"minItems":len(ids),"maxItems":len(ids)}})
    rows = []
    if ids:
        answer = ask("classify", {"ids":ids}, sheets, schema)
        Draft202012Validator(schema).validate(answer)
        rows = answer["materials"]
        if {r["id"] for r in rows} != set(ids):
            raise ValueError("素材分类编号重复或遗漏")
    save(run/"material-classification.json", {"materials":rows}, indent=2)
    by_id = {row["id"]:row for row in rows}
    return [{**asset,"classification":by_id[asset["id"]]} for asset in catalog]


def crop_center(size, aspect, focus):
    # Fraction of the full source retained on each axis by aspect-cover.
    ratio = size[0]/size[1]
    fractions = (min(1,aspect/ratio), min(1,ratio/aspect))
    return [round(max(0,min(1,(f-span/2)/(1-span))),5) if span < 1-1e-9 else .5
            for f,span in zip(focus,fractions)]


def match(analysis, catalog, on_cutout=None):
    ctx = binding.context_for({"catalog":catalog,"reference_size":analysis["reference_size"]}, analysis)
    slots = ctx["photo_slots"]
    if not slots:
        return []
    binding.schema_for(ctx, analysis, allow_reuse=True)
    objects = {o["id"]:o for o in analysis["objects"]}
    uses = {}
    chosen = {}
    for need_cutout in (True,False):
        waiting = [s for s in slots if not s.get("linked_to") and s["requires_segmentable_subject"] == need_cutout]
        if waiting and need_cutout and not any(a["classification"]["cutout_ok"] for a in catalog):
            raise ValueError("客户素材中没有可抠主体，需要抠像的槽位：" + ", ".join(s["slot_id"] for s in waiting))
        while waiting:
            def rank(pair):
                slot, asset = pair
                info = asset["classification"]
                ratio = asset["size"][0]/asset["size"][1]
                wanted = objects[slot["slot_id"]].get("subject_type","other")
                order = list(dict.fromkeys([wanted, "person", "animal", "scenery", "food", "other"]))
                preference = order.index(info["category"])
                use_count = uses.get(asset.get("sha256",asset["id"]),0)
                return (bool(use_count), need_cutout and not info["cutout_ok"], preference, use_count,
                        abs(math.log(ratio/slot["aspect_ratio"])))
            slot, asset = min(((s,a) for s in waiting for a in catalog),key=rank)
            oid = slot["slot_id"]
            info = asset["classification"]
            center = crop_center(asset["size"],slot["aspect_ratio"],info["focus_point"])
            independent_cutout = objects[oid].get("mode") == "cutout"
            wanted = objects[oid].get("subject_type","other")
            reason = f"槽位={wanted} 素材={info['category']} 需要抠像={need_cutout} 可抠={info['cutout_ok']} 焦点={info['focus_point']}"
            chosen[oid] = {"slot_id":oid,"asset_id":asset["id"],"reason":reason,
                           "source_crop":[0,0,1,1],"crop_center":[.5,.5] if independent_cutout else center,"mirror_x":False}
            if need_cutout and on_cutout is not None:
                on_cutout(asset)
            waiting.remove(slot)
            key = asset.get("sha256",asset["id"])
            uses[key] = uses.get(key,0) + 1
    for slot in slots:
        if slot.get("linked_to"):
            base = chosen[slot["linked_to"]]
            chosen[slot["slot_id"]] = {**base,"slot_id":slot["slot_id"],"reason":"复用底图 "+slot["linked_to"]}
    rows = binding.validate({"photos":[chosen[s["slot_id"]] for s in slots]},ctx,analysis,catalog,allow_reuse=True)["photos"]
    for row in rows:
        log(row["slot_id"]+" "+row["reason"])
    return rows
