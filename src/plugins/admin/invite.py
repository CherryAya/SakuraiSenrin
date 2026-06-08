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
import httpx
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.exception import ParserExit
from nonebot.matcher import Matcher
from nonebot.params import ShellCommandArgs
from nonebot.permission import SUPERUSER
from nonebot.plugin import CommandGroup, on_fullmatch
from nonebot.rule import ArgumentParser, to_me
from PIL import Image, ImageDraw, ImageFont

from src.database.core.consts import GroupStatus, InvitationStatus, Permission
from src.lib.consts import MAPLE_FONT_PATH, TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_static_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.lib.types import UNSET, Unset, is_set
from src.lib.utils.common import AvatarFetcher, get_current_time
from src.repositories import group_repo, invite_repo

name = tr("zh-CN", "plugin.admin_invite.name")
description = tr("zh-CN", "plugin.admin_invite.description")


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    locale = ctx.locale if ctx is not None else "zh-CN"
    return build_static_docs(
        name_key="plugin.admin_invite.name",
        description_key="plugin.admin_invite.description",
        content_key="plugin.admin_invite.docs",
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        locale=locale,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.SUPERUSER,
        "no_check": True,
        "docs": create_docs_meta(
            build_docs,
            visible=True,
            category="admin",
            order=130,
        ),
    },
)


async def is_reply(event: MessageEvent) -> bool:
    return event.reply is not None


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
admin_invite = admin_command_group.shell_command(
    cmd="invite",
    aliases={"邀请管理"},
    parser=invite_parser,
)

approve_matcher = on_fullmatch(
    ("y", "approve", "通过", "同意", "批准"),
    ignorecase=True,
    rule=to_me() & is_reply,
    permission=SUPERUSER,
    priority=5,
    block=False,
)
reject_matcher = on_fullmatch(
    ("n", "reject", "拒绝", "驳回", "反对"),
    ignorecase=True,
    rule=to_me() & is_reply,
    permission=SUPERUSER,
    priority=5,
    block=False,
)


@dataclass
class InviteContext:
    bot: Bot
    matcher: Matcher
    approve: bool
    locale: LocaleCode

    msg_id: str | Unset = UNSET
    flag: str | Unset = UNSET
    group_id: str | Unset = UNSET
    invitation_id: int | Unset = UNSET
    silent: bool = False


@dataclass
class AdminInviteContext:
    bot: Bot
    matcher: Matcher
    operator_id: str
    locale: LocaleCode

    flag: str | Unset = UNSET
    group_id: str | Unset = UNSET
    is_all: bool = False


