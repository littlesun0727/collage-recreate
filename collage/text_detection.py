"""Local text detection for validating a cleaned crop, without recognizing its text."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image

workers = ThreadPoolExecutor(max_workers=5, thread_name_prefix="text-detection")
_session = None
_lock = Lock()


def session():
    global _session
    with _lock:
        if _session is None:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            model = Path(__file__).resolve().parents[1] / "models/ppocr-det.onnx"
            _session = ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])
        return _session


def detect(image):
    """Return axis-aligned text boxes in crop coordinates."""
    w, h = image.size
    scale = min(3, 960 / max(w, h))
    size = tuple(max(32, round(n * scale / 32) * 32) for n in (w, h))
    rgb = np.asarray(image.convert("RGB").resize(size, Image.Resampling.BILINEAR))
    bgr = rgb[:, :, ::-1].astype(np.float32) / 255
    x = (bgr - np.float32([.485, .456, .406])) / np.float32([.229, .224, .225])
    model = session()
    prob = model.run(None, {model.get_inputs()[0].name: np.ascontiguousarray(x.transpose(2, 0, 1)[None])})[0].squeeze()
    contours, _ = cv2.findContours((prob > .3).astype("uint8"), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        center, (cw, ch), angle = cv2.minAreaRect(contour)
        if min(cw, ch) < 3:
            continue
        poly = cv2.boxPoints((center, (cw, ch), angle))
        x, y, rw, rh = cv2.boundingRect(contour)
        mask = np.zeros((rh, rw), np.uint8)
        cv2.fillPoly(mask, [np.int32(poly - [x, y])], 1)
        if cv2.mean(prob[y:y+rh, x:x+rw], mask=mask)[0] < .6:
            continue
        pad = .75 * cw * ch / (cw + ch)
        poly = cv2.boxPoints((center, (cw + 2*pad, ch + 2*pad), angle))
        poly *= [w / prob.shape[1], h / prob.shape[0]]
        boxes.append([max(0, int(np.floor(poly[:, 0].min()))), max(0, int(np.floor(poly[:, 1].min()))),
                      min(w, int(np.ceil(poly[:, 0].max()))), min(h, int(np.ceil(poly[:, 1].max())))])
    return sorted(boxes, key=lambda b: (b[1], b[0]))


def residual_strokes(reference, cleaned, boxes):
    """Flag a legible ghost that no longer produces a detector box."""
    def detail(im):
        gray = np.asarray(im.convert("L"), dtype=np.float32)
        return gray - cv2.GaussianBlur(gray, (0, 0), 2)
    ref, base = detail(reference), detail(cleaned)
    remaining = []
    for box in boxes:
        l, t, r, b = box
        a, z = ref[t:b, l:r], base[t:b, l:r]
        if a.size < 64 or a.std() < 5 or z.std() < max(4, .35 * a.std()):
            continue
        score = cv2.matchTemplate(z, a, cv2.TM_CCOEFF_NORMED)[0, 0]
        if score >= .55:
            remaining.append(box)
    return remaining


def matches(a, b):
    """Allow split/merged words on the same line, but reject incidental intersections."""
    dx = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    dy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    ah, bh = a[3]-a[1], b[3]-b[1]
    return (dx > 0 and dy > 0 and dx >= .5 * min(a[2]-a[0], b[2]-b[0])
            and dy >= .55 * max(ah, bh))

