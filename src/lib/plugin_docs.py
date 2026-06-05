"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-04-04 14:58:00
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 14:58:00
Description: plugin docs 元数据与渲染工具
"""

from collections.abc import Awaitable, Callable
from typing import TypedDict

from nonebot.adapters.onebot.v11.message import Message

from src.database.core.consts import Permission

from .consts import TriggerType

type DocsProvider = Callable[[], Message | Awaitable[Message]]


class DocsMeta(TypedDict):
    visible: bool
    category: str
    order: int
    provider: DocsProvider


def create_docs_meta(
    provider: DocsProvider,
    *,
    visible: bool,
    category: str,
    order: int,
) -> DocsMeta:
    return {
        "visible": visible,
        "category": category,
        "order": order,
        "provider": provider,
    }


def build_static_docs(
    *,
    name: str,
    description: str,
    content: str,
    trigger: TriggerType,
    permission: Permission,
) -> Message:
    body = content.strip() or "暂无说明"
    desc = description.strip() or "暂无描述"
    return Message(
        (
            f"===== {name} =====\n"
            f"触发方式: {trigger}\n"
            f"权限: {permission}\n\n"
            f"{desc}\n\n"
            "用法:\n"
            f"{body}"
        ).strip()
    )