class InvitationListRenderer:
    """
    邀请列表排版与高分辨率图像渲染器

    该类负责将待处理的群组/好友邀请数据转换为结构化的列表图像。
    采用 3.2 倍超采样机制（2K 缩放标准）以保证文字与头像的边缘抗锯齿效果。
    具备动态高度计算、头像防重叠排版以及基于柔和粉色系的二次元 UI 布局。

    Note: Gemini 写的，AI 神力！

    有股味我也懒得改了你就说他能不能用，能用的代码就是好代码对吧！
    """

    def __init__(self, font_path: str | Path = MAPLE_FONT_PATH) -> None:
        self.BG_COLOR = (255, 217, 222)  # #ffd9de
        self.TEXT_COLOR = (180, 76, 76)  # #b44c4c
        self.ITEM_BG_COLOR = (255, 240, 245)  # #fff0f5
        self.SUB_TEXT_COLOR = (200, 110, 110)  # 次要文本颜色
        self.HIGHLIGHT_COLOR = (220, 90, 100)  # 强调色(如ID、序号)

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
        title_text = "待 处 理 邀 请 列 表"
        title_bbox = draw.textbbox((0, 0), title_text, font=self.font_title)
        title_x = (self.RENDER_WIDTH - (title_bbox[2] - title_bbox[0])) // 2
        draw.text(
            (title_x, self.PADDING),
            title_text,
            font=self.font_title,
            fill=self.TEXT_COLOR,
        )

        current_time_str = arrow.get(get_current_time()).strftime("%Y-%m-%d %H:%M:%S")
        stats_text = (
            f"总计: {len(invitations)} 条未处理请求 | 生成时间: {current_time_str}"
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
                f"(群号: {item['group_id']})",
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

            id_text = f"邀请ID: {item['invitation_id']}"
            draw.text(
                (content_x, line_y),
                id_text,
                font=self.font_p,
                fill=self.HIGHLIGHT_COLOR,
            )
            id_bbox = draw.textbbox((0, 0), id_text, font=self.font_p)
            id_width = id_bbox[2] - id_bbox[0]

            other_info = f" | flag: {item['flag']} | 申请时间: {item['time']}"
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
) -> bytes:
    """
    提供给外部调用的异步门面函数。
    负责并发拉取所有头像，并调用渲染器生成最终图片的 Bytes。

    Note: Gemini 写的，AI 神力！

    有股味我也懒得改了你就说他能不能用，能用的代码就是好代码对吧！
    """
    async with httpx.AsyncClient() as client:
        tasks = []
        for item in invitations_data:
            group_url = (
                f"https://p.qlogo.cn/gh/{item['group_id']}/{item['group_id']}/100"
            )
            user_url = f"http://q1.qlogo.cn/g?b=qq&nk={item['inviter_id']}&s=100"

            tasks.append(
                AvatarFetcher.fetch(client, group_url, size=300, is_user=False)
            )
            tasks.append(AvatarFetcher.fetch(client, user_url, size=150, is_user=True))
        fetched_images = await asyncio.gather(*tasks)
        for i, item in enumerate(invitations_data):
            item["group_avatar_img"] = fetched_images[i * 2]
            item["user_avatar_img"] = fetched_images[i * 2 + 1]

    renderer = InvitationListRenderer()
    return renderer.render(invitations_data)


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
        await ctx.matcher.send(tr(ctx.locale, "admin.invite.lookup_failed"))
        return False

    if invitation.status.is_processed:
        operator = invitation.operator
        if not ctx.silent:
            await ctx.matcher.send(
                tr(
                    ctx.locale,
                    "admin.invite.processed",
                    operator_name=operator.user_name,
                    operator_id=operator.user_id,
                    status=invitation.status,
                    group_id=invitation.group_id,
                    group_name=invitation.group.group_name,
                    inviter_name=invitation.inviter.user_name,
                    flag=invitation.flag,
                )
            )
        return False

    if flag := invitation.flag:
        await ctx.bot.set_group_add_request(
            flag=flag,
            sub_type="invite",
            approve=ctx.approve,
        )
    elif not ctx.approve:
        await ctx.bot.set_group_leave(group_id=int(invitation.group_id))

    if ctx.approve:
        action = "同意"
        invitation_status = InvitationStatus.APPROVED
        group_status = GroupStatus.AUTHORIZED
    else:
        action = "拒绝"
        invitation_status = InvitationStatus.REJECTED
        group_status = GroupStatus.UNAUTHORIZED

    await invite_repo.update_status(
        invitation_id=invitation.id,
        status=invitation_status,
    )
    await group_repo.update_status(
        group_id=invitation.group_id,
        status=group_status,
    )
    if not ctx.silent:
        await ctx.matcher.send(
            tr(
                ctx.locale,
                "admin.invite.action_done",
                action=action,
                invitation_id=invitation.id,
                group_id=invitation.group_id,
                group_name=invitation.group.group_name,
                inviter_name=invitation.inviter.user_name,
                flag=invitation.flag,
            ),
            reply_message=True,
        )
    return True


async def handle_list(ctx: AdminInviteContext) -> None:
    db_results = await invite_repo.get_by_status(InvitationStatus.PENDING)

    if not db_results:
        await ctx.matcher.finish(tr(ctx.locale, "admin.invite.pending.none"))

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
                "flag": inv.flag or "无",
            }
        )

    img_bytes = await generate_invitation_image_bytes(render_data)

    await ctx.matcher.send(MessageSegment.image(img_bytes))


