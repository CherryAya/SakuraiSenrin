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
from nonebot.adapters.onebot.v11.message import MessageSegment
from nonebot.exception import ParserExit
from nonebot.matcher import Matcher
from nonebot.params import ShellCommandArgs
from nonebot.permission import SUPERUSER
from nonebot.plugin import CommandGroup, PluginMetadata, on_fullmatch
from nonebot.rule import ArgumentParser, to_me
from PIL import Image, ImageDraw, ImageFont

from src.database.core.consts import GroupStatus, InvitationStatus, Permission
from src.lib.consts import MAPLE_FONT_PATH, TriggerType
from src.lib.types import UNSET, Unset, is_set
from src.lib.utils.common import AvatarFetcher, get_current_time
from src.repositories import group_repo, invite_repo

name = "邀请管理模块"
description = """
群组管理模块:
  处理群聊邀请事件
""".strip()

usage = f"""
===== {name} =====

命令前缀: #admin.invite / #邀请管理

1.查看列表
  list / show / ls / 列表
  示例: #admin.invite list

2.同意邀请
  approve / 同意
  示例: #admin.invite approve -g <群号>
  快捷操作: 对邀请通知消息直接回复 y / 同意

3.拒绝邀请
  reject / 拒绝
  示例: #admin.invite reject -g <群号>
  批量操作: #admin.invite reject --all
  快捷操作: 对邀请通知消息直接回复 n / 拒绝

4.忽略邀请
  ignore / 忽略
  示例: #admin.invite ignore -g <群号>
  批量操作: #admin.invite ignore --all

5.操作日志
  log / 日志
  示例: #admin.invite log [-g <群号>]

6.帮助信息
  help / 帮助
  示例: #admin.invite help

[注意事项]:
1. 所有操作均需要【Senrin】管理员 (SUPERUSER) 权限。
2. 同意/拒绝/忽略 操作支持使用 -g <群号> 或 -f <Flag> 进行精准匹配。
3. 快捷回复操作 (y/n) 仅限直接回复机器人推送的邀请通知消息时生效。
""".strip()

__plugin_meta__ = PluginMetadata(
    name=name,
    description=description,
    usage=usage,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.SUPERUSER,
        "no_check": True,
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
        await ctx.matcher.send("无法使用所提供的信息找到对应的邀请。")
        return False

    if invitation.status != InvitationStatus.PENDING:
        operator = invitation.operator
        if not ctx.silent:
            await ctx.matcher.send(
                f"邀请已被 {operator.user_name}({operator.user_id}) 处理，无法操作。\n"
                f"当前状态：{invitation.status}\n"
                "━━━━━━━━━━━━━━━━\n"
                f"群号：{invitation.group_id}\n"
                f"群名：{invitation.group.group_name}\n"
                f"邀请者：{invitation.inviter.user_name}\n"
                f"邀请 flag：{invitation.flag}\n"
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
            (
                f"已{action}群聊邀请 {invitation.id}\n"
                f"群号：{invitation.group_id}\n"
                f"群名：{invitation.group.group_name}\n"
                f"邀请者：{invitation.inviter.user_name}\n"
                f"邀请 flag：{invitation.flag}\n"
            ),
            reply_message=True,
        )
    return True


async def handle_list(ctx: AdminInviteContext) -> None:
    db_results = await invite_repo.get_by_status(InvitationStatus.PENDING)

    if not db_results:
        await ctx.matcher.finish("当前没有待处理的邀请哦。")

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
        )
        await handle_invitation(ic_ctx)
        return

    invs = await invite_repo.get_by_status(InvitationStatus.PENDING)
    if not invs:
        await ctx.matcher.finish("当前没有需要拒绝的待处理邀请哦。")

    success_count = 0
    details = []
    for inv in invs:
        ic_ctx = InviteContext(
            bot=ctx.bot,
            matcher=ctx.matcher,
            invitation_id=inv.id,
            approve=False,
            silent=True,
        )
        if await handle_invitation(ic_ctx):
            success_count += 1

            details.append(f"{inv.group.group_name} ({inv.group_id})")

    msg = "========== 批量拒绝 ==========\n"
    if details:
        msg += "\n".join(details) + "\n"
    else:
        msg += "  (无成功处理的邀请记录)\n"
    msg += "------------------------------\n"
    msg += f"统计: 共拒绝了 {success_count} 个待处理邀请。"

    await ctx.matcher.send(msg)


async def handle_ignore(ctx: AdminInviteContext) -> None:
    if not ctx.is_all:
        inv = None
        if is_set(ctx.group_id):
            inv = await invite_repo.get_by_group_id(ctx.group_id)
        elif is_set(ctx.flag):
            inv = await invite_repo.get_by_flag(ctx.flag)

        if not inv:
            await ctx.matcher.finish("未找到对应的邀请记录。")
        await invite_repo.update_status(inv.id, InvitationStatus.IGNORED)
        msg = (
            "======= 操作成功: 已忽略 =======\n"
            f"群名：{inv.group.group_name}\n"
            f"群号：{inv.group_id}\n"
        )
        await ctx.matcher.send(msg)
        return
    invs = await invite_repo.ignore_all_pending()
    if not invs:
        await ctx.matcher.finish("当前没有需要忽略的待处理邀请哦。")

    details = []
    for inv in invs:
        details.append(f"{inv.group.group_name} ({inv.group_id})")
    msg = "========== 批量忽略 ==========\n"
    if details:
        msg += "\n".join(details) + "\n"
    msg += "------------------------------\n"
    msg += f"统计: 共清理了 {len(invs)} 个待处理邀请。"

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
    )
    await handle_invitation(ctx)


@admin_invite.handle()
async def _(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Namespace | ParserExit = ShellCommandArgs(),
) -> None:
    if isinstance(args, ParserExit):
        if args.status == 0:
            await admin_invite.finish(args.message)
        else:
            await admin_invite.finish(f"参数错误:\n{args.message}")

    action = args.action
    flag = getattr(args, "flag", UNSET)
    group_id = getattr(args, "gid", UNSET)
    is_all = getattr(args, "all", False)

    ctx = AdminInviteContext(
        bot=bot,
        matcher=matcher,
        operator_id=str(event.user_id),
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
            await admin_invite.finish("未知的操作指令。")

    await handler(ctx)
    await admin_invite.finish()
