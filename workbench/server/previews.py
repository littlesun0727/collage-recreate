"""Disposable, bounded preview cache; source images are never modified."""
import io
import os
from pathlib import Path
import tempfile
import threading
import time

from PIL import Image, ImageOps


class PreviewCache:
    SIZES = {160, 480, 1280}

    def __init__(self, root, max_bytes=512 * 1024 * 1024):
        self.root = Path(root)
        self.max_bytes = max_bytes
        # Bound simultaneous decoding of large source images and deduplicate work.
        self._lock = threading.Lock()
        self._last_prune = 0

    def get(self, token, source, crop, size):
        if size not in self.SIZES:
            raise ValueError('Unsupported preview size')
        target = self.root / f'{token}-{size}-v1.webp'
        # Already generated previews must not queue behind an unrelated large image.
        try:
            return target.read_bytes()
        except OSError:
            pass
        with self._lock:
            try:
                return target.read_bytes()
            except OSError:
                pass
            with Image.open(source) as original:
                image = ImageOps.exif_transpose(original)
                if crop:
                    image = image.crop(crop)
                image.thumbnail((size, size), Image.Resampling.LANCZOS)
                image = image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
                output = io.BytesIO()
                image.save(output, 'WEBP', quality=78, method=4)
                body = output.getvalue()
            temporary = None
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=self.root, suffix='.tmp', delete=False) as stream:
                    temporary = stream.name
                    stream.write(body)
                os.replace(temporary, target)
                self._prune()
            except OSError:
                # A full/read-only cache must not make an otherwise valid image fail.
                pass
            finally:
                if temporary:
                    try:
                        Path(temporary).unlink(missing_ok=True)
                    except OSError:
                        pass
            return body

    def _prune(self):
        now = time.monotonic()
        if now - self._last_prune < 60:
            return
        self._last_prune = now
        entries = [(p.stat().st_mtime_ns, p.stat().st_size, p) for p in self.root.glob('*.webp')]
        total = sum(size for _, size, _ in entries)
        for _, size, path in sorted(entries):
            if total <= self.max_bytes:
                break
            path.unlink(missing_ok=True)
            total -= size
