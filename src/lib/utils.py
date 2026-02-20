"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-20 00:26:45
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-21 01:22:28
Description: 通用工具
"""

from datetime import timedelta
import re

from httpx import AsyncClient

from src.config import config


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


async def get_qq_avatar(user_id: str, size: int = 640) -> bytes:
    async with AsyncClient() as client:
        return (
            await client.get(f"http://q1.qlogo.cn/g?b=qq&nk={user_id}&s={size}")
        ).read()


from datetime import datetime


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
            "────────────────────",
            "原始输入:",
            f"\t{user_input}",
            "────────────────────",
            "操作指引:",
            f"\t帮助指令:\t{help_command}",
            f"\t反馈群组:\t{config.MAIN_GROUP_ID}",
            "━━━━━━━━━━━━━━━━━━━━",
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
            f"事件名称:\t{name}\n"
            f"通知时间:\t{time_str}\n"
            "────────────────────\n"
            "事件详情:\n"
        )

        details_str = str(event_details or "无详细说明")
        indented_details = "\n".join([f"\t{line}" for line in details_str.split("\n")])

        footer = "\n━━━━━━━━━━━━━━━━━━━━"

        return header + body + indented_details + footer
