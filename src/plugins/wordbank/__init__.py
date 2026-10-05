"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:30:24
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:39
Description: 插件入口
"""

from __future__ import annotations

from nonebot import on_message, on_notice
from nonebot.plugin import on_command
from nonebot.rule import to_me

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.plugin_meta import create_plugin_metadata
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

from . import bootstrap as _bootstrap  # noqa: F401  注册启动钩子与定时任务

# 导入入口模块即完成全部 matcher 注册（模块级 @matcher.handle()）：
#   entry_commands —— 命令 / 引导流程
#   entry_runtime  —— 回复路由 / 被动响应 / 通知
from . import entry_commands as entry_commands
from . import entry_runtime as entry_runtime
from .docs_support import wordbank_docs_meta
from .lifecycle import (
    initialize_wordbank_plugin as initialize_wordbank_plugin,
)

name = tr("zh-CN", "plugin.wordbank.name")
description = tr("zh-CN", "plugin.wordbank.description")


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#74C0FC",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "i18n": {
            "name_key": "plugin.wordbank.name",
            "description_key": "plugin.wordbank.description",
        },
        "docs": wordbank_docs_meta(),
    },
)
