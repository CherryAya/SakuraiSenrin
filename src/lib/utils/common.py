"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-20 00:26:45
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-25 01:57:37
Description: 通用工具
"""

from datetime import datetime, timedelta
import io
import re

import httpx
from PIL import Image, ImageDraw, ImageFont

from src.config import config
from src.lib.consts import MAPLE_FONT_PATH


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


class AlertTemplate:
    @staticmethod
    def build_exception_notification(
        user_input: str,
        exception_type: str,
        help_command: str,
        timestamp: datetime | None = None,
    ) -> str:
        """
        构造异常消息模板，用于提示用户输入错误，并提供帮助文档或具体指令。

        :param user_input: 用户的不合预期的输入内容。
        :param exception_type: 错误类型的简短描述。
        :param help_command: 提供给用户的帮助文档指令。
        :param timestamp: 错误发生的时间，默认为当前时间。
        :return: 适配移动端展示的结构化异常消息。
        """
        now = timestamp or datetime.now()
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            "━━━━━━ 输入错误 ━━━━━━",
            f"错误类型:\t{exception_type}",
            f"发生时间:\t{time_str}",
            "────────────────",
            "原始输入:",
            f"\t{user_input}",
            "────────────────",
            "操作指引:",
            f"\t帮助指令:\t{help_command}",
            f"\t反馈群组:\t{config.MAIN_GROUP_ID}",
            "━━━━━━━━━━━━━━━━",
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
        now = timestamp or datetime.now()
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        name = event_name or "系统通知"

        header = "━━━━━━ 管理通知 ━━━━━━\n"
        body = (
            f"事件名称:\t{name}\n通知时间:\t{time_str}\n────────────────\n事件详情:\n"
        )

        details_str = str(event_details or "无详细说明")
        indented_details = "\n".join([f"\t{line}" for line in details_str.split("\n")])

        footer = "\n━━━━━━━━━━━━━━━━"

        return header + body + indented_details + footer


class AvatarFetcher:
    """
    异步头像获取与处理工具

    Note: Gemini 写的，AI 神力！

    有股味我也懒得改了你就说他能不能用，能用的代码就是好代码对吧！
    """

    @staticmethod
    def create_default_avatar(
        size: int, text: str = "群", bg_color: tuple = (255, 225, 230)
    ) -> Image.Image:
        """生成默认头像"""
        img = Image.new("RGBA", (size, size), bg_color)
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(MAPLE_FONT_PATH, int(size * 0.5))
            bbox = draw.textbbox((0, 0), text, font=font)
            text_x = (size - (bbox[2] - bbox[0])) / 2
            text_y = (size - (bbox[3] - bbox[1])) / 2 - (size * 0.1)
            draw.text((text_x, text_y), text, fill=(180, 76, 76), font=font)
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
            default_text = "人" if is_user else "群"
            img = cls.create_default_avatar(size, default_text)
            if is_user:
                return cls.apply_circle_mask(img)
            else:
                return cls.apply_rounded_mask(img, radius=int(size * 0.15))
