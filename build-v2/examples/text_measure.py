"""Inspect measured sizes rather than deriving every font size from box height."""
from build_v2_core.script_api import render_text


def compare(size, wording, font, requested_sizes, color):
    return [render_text(size,wording,font,font_size_px=px,color=color) for px in requested_sizes]
