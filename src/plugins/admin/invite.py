"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:20:23
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 14:17:23
Description: 邀请管理插件
"""

from __future__ import annotations

from argparse import Namespace
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import io
from pathlib import Path
from typing import Any

import arrow
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.exception import ParserExit
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import CommandGroup, on_message
from nonebot.rule import ArgumentParser, to_me
from PIL import Image, ImageDraw, ImageFont

from src.database.consts import WritePolicy
from src.database.core.consts import InvitationStatus, Permission
from src.lib.consts import MAPLE_FONT_PATH, TriggerType
from src.lib.demo_theme import SENRIN_V3_ADMIN_INVITE_IMAGE_THEME
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.long_task import (
    CompositeProgressSink,
    LoggerProgressSink,
    LongTaskRunner,
    LongTaskSpec,
    MessageEventProgressSink,
)
from src.lib.message_plan import (
    DeliveryPlan,
    MessagePlanEntry,
    MessagePlanInput,
    ReplyRefBlock,
    TextBlock,
    build_image_plan_entry,
    deliver_message_plan,
    finish_with_message,
)
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_doc_demo_plan_entry,
    build_readme_docs_plan_entry,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.lib.reply_router import (
    ReplyRoute,
    build_reply_rule,
    dispatch_reply_route,
    register_reply_route,
)
from src.lib.types import UNSET, Unset, is_set
from src.lib.utils.common import get_current_time
from src.lib.utils.img import QQAvatar
from src.repositories import group_repo, invite_repo, user_repo
from src.services.info import resolve_user_name
from src.services.runtime_policy import resolve_invitation_transition

name = tr("zh-CN", "plugin.admin_invite.name")
description = tr("zh-CN", "plugin.admin_invite.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "invite" / "README.MD"
INVITATION_AVATAR_CONCURRENCY = 8


def build_docs(ctx: DocsRenderContext | None = None) -> MessagePlanInput:
    return build_readme_docs_plan_entry(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        ctx=ctx,
    )


def _build_error_demo(
    locale: LocaleCode,
    message: str,
    feature_query: str | None,
) -> MessagePlanInput:
    return build_doc_demo_plan_entry(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        locale=locale,
        feature_query=feature_query,
        prefix_text=message,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.3.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.SUPERUSER,
        "no_check": True,
        "i18n": {
            "name_key": "plugin.admin_invite.name",
            "description_key": "plugin.admin_invite.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="admin",
            order=130,
            source=DOCS_SOURCE,
            slug="admin.invite",
            parent_slug="admin",
            aliases=("邀请管理模块", "邀请管理", "admin.invite"),
        ),
    },
)


APPROVE_REPLY_ALIASES = {"y", "approve", "通过", "同意", "批准"}
REJECT_REPLY_ALIASES = {"n", "reject", "拒绝", "驳回", "反对"}


def _normalize_reply_text(text: str) -> str:
    return text.strip().lower()


def _is_approve_reply_text(text: str) -> bool:
    normalized = _normalize_reply_text(text)
    return normalized in APPROVE_REPLY_ALIASES


def _is_reject_reply_text(text: str) -> bool:
    normalized = _normalize_reply_text(text)
    return normalized in REJECT_REPLY_ALIASES


def _get_reply_message_ids(event: MessageEvent) -> tuple[str, ...]:
    reply = getattr(event, "reply", None)
    if reply is None:
        return ()
    message_ids: list[str] = []
    for attr_name in ("real_id", "message_id"):
        value = getattr(reply, attr_name, None)
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in message_ids:
            message_ids.append(text)
    return tuple(message_ids)


async def _legacy_invitation_from_reply(event: MessageEvent) -> Any | None:
    for message_id in _get_reply_message_ids(event):
        invitation = await invite_repo.get_by_message_id(message_id)
        if invitation is not None:
            return invitation
    return None


async def _legacy_is_invitation_reply(event: MessageEvent) -> bool:
    return await _legacy_invitation_from_reply(event) is not None


async def _handle_registered_invite_reply_target(
    bot: Bot,
    event: MessageEvent,
    target: object,
) -> int | None:
    _ = (bot, event)
    payload = getattr(target, "payload", {})
    if not isinstance(payload, dict):
        return None
    raw_invitation_id = payload.get("invitation_id", 0)
    if isinstance(raw_invitation_id, int):
        return raw_invitation_id
    if isinstance(raw_invitation_id, str) and raw_invitation_id.isdigit():
        return int(raw_invitation_id)
    return None


async def _legacy_handle_invite_reply(
    bot: Bot,
    event: MessageEvent,
) -> int | None:
    _ = bot
    invitation = await _legacy_invitation_from_reply(event)
    if invitation is None:
        return None
    return int(invitation.id)


# fmt: off
invite_parser = ArgumentParser()
subparsers = invite_parser.add_subparsers(dest="action", required=True, help="执行的操作") # noqa: E501

list_parser = subparsers.add_parser("list", aliases=["show", "ls", "列表"], help="查看邀请列表") # noqa: E501

approve_parser = subparsers.add_parser("approve", aliases=["同意"], help="同意群组/好友邀请") # noqa: E501
approve_group = approve_parser.add_mutually_exclusive_group(required=True)
approve_group.add_argument("-f", "--flag", type=str, help="邀请标识 (Flag)")
approve_group.add_argument("-g", "--gid", type=str, help="群组 ID")

reject_parser = subparsers.add_parser("reject", aliases=["拒绝"], help="拒绝群组/好友邀请") # noqa: E501
reject_group = reject_parser.add_mutually_exclusive_group(required=True)
reject_group.add_argument("-f", "--flag", type=str, help="邀请标识 (Flag)")
reject_group.add_argument("-g", "--gid", type=str, help="群组 ID")
reject_group.add_argument("--all", action="store_true", help="拒绝所有待处理邀请")

ignore_parser = subparsers.add_parser("ignore", aliases=["忽略"], help="忽略群组/好友邀请") # noqa: E501
ignore_group = ignore_parser.add_mutually_exclusive_group(required=True)
ignore_group.add_argument("-f", "--flag", type=str, help="邀请标识 (Flag)")
ignore_group.add_argument("-g", "--gid", type=str, help="群组 ID")
ignore_group.add_argument("--all", action="store_true", help="忽略所有待处理邀请")

log_parser = subparsers.add_parser("log", aliases=["日志"], help="查看邀请处理日志")
log_parser.add_argument("-g", "--gid", type=str, help="群组 ID")
# fmt: on

admin_command_group = CommandGroup("admin")
admin_invite = admin_command_group.command(
    "invite",
    aliases={"邀请管理"},
    permission=SUPERUSER,
    priority=5,
    block=False,
)

register_reply_route(
    ReplyRoute(
        name="admin.invite.approve",
        context_kinds=("admin.invite.approval",),
        text_matcher=_is_approve_reply_text,
        handler=_handle_registered_invite_reply_target,
        legacy_rule=_legacy_is_invitation_reply,
        legacy_handler=_legacy_handle_invite_reply,
    )
)
register_reply_route(
    ReplyRoute(
        name="admin.invite.reject",
        context_kinds=("admin.invite.approval",),
        text_matcher=_is_reject_reply_text,
        handler=_handle_registered_invite_reply_target,
        legacy_rule=_legacy_is_invitation_reply,
        legacy_handler=_legacy_handle_invite_reply,
    )
)

approve_matcher = on_message(
    rule=to_me() & build_reply_rule("admin.invite.approve"),
    permission=SUPERUSER,
    priority=5,
    block=False,
)
reject_matcher = on_message(
    rule=to_me() & build_reply_rule("admin.invite.reject"),
    permission=SUPERUSER,
    priority=5,
    block=False,
)


@dataclass
class InviteContext:
    bot: Bot
    event: MessageEvent
    matcher: Matcher
    approve: bool
    locale: LocaleCode

    msg_id: str | Unset = UNSET
    flag: str | Unset = UNSET
    group_id: str | Unset = UNSET
    invitation_id: int | Unset = UNSET
    operator_id: str | Unset = UNSET
    silent: bool = False


@dataclass
class AdminInviteContext:
    bot: Bot
    event: MessageEvent
    matcher: Matcher
    operator_id: str
    locale: LocaleCode

    flag: str | Unset = UNSET
    group_id: str | Unset = UNSET
    is_all: bool = False


async def _send_reusable_text(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    *,
    message: str,
) -> None:
    await deliver_message_plan(
        bot,
        plan=DeliveryPlan(
            messages=(message,),
            source_kind="admin_invite",
        ),
        event=event,
    )


async def _ensure_operator_persisted(bot: Bot, operator_id: str) -> None:
    operator_name = await resolve_user_name(bot, operator_id)
    await user_repo.save_user(
        user_id=operator_id,
        user_name=operator_name,
        policy=WritePolicy.IMMEDIATE,
    )


def _render_processed_message(ctx: InviteContext, invitation: Any) -> str:
    operator = invitation.operator
    operator_name = operator.user_name if operator else "UNKNOWN"
    operator_id = operator.user_id if operator else (invitation.operator_id or "-")
    return tr(
        ctx.locale,
        "admin.invite.processed",
        operator_name=operator_name,
        operator_id=operator_id,
        status=invitation.status,
        group_id=invitation.group_id,
        group_name=invitation.group.group_name,
        inviter_name=invitation.inviter.user_name,
        flag=invitation.flag,
    )


class InvitationListRenderer:
    locale: LocaleCode

    """
    邀请列表排版与高分辨率图像渲染器

    该类负责将待处理的群组/好友邀请数据转换为结构化的列表图像。
    采用 3.2 倍超采样机制（2K 缩放标准）以保证文字与头像的边缘抗锯齿效果。
    具备动态高度计算、头像防重叠排版以及基于柔和粉色系的二次元 UI 布局。

    Note: Gemini 写的，AI 神力！

    有股味我也懒得改了你就说他能不能用，能用的代码就是好代码对吧！
    """

    def __init__(
        self,
        locale: LocaleCode,
        font_path: str | Path = MAPLE_FONT_PATH,
    ) -> None:
        self.locale = locale
        self.theme = SENRIN_V3_ADMIN_INVITE_IMAGE_THEME
        self.BG_COLOR = self.theme.bg_color
        self.TEXT_COLOR = self.theme.text_color
        self.ITEM_BG_COLOR = self.theme.item_bg_color
        self.SUB_TEXT_COLOR = self.theme.sub_text_color
        self.HIGHLIGHT_COLOR = self.theme.highlight_color

        # 尺寸与超采样配置 (渲染宽度 800*3.2=2560 达到 2K 标准)
        self.SCALE = 3.2
        self.RENDER_WIDTH = int(800 * self.SCALE)
        self.PADDING = int(30 * self.SCALE)

        # 字体大小配置
        self.TITLE_SIZE = int(36 * self.SCALE)
        self.STATS_SIZE = int(16 * self.SCALE)
        self.H1_SIZE = int(22 * self.SCALE)
        self.H2_SIZE = int(18 * self.SCALE)
        self.P_SIZE = int(14 * self.SCALE)

        self.font_title = ImageFont.truetype(font_path, self.TITLE_SIZE)
        self.font_stats = ImageFont.truetype(font_path, self.STATS_SIZE)
        self.font_h1 = ImageFont.truetype(font_path, self.H1_SIZE)
        self.font_h2 = ImageFont.truetype(font_path, self.H2_SIZE)
        self.font_p = ImageFont.truetype(font_path, self.P_SIZE)

    def render(self, invitations: list[dict[str, Any]]) -> bytes:
        """渲染图像并返回 bytes"""
        item_spacing = int(20 * self.SCALE)
        card_padding = int(24 * self.SCALE)

        # 头像尺寸配置
        group_avatar_size = int(88 * self.SCALE)
        user_avatar_size = int(28 * self.SCALE)

        # 计算高度：三行文字 + 间距
        content_height = (
            self.H1_SIZE
            + max(self.H2_SIZE, user_avatar_size)
            + self.P_SIZE
            + int(30 * self.SCALE)
        )
        card_height = max(group_avatar_size, content_height) + card_padding * 2

        title_area_height = (
            self.TITLE_SIZE + self.STATS_SIZE + int(20 * self.SCALE) + self.PADDING * 2
        )
        total_height = (
            title_area_height
            + len(invitations) * (card_height + item_spacing)
            + self.PADDING
        )

        # 创建画布
        img = Image.new("RGB", (self.RENDER_WIDTH, total_height), self.BG_COLOR)
        draw = ImageDraw.Draw(img)

        # --- 1. 绘制顶部标题与统计信息 ---
        title_text = tr(self.locale, "admin.invite.image.title")
        title_bbox = draw.textbbox((0, 0), title_text, font=self.font_title)
        title_x = (self.RENDER_WIDTH - (title_bbox[2] - title_bbox[0])) // 2
        draw.text(
            (title_x, self.PADDING),
            title_text,
            font=self.font_title,
            fill=self.TEXT_COLOR,
        )

        current_time_str = arrow.get(get_current_time()).strftime("%Y-%m-%d %H:%M:%S")
        stats_text = tr(
            self.locale,
            "admin.invite.image.stats",
            count=len(invitations),
            time=current_time_str,
        )
        stats_bbox = draw.textbbox((0, 0), stats_text, font=self.font_stats)
        stats_x = (self.RENDER_WIDTH - (stats_bbox[2] - stats_bbox[0])) // 2
        draw.text(
            (stats_x, self.PADDING + self.TITLE_SIZE + int(15 * self.SCALE)),
            stats_text,
            font=self.font_stats,
            fill=self.SUB_TEXT_COLOR,
        )

        # --- 2. 绘制卡片列表 ---
        current_y = title_area_height

        for index, item in enumerate(invitations, start=1):
            card_bbox = [
                self.PADDING,
                current_y,
                self.RENDER_WIDTH - self.PADDING,
                current_y + card_height,
            ]
            draw.rounded_rectangle(
                card_bbox, radius=int(15 * self.SCALE), fill=self.ITEM_BG_COLOR
            )

            avatar_x = self.PADDING + card_padding
            avatar_y = current_y + card_padding
            g_avatar = item["group_avatar_img"].resize(
                (group_avatar_size, group_avatar_size), Image.Resampling.LANCZOS
            )
            img.paste(g_avatar, (avatar_x, avatar_y), mask=g_avatar)

            content_x = avatar_x + group_avatar_size + int(25 * self.SCALE)
            line_y = current_y + card_padding

            index_text = f"#{index:02d} "
            draw.text(
                (content_x, line_y),
                index_text,
                font=self.font_h1,
                fill=self.HIGHLIGHT_COLOR,
            )
            idx_bbox = draw.textbbox((0, 0), index_text, font=self.font_h1)
            idx_width = idx_bbox[2] - idx_bbox[0]

            draw.text(
                (content_x + idx_width, line_y),
                item["group_name"],
                font=self.font_h1,
                fill=self.TEXT_COLOR,
            )
            gname_bbox = draw.textbbox((0, 0), item["group_name"], font=self.font_h1)
            gname_width = gname_bbox[2] - gname_bbox[0]

            draw.text(
                (
                    content_x + idx_width + gname_width + int(10 * self.SCALE),
                    line_y + int(5 * self.SCALE),
                ),
                tr(
                    self.locale,
                    "admin.invite.image.group_id",
                    group_id=item["group_id"],
                ),
                font=self.font_p,
                fill=self.SUB_TEXT_COLOR,
            )

            line_y += self.H1_SIZE + int(15 * self.SCALE)

            u_avatar = item["user_avatar_img"].resize(
                (user_avatar_size, user_avatar_size), Image.Resampling.LANCZOS
            )
            img.paste(u_avatar, (content_x, line_y), mask=u_avatar)

            user_text_x = content_x + user_avatar_size + int(12 * self.SCALE)
            text_offset_y = (user_avatar_size - self.H2_SIZE) // 2
            draw.text(
                (user_text_x, line_y + text_offset_y),
                f"{item['inviter_name']}({item['inviter_id']})",
                font=self.font_h2,
                fill=self.TEXT_COLOR,
            )

            line_y += max(self.H2_SIZE, user_avatar_size) + int(15 * self.SCALE)

            id_text = tr(
                self.locale,
                "admin.invite.image.invitation_id",
                invitation_id=item["invitation_id"],
            )
            draw.text(
                (content_x, line_y),
                id_text,
                font=self.font_p,
                fill=self.HIGHLIGHT_COLOR,
            )
            id_bbox = draw.textbbox((0, 0), id_text, font=self.font_p)
            id_width = id_bbox[2] - id_bbox[0]

            other_info = tr(
                self.locale,
                "admin.invite.image.other_info",
                flag=item["flag"],
                time=item["time"],
            )
            draw.text(
                (content_x + id_width, line_y),
                other_info,
                font=self.font_p,
                fill=self.SUB_TEXT_COLOR,
            )

            current_y += card_height + item_spacing

        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()


async def generate_invitation_image_bytes(
    invitations_data: list[dict[str, Any]],
    locale: LocaleCode,
    *,
    task: LongTaskRunner | None = None,
) -> bytes:
    """
    提供给外部调用的异步门面函数。
    负责并发拉取所有头像，并调用渲染器生成最终图片的 Bytes。

    Note: Gemini 写的，AI 神力！

    有股味我也懒得改了你就说他能不能用，能用的代码就是好代码对吧！
    """
    avatar_limiter = asyncio.Semaphore(INVITATION_AVATAR_CONCURRENCY)
    avatar_tasks = [
        asyncio.create_task(_load_invitation_avatar_pair(index, item, avatar_limiter))
        for index, item in enumerate(invitations_data)
    ]
    fetched_images: list[tuple[Image.Image, Image.Image] | None] = [
        None for _ in invitations_data
    ]
    total = len(avatar_tasks)
    if task is not None and total > 0:
        await task.advance(
            "fetching_avatars",
            current=0,
            total=total,
            metadata={"count": total},
        )
    for completed_count, avatar_task in enumerate(
        asyncio.as_completed(avatar_tasks),
        start=1,
    ):
        index, images = await avatar_task
        fetched_images[index] = images
        if task is not None:
            await task.advance(
                "fetching_avatars",
                current=completed_count,
                total=total,
                metadata={"count": total},
            )
    for item, images in zip(
        invitations_data,
        fetched_images,
        strict=True,
    ):
        assert images is not None
        group_avatar, user_avatar = images
        assert group_avatar is not None
        assert user_avatar is not None
        item["group_avatar_img"] = group_avatar
        item["user_avatar_img"] = user_avatar

    if task is not None:
        await task.advance(
            "rendering",
            current=total,
            total=total,
            metadata={"count": total},
        )
    renderer = InvitationListRenderer(locale)
    return renderer.render(invitations_data)


async def _load_invitation_avatar_pair(
    index: int,
    item: dict[str, Any],
    limiter: asyncio.Semaphore,
) -> tuple[int, tuple[Image.Image, Image.Image]]:
    return index, await _fetch_invitation_avatars(item, limiter)


async def _fetch_invitation_avatars(
    item: dict[str, Any],
    limiter: asyncio.Semaphore,
) -> tuple[Image.Image, Image.Image]:
    group_avatar_size = 300
    user_avatar_size = 150
    async with limiter:
        group_avatar, user_avatar = await asyncio.gather(
            QQAvatar.fetch_group(str(item["group_id"]), size=group_avatar_size),
            QQAvatar.fetch_user(str(item["inviter_id"]), size=user_avatar_size),
        )
    return (
        group_avatar.circle_corner(group_avatar_size * 0.15).image.copy(),
        user_avatar.circle().image.copy(),
    )


async def handle_invitation(ctx: InviteContext) -> bool:
    if is_set(ctx.msg_id):
        invitation = await invite_repo.get_by_message_id(ctx.msg_id)
        if not invitation:
            return False
    elif is_set(ctx.invitation_id):
        invitation = await invite_repo.get_by_id(ctx.invitation_id)
        if not invitation:
            return False
    elif is_set(ctx.group_id):
        invitation = await invite_repo.get_by_group_id(ctx.group_id)
        if not invitation:
            return False
    elif is_set(ctx.flag):
        invitation = await invite_repo.get_by_flag(ctx.flag)
        if not invitation:
            return False
    else:
        await _send_reusable_text(
            ctx.bot,
            ctx.matcher,
            ctx.event,
            message=tr(ctx.locale, "admin.invite.lookup_failed"),
        )
        return False

    if invitation.status.is_processed:
        if not ctx.silent:
            await _send_reusable_text(
                ctx.bot,
                ctx.matcher,
                ctx.event,
                message=_render_processed_message(ctx, invitation),
            )
        return False

    try:
        invitation_status, group_status = resolve_invitation_transition(
            approve=ctx.approve,
            group_status=invitation.group.status,
        )
    except ValueError:
        if not ctx.silent:
            await _send_reusable_text(
                ctx.bot,
                ctx.matcher,
                ctx.event,
                message=tr(ctx.locale, "admin.invite.need_unban_first"),
            )
        return False

    if flag := invitation.flag:
        await ctx.bot.set_group_add_request(
            flag=flag,
            sub_type=invitation.sub_type,
            approve=ctx.approve,
        )
    elif not ctx.approve:
        await ctx.bot.set_group_leave(group_id=int(invitation.group_id))

    action = (
        tr(ctx.locale, "admin.invite.action.approve")
        if ctx.approve
        else tr(ctx.locale, "admin.invite.action.reject")
    )

    operator_id = str(ctx.operator_id) if is_set(ctx.operator_id) else None
    if operator_id is not None:
        await _ensure_operator_persisted(ctx.bot, operator_id)

    await invite_repo.update_status(
        invitation_id=invitation.id,
        status=invitation_status,
        operator_id=operator_id,
    )
    await group_repo.update_status(
        group_id=invitation.group_id,
        status=group_status,
    )
    if not ctx.silent:
        message_id = getattr(ctx.event, "message_id", None)
        blocks: list[ReplyRefBlock | TextBlock] = []
        if message_id is not None:
            blocks.append(ReplyRefBlock(message_id=str(message_id)))
        blocks.append(
            TextBlock(
                tr(
                    ctx.locale,
                    "admin.invite.action_done",
                    action=action,
                    invitation_id=invitation.id,
                    group_id=invitation.group_id,
                    group_name=invitation.group.group_name,
                    inviter_name=invitation.inviter.user_name,
                    flag=invitation.flag,
                )
            )
        )
        await deliver_message_plan(
            ctx.bot,
            plan=DeliveryPlan(
                messages=(MessagePlanEntry(blocks=tuple(blocks)),),
                source_kind="admin_invite_action_done",
                allow_asset_reuse=False,
            ),
            event=ctx.event,
        )
    return True


def _build_progress_sink(bot: Bot, event: MessageEvent) -> CompositeProgressSink:
    return CompositeProgressSink(
        LoggerProgressSink(),
        MessageEventProgressSink(bot, event),
    )


async def handle_list(ctx: AdminInviteContext) -> None:
    empty_message: str | None = None
    async with LongTaskRunner(
        LongTaskSpec(
            task_name="admin.invite.list",
            source_kind="admin_invite_list",
            prompt=tr(ctx.locale, "admin.invite.list.processing"),
            threshold_ms=800,
        ),
        sink=_build_progress_sink(ctx.bot, ctx.event),
    ) as long_task:
        await long_task.advance("loading_records")
        db_results = await invite_repo.get_by_status(InvitationStatus.PENDING)
        if not db_results:
            empty_message = tr(ctx.locale, "admin.invite.pending.none")
        else:
            render_data = []
            for inv in db_results:
                render_data.append(
                    {
                        "invitation_id": inv.id,
                        "group_name": inv.group.group_name,
                        "group_id": inv.group.group_id,
                        "inviter_name": inv.inviter.user_name,
                        "inviter_id": inv.inviter.user_id,
                        "time": arrow.get(inv.created_at).strftime("%Y-%m-%d %H:%M"),
                        "flag": (
                            inv.flag or tr(ctx.locale, "admin.invite.image.flag.none")
                        ),
                    }
                )
            img_bytes = await generate_invitation_image_bytes(
                render_data,
                ctx.locale,
                task=long_task,
            )
            await long_task.advance(
                "delivering",
                metadata={"count": len(render_data)},
            )
            await deliver_message_plan(
                ctx.bot,
                plan=DeliveryPlan(
                    messages=(build_image_plan_entry(img_bytes),),
                    source_kind="admin_invite_list",
                ),
                event=ctx.event,
            )

    if empty_message is not None:
        await finish_with_message(
            ctx.bot,
            ctx.matcher,
            event=ctx.event,
            message=empty_message,
            source_kind="admin_invite",
        )


async def handle_approve(ctx: AdminInviteContext) -> None:
    ic_ctx = InviteContext(
        bot=ctx.bot,
        event=ctx.event,
        matcher=ctx.matcher,
        flag=ctx.flag,
        group_id=ctx.group_id,
        approve=True,
        locale=ctx.locale,
        operator_id=ctx.operator_id,
    )
    await handle_invitation(ic_ctx)


async def handle_reject(ctx: AdminInviteContext) -> None:
    if not ctx.is_all:
        ic_ctx = InviteContext(
            bot=ctx.bot,
            event=ctx.event,
            matcher=ctx.matcher,
            flag=ctx.flag,
            group_id=ctx.group_id,
            approve=False,
            locale=ctx.locale,
            operator_id=ctx.operator_id,
        )
        await handle_invitation(ic_ctx)
        return

    invs = await invite_repo.get_by_status(InvitationStatus.PENDING)
    if not invs:
        await finish_with_message(
            ctx.bot,
            ctx.matcher,
            event=ctx.event,
            message=tr(ctx.locale, "admin.invite.reject.none"),
            source_kind="admin_invite",
        )
        return

    success_count = 0
    details = []
    for inv in invs:
        ic_ctx = InviteContext(
            bot=ctx.bot,
            event=ctx.event,
            matcher=ctx.matcher,
            invitation_id=inv.id,
            approve=False,
            silent=True,
            locale=ctx.locale,
            operator_id=ctx.operator_id,
        )
        if await handle_invitation(ic_ctx):
            success_count += 1

            details.append(f"{inv.group.group_name} ({inv.group_id})")

    msg = tr(ctx.locale, "admin.invite.bulk.reject.title") + "\n"
    if details:
        msg += "\n".join(details) + "\n"
    else:
        msg += tr(ctx.locale, "admin.invite.bulk.none_processed") + "\n"
    msg += tr(ctx.locale, "admin.invite.bulk.separator") + "\n"
    msg += tr(ctx.locale, "admin.invite.bulk.reject.summary", count=success_count)

    await _send_reusable_text(ctx.bot, ctx.matcher, ctx.event, message=msg)


async def handle_ignore(ctx: AdminInviteContext) -> None:
    if not ctx.is_all:
        inv = None
        if is_set(ctx.group_id):
            inv = await invite_repo.get_by_group_id(ctx.group_id)
        elif is_set(ctx.flag):
            inv = await invite_repo.get_by_flag(ctx.flag)

        if not inv:
            await finish_with_message(
                ctx.bot,
                ctx.matcher,
                event=ctx.event,
                message=tr(ctx.locale, "admin.invite.record.not_found"),
                source_kind="admin_invite",
            )
            return
        if inv.status.is_processed:
            await _send_reusable_text(
                ctx.bot,
                ctx.matcher,
                ctx.event,
                message=_render_processed_message(
                    InviteContext(
                        bot=ctx.bot,
                        event=ctx.event,
                        matcher=ctx.matcher,
                        approve=False,
                        locale=ctx.locale,
                    ),
                    inv,
                ),
            )
            return
        await _ensure_operator_persisted(ctx.bot, ctx.operator_id)
        await invite_repo.update_status(
            inv.id,
            InvitationStatus.IGNORED,
            operator_id=ctx.operator_id,
        )
        msg = tr(
            ctx.locale,
            "admin.invite.ignore.done",
            group_name=inv.group.group_name,
            group_id=inv.group_id,
        )
        await _send_reusable_text(ctx.bot, ctx.matcher, ctx.event, message=msg)
        return
    await _ensure_operator_persisted(ctx.bot, ctx.operator_id)
    invs = await invite_repo.ignore_all_pending(operator_id=ctx.operator_id)
    if not invs:
        await finish_with_message(
            ctx.bot,
            ctx.matcher,
            event=ctx.event,
            message=tr(ctx.locale, "admin.invite.ignore.none"),
            source_kind="admin_invite",
        )
        return

    details = []
    for inv in invs:
        details.append(f"{inv.group.group_name} ({inv.group_id})")
    msg = tr(ctx.locale, "admin.invite.bulk.ignore.title") + "\n"
    if details:
        msg += "\n".join(details) + "\n"
    msg += tr(ctx.locale, "admin.invite.bulk.separator") + "\n"
    msg += tr(ctx.locale, "admin.invite.bulk.ignore.summary", count=len(invs))

    await _send_reusable_text(ctx.bot, ctx.matcher, ctx.event, message=msg)


async def handle_log(ctx: AdminInviteContext) -> None:
    await _send_reusable_text(
        ctx.bot,
        ctx.matcher,
        ctx.event,
        message=tr(ctx.locale, "admin.invite.log.unavailable"),
    )


@approve_matcher.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    invitation_id = await dispatch_reply_route("admin.invite.approve", bot, event)
    if invitation_id is None:
        await matcher.finish()
    ctx = InviteContext(
        bot=bot,
        event=event,
        matcher=matcher,
        approve=True,
        invitation_id=invitation_id,
        operator_id=str(event.user_id),
        locale=await resolve_locale(str(getattr(event, "group_id", "")) or None),
    )
    await handle_invitation(ctx)


@reject_matcher.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    invitation_id = await dispatch_reply_route("admin.invite.reject", bot, event)
    if invitation_id is None:
        await matcher.finish()
    ctx = InviteContext(
        bot=bot,
        event=event,
        matcher=matcher,
        approve=False,
        invitation_id=invitation_id,
        operator_id=str(event.user_id),
        locale=await resolve_locale(str(getattr(event, "group_id", "")) or None),
    )
    await handle_invitation(ctx)


@admin_invite.handle()
async def _(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    argv = arg.extract_plain_text().strip().split()
    if not argv:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=build_docs(DocsRenderContext(locale=locale)),
            source_kind="admin_invite",
        )
        return
    try:
        args: Namespace | ParserExit = invite_parser.parse_args(argv)
    except ParserExit as exc:
        args = exc
    if isinstance(args, ParserExit):
        if args.status == 0:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=build_docs(DocsRenderContext(locale=locale)),
                source_kind="admin_invite",
            )
            return
        else:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=_build_error_demo(
                    locale,
                    tr(
                        locale,
                        "admin.invite.args_error",
                        message=tr(locale, "admin.invite.args_error.detail"),
                    ),
                    argv[0].lower() if argv else None,
                ),
                source_kind="admin_invite",
            )
            return

    action = args.action
    flag = getattr(args, "flag", UNSET)
    group_id = getattr(args, "gid", UNSET)
    is_all = getattr(args, "all", False)

    ctx = AdminInviteContext(
        bot=bot,
        event=event,
        matcher=matcher,
        operator_id=str(event.user_id),
        locale=locale,
        flag=flag,
        group_id=group_id,
        is_all=is_all,
    )

    handler: Callable[[AdminInviteContext], Awaitable[None]]
    match action:
        case "list" | "show" | "ls" | "列表":
            handler = handle_list
        case "approve" | "同意":
            handler = handle_approve
        case "reject" | "拒绝":
            handler = handle_reject
        case "ignore" | "忽略":
            handler = handle_ignore
        case "log" | "日志":
            handler = handle_log
        case _:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=_build_error_demo(
                    locale,
                    tr(locale, "admin.invite.unknown_command"),
                    None,
                ),
                source_kind="admin_invite",
            )
            return

    await handler(ctx)
    await admin_invite.finish()