async def handle_approve(ctx: AdminInviteContext) -> None:
    ic_ctx = InviteContext(
        bot=ctx.bot,
        matcher=ctx.matcher,
        flag=ctx.flag,
        group_id=ctx.group_id,
        approve=True,
        locale=ctx.locale,
    )
    await handle_invitation(ic_ctx)


async def handle_reject(ctx: AdminInviteContext) -> None:
    if not ctx.is_all:
        ic_ctx = InviteContext(
            bot=ctx.bot,
            matcher=ctx.matcher,
            flag=ctx.flag,
            group_id=ctx.group_id,
            approve=False,
            locale=ctx.locale,
        )
        await handle_invitation(ic_ctx)
        return

    invs = await invite_repo.get_by_status(InvitationStatus.PENDING)
    if not invs:
        await ctx.matcher.finish(tr(ctx.locale, "admin.invite.reject.none"))

    success_count = 0
    details = []
    for inv in invs:
        ic_ctx = InviteContext(
            bot=ctx.bot,
            matcher=ctx.matcher,
            invitation_id=inv.id,
            approve=False,
            silent=True,
            locale=ctx.locale,
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

    await ctx.matcher.send(msg)


async def handle_ignore(ctx: AdminInviteContext) -> None:
    if not ctx.is_all:
        inv = None
        if is_set(ctx.group_id):
            inv = await invite_repo.get_by_group_id(ctx.group_id)
        elif is_set(ctx.flag):
            inv = await invite_repo.get_by_flag(ctx.flag)

        if not inv:
            await ctx.matcher.finish(tr(ctx.locale, "admin.invite.record.not_found"))
        await invite_repo.update_status(inv.id, InvitationStatus.IGNORED)
        msg = tr(
            ctx.locale,
            "admin.invite.ignore.done",
            group_name=inv.group.group_name,
            group_id=inv.group_id,
        )
        await ctx.matcher.send(msg)
        return
    invs = await invite_repo.ignore_all_pending()
    if not invs:
        await ctx.matcher.finish(tr(ctx.locale, "admin.invite.ignore.none"))

    details = []
    for inv in invs:
        details.append(f"{inv.group.group_name} ({inv.group_id})")
    msg = tr(ctx.locale, "admin.invite.bulk.ignore.title") + "\n"
    if details:
        msg += "\n".join(details) + "\n"
    msg += tr(ctx.locale, "admin.invite.bulk.separator") + "\n"
    msg += tr(ctx.locale, "admin.invite.bulk.ignore.summary", count=len(invs))

    await ctx.matcher.send(msg)


async def handle_log(ctx: AdminInviteContext) -> None:
    raise NotImplementedError("还没做")  # TODO


@approve_matcher.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    msg_id = str(event.reply.message_id)  # type: ignore
    ctx = InviteContext(
        bot=bot,
        matcher=matcher,
        approve=True,
        msg_id=msg_id,
        locale=await resolve_locale(),
    )
    await handle_invitation(ctx)


@reject_matcher.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    msg_id = str(event.reply.message_id)  # type: ignore
    ctx = InviteContext(
        bot=bot,
        matcher=matcher,
        approve=False,
        msg_id=msg_id,
        locale=await resolve_locale(),
    )
    await handle_invitation(ctx)


@admin_invite.handle()
async def _(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Namespace | ParserExit = ShellCommandArgs(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if isinstance(args, ParserExit):
        if args.status == 0:
            await admin_invite.finish(args.message)
        else:
            await admin_invite.finish(
                tr(locale, "admin.invite.args_error", message=args.message)
            )

    action = args.action
    flag = getattr(args, "flag", UNSET)
    group_id = getattr(args, "gid", UNSET)
    is_all = getattr(args, "all", False)

    ctx = AdminInviteContext(
        bot=bot,
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
            await admin_invite.finish(tr(locale, "admin.invite.unknown_command"))

    await handler(ctx)
    await admin_invite.finish()
