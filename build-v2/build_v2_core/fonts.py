"""Explicit font registry; no silent missing-font substitution."""
import os
from functools import lru_cache
from pathlib import Path

from .common import BuildError, sha


def discover(extra_dirs=()):
    roots = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts', Path('/usr/share/fonts/truetype/dejavu')]
    preferred = {'arial.ttf', 'arialbd.ttf', 'times.ttf', 'timesbd.ttf', 'comic.ttf', 'comicbd.ttf', 'segoepr.ttf', 'msyh.ttc', 'simsun.ttc', 'simkai.ttf', 'simhei.ttf', 'DejaVuSans.ttf', 'DejaVuSerif.ttf'}
    found = {}
    for directory in [*roots, *map(Path, extra_dirs)]:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in ('.ttf', '.otf', '.ttc'):
                continue
            if directory in roots and path.name not in preferred:
                continue
            key = path.stem.lower()
            if key in found and found[key]['path'] != str(path.resolve()):
                raise BuildError('font_conflict', f'Duplicate font key: {key}')
            found[key] = {'id': key, 'path': str(path.resolve()), 'sha256': sha(path), 'index': 0}
    if not found:
        raise BuildError('missing_fonts', 'No fonts found; pass --font-dir')
    return found


@lru_cache(maxsize=128)
def _coverage(path, index, digest, mtime_ns, size):
    # Digest and stat are cache identity, never model-provided font names.
    from fontTools.ttLib import TTFont
    with TTFont(path, fontNumber=index, lazy=True) as font:
        return frozenset(code for code, glyph in (font.getBestCmap() or {}).items()
                         if font.getGlyphID(glyph) != 0)


def missing_characters(font_info, text):
    path = Path(font_info['path'])
    stat = path.stat()
    coverage = _coverage(str(path), font_info['index'], font_info['sha256'], stat.st_mtime_ns, stat.st_size)
    return sorted({char for char in text if not char.isspace() and ord(char) not in coverage}, key=ord)


def font_style(font_id):
    key = font_id.lower()
    if key in ('times', 'timesbd', 'simsun', 'dejavuserif'):
        return 'serif'
    if key in ('comic', 'comicbd', 'segoepr', 'simkai'):
        return 'handwritten'
    if key in ('arial', 'arialbd', 'msyh', 'simhei', 'dejavusans'):
        return 'sans'
    return 'unknown'


def resolve_font(text, requested_id, registry):
    if requested_id not in registry:
        raise BuildError('missing_font', f'Unknown requested font: {requested_id}')
    requested = registry[requested_id]
    missing = missing_characters(requested, text)
    decision = {'requested_font_id': requested_id, 'effective_font_id': requested_id,
                'coverage_checked': True, 'missing_characters': missing, 'fallback': False}
    if not missing:
        return requested, decision
    style = font_style(requested_id)
    preferred = {'serif': ['simsun', 'simkai', 'msyh', 'simhei'],
                 'handwritten': ['simkai', 'simsun', 'msyh', 'simhei'],
                 'sans': ['msyh', 'simhei', 'simsun', 'simkai'],
                 'unknown': ['msyh', 'simsun', 'simhei', 'simkai']}[style]
    candidates = sorted((key for key in registry if key != requested_id), key=lambda key: (
        font_style(key) != style, preferred.index(key) if key in preferred else len(preferred), key))
    for key in candidates:
        if not missing_characters(registry[key], text):
            decision.update(effective_font_id=key, fallback=True,
                            reason='Requested font lacks required glyphs; selected an installed font covering the entire text',
                            appearance_approximation=True, requested_style=style, effective_style=font_style(key))
            return registry[key], decision
    codes = ', '.join(f'U+{ord(char):04X}' for char in missing)
    raise BuildError('font_coverage', f'No installed font covers the entire text; {requested_id} is missing {codes}')
