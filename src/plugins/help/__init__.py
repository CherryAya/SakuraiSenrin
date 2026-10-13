"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:22:09
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-06-12 01:10:00
Description: 帮助插件
"""

from pathlib import Path

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import on_command

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
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
    ImageBytesBlock,
    MessagePlanBlock,
    MessagePlanEntry,
    MessagePlanInput,
    TextBlock,
    build_text_plan_entry,
    deliver_message_plan,
    finish_matcher,
    finish_with_message,
)
from src.lib.onebot_forward import resolve_forward_sender
from src.lib.plugin_docs import (
    FeatureDoc,
    build_doc_tree,
    build_feature_copy_text,
    build_plugin_summary_copy_text,
    build_simple_leaf_copy_text,
    build_static_entry_copy_text,
    can_view_node,
    create_docs_meta,
    feature_command_sections,
    filter_features_by_permission,
    load_demo_bytes,
    match_doc_node,
    match_feature,
    render_demo_png,
    render_doc_node_overview,
    render_feature_deep_dive,
    render_plugin_summary,
    render_static_entry,
    resolve_help_entry_shape,
)
from src.lib.plugin_docs import (
    render_plugin_guide as render_plugin_guide,
)
from src.lib.plugin_meta import create_plugin_metadata

from .entries import (
    _is_project_plugin as _is_project_plugin,
)
from .entries import (
    _iter_docs_entries as _iter_docs_entries,
)
from .entries import (
    _read_plugin_permission as _read_plugin_permission,
)
from .matching import (
    _can_view_entry as _can_view_entry,
)
from .matching import (
    _filter_authorized_entries as _filter_authorized_entries,
)
from .matching import (
    _match_entry as _match_entry,
)
from .matching import (
    _match_exact_entry as _match_exact_entry,
)
from .messages import (
    _build_ambiguous_message as _build_ambiguous_message,
)
from .messages import (
    _build_index_message as _build_index_message,
)
from .messages import (
    _build_permission_denied_message as _build_permission_denied_message,
)
from .messages import (
    _compose_help_reply as _compose_help_reply,
)
from .messages import (
    _resolve_child_entry as _resolve_child_entry,
)
from .messages import (
    _single_message_plan as _single_message_plan,
)
from .messages import (
    _split_query as _split_query,
)
from .models import DocsEntry as DocsEntry
from .models import MatchResult as MatchResult
from .models import RootSection as RootSection

name = tr("zh-CN", "plugin.help.name")
description = tr("zh-CN", "plugin.help.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"
HELP_FORWARD_FALLBACK_NICKNAME = "SakuraiSenrin"


HelpDeliveryPlan = DeliveryPlan

__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#5C7CFA",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "i18n": {
            "name_key": "plugin.help.name",
            "description_key": "plugin.help.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="core",
            order=10,
            source=DOCS_SOURCE,
            slug="help",
            aliases=("帮助文档", "帮助中心"),
        ),
    },
)

help_matcher = on_command(
    "help",
    aliases={"帮助"},
    priority=5,
    block=True,
)


async def _resolve_actor_permission(bot: Bot, event: MessageEvent) -> Permission:
    if await SUPERUSER(bot, event):
        return (
            Permission.NORMAL
            | Permission.GROUP_ADMIN
            | Permission.GROUP_OWNER
            | Permission.SUPERUSER
        )
    if isinstance(event, GroupMessageEvent):
        role = getattr(event.sender, "role", "")
        if role == "owner":
            return Permission.NORMAL | Permission.GROUP_ADMIN | Permission.GROUP_OWNER
        if role == "admin":
            return Permission.NORMAL | Permission.GROUP_ADMIN
    return Permission.NORMAL


_build_text_plan_entry = build_text_plan_entry


def _compose_plugin_guide_messages(
    entry: DocsEntry,
    *,
    features: tuple[FeatureDoc, ...],
    child_entries: list[DocsEntry],
    locale: LocaleCode,
    actor_permission: Permission,
) -> tuple[MessagePlanInput, ...]:
    messages: list[MessagePlanEntry] = []
    summary_text = build_plugin_summary_copy_text(entry.node)
    summary_blocks: list[MessagePlanBlock] = []
    if summary_text:
        summary_blocks.append(TextBlock(summary_text))
    summary_blocks.append(
        ImageBytesBlock(
            render_plugin_summary(
                entry.node,
                locale=locale,
                actor_permission=actor_permission,
            )
        )
    )
    messages.append(MessagePlanEntry(blocks=tuple(summary_blocks)))

    for feature in features:
        lines = [f"👉 {feature.title}"]
        for trigger in feature_command_sections(
            entry.node.bundle,
            feature,
            entry.node.title,
        ):
            normalized = trigger.strip()
            if normalized and not normalized.startswith("#"):
                normalized = f"#{normalized}"
            lines.append(normalized)
        messages.append(
            MessagePlanEntry(
                blocks=(
                    TextBlock("\n".join(lines).strip()),
                    ImageBytesBlock(
                        load_demo_bytes(entry.node.bundle, feature)
                        or render_demo_png(entry.node.bundle, feature)
                    ),
                )
            )
        )

    if child_entries:
        child_lines = [tr(locale, "docs.render.guide.child_module_label")]
        for child_entry in child_entries:
            child_lines.append(
                tr(
                    locale,
                    "docs.render.plugin_summary.entry_marker",
                    title=child_entry.node.title,
                )
            )
            child_lines.append(f"#help {child_entry.node.slug}")
            child_summary = child_entry.node.summary.strip()
            if child_summary:
                child_lines.append(child_summary)
        messages.append(build_text_plan_entry("\n".join(child_lines).strip()))
    return tuple(messages)


async def _resolve_forward_sender(bot: Bot) -> tuple[int, str]:
    return await resolve_forward_sender(
        bot,
        fallback_nickname=HELP_FORWARD_FALLBACK_NICKNAME,
    )


async def _send_help_forward(
    bot: Bot,
    event: MessageEvent,
    plan: DeliveryPlan,
) -> None:
    await deliver_message_plan(
        bot,
        plan=DeliveryPlan(
            messages=plan.messages,
            source_kind=plan.source_kind or "help",
            fallback_nickname=plan.fallback_nickname or HELP_FORWARD_FALLBACK_NICKNAME,
            allow_asset_reuse=plan.allow_asset_reuse,
            force_forward=True,
        ),
        event=event,
    )


async def _deliver_help_plan(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    plan: DeliveryPlan,
    *,
    locale: LocaleCode = "zh-CN",
) -> None:
    if not plan.should_forward:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=plan.messages[0],
            source_kind=plan.source_kind or "help",
            allow_asset_reuse=plan.allow_asset_reuse,
        )
        return
    async with LongTaskRunner(
        LongTaskSpec(
            task_name="help.forward_delivery",
            source_kind=plan.source_kind or "help",
            prompt=plan.wait_message
            or _build_text_plan_entry(tr(locale, "help.forward.wait_prompt")),
            threshold_ms=800,
        ),
        sink=CompositeProgressSink(
            LoggerProgressSink(),
            MessageEventProgressSink(bot, event),
        ),
    ):
        await deliver_message_plan(
            bot,
            plan=DeliveryPlan(
                messages=plan.messages,
                source_kind=plan.source_kind or "help",
                fallback_nickname=plan.fallback_nickname
                or HELP_FORWARD_FALLBACK_NICKNAME,
                allow_asset_reuse=plan.allow_asset_reuse,
                force_forward=True,
            ),
            event=event,
        )
    await finish_matcher(matcher)


async def _resolve_docs_delivery_plan(
    entry: DocsEntry,
    locale: LocaleCode,
    *,
    feature_query: str | None = None,
    actor_permission: Permission = Permission.NORMAL,
    all_entries: list[DocsEntry] | None = None,
) -> DeliveryPlan:
    scoped_entries = all_entries or [entry]
    tree = build_doc_tree([item.node for item in scoped_entries])
    children = tuple(
        child
        for child in tree.children_of(entry.node.slug)
        if can_view_node(child, actor_permission)
    )
    child_entries = [
        item
        for item in scoped_entries
        if item.node.parent_slug == entry.node.slug
        and can_view_node(item.node, actor_permission)
    ]
    entry_shape = resolve_help_entry_shape(
        entry.node,
        actor_permission=actor_permission,
        children=children,
    )
    if feature_query:
        child_entry, child_feature_query = _resolve_child_entry(
            entry,
            feature_query,
            actor_permission=actor_permission,
            all_entries=all_entries or [entry],
        )
        if child_entry is not None:
            return await _resolve_docs_delivery_plan(
                child_entry,
                locale,
                feature_query=child_feature_query,
                actor_permission=actor_permission,
                all_entries=scoped_entries,
            )
        visible_features = tuple(
            feature
            for feature in entry.node.features
            if feature.permission == Permission.NONE
            or actor_permission.has(feature.permission)
        )
        match = match_feature(visible_features, feature_query)
        if match.status == "matched" and match.feature is not None:
            image_bytes = render_feature_deep_dive(
                entry.node,
                match.feature,
                locale=locale,
            )
            return _single_message_plan(
                _compose_help_reply(
                    image_bytes,
                    build_feature_copy_text(
                        entry.node,
                        match.feature,
                        locale=locale,
                    ),
                )
            )
        child_match = match_doc_node(children, feature_query)
        if child_match.status == "matched" and child_match.node is not None:
            child_entry = next(
                (
                    item
                    for item in scoped_entries
                    if item.node.slug == child_match.node.slug
                ),
                None,
            )
            if child_entry is not None:
                features = filter_features_by_permission(
                    child_entry.node.features,
                    actor_permission=actor_permission,
                )
                child_shape = resolve_help_entry_shape(
                    child_entry.node,
                    actor_permission=actor_permission,
                    children=tree.children_of(child_entry.node.slug),
                )
                if child_shape == "static_entry":
                    image_bytes = render_static_entry(
                        child_entry.node,
                        locale=locale,
                        actor_permission=actor_permission,
                    )
                    return _single_message_plan(
                        _compose_help_reply(
                            image_bytes,
                            build_static_entry_copy_text(
                                child_entry.node,
                                locale=locale,
                            ),
                        )
                    )
                return DeliveryPlan(
                    messages=_compose_plugin_guide_messages(
                        child_entry,
                        features=tuple(features),
                        child_entries=[],
                        locale=locale,
                        actor_permission=actor_permission,
                    )
                )
        if match.status == "ambiguous":
            return _single_message_plan(
                _build_text_plan_entry(
                    "\n".join(
                        [
                            tr(
                                locale,
                                "help.query.feature_ambiguous.title",
                                query=feature_query,
                            ),
                            tr(locale, "help.query.feature_ambiguous.hint"),
                            "",
                            *(
                                f"- {feature.title} ({feature.slug})"
                                for feature in match.candidates
                            ),
                        ]
                    ).strip()
                )
            )
        return _single_message_plan(
            _build_text_plan_entry(
                tr(locale, "help.query.not_found", query=feature_query)
            )
        )
    features = filter_features_by_permission(
        entry.node.features,
        actor_permission=actor_permission,
    )
    if entry_shape == "static_entry":
        image_bytes = render_static_entry(
            entry.node,
            locale=locale,
            actor_permission=actor_permission,
        )
        return _single_message_plan(
            _compose_help_reply(
                image_bytes,
                build_static_entry_copy_text(
                    entry.node,
                    locale=locale,
                ),
            )
        )
    if entry_shape == "overview_group":
        if features:
            return DeliveryPlan(
                messages=_compose_plugin_guide_messages(
                    entry,
                    features=features,
                    child_entries=child_entries,
                    locale=locale,
                    actor_permission=actor_permission,
                )
            )
        overview_text = str(
            render_doc_node_overview(
                entry.node,
                locale=locale,
                include_demo=False,
                actor_permission=actor_permission,
                children=children,
            )
        )
        return _single_message_plan(_build_text_plan_entry(overview_text))
    if entry_shape == "simple_leaf" and features:
        feature = features[0]
        image_bytes = render_feature_deep_dive(
            entry.node,
            feature,
            locale=locale,
            actor_permission=actor_permission,
        )
        return _single_message_plan(
            _compose_help_reply(
                image_bytes,
                build_simple_leaf_copy_text(
                    entry.node,
                    feature,
                    locale=locale,
                ),
            )
        )
    return DeliveryPlan(
        messages=_compose_plugin_guide_messages(
            entry,
            features=features,
            child_entries=child_entries,
            locale=locale,
            actor_permission=actor_permission,
        )
    )


@help_matcher.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    entries = _iter_docs_entries(locale)
    actor_permission = await _resolve_actor_permission(bot, event)
    authorized_entries = _filter_authorized_entries(entries, actor_permission)
    query = arg.extract_plain_text().strip()
    if not query:
        await _deliver_help_plan(
            bot,
            matcher,
            event,
            _single_message_plan(
                _build_index_message(authorized_entries, locale, actor_permission)
            ),
            locale=locale,
        )
        return

    match_result = _match_entry(authorized_entries, query)
    feature_query: str | None = None
    if match_result.status != "matched" and " " in query:
        plugin_query, feature_query = _split_query(query)
        match_result = _match_entry(authorized_entries, plugin_query)

    if match_result.status == "not_found":
        denied_match = _match_exact_entry(entries, query)
        if denied_match.status == "not_found" and " " in query:
            plugin_query, _ = _split_query(query)
            denied_match = _match_exact_entry(entries, plugin_query)
        if (
            denied_match.status == "matched"
            and denied_match.entry is not None
            and not _can_view_entry(denied_match.entry, actor_permission)
        ):
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=_build_permission_denied_message(denied_match.entry, locale),
                source_kind="help",
            )
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=_build_text_plan_entry(
                tr(locale, "help.query.not_found", query=query)
            ),
            source_kind="help",
        )

    if match_result.status == "ambiguous":
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=_build_ambiguous_message(
                query,
                match_result.candidates or [],
                locale,
            ),
            source_kind="help",
        )

    assert match_result.entry is not None
    if not _can_view_entry(match_result.entry, actor_permission):
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=_build_permission_denied_message(match_result.entry, locale),
            source_kind="help",
        )

    docs_plan = await _resolve_docs_delivery_plan(
        match_result.entry,
        locale,
        feature_query=feature_query,
        actor_permission=actor_permission,
        all_entries=entries,
    )
    await _deliver_help_plan(bot, matcher, event, docs_plan, locale=locale)


# forward 辅助被外部测试直接引用，显式导出供 pyright 识别。
__all__ = [
    "_resolve_forward_sender",
    "_send_help_forward",
]
