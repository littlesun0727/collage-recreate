"""Extract fixed PNGs; customer slots provide removal context only."""
import os
import math
from . import background
from PIL import Image
from .frame_links import carrier_photos
from .layout.window_shapes import window_outline
from .layout.windows import occluding_outlines, tilted_envelope
from .timings import log
from .lettering import finish_text_layers
from .object_roles import is_customer_photo
from .reveal_transport import fetch_batch, reserve_submission
from .reveal_order import apply_batch_order
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
        try:
            with Image.open(path) as im:
                if im.format != "PNG" or (alpha and "A" not in im.getbands()):
                    continue
                if abs(im.width / im.height / (size[0] / size[1]) - 1) > .02:
                    continue
                image = im.convert("RGBA" if alpha else "RGB").resize(size, Image.Resampling.LANCZOS)
        except OSError as exc:
            log(f"读取 {path.name} 失败，尝试备用版本：{exc}")
            continue
        if not alpha or image.getchannel("A").getbbox():
            return image
    raise ValueError(f"Missing usable Reveal layer {index}")


def reveal_targets(layers):
    """Walk bottom to top for occlusion, then submit in removal order."""
    framed = {p["id"]: frame["id"] for frame in layers if frame["kind"] == "frame"
              for p in carrier_photos(frame, layers)}
    targets = []
    lower_fixed = []
    for obj in layers[1:]:
        if (obj["kind"] == "text" and (obj.get("text") or "").strip()) or obj.get("linked_to"):
            continue
        if is_customer_photo(obj):
            reason = None
            if obj["id"] in framed:
                reason = f"关联边框 {framed[obj['id']]}"
            elif lower_fixed:
                contour = window_outline(obj)
                if contour is None:  # Independent cutout: only its placement box is known.
                    contour = window_outline({"bbox": obj["bbox"], "rotation": obj.get("rotation", 0)})
                covered = [oid for oid, outline in lower_fixed
                           if occluding_outlines(outline, [contour], min_share=0)]
                if covered:
                    reason = f"与下方固定层相交 {', '.join(covered)}"
            if reason is None:
                log(f"360 跳过照片 {obj['id']}，无关联边框或下方固定层遮挡")
                continue
            log(f"360 保留照片辅助框 {obj['id']}，{reason}")
        else:
            outline = window_outline({"bbox": obj["bbox"], "rotation": obj.get("rotation", 0)})
            lower_fixed.append((obj["id"], outline))
        targets.append(obj)
    return targets[::-1]


def extract(run, analysis, clean_background=None):
    size = tuple(analysis["reference_size"])
    by_id = {obj["id"]: obj for obj in analysis["objects"]}
    bg = by_id[analysis["layer_order"][0]]
    clean = bg.get("background_type") == "reference_clean"
    layers = [by_id[i] for i in analysis["layer_order"]]
    targets = reveal_targets(layers)
    fixed = {o["id"] for o in targets if not is_customer_photo(o)}
    log(f"360 提取框 {len(targets)} 个，待提取固定元素 {len(fixed)} 个")
    folder = run/"fixed"
    folder.mkdir()
    # Use numeric filenames independent of model IDs and platform-reserved names.
    files = {}
    dropped = set()
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
        log(f"{task.name} 构造请求框，元素顺序 {[obj['id'] for obj in group]}")
        reserve_submission(task, root)
        skipped = set(fetch_batch(task, current, boxes, os.environ["360_API_KEY"]) or ())
        result = read(task/"query_response.json")["output"]
        indices = result["boxes_mapping_index"]
        # A partial mapping is usable. Output PNG numbers follow the returned order.
        if (not isinstance(indices, list)
                or any(type(i) is not int or not 0 <= i < len(group) for i in indices)
                or len(set(indices)) != len(indices)):
            raise ValueError("Invalid Reveal layer mapping")
        for index, pos in enumerate(indices, 1):
            oid = group[pos]["id"]
            if index in skipped:
                if oid in fixed:
                    dropped.add(oid)
                    log(f"{task.name} 下载失败，舍弃固定层 {oid}")
                continue
            if oid in fixed:
                try:
                    image = layer_image(task, index, size, True)
                except ValueError:
                    if group[pos]["kind"] == "text":
                        continue  # Preserve reference pixels in finish_text_layers.
                    raise
                if group[pos]["kind"] == "text":
                    box = tuple(group[pos]["bbox"])
                    patch = image.crop(box)
                    if not patch.getchannel("A").getbbox():
                        continue
                    image = Image.new("RGBA", size)
                    image.paste(patch, box)
                store(oid, image)
                log(f"{task.name} 返回层 {index} → {oid} → {files[oid].name}")
        # The mapping is authoritative even for customer slots whose PNG is unused.
        returned = [group[pos]["id"] for pos in indices]
        apply_batch_order(layers, returned)
        log(f"{task.name} 按 360 返回层从底到顶排列：{returned}")
        if start + 20 < len(targets):
            if 0 in skipped:
                log(f"{task.name} 缺少后续拆层所需的残余图，保留已有结果并跳过剩余批次")
                break
            current = task/"residual.png"
            layer_image(task, 0, size, False).save(current)
    if clean:
        log("360 提取结束，读取 reference_clean 结果")
        # Join after extraction so 360 and background generation run concurrently.
        path = clean_background.result() if clean_background is not None else background.clean(run, bg, size)
        with Image.open(path) as im:
            store(bg["id"], im.convert("RGBA"))
    elif not is_customer_photo(bg):
        store(bg["id"], Image.new("RGBA", size, bg["style"]["fill"]))
    layers = [o for o in layers if o["id"] not in dropped]
    order = [o["id"] for o in layers]
    save(root/"scene-order.json", order)
    visible_layers = [o for o in layers if o["kind"] == "text" or o["id"] not in fixed or o["id"] in files]
    if fixed - files.keys():
        log(f"360 未返回固定层 {sorted(fixed - files.keys())}")
    log("开始文字重叠检查、擦除与独立文字重绘")
    decisions = finish_text_layers(run, visible_layers, files, store, size)
    save(run/"lettering.json", decisions, indent=2)
    return files
