"""Compatibility exports for water profile renderers."""

from __future__ import annotations

from src.lib.i18n.types import LocaleCode

from ..models import WaterProfileCardData
from .full import build_my_water_image
from .shared import build_my_water_text_fallback
from .simple import build_my_water_simple_image


async def build_my_water_fallback_text(
    data: WaterProfileCardData,
    locale: LocaleCode,
) -> str:
    return build_my_water_text_fallback(data, locale)
