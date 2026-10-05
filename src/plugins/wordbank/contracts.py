"""wordbank 插件内部回调契约（Protocol）。

历史上这些依赖通过 `Any` 参数 + 字符串动态派发（`getattr(plugin, name)`）表达，
既丢失类型信息也无法被静态检查。本模块把它们集中声明为显式 Protocol，
使各层之间的可调用依赖可被 pyright 完整校验。
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING, Protocol

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.matcher import Matcher
from nonebot.typing import T_State

from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import MessagePlanInput

if TYPE_CHECKING:
    from src.lib.message_delivery import DeliveryResult
    from src.plugins.wordbank.database.types import WordbankSearchPage
    from src.plugins.wordbank.handlers.commands import ParsedSearch


class ErrorBuilder(Protocol):
    """把业务异常翻译成可投递的错误消息。"""

    def __call__(
        self,
        exc: Exception,
        locale: LocaleCode,
        *,
        default_feature: str | None = None,
    ) -> MessagePlanInput: ...


class InitializePlugin(Protocol):
    """幂等的插件初始化入口。"""

    def __call__(self) -> Awaitable[None]: ...


class RecordViewMessage(Protocol):
    """记录搜索结果视图的消息引用（用于回复路由）。"""

    async def __call__(
        self,
        *,
        send_result: DeliveryResult,
        event: MessageEvent,
        parsed: ParsedSearch,
        page: WordbankSearchPage,
        has_image: bool,
    ) -> None: ...


class SendGroupDetailView(Protocol):
    """渲染并投递群详情视图。"""

    async def __call__(
        self,
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
        locale: LocaleCode,
        *,
        trigger_group_id: int,
        page: int,
        finish_after_send: bool = True,
    ) -> None: ...


class FinishGuidedSearch(Protocol):
    """收尾引导式搜索：渲染结果卡片并进入分页会话。"""

    async def __call__(
        self,
        bot: Bot,
        matcher: Matcher,
        state: T_State,
        event: MessageEvent,
        locale: LocaleCode,
        *,
        page_number: int,
        clamp_page: bool = False,
    ) -> None: ...
