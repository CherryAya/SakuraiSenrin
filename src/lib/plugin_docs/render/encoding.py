"""Image encoding helpers for docs renderers."""

from __future__ import annotations

from io import BytesIO

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
    if rgb.width <= WEBP_MAX_DIMENSION and rgb.height <= WEBP_MAX_DIMENSION:
        rgb.save(buffer, format="WEBP", quality=webp_quality, method=webp_method)
        return buffer.getvalue()
    rgb.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
