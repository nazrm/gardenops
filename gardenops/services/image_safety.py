from __future__ import annotations

import warnings
from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import partial
from threading import RLock
from typing import Any

from anyio import CapacityLimiter, to_thread
from PIL import Image

_PILLOW_DECODE_LOCK = RLock()
_IMAGE_PROCESSING_LIMITER = CapacityLimiter(2)


async def process_image_in_thread[T](func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    return await to_thread.run_sync(
        partial(func, *args, **kwargs), limiter=_IMAGE_PROCESSING_LIMITER
    )


@contextmanager
def pillow_pixel_limit(max_pixels: int) -> Generator[None]:
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
