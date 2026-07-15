from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

from PIL import Image

_PILLOW_DECODE_LOCK = RLock()


@contextmanager
def pillow_pixel_limit(max_pixels: int) -> Iterator[None]:
    """Apply Pillow's process-wide bomb limit without cross-request races."""
    with _PILLOW_DECODE_LOCK:
        previous_max_pixels = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max(1, int(max_pixels))
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                yield
        finally:
            Image.MAX_IMAGE_PIXELS = previous_max_pixels
