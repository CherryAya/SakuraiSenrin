"""wordbank 全部 matcher 声明。

集中声明后，各 entry_* 模块可直接用 `@matcher.handle()` 注册模块级处理函数，
无须 register_* 工厂与依赖注入。
"""

from __future__ import annotations

from nonebot import on_message, on_notice
from nonebot.plugin import on_command
from nonebot.rule import to_me

from src.lib.reply_router import build_reply_rule

wordbank_command = on_command(
    "wordbank",
    aliases={"词库", "wordbank.help"},
    priority=5,
    block=True,
)


wordbank_add_command = on_command(
    ("wordbank", "add"),
    aliases={"添加词条"},
    priority=5,
    block=True,
)


wordbank_search_command = on_command(
    ("wordbank", "search"),
    aliases={"搜索词条"},
    priority=5,
    block=True,
)


wordbank_pending_command = on_command(
    ("wordbank", "pending"),
    aliases={"待审核词条"},
    priority=5,
    block=True,
)


wordbank_rank_command = on_command(
    ("wordbank", "rank"),
    aliases={"苦瓜榜"},
    priority=5,
    block=True,
)


wordbank_approve_command = on_command(
    ("wordbank", "approve"),
    aliases={"通过词条", "审核通过词条"},
    priority=5,
    block=True,
)


wordbank_reject_command = on_command(
    ("wordbank", "reject"),
    aliases={"拒绝词条", "驳回词条"},
    priority=5,
    block=True,
)


wordbank_delete_command = on_command(
    ("wordbank", "delete"),
    aliases={("wordbank", "del"), "删除词条"},
    priority=5,
    block=True,
)


wordbank_restore_command = on_command(
    ("wordbank", "restore"),
    aliases={"恢复词条"},
    priority=5,
    block=True,
)


wordbank_reply_command = on_message(
    rule=to_me() & build_reply_rule("wordbank.response"),
    priority=5,
    block=True,
)


wordbank_approval_reply_command = on_message(
    rule=to_me() & build_reply_rule("wordbank.approval"),
    priority=5,
    block=True,
)


wordbank_view_reply_command = on_message(
    rule=to_me() & build_reply_rule("wordbank.view"),
    priority=6,
    block=True,
)


wordbank_passive = on_message(priority=95, block=False)


wordbank_notice = on_notice(priority=95, block=False)
