"""Extract fixed PNGs; customer slots provide removal context only."""
import os
import math
from PIL import Image
from .layout.windows import tilted_envelope
from .lettering import finish_text_layers
from .object_roles import is_customer_photo
from .reveal_transport import fetch_batch, reserve_submission
from .storage import read, save


def request_object(target, size, padding=.1, padding_mode='capped'):
    """Expand an original bbox, enforce the API minimum, then clamp to the reference."""
    if not math.isfinite(padding) or not 0 <= padding <= 1:
        raise ValueError('Reveal padding must be a finite fraction in 0..1')
    if padding_mode not in ['capped','ratio']:
        raise ValueError('Unknown Reveal padding mode')
    l,t,r,b=target['bbox'];w,h=size
    if padding_mode=='ratio':
        px=(r-l)*padding;py=(b-t)*padding
    else:
        scale=min(1,1024/max(w,h))
        edge=min(padding*min(r-l,b-t),6/scale)
        px=py=edge
    box=[max(0,math.floor(l-px)),max(0,math.floor(t-py)),
         min(w,math.ceil(r+px)),min(h,math.ceil(b+py))]
    minimum=math.ceil(8*max(w,h)/1024)
    for axis,limit in [(0,w),(1,h)]:
        span=min(limit,minimum)
        if box[axis+2]-box[axis]<span:
            box[axis]=max(0,min(limit-span,math.floor((box[axis]+box[axis+2]-span)/2)))
            box[axis+2]=box[axis]+span
    result={'id':target['id'],'bbox':box,'original_bbox':list(target['bbox']),'padding':padding}
    if padding_mode!='ratio':result['padding_mode']=padding_mode
    return result


def layer_image(task, index, size, alpha):
    for variant in ("aug", "base"):
        path = task / f"layers_{variant}_{index:02d}.png"
        if not path.exists():
            continue
        with Image.open(path) as im:
            if im.format != "PNG" or (alpha and "A" not in im.getbands()):
                continue
            if abs(im.width / im.height / (size[0] / size[1]) - 1) > .02:
                raise ValueError("Reveal returned a layer with an incompatible canvas")
            image = im.convert("RGBA" if alpha else "RGB").resize(size, Image.Resampling.LANCZOS)
        if not alpha or image.getchannel("A").getbbox():
            return image
    raise ValueError(f"Missing usable Reveal layer {index}")


def extract(run, analysis):
    size = tuple(analysis["reference_size"])
    by_id = {obj["id"]: obj for obj in analysis["objects"]}
    bg = by_id[analysis["layer_order"][0]]
    clean = bg.get("background_type") == "reference_clean"
    layers = [by_id[i] for i in analysis["layer_order"]]
    ordered = [o for o in reversed(layers[1:]) if o["kind"] != "text"]
    fixed = {o["id"] for o in ordered if not is_customer_photo(o)}
    # Remove original photographic content when uncovering a clean base or fixed layers beneath it.
    targets = [o for o in ordered if not o.get("linked_to")] if clean or fixed else []
    folder = run/"fixed"
    folder.mkdir()
    # Use numeric filenames independent of model IDs and platform-reserved names.
    files = {}
    def store(oid, image):
        path = folder / f"layer_{len(files):03d}.png"
        image.save(path)
        files[oid] = path

    current = run/"prepared/reference.png"
    root = run/"reveal"
    save(root/"submission-budget.json", {"limit": max(1, (len(targets)+19)//20)*3})
    for start in range(0, len(targets), 20):
        group = targets[start:start+20]
        boxes = []
        for o in group:
            l,t,r,b = tilted_envelope(o["bbox"], o.get("rotation", 0))
            box = [max(0,int(l)), max(0,int(t)), min(size[0],math.ceil(r)), min(size[1],math.ceil(b))]
            boxes.append(request_object({"id": o["id"], "bbox": box}, size))
        task = root/f"batch_{start//20:02d}"
        reserve_submission(task, root)
        fetch_batch(task, current, boxes, os.environ["360_API_KEY"])
        result = read(task/"query_response.json")["output"]
        indices = result["boxes_mapping_index"]
        # A partial mapping is usable. Output PNG numbers follow the returned order.
        if (not isinstance(indices, list)
                or any(type(i) is not int or not 0 <= i < len(group) for i in indices)
                or len(set(indices)) != len(indices)):
            raise ValueError("Invalid Reveal layer mapping")
        for index, pos in enumerate(indices, 1):
            oid = group[pos]["id"]
            if oid in fixed:
                store(oid, layer_image(task, index, size, True))
        if clean or start + 20 < len(targets):
            current = task/"residual.png"
            layer_image(task, 0, size, False).save(current)
    if clean:
        with Image.open(current) as im:
            store(bg["id"], im.convert("RGBA"))
    elif not is_customer_photo(bg):
        store(bg["id"], Image.new("RGBA", size, bg["style"]["fill"]))
    visible_layers = [o for o in layers if o["id"] not in fixed or o["id"] in files]
    decisions = finish_text_layers(run, visible_layers, files, store, size)
    if decisions:
        save(run/"lettering.json", decisions)
    return files
