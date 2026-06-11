from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import CommandGroup

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import LOCALE_NAMES, normalize_locale, resolve_locale, tr
from src.lib.plugin_docs import DocsRenderContext, build_readme_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.repositories import i18n_repo

name = tr("zh-CN", "plugin.admin_i18n.name")
description = tr("zh-CN", "plugin.admin_i18n.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "i18n" / "README.MD"


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    return build_readme_docs(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        ctx=ctx,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.SUPERUSER,
        "i18n": {
            "name_key": "plugin.admin_i18n.name",
            "description_key": "plugin.admin_i18n.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="admin",
            order=125,
            source=DOCS_SOURCE,
            slug="admin.i18n",
            parent_slug="admin",
            aliases=("语言管理模块", "语言管理", "admin.i18n"),
        ),
    },
)

admin_command_group = CommandGroup(
    cmd="admin",
    permission=SUPERUSER,
    priority=5,
    block=False,
)
admin_i18n = admin_command_group.command("i18n", aliases={"语言管理"})


def _choices_text() -> str:
    return ", ".join(f"{key}({value})" for key, value in LOCALE_NAMES.items())


def _resolve_group_id(event: MessageEvent, raw_group_id: str | None) -> str | None:
    if raw_group_id:
        return raw_group_id
    if isinstance(event, GroupMessageEvent):
        return str(event.group_id)
    return None


@admin_i18n.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg()) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    args = arg.extract_plain_text().strip().split()
    if not args:
        await matcher.finish(build_docs(DocsRenderContext(locale=locale)))

    action = args[0].lower()

    if action == "show":
        group_id = _resolve_group_id(event, args[1] if len(args) > 1 else None)
        global_default = await i18n_repo.get_default_locale()
        if group_id is None:
            await matcher.finish(
                "\n".join(
                    [
                        tr(locale, "admin.i18n.show.title"),
                        tr(
                            locale,
                            "admin.i18n.show.global_default",
                            locale=global_default,
                        ),
                    ]
                )
            )
            return
        group_locale = await i18n_repo.get_group_locale(group_id)
        effective = await i18n_repo.resolve_locale(group_id)
        lines = [
            tr(locale, "admin.i18n.show.title"),
            tr(locale, "admin.i18n.show.scope", group_id=group_id),
            tr(locale, "admin.i18n.show.global_default", locale=global_default),
            (
                tr(locale, "admin.i18n.show.group_override", locale=group_locale)
                if group_locale is not None
                else tr(locale, "admin.i18n.show.group_override.none")
            ),
            tr(locale, "admin.i18n.show.effective", locale=effective),
        ]
        await matcher.finish("\n".join(lines))
        return

    if action == "default":
        if len(args) < 2:
            await matcher.finish(build_docs(DocsRenderContext(locale=locale)))
            return
        locale_code = normalize_locale(args[1])
        if locale_code is None:
            await matcher.finish(
                tr(
                    locale,
                    "admin.i18n.locale.invalid",
                    locale=args[1],
                    choices=_choices_text(),
                )
            )
            return
        await i18n_repo.set_default_locale(locale_code)
        await matcher.finish(
            tr(locale, "admin.i18n.default.updated", locale=locale_code)
        )
        return

    if action == "set":
        if len(args) < 2:
            await matcher.finish(build_docs(DocsRenderContext(locale=locale)))
            return
        locale_code = normalize_locale(args[1])
        if locale_code is None:
            await matcher.finish(
                tr(
                    locale,
                    "admin.i18n.locale.invalid",
                    locale=args[1],
                    choices=_choices_text(),
                )
            )
            return
        group_id = _resolve_group_id(event, args[2] if len(args) > 2 else None)
        if group_id is None:
            await matcher.finish(tr(locale, "admin.i18n.group.required"))
            return
        if not group_id.isdigit():
            await matcher.finish(
                tr(locale, "admin.i18n.group.invalid", group_id=group_id)
            )
            return
        await i18n_repo.set_group_locale(group_id, locale_code)
        await matcher.finish(
            tr(
                locale,
                "admin.i18n.group.updated",
                group_id=group_id,
                locale=locale_code,
            )
        )
        return

    if action == "clear":
        group_id = _resolve_group_id(event, args[1] if len(args) > 1 else None)
        if group_id is None:
            await matcher.finish(tr(locale, "admin.i18n.group.required"))
            return
        if not group_id.isdigit():
            await matcher.finish(
                tr(locale, "admin.i18n.group.invalid", group_id=group_id)
            )
            return
        changed = await i18n_repo.clear_group_locale(group_id)
        if not changed:
            await matcher.finish(
                tr(locale, "admin.i18n.group.already_cleared", group_id=group_id)
            )
            return
        await matcher.finish(tr(locale, "admin.i18n.group.cleared", group_id=group_id))
        return

    if action == "list":
        rows = await i18n_repo.list_group_locales()
        if not rows:
            await matcher.finish(tr(locale, "admin.i18n.list.empty"))
            return
        lines = [tr(locale, "admin.i18n.list.title")]
        for group_id, locale_code in rows:
            lines.append(f"- {group_id}: {locale_code}")
        await matcher.finish("\n".join(lines))
        return

    await matcher.finish(build_docs(DocsRenderContext(locale=locale)))
