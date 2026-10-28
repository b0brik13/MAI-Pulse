"""Aspect-ratio safe display measurements for media in MAI Pulse."""
from __future__ import annotations

import io
from PIL import Image, ImageOps


def image_size(data: bytes) -> tuple[int, int]:
    """Read natural pixels, respecting EXIF orientation (without modifying image)."""
    with Image.open(io.BytesIO(data)) as original:
        oriented = ImageOps.exif_transpose(original)
        return oriented.size


def fit_inside(width: int, height: int, box_width: int, box_height: int) -> tuple[int, int]:
    """Fit complete image in rectangle; never crop or alter its aspect ratio."""
    if min(width, height, box_width, box_height) <= 0:
        raise ValueError('Размеры изображения должны быть положительными.')
    factor = min(box_width / width, box_height / height)
    return max(1, round(width * factor)), max(1, round(height * factor))


def feed_height(image_width: int, image_height: int, *, display_width: int = 676, max_height: int = 950) -> int:
    """Height of aspect-ratio-preserving feed image, capped for long portraits."""
    if min(image_width, image_height, display_width, max_height) <= 0:
        raise ValueError('Размеры изображения должны быть положительными.')
    return max(70, min(max_height, round(display_width * image_height / image_width)))
