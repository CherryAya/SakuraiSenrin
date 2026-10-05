"""wordbank 视图渲染与投递层。

本模块是 wordbank 的「渲染 + 投递」边界：把业务数据渲染为 `DeliveryPlan` 并交给统一
投递层，同时登记消息引用（供回复路由复用）。它位于 handlers / guided_flow 之上、
`entry_*` 入口层之下，且**不依赖任何 entry_* 模块**，因此入口层可以直接静态导入，
不会再出现历史上的 `entry_runtime → __init__ → entry_runtime` 绕环与 getattr 派发。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupMessageEvent,
    MessageEvent,
    NoticeEvent,
)
from nonebot.matcher import Matcher
from nonebot.typing import T_State

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.interaction import clear_interaction_errors
from src.lib.interactive_recall import register_root_message
from src.lib.long_task import (
    CompositeProgressSink,
    LoggerProgressSink,
    LongTaskRunner,
    LongTaskSink,
    LongTaskSpec,
    MatcherProgressSink,
    MessageEventProgressSink,
)
from src.lib.message_delivery import DeliveryTarget
from src.lib.message_plan import (
    DeliveryPlan,
    MessagePlanInput,
    deliver_message_plan,
    render_message_plan_input,
)
from src.lib.reply_router import (
    record_reply_context_from_send_result,
)
from src.logger import logger

from .database.types import WordbankGroupDetail
from .debug import elapsed_ms, log_perf, perf_start
from .guided_flow import finish_guided_search as _guided_finish_guided_search
from .handlers import (
    build_group_detail_message,
    build_wordbank_reply_context_spec,
)
from .handlers.commands import (
    ParsedSearch,
    execute_search_page,
    parse_search_args,
    render_search_page_message,
)
from .handlers.passive import (
    CompiledPassiveResponse,
    PassiveResponse,
    compile_passive_response,
    execute_passive_post_actions,
    message_segment_stats,
)
from .services import wordbank_media_service, wordbank_service

if TYPE_CHECKING:
    from src.lib.message_delivery import DeliveryResult

    from .database.types import WordbankSearchPage


def build_progress_sink(
    *,
    bot: Bot | None,
    event: MessageEvent,
    matcher: Matcher | None = None,
) -> CompositeProgressSink:
    """按可用上下文组装长任务进度接收器。"""
    sinks: list[LongTaskSink] = [LoggerProgressSink()]
    if bot is not None:
        sinks.append(MessageEventProgressSink(bot, event))
    elif matcher is not None:
        sinks.append(MatcherProgressSink(matcher))
    return CompositeProgressSink(*sinks)


def extract_sent_message_id(
    send_result: DeliveryResult | Mapping[str, object],
) -> str | None:
    """从投递结果中取出 message_id（兼容测试替身传入 dict 的形态）。"""
    if isinstance(send_result, Mapping):
        value = send_result.get("message_id")
    else:
        value = send_result.message_id
    if value is None:
        return None
    return str(value)


def event_message_type(event: MessageEvent) -> str:
    """群消息返回 group，其余（含私聊）返回 private。"""
    return "group" if isinstance(event, GroupMessageEvent) else "private"


def notice_delivery_target(event: NoticeEvent) -> DeliveryTarget:
    """根据通知事件推导投递目标。"""
    group_id = str(getattr(event, "group_id", "") or "")
    if group_id:
        return DeliveryTarget(kind="group", target_id=group_id)
    return DeliveryTarget(
        kind="private",
        target_id=str(getattr(event, "user_id", "")),
    )


async def record_view_message(
    *,
    bot: Bot | None = None,
    send_result: DeliveryResult | Mapping[str, object],
    fallback_message: MessagePlanInput | None = None,
    event: MessageEvent,
    context_type: str,
    trigger_group_id: int,
    current_page: int,
    keyword: str,
    field: str,
    creator_id: str,
    has_image: bool,
    group_ids: Sequence[int],
) -> None:
    """登记视图类消息引用，失败时降级为告警日志。"""
    message_id = extract_sent_message_id(send_result)
    if message_id is None:
        return
    try:
        await wordbank_service.record_message_ref(
            ref_kind="view",
            message_id=message_id,
            context_type=context_type,
            trigger_group_id=trigger_group_id,
            current_page=current_page,
            keyword=keyword,
            field=field,
            creator_id=creator_id,
            has_image=has_image,
            group_ids=group_ids,
            group_id=str(getattr(event, "group_id", "") or ""),
            user_id=str(event.user_id),
            message_type=event_message_type(event),
        )
        if bot is not None and fallback_message is not None:
            await record_reply_context_from_send_result(
                bot,
                send_result=send_result,
                context_spec=build_wordbank_reply_context_spec(
                    context_kind="wordbank.view",
                    ref_kind="view",
                    trigger_group_id=trigger_group_id,
                    group_id=str(getattr(event, "group_id", "") or ""),
                    user_id=str(event.user_id),
                    message_type=event_message_type(event),
                    context_type=context_type,
                    current_page=current_page,
                    keyword=keyword,
                    field=field,
                    creator_id=creator_id,
                    has_image=has_image,
                    group_ids=group_ids,
                ),
                source_kind="wordbank_view",
                origin_message_type=event_message_type(event),
                origin_target_id=(
                    str(getattr(event, "group_id", "") or "") or str(event.user_id)
                ),
                fallback_message=render_message_plan_input(fallback_message),
            )
    except Exception as exc:
        logger.warning(f"[Wordbank] view message record skipped: {exc}")


async def record_search_result_view_message(
    *,
    bot: Bot | None = None,
    send_result: DeliveryResult | Mapping[str, object],
    fallback_message: MessagePlanInput | None = None,
    event: MessageEvent,
    parsed: ParsedSearch,
    page: WordbankSearchPage,
    has_image: bool,
) -> None:
    """登记搜索结果卡片的消息引用。"""
    await record_view_message(
        bot=bot,
        send_result=send_result,
        fallback_message=fallback_message,
        event=event,
        context_type="search_result",
        trigger_group_id=0,
        current_page=parsed.page,
        keyword=parsed.keyword,
        field=parsed.field,
        creator_id=parsed.creator_id,
        has_image=has_image,
        group_ids=[item.trigger_group_id for item in page.items],
    )


async def record_group_detail_view_message(
    *,
    bot: Bot | None = None,
    send_result: DeliveryResult | Mapping[str, object],
    fallback_message: MessagePlanInput | None = None,
    event: MessageEvent,
    trigger_group_id: int,
    page: int,
    has_image: bool,
) -> None:
    """登记群详情卡片的消息引用。"""
    await record_view_message(
        bot=bot,
        send_result=send_result,
        fallback_message=fallback_message,
        event=event,
        context_type="group_detail",
        trigger_group_id=trigger_group_id,
        current_page=page,
        keyword="",
        field="",
        creator_id="",
        has_image=has_image,
        group_ids=[trigger_group_id],
    )


async def record_passive_response_message(
    response: PassiveResponse,
    send_result: DeliveryResult | Mapping[str, object],
    *,
    bot: Bot | None = None,
    fallback_message: MessagePlanInput | None = None,
) -> None:
    """登记被动响应消息引用，失败时降级为告警日志。"""
    message_id = extract_sent_message_id(send_result)
    if message_id is None:
        return
    try:
        await wordbank_service.record_message_ref(
            ref_kind="response",
            message_id=message_id,
            trigger_group_id=response.trigger_group_id,
            trigger_variant_id=response.trigger_variant_id,
            response_item_id=response.response_item_id,
            group_id=response.group_id,
            user_id=response.user_id,
            message_type=response.message_type,
        )
        if bot is not None and fallback_message is not None:
            await record_reply_context_from_send_result(
                bot,
                send_result=send_result,
                context_spec=build_wordbank_reply_context_spec(
                    context_kind="wordbank.response",
                    ref_kind="response",
                    trigger_group_id=response.trigger_group_id,
                    trigger_variant_id=response.trigger_variant_id,
                    response_item_id=response.response_item_id,
                    group_id=response.group_id,
                    user_id=response.user_id,
                    message_type=response.message_type,
                ),
                source_kind="wordbank_response",
                origin_message_type=response.message_type,
                origin_target_id=(
                    response.group_id
                    if response.message_type == "group"
                    else response.user_id
                ),
                fallback_message=render_message_plan_input(fallback_message),
            )
    except Exception as exc:
        logger.warning(f"[Wordbank] response message record skipped: {exc}")


def group_detail_has_image(detail: WordbankGroupDetail) -> bool:
    """判断群详情（触发词或任一响应）是否含图片原子。"""
    if any(atom.kind == "image" for atom in detail.trigger_shape.atoms):
        return True
    return any(
        atom.kind == "image"
        for response in detail.responses
        for atom in response.response_shape.atoms
    )


async def _render_search_result_view(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    keyword: str,
    image_scores: dict[int, float] | None,
    state: T_State | None,
) -> None:
    parsed = parse_search_args(keyword)
    if state is None:
        page = await execute_search_page(
            wordbank_service,
            parsed=parsed,
            image_scores=image_scores,
        )
        message = await render_search_page_message(
            page,
            parsed=parsed,
            locale=locale,
            has_image=image_scores is not None,
            media_service=wordbank_media_service,
        )
        plan_result = await deliver_message_plan(
            bot,
            plan=DeliveryPlan(
                messages=(message,),
                source_kind="wordbank_view",
            ),
            event=event,
        )
        await record_search_result_view_message(
            bot=bot,
            send_result=plan_result.results[0],
            fallback_message=message,
            event=event,
            parsed=parsed,
            page=page,
            has_image=image_scores is not None,
        )
        await matcher.finish()
        return

    clear_interaction_errors(state)
    state["wordbank_locale"] = locale
    state["wordbank_guided_search_field"] = parsed.field
    state["wordbank_guided_search_keyword"] = parsed.keyword
    state["wordbank_guided_search_creator_id"] = parsed.creator_id
    state["wordbank_guided_search_has_image"] = image_scores is not None
    state["wordbank_guided_search_image_scores"] = dict(image_scores or {})
    state["wordbank_guided_search_requires_creator"] = False
    register_root_message(state, event)
    await finish_guided_search_view(
        bot,
        matcher,
        state,
        event,
        locale,
        page_number=parsed.page,
    )


async def send_search_result_view(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    keyword: str,
    image_scores: dict[int, float] | None = None,
    state: T_State | None = None,
) -> None:
    """渲染并投递搜索结果卡片。

    传入 `state` 时进入引导式搜索会话（不额外开长任务，由后续分页动作负责），
    否则以长任务进度提示包裹一次性查询。
    """
    if state is not None:
        await _render_search_result_view(
            bot,
            matcher,
            event,
            locale,
            keyword=keyword,
            image_scores=image_scores,
            state=state,
        )
        return
    async with LongTaskRunner(
        LongTaskSpec(
            task_name="wordbank.search.direct_view",
            source_kind="wordbank_view",
            prompt=tr(locale, "wordbank.view.processing"),
            threshold_ms=800,
        ),
        sink=build_progress_sink(bot=bot, event=event, matcher=matcher),
    ) as long_task:
        await long_task.advance("rendering")
        await _render_search_result_view(
            bot,
            matcher,
            event,
            locale,
            keyword=keyword,
            image_scores=image_scores,
            state=None,
        )


async def _render_group_detail_view(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    trigger_group_id: int,
    page: int,
    finish_after_send: bool,
) -> None:
    message, detail, _ = await build_group_detail_message(
        wordbank_service,
        trigger_group_id=trigger_group_id,
        page=page,
        locale=locale,
        media_service=wordbank_media_service,
    )
    plan_result = await deliver_message_plan(
        bot,
        plan=DeliveryPlan(
            messages=(message,),
            source_kind="wordbank_view",
        ),
        event=event,
    )
    await record_group_detail_view_message(
        bot=bot,
        send_result=plan_result.results[0],
        fallback_message=message,
        event=event,
        trigger_group_id=trigger_group_id,
        page=page,
        has_image=group_detail_has_image(detail),
    )
    if finish_after_send:
        await matcher.finish()


async def send_group_detail_view(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    trigger_group_id: int,
    page: int,
    finish_after_send: bool = True,
) -> None:
    """渲染并投递群详情卡片（始终包裹长任务进度提示）。"""
    async with LongTaskRunner(
        LongTaskSpec(
            task_name="wordbank.group.detail_view",
            source_kind="wordbank_view",
            prompt=tr(locale, "wordbank.view.processing"),
            threshold_ms=800,
        ),
        sink=build_progress_sink(bot=bot, event=event, matcher=matcher),
    ) as long_task:
        await long_task.advance("rendering")
        await _render_group_detail_view(
            bot,
            matcher,
            event,
            locale,
            trigger_group_id=trigger_group_id,
            page=page,
            finish_after_send=finish_after_send,
        )


async def finish_guided_search_view(
    bot: Bot,
    matcher: Matcher,
    state: T_State,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    page_number: int,
    clamp_page: bool = False,
) -> None:
    """收尾引导式搜索：长任务包裹下渲染结果卡片并进入分页会话。"""
    async with LongTaskRunner(
        LongTaskSpec(
            task_name="wordbank.search.guided_view",
            source_kind="wordbank_view",
            prompt=tr(locale, "wordbank.view.processing"),
            threshold_ms=800,
        ),
        sink=build_progress_sink(bot=bot, event=event, matcher=matcher),
    ) as long_task:
        await long_task.advance("rendering")
        await _guided_finish_guided_search(
            bot,
            matcher,
            state,
            event,
            locale,
            page_number=page_number,
            clamp_page=clamp_page,
            media_service=wordbank_media_service,
            wordbank_service=wordbank_service,
            record_search_result_view_message=record_search_result_view_message,
        )


async def deliver_passive_response(
    bot: Bot,
    response: PassiveResponse,
    *,
    locale: LocaleCode,
    source_kind: str,
    log_prefix: str,
    event: MessageEvent | None = None,
    target: DeliveryTarget | None = None,
    handle_ms: float = 0.0,
) -> CompiledPassiveResponse:
    """编译并投递被动响应，随后登记消息引用。

    统一 passive / notice 两条入口的投递循环：编译 → 发送 → 后续动作 → 记录。
    `event` 与 `target` 二选一（受 `deliver_message_plan` 的投递约束）。
    返回编译结果，调用方可据此复用（例如判断是否有可见输出）。
    """
    start = perf_start()
    build_start = perf_start()
    compiled = await compile_passive_response(
        response,
        locale=locale,
        media_service=wordbank_media_service,
    )
    build_ms = elapsed_ms(build_start)
    message = compiled.message
    image_trace_fields = compiled.image_trace_fields
    post_action_count = len(compiled.post_actions)
    if message is None and not compiled.post_actions:
        log_perf(
            f"{log_prefix}.no_output",
            start=start,
            message_type=response.message_type,
            response_item_id=response.response_item_id,
            handle_ms=f"{handle_ms:.2f}",
            build_ms=f"{build_ms:.2f}",
        )
        return compiled
    segment_count, image_segment_count = (
        message_segment_stats(message) if message is not None else (0, 0)
    )
    send_result: Any = None
    send_ms = 0.0
    if message is not None:
        log_perf(
            f"{log_prefix}.send.begin",
            message_type=response.message_type,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            post_action_count=post_action_count,
            **cast(Any, image_trace_fields),
        )
        send_start = perf_start()
        plan_result = await deliver_message_plan(
            bot,
            plan=DeliveryPlan(
                messages=(message,),
                source_kind=source_kind,
            ),
            event=event,
            target=target,
        )
        send_result = plan_result.results[0]
        send_ms = elapsed_ms(send_start)
        log_perf(
            f"{log_prefix}.send.done",
            start=send_start,
            message_type=response.message_type,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            post_action_count=post_action_count,
            **cast(Any, image_trace_fields),
        )
    action_start = perf_start()
    await execute_passive_post_actions(bot, response, compiled.post_actions)
    action_ms = elapsed_ms(action_start) if compiled.post_actions else 0.0
    record_start = perf_start()
    if send_result is not None:
        await record_passive_response_message(
            response,
            send_result,
            bot=bot,
            fallback_message=message,
        )
    record_ms = elapsed_ms(record_start)
    log_perf(
        f"{log_prefix}.sent",
        start=start,
        message_type=response.message_type,
        trigger_group_id=response.trigger_group_id,
        response_item_id=response.response_item_id,
        segment_count=segment_count,
        image_segment_count=image_segment_count,
        post_action_count=post_action_count,
        handle_ms=f"{handle_ms:.2f}",
        build_ms=f"{build_ms:.2f}",
        send_ms=f"{send_ms:.2f}",
        action_ms=f"{action_ms:.2f}",
        record_ms=f"{record_ms:.2f}",
        **cast(Any, image_trace_fields),
    )
    return compiled
