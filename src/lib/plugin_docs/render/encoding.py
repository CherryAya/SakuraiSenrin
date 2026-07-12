"""Image encoding helpers for docs renderers."""

from __future__ import annotations

from io import BytesIO
from math import floor

from PIL import Image

WEBP_MAX_DIMENSION = 16383


def encode_docs_image(
    image: Image.Image,
    *,
    webp_quality: int,
    webp_method: int,
) -> bytes:
    buffer = BytesIO()
    rgb = image.convert("RGB")
    if rgb.width > WEBP_MAX_DIMENSION or rgb.height > WEBP_MAX_DIMENSION:
        scale = min(
            WEBP_MAX_DIMENSION / float(rgb.width),
            WEBP_MAX_DIMENSION / float(rgb.height),
        )
        resized = (
            max(1, floor(rgb.width * scale)),
            max(1, floor(rgb.height * scale)),
        )
        rgb = rgb.resize(resized, Image.Resampling.LANCZOS)
    rgb.save(buffer, format="WEBP", quality=webp_quality, method=webp_method)
    return buffer.getvalue()
