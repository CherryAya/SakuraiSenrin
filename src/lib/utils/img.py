"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-25 13:25:55
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-25 22:21:41
Description: 图像工具类，AI 神力！
"""

import io

import httpx
from PIL import Image, ImageDraw, ImageFont
from pil_utils import BuildImage

from src.lib.consts import MAPLE_FONT_PATH


class QQAvatar:
    _shared_client: httpx.AsyncClient | None = None

    @classmethod
    def _get_shared_client(cls) -> httpx.AsyncClient:
        if cls._shared_client is None or cls._shared_client.is_closed:
            cls._shared_client = httpx.AsyncClient(timeout=5.0)
        return cls._shared_client

    @classmethod
    async def fetch_user(cls, uid: str, size: int = 100) -> BuildImage:
        s_param = 640 if size > 100 else 100
        url = f"https://q1.qlogo.cn/g?b=qq&nk={uid}&s={s_param}"
        return await cls._fetch(url, size, "人")

    @classmethod
    async def _fetch(cls, url: str, size: int, fallback: str) -> BuildImage:
        client = cls._get_shared_client()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            img = BuildImage.open(io.BytesIO(resp.content)).convert("RGBA")
        except Exception:
            img = cls._generate_fallback(size, fallback)
        return img.resize((size, size))

    @staticmethod
    def _generate_fallback(size: int, text: str) -> BuildImage:
        img = Image.new("RGBA", (size, size), (255, 225, 230))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(MAPLE_FONT_PATH, int(size * 0.5))
        except OSError:
            font = ImageFont.load_default()
        draw.text(
            (size / 2, size / 2 - size * 0.05),
            text,
            fill=(180, 76, 76),
            font=font,
            anchor="mm",
        )
        return BuildImage(img)
