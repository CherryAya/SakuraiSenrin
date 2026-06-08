"""plugin docs 元数据与渲染工具。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

from nonebot.adapters.onebot.v11.message import Message

from src.database.core.consts import Permission
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

from .consts import TriggerType


@dataclass(slots=True, frozen=True)
class DocsRenderContext:
    locale: LocaleCode


type DocsResult = Message | Awaitable[Message] | str | Awaitable[str]
type DocsProvider = Callable[..., DocsResult]


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
    name: str | None = None,
    description: str | None = None,
    content: str | None = None,
    name_key: MessageKey | None = None,
    description_key: MessageKey | None = None,
    content_key: MessageKey | None = None,
    trigger: TriggerType,
    permission: Permission,
    locale: LocaleCode = "zh-CN",
) -> Message:
    body = (
        tr(locale, content_key).strip()
        if content_key is not None
        else (content or "").strip()
    ) or tr(locale, "docs.default.empty")
    desc = (
        tr(locale, description_key).strip()
        if description_key is not None
        else (description or "").strip()
    ) or tr(locale, "docs.default.no_description")
    title = tr(locale, name_key) if name_key is not None else (name or "")
    return Message(
        (
            f"===== {title} =====\n"
            f"{tr(locale, 'docs.default.trigger')}: {trigger}\n"
            f"{tr(locale, 'docs.default.permission')}: {permission}\n\n"
            f"{desc}\n\n"
            f"{tr(locale, 'docs.default.usage')}:\n"
            f"{body}"
        ).strip()
    )
