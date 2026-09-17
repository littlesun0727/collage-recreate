"""A deterministic, fictional fixture for exercising the public workflow."""
from pathlib import Path

from PIL import Image, ImageDraw

from .core import apply_patch, initialize, require, write_json
from .render import render


def demo(task):
    require(not task.root.exists(), "TASK_EXISTS", "Choose a new directory for the demo")
    inputs = task.root / "private" / "demo-inputs"
    inputs.mkdir(parents=True)
    photo = Image.new("RGB", (400, 240), "#c5dbdb")
    draw = ImageDraw.Draw(photo)
    draw.polygon([(0, 200), (110, 80), (200, 190), (300, 50), (400, 200)], fill="#467477")
    draw.rectangle((0, 200, 400, 240), fill="#b5be78")
    photo.save(inputs / "landscape.png")
    reference = Image.new("RGB", (640, 480), "#f5f1e7")
    reference.paste(photo.resize((300, 180)), (300, 170))
    reference.save(inputs / "reference.png")
    frame = Image.new("RGBA", (280, 250))
    ImageDraw.Draw(frame).rectangle((4, 4, 275, 245), outline="#b25b47", width=8)
    frame.save(inputs / "frame.png")
    root = Path(__file__).resolve().parents[2]
    request = {"canvas": {"width": 640, "height": 480, "background": "#f5f1e7"},
               "reference": "reference.png",
               "assets": {"photo": {"path": "landscape.png"}, "frame": {"path": "frame.png"},
                          "font": {"kind": "font", "path": str(root / "assets/fonts/DejaVuSans.ttf"),
                                   "redistributable": True, "license": str(root / "assets/fonts/LICENSE_DEJAVU")}},
               "texts": {"title": "A quiet\nafternoon", "label": "KEEP THIS MOMENT", "note": "Static. Layered. Editable."}}
    write_json(inputs / "request.json", request)
    initialize(task, inputs / "request.json")
    patch = {"expected_revision": 1, "groups": [{"id": "card"}], "elements": [
        {"id": "title", "type": "text", "text": "title", "font": "font", "font_size": 42,
         "x": 38, "y": 45, "width": 480, "height": 140, "color": "#314f51"},
        {"id": "photo", "type": "image", "asset": "photo", "x": 320, "y": 190, "width": 260, "height": 200, "group": "card"},
        {"id": "frame", "type": "image", "asset": "frame", "x": 310, "y": 180, "width": 280, "height": 250, "group": "card"},
        {"id": "label", "type": "text", "text": "label", "font": "font", "font_size": 15,
         "x": 327, "y": 402, "width": 250, "height": 20, "group": "card", "color": "#7d493d"},
        {"id": "note", "type": "text", "text": "note", "font": "font", "font_size": 15,
         "x": 38, "y": 425, "width": 270, "height": 30, "color": "#314f51"}
    ]}
    write_json(inputs / "layout.json", patch)
    apply_patch(task, inputs / "layout.json")
    return render(task)
