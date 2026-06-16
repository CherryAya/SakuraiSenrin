"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-20 00:26:45
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 14:17:04
Description: 通用工具
"""

from __future__ import annotations

import io
import re
import time
from typing import TYPE_CHECKING

import arrow
import httpx
from PIL import Image, ImageDraw, ImageFont

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.demo_theme import SENRIN_V3_AVATAR_FALLBACK_THEME

if TYPE_CHECKING:
    from datetime import datetime, timedelta


def time_to_timedelta(time_str: str) -> timedelta:
    time_units = {
        "d": 86400,
        "h": 3600,
        "m": 60,
        "s": 1,
    }

    total_seconds = 0
    pattern = r"(\d+)([dhms])"
    matches = re.findall(pattern, time_str)

    for value, unit in matches:
        total_seconds += int(value) * time_units[unit]
    if total_seconds <= 0:
        raise ValueError
    return timedelta(seconds=total_seconds)


def split_list(input_list: list, size: int) -> list[list]:
    return [input_list[i : i + size] for i in range(0, len(input_list), size)]


def get_current_time() -> int:
    return int(time.time())  # noqa: TID251


class AlertTemplate:
    @staticmethod
    def build_exception_notification(
        user_input: str,
        exception_type: str,
        help_command: str,
        timestamp: int | None = None,
    ) -> str:
        """
        构造异常消息模板，用于提示用户输入错误，并提供帮助文档或具体指令。

        :param user_input: 用户的不合预期的输入内容。
        :param exception_type: 错误类型的简短描述。
        :param help_command: 提供给用户的帮助文档指令。
        :param timestamp: 错误发生的时间，默认为当前时间。
        :return: 适配移动端展示的结构化异常消息。
        """
        from src.config import config
        from src.lib.i18n.runtime import tr

        now = arrow.get(timestamp or get_current_time())
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            tr("zh-CN", "alert.exception.header"),
            tr("zh-CN", "alert.exception.type", exception_type=exception_type),
            tr("zh-CN", "alert.exception.time", time=time_str),
            tr("zh-CN", "alert.separator"),
            tr("zh-CN", "alert.exception.input"),
            f"\t{user_input}",
            tr("zh-CN", "alert.separator"),
            tr("zh-CN", "alert.exception.guide"),
            tr("zh-CN", "alert.exception.help", help_command=help_command),
            tr("zh-CN", "alert.exception.feedback", group_id=config.MAIN_GROUP_ID),
            tr("zh-CN", "alert.footer"),
        ]
        return "\n".join(lines)

    @staticmethod
    def build_tip_notification(
        event_name: str | None,
        event_details: str | None,
        timestamp: datetime | None = None,
    ) -> str:
        """
        构造通知消息模板，用于发送给管理员。

        :param event_name: 事件的名称，例如 "用户登录失败"。
        :param event_details: 事件的详细信息，例如 "用户尝试登录 3 次失败"。
        :param timestamp: 事件发生的时间，默认为当前时间。
        :return: 格式化的 Message 对象。
        """
        from src.lib.i18n.runtime import tr

        now = now = arrow.get(timestamp or get_current_time())
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        name = event_name or tr("zh-CN", "alert.tip.default_event_name")

        header = tr("zh-CN", "alert.tip.header") + "\n"
        body = (
            tr("zh-CN", "alert.tip.name", event_name=name)
            + "\n"
            + tr("zh-CN", "alert.tip.time", time=time_str)
            + "\n"
            + tr("zh-CN", "alert.separator")
            + "\n"
            + tr("zh-CN", "alert.tip.details")
            + "\n"
        )

        details_str = str(event_details or tr("zh-CN", "alert.tip.empty_details"))
        indented_details = "\n".join([f"\t{line}" for line in details_str.split("\n")])

        footer = "\n" + tr("zh-CN", "alert.footer")

        return header + body + indented_details + footer


class AvatarFetcher:
    """
    异步头像获取与处理工具

    Note: Gemini 写的，AI 神力！

    有股味我也懒得改了你就说他能不能用，能用的代码就是好代码对吧！
    """

    @staticmethod
    def create_default_avatar(
        size: int,
        text: str | None = None,
        bg_color: tuple = SENRIN_V3_AVATAR_FALLBACK_THEME.bg_color,
    ) -> Image.Image:
        """生成默认头像"""
        from src.lib.i18n.runtime import tr

        resolved_text = text or tr("zh-CN", "avatar.default.group")
        img = Image.new("RGBA", (size, size), bg_color)
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(MAPLE_FONT_PATH, int(size * 0.5))
            bbox = draw.textbbox((0, 0), resolved_text, font=font)
            text_x = (size - (bbox[2] - bbox[0])) / 2
            text_y = (size - (bbox[3] - bbox[1])) / 2 - (size * 0.1)
            draw.text(
                (text_x, text_y),
                resolved_text,
                fill=SENRIN_V3_AVATAR_FALLBACK_THEME.text_color,
                font=font,
            )
        except OSError:
            pass
        return img

    @staticmethod
    def apply_circle_mask(img: Image.Image) -> Image.Image:
        """将正方形图像裁剪为正圆形（用于用户头像）"""
        size = min(img.size)
        img = img.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
        mask = Image.new("L", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, size, size), fill=255)
        img.putalpha(mask)
        return img

    @staticmethod
    def apply_rounded_mask(img: Image.Image, radius: int) -> Image.Image:
        """将正方形图像裁剪为圆角矩形（用于群头像）"""
        size = min(img.size)
        img = img.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
        mask = Image.new("L", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
        img.putalpha(mask)
        return img

    @classmethod
    async def fetch(
        cls, client: httpx.AsyncClient, url: str, size: int, is_user: bool = False
    ) -> Image.Image:
        try:
            resp = await client.get(url, timeout=5.0)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content)).convert("RGBA")

            if is_user:
                return cls.apply_circle_mask(img)
            else:
                return cls.apply_rounded_mask(img, radius=int(size * 0.15))
        except Exception:
            from src.lib.i18n.runtime import tr

            default_text = (
                tr("zh-CN", "avatar.default.user")
                if is_user
                else tr("zh-CN", "avatar.default.group")
            )
            img = cls.create_default_avatar(size, default_text)
            if is_user:
                return cls.apply_circle_mask(img)
            else:
                return cls.apply_rounded_mask(img, radius=int(size * 0.15))
