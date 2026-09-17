"""Read static-reference metadata or save a lossless crop; no model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from collage_recreate.core import ToolError, file_hash, load_image, png_bytes, require


def inspect_reference(source, *, crop=None, output=None):
    source = Path(source).resolve()
    require(source.is_file(), "FILE_MISSING", "Reference file does not exist")
    image = load_image(source)
    width, height = image.size
    result = {
        "ok": True, "source": str(source), "source_sha256": file_hash(source),
        "source_size": [width, height],
        "alpha_range": list(image.getchannel("A").getextrema()),
    }
    if crop is None:
        require(output is None, "OUTPUT_WITHOUT_CROP", "--output requires --crop")
        return result
    require(
        len(crop) == 4 and all(type(value) is int for value in crop),
        "CROP_INVALID", "Crop must contain four integer pixel coordinates",
    )
    left, top, right, bottom = crop
    require(
        0 <= left < right <= width and 0 <= top < bottom <= height,
        "CROP_INVALID", "Crop must be inside the oriented source: left < right, top < bottom",
    )
    require(output is not None, "OUTPUT_REQUIRED", "Crop requires a new .png output path")
    output = Path(output).resolve()
    require(output.suffix.lower() == ".png", "OUTPUT_INVALID", "Crop output must end with .png")
    require(output != source, "SOURCE_OVERWRITE", "Cannot overwrite the source")
    require(not output.exists(), "OUTPUT_EXISTS", "Choose a new output file; existing files are preserved")
    content = png_bytes(image.crop((left, top, right, bottom)))
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        raise ToolError("OUTPUT_EXISTS", "Choose a new output file; existing files are preserved") from None
    result.update({
        "crop_pixels": [left, top, right, bottom],
        "crop_normalized": [left / width, top / height, right / width, bottom / height],
        "output": str(output), "output_size": [right - left, bottom - top],
        "output_sha256": file_hash(output),
    })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "--reference", dest="input", required=True, help="Static source image; read only")
    parser.add_argument("--crop", nargs=4, type=int, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    parser.add_argument("--output", help="New PNG path, required with --crop")
    args = parser.parse_args(argv)
    try:
        result = inspect_reference(args.input, crop=args.crop, output=args.output)
    except ToolError as exc:
        result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except OSError:
        result = {"ok": False, "error": {"code": "FILE_IO", "message": "Cannot read or write the requested path"}}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
