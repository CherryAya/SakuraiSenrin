"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-18 23:51:56
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-06-11 19:10:00
Description: 学习词库-传统版
"""

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import cast

from nonebot import on_notice
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    FriendRecallNoticeEvent,
    GroupRecallNoticeEvent,
    MessageEvent,
    NoticeEvent,
)
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import on_command
from nonebot.typing import T_State

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.guided_state import (
    cancel_guided_state_resources,
    copy_guided_state_snapshot,
    guided_locale,
    state_keys_with_prefix,
    state_value,
)
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.interaction import (
    abort_if_revoke_signal,
    clear_interaction_errors,
    record_interaction_error,
)
from src.lib.interactive_recall import (
    find_recall_session,
    is_supported_recall_notice,
    rebuild_temp_matcher,
    register_recall_checkpoint,
    register_root_message,
)
from src.lib.long_task import (
    CompositeProgressSink,
    LoggerProgressSink,
    LongTaskRunner,
    LongTaskSpec,
    MatcherProgressSink,
    MessageEventProgressSink,
)
from src.lib.message_delivery import resolve_notice_delivery_target
from src.lib.message_plan import (
    DeliveryPlan,
    MessagePlanEntry,
    MessagePlanInput,
    deliver_message_plan,
    finish_with_message,
    pause_with_message,
    reject_with_message,
)
from src.lib.plugin_docs import (
    build_doc_demo_plan_entry,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger
from src.plugins.wordbank.debug import (
    describe_batch_errors,
    describe_message_segments,
    describe_shape,
)
from src.plugins.wordbank.forward_batch import (
    build_response_input_payload,
    is_forward_input,
)
from src.plugins.wordbank.handlers.commands import _default_i18n_text
from src.plugins.wordbank.handlers.submission import (
    SubmissionLifecycle,
    SubmissionPayload,
)
from src.plugins.wordbank.message_model import MessageShape
from src.plugins.wordbank.text_parsing import has_meaningful_text

name = tr("zh-CN", "plugin.study.name")
description = tr("zh-CN", "plugin.study.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


# region 插件元数据与 matcher 注册
def _study_error_plan_entry(
    exc: Exception,
    locale: LocaleCode,
) -> MessagePlanEntry:
    from src.plugins.wordbank.handlers.commands import localize_command_error

    return build_doc_demo_plan_entry(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        locale=locale,
        prefix_text=localize_command_error(exc, locale),
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#3BC9DB",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "i18n": {
            "name_key": "plugin.study.name",
            "description_key": "plugin.study.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="fun",
            order=85,
            source=DOCS_SOURCE,
        ),
    },
)

study_command = on_command(
    "study",
    aliases={"学习"},
    priority=5,
    block=True,
)
study_recall_notice = on_notice(priority=5, block=False)
# endregion

GUIDED_MAX_ERRORS = 3
STUDY_STEP_MODE = 1
STUDY_STEP_GROUP_BLOCK = 2
STUDY_STEP_TRIGGER = 3
STUDY_STEP_RESPONSE = 4
STUDY_STEP_WEIGHT = 5
# TODO: 本组仅覆盖转发/权重待处理键，而 TRIGGER / RESPONSE 步骤注册检查点时
# 未传 cleanup_keys（默认空），撤回时各步的清理范围不一致，需确认是否有意为之。
STUDY_RECALL_PENDING_KEYS: tuple[str, ...] = (
    "study_forward_response_pending",
    "study_forward_response_event",
    "study_forward_split_shapes",
    "study_weight_pending",
)

# region recall 快照保留键
# 流程按「触发词 → 回答 → 权重」推进，每一步确认后都需要在撤回重建时恢复已确认
# 的状态。集中声明避免各处理函数散落手写、彼此漏字段。
# TRIGGER 步骤时尚未确认回答，故不含 trigger/response shape。
STUDY_SNAPSHOT_KEYS_AFTER_TRIGGER: tuple[str, ...] = (
    "study_trig_mode",
    "study_group_block",
    "study_trigger_preloaded",
    "study_response_after_preloaded_trigger",
)
# RESPONSE 步骤确认回答后追加 trigger shape 与后续步骤标记。
STUDY_SNAPSHOT_KEYS_AFTER_RESPONSE: tuple[str, ...] = (
    *STUDY_SNAPSHOT_KEYS_AFTER_TRIGGER,
    "study_trigger_shape",
    "study_weight_after_preloaded_trigger",
)
# WEIGHT 步骤只差提交本身，需再带上 response shape 供最终落库。
STUDY_SNAPSHOT_KEYS_AFTER_WEIGHT: tuple[str, ...] = (
    *STUDY_SNAPSHOT_KEYS_AFTER_RESPONSE,
    "study_response_shape",
)
# endregion


# region 引导步骤处理
# 本区与 wordbank/guided_flow.py 的 record_guided_* 系列刻意保持各自实现：
# study 独有「触发词预载」概念（study_trigger_preloaded）、五步权重流程，以及
# 合并转发选择后的权重步时序；强行统一会让调用方参数膨胀到 8-10 个字段而更难读。
# 两边共享的 state 内核已收敛到 src/lib/guided_state.py。
@lru_cache(maxsize=1)
def _build_study_submission_lifecycle() -> SubmissionLifecycle:
    from src.plugins.wordbank.services import wordbank_media_service, wordbank_service

    return SubmissionLifecycle(
        service=wordbank_service,
        media_service=wordbank_media_service,
        submission_source_kind="study_submission",
        batch_submission_source_kind="study_batch_submission",
        batch_feedback_nickname_builder=lambda locale: tr(
            locale,
            "wordbank.batch_add.study_forward_nickname",
        ),
    )


async def _finalize_study_submission(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    submission: SubmissionPayload,
    locale: LocaleCode,
    *,
    source_event: MessageEvent | None = None,
) -> None:
    await _build_study_submission_lifecycle().finalize(
        matcher,
        bot,
        event,
        submission,
        locale,
        source_event=source_event,
    )


async def _abort_study_on_revoke(
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
) -> None:
    await abort_if_revoke_signal(
        event,
        matcher,
        message=tr(locale, "interaction.cancelled"),
    )


async def _reject_study_error(
    matcher: Matcher,
    state: T_State,
    locale: LocaleCode,
    message: MessagePlanInput,
) -> None:
    if record_interaction_error(state) >= GUIDED_MAX_ERRORS:
        await finish_with_message(
            None,
            matcher,
            message=tr(locale, "interaction.too_many_errors"),
            source_kind="study_guided",
        )
        return
    await reject_with_message(matcher, message=message)


def _copy_study_state(
    state: Mapping[str, object],
    *,
    keep_keys: tuple[str, ...],
) -> dict[str, object]:
    return copy_guided_state_snapshot(
        state,
        locale_key="study_locale",
        source_event_key="study_submission_source_event",
        keep_keys=keep_keys,
    )


def _prompt_for_step(locale: LocaleCode, step_index: int) -> str:
    prompt_by_step = {
        STUDY_STEP_MODE: tr(locale, "wordbank.guided.study.mode_prompt"),
        STUDY_STEP_GROUP_BLOCK: tr(locale, "wordbank.guided.study.group_block_prompt"),
        STUDY_STEP_TRIGGER: tr(locale, "wordbank.guided.study.trigger_prompt"),
        STUDY_STEP_RESPONSE: tr(locale, "wordbank.guided.study.response_prompt"),
        STUDY_STEP_WEIGHT: tr(locale, "wordbank.guided.study.weight_prompt"),
    }
    return prompt_by_step[step_index]


def _register_study_checkpoint(
    state: T_State,
    event: MessageEvent,
    *,
    step_index: int,
    locale: LocaleCode,
    snapshot: Mapping[str, object],
    cleanup_keys: tuple[str, ...] = (),
) -> None:
    register_recall_checkpoint(
        state,
        message_id=getattr(event, "message_id", ""),
        step_index=step_index,
        prompt=_prompt_for_step(locale, step_index),
        state_snapshot=snapshot,
        cleanup_keys=cleanup_keys,
    )


def _guided_media_task(
    *,
    task_name: str,
    locale: LocaleCode,
    matcher: Matcher,
) -> LongTaskRunner:
    """构造引导式流程共用的媒体处理长任务。

    所有引导步骤的 prompt、阈值与进度来源一致，仅 task_name 不同，故在此收口。
    """
    return LongTaskRunner(
        LongTaskSpec(
            task_name=task_name,
            source_kind="study_guided",
            prompt=tr(locale, "wordbank.add.processing_with_media"),
            threshold_ms=800,
        ),
        sink=CompositeProgressSink(
            LoggerProgressSink(),
            MatcherProgressSink(matcher),
        ),
    )


def _study_locale(state: Mapping[str, object]) -> LocaleCode:
    return guided_locale(state, locale_key="study_locale")


def _contains_study_pair_separator(text: str) -> bool:
    return any(sep in text for sep in ("=>", "->", "回答", "回复"))


def _study_state_keys(state: Mapping[str, object]) -> list[str]:
    return state_keys_with_prefix(state, "study_")


async def _start_guided_study_from_partial_args(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    text: str,
    *,
    has_images: bool,
) -> bool:
    from src.plugins.wordbank.handlers.commands import (
        parse_study_group_block_choice,
        parse_study_mode_choice,
    )
    from src.plugins.wordbank.handlers.media_helpers import (
        shape_from_trigger_text_value,
    )
    from src.plugins.wordbank.services import wordbank_service
    from src.plugins.wordbank.services.rules import RuleError
    from src.plugins.wordbank.text_parsing import rest_after_token, tokenize_shell_like

    source = text
    if not source or _contains_study_pair_separator(source):
        return False
    try:
        tokens = tokenize_shell_like(source)
    except ValueError:
        return False
    if not tokens:
        return False
    try:
        trig_mode = parse_study_mode_choice(tokens[0].value)
    except RuleError:
        return False

    await wordbank_service.initialize()
    state["study_locale"] = locale
    clear_interaction_errors(state)
    register_root_message(state, event)
    state["study_trig_mode"] = trig_mode
    # 命令行已带上触发词模式，对应的步骤处理器须跳过本次输入（见命令入口级联说明）。
    state["study_skip_mode_step"] = True

    if len(tokens) == 1:
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.study.group_block_prompt"),
        )
        return True

    try:
        group_block = parse_study_group_block_choice(tokens[1].value)
    except RuleError as exc:
        await pause_with_message(
            matcher,
            message=_study_error_plan_entry(exc, locale),
        )
        return True

    state["study_group_block"] = group_block
    # 同上：分组范围已由命令行给出，分组范围步骤跳过本次输入。
    state["study_skip_group_block_step"] = True

    if len(tokens) == 2:
        if has_images:
            return False
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.study.trigger_prompt"),
        )
        return True

    trigger_text = rest_after_token(source, tokens[1])
    if not trigger_text:
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.study.trigger_prompt"),
        )
        return True

    if len(tokens) == 3 and not has_images:
        state["study_trigger_shape"] = shape_from_trigger_text_value(trigger_text)
        state["study_trigger_preloaded"] = True
        state["study_response_after_preloaded_trigger"] = True
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.study.response_prompt"),
        )
        return True

    return False


async def _cancel_study_resources(
    state: Mapping[str, object],
    cleanup_keys: tuple[str, ...] = STUDY_RECALL_PENDING_KEYS,
) -> None:
    await cancel_guided_state_resources(state, cleanup_keys)


async def _record_study_trigger(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.handlers import build_message_shape_from_message
    from src.plugins.wordbank.handlers.media_helpers import (
        shape_from_trigger_text_value,
    )
    from src.plugins.wordbank.services import wordbank_media_service

    plain_text = event.message.extract_plain_text()
    if has_meaningful_text(plain_text) and len(event.message) == 1:
        shape = shape_from_trigger_text_value(plain_text)
    else:
        long_task = _guided_media_task(
            task_name="study.guided.trigger_shape",
            locale=locale,
            matcher=matcher,
        )
        async with long_task:
            shape = await build_message_shape_from_message(
                wordbank_media_service,
                event.message,
                task=long_task,
            )
    if shape.is_empty():
        await _reject_study_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.trigger_empty"),
        )
        return
    clear_interaction_errors(state)
    locale = _study_locale(state)
    snapshot = _copy_study_state(
        state,
        keep_keys=STUDY_SNAPSHOT_KEYS_AFTER_TRIGGER,
    )
    state["study_trigger_shape"] = shape
    _register_study_checkpoint(
        state,
        event,
        step_index=STUDY_STEP_TRIGGER,
        locale=locale,
        snapshot=snapshot,
    )
    await pause_with_message(
        matcher,
        message=tr(locale, "wordbank.guided.study.response_prompt"),
    )


async def _record_study_response(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.services import wordbank_media_service

    if is_forward_input(event):
        state["study_forward_response_pending"] = True
        state["study_forward_response_event"] = event
        logger.debug(
            "[Study][guided] forward response detected | "
            f"{describe_message_segments(event.message)}"
        )
        clear_interaction_errors(state)
        locale = _study_locale(state)
        snapshot = _copy_study_state(
            state,
            keep_keys=STUDY_SNAPSHOT_KEYS_AFTER_RESPONSE,
        )
        _register_study_checkpoint(
            state,
            event,
            step_index=STUDY_STEP_RESPONSE,
            locale=locale,
            snapshot=snapshot,
        )
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.forward_response_prompt"),
        )
        return
    long_task = _guided_media_task(
        task_name="study.guided.response_shape",
        locale=locale,
        matcher=matcher,
    )
    async with long_task:
        payload = await build_response_input_payload(
            bot,
            event,
            media_service=wordbank_media_service,
            task=long_task,
        )
    shape = payload.whole_shape
    if payload.input_kind != "single":
        await _reject_study_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.forward_message_not_found"),
        )
        return
    if shape.is_empty():
        await _reject_study_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.response_empty"),
        )
        return
    clear_interaction_errors(state)
    locale = _study_locale(state)
    snapshot = _copy_study_state(
        state,
        keep_keys=STUDY_SNAPSHOT_KEYS_AFTER_RESPONSE,
    )
    state["study_response_shape"] = shape
    state["study_submission_source_event"] = event
    state["study_weight_after_preloaded_trigger"] = True
    state["study_weight_pending"] = True
    _register_study_checkpoint(
        state,
        event,
        step_index=STUDY_STEP_RESPONSE,
        locale=locale,
        snapshot=snapshot,
    )
    await pause_with_message(
        matcher,
        message=tr(locale, "wordbank.guided.study.weight_prompt"),
    )


async def _record_study_forward_response_choice(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.services import wordbank_media_service

    if not state.get("study_forward_response_pending"):
        return
    choice = event.message.extract_plain_text().strip().lower()
    state_keys = _study_state_keys(state)
    logger.debug(
        "[Study][guided] forward response choice | "
        f"choice={choice or '-'} state_keys={state_keys}"
    )
    response_event = state_value(state, "study_forward_response_event", MessageEvent)
    if response_event is None:
        logger.debug(
            "[Study][guided] forward response choice missing response_event | "
            f"choice={choice or '-'}"
        )
        await _reject_study_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.forward_message_not_found"),
        )
        return
    if choice in {"1", "whole", "整体"}:
        long_task = _guided_media_task(
            task_name="study.guided.forward_response_whole",
            locale=locale,
            matcher=matcher,
        )
        async with long_task:
            payload = await build_response_input_payload(
                bot,
                response_event,
                media_service=wordbank_media_service,
                task=long_task,
            )
        if payload.input_kind != "forward":
            await _reject_study_error(
                matcher,
                state,
                locale,
                tr(locale, "wordbank.error.forward_message_not_found"),
            )
            return
        state["study_response_shape"] = payload.whole_shape
        state["study_submission_source_event"] = response_event
        state["study_weight_after_preloaded_trigger"] = True
        state["study_weight_pending"] = True
        state.pop("study_forward_response_pending", None)
        state.pop("study_forward_response_event", None)
        state.pop("study_forward_split_shapes", None)
        whole_description = describe_shape(payload.whole_shape)
        logger.debug(
            "[Study][guided] forward response imported whole | "
            f"source_message_id={payload.source_message_id or '-'} "
            f"node_count={len(payload.split_shapes)} whole={whole_description}"
        )
        clear_interaction_errors(state)
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.study.weight_prompt"),
        )
        return
    if choice in {"2", "split", "拆开"}:
        long_task = _guided_media_task(
            task_name="study.guided.forward_response_split",
            locale=locale,
            matcher=matcher,
        )
        async with long_task:
            payload = await build_response_input_payload(
                bot,
                response_event,
                media_service=wordbank_media_service,
                task=long_task,
            )
        if payload.input_kind != "forward":
            await _reject_study_error(
                matcher,
                state,
                locale,
                tr(locale, "wordbank.error.forward_message_not_found"),
            )
            return
        if not payload.split_shapes:
            await _reject_study_error(
                matcher,
                state,
                locale,
                tr(locale, "wordbank.error.forward_message_empty"),
            )
            return
        state["study_response_shape"] = payload.split_shapes[0]
        state["study_forward_split_shapes"] = payload.split_shapes
        state["study_submission_source_event"] = response_event
        state["study_weight_after_preloaded_trigger"] = True
        state["study_weight_pending"] = True
        state.pop("study_forward_response_pending", None)
        state.pop("study_forward_response_event", None)
        first_shape = payload.split_shapes[0] if payload.split_shapes else None
        logger.debug(
            "[Study][guided] forward response imported split | "
            f"source_message_id={payload.source_message_id or '-'} "
            f"node_count={len(payload.split_shapes)} "
            f"split_count={len(payload.split_shapes)} "
            f"first={describe_shape(first_shape)}"
        )
        clear_interaction_errors(state)
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.study.weight_prompt"),
        )
        return
    await _reject_study_error(
        matcher,
        state,
        locale,
        tr(locale, "wordbank.error.forward_response_choice_invalid"),
    )


async def _record_study_weight_and_finish(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.handlers.commands import (
        parse_guided_weight,
    )
    from src.plugins.wordbank.services.rules import RuleError

    try:
        parse_guided_weight(event.message.extract_plain_text())
    except RuleError as exc:
        await _reject_study_error(
            matcher,
            state,
            locale,
            _study_error_plan_entry(exc, locale),
        )
        return
    clear_interaction_errors(state)
    state.pop("study_weight_pending", None)
    _register_study_checkpoint(
        state,
        event,
        step_index=STUDY_STEP_WEIGHT,
        locale=locale,
        snapshot=_copy_study_state(
            state,
            keep_keys=STUDY_SNAPSHOT_KEYS_AFTER_WEIGHT,
        ),
    )
    await _finish_guided_study(bot, matcher, event, state, locale)


async def _finish_guided_study(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.handlers import (
        handle_guided_study_shape_result,
    )
    from src.plugins.wordbank.services import wordbank_service
    from src.plugins.wordbank.services.rules import (
        RuleError,
        build_legacy_study_shortcut_rule,
    )

    try:
        state_keys = _study_state_keys(state)
        logger.debug(f"[Study][guided] finish start | state_keys={state_keys}")
        source_event = state_value(state, "study_submission_source_event", MessageEvent)
        trigger_shape = state_value(state, "study_trigger_shape", MessageShape)
        response_shape = state_value(state, "study_response_shape", MessageShape)
        if trigger_shape is None or trigger_shape.is_empty():
            raise RuleError(
                _default_i18n_text("wordbank.error.trigger_empty"),
                key="wordbank.error.trigger_empty",
            )
        if "study_forward_split_shapes" in state:
            split_shapes = tuple(
                shape
                for shape in state.get("study_forward_split_shapes", ())
                if isinstance(shape, MessageShape)
            )
            raw_split_count = len(
                tuple(state.get("study_forward_split_shapes", ()) or ())
            )
            logger.debug(
                "[Study][guided] finish split branch | "
                f"raw_split_count={raw_split_count} "
                f"filtered_split_count={len(split_shapes)} "
                f"trigger={describe_shape(trigger_shape)}"
            )
            if not split_shapes:
                raise RuleError(
                    _default_i18n_text("wordbank.error.response_empty"),
                    key="wordbank.error.response_empty",
                )
            raw_rule = build_legacy_study_shortcut_rule(
                str(state.get("study_trig_mode", "")),
                str(state.get("study_group_block", "")),
                is_group=bool(getattr(event, "group_id", "")),
            )
            raw_rule["weight"] = int(event.message.extract_plain_text().strip())
            batch = await wordbank_service.add_message_entries(
                trigger_shape=trigger_shape,
                response_shapes=split_shapes,
                raw_rule=raw_rule,
                group_id=str(getattr(event, "group_id", "")),
                user_id=str(event.user_id),
                is_group=bool(getattr(event, "group_id", "")),
            )
            batch_errors = describe_batch_errors(
                [item.error for item in batch.items if not item.ok]
            )
            logger.debug(
                "[Study][guided] finish split result | "
                f"total={batch.total} success={batch.success} failed={batch.failed} "
                f"errors={batch_errors}"
            )
            if batch.success <= 0:
                raise RuleError(
                    _default_i18n_text("wordbank.error.response_empty"),
                    key="wordbank.error.response_empty",
                )
            await _finalize_study_submission(
                matcher,
                bot,
                event,
                batch,
                locale=locale,
                source_event=source_event,
            )
            return
        if response_shape is None or response_shape.is_empty():
            raise RuleError(
                _default_i18n_text("wordbank.error.response_empty"),
                key="wordbank.error.response_empty",
            )
        result = await handle_guided_study_shape_result(
            wordbank_service,
            event=event,
            trig_mode_text=str(state.get("study_trig_mode", "")),
            group_block_text=str(state.get("study_group_block", "")),
            trigger_shape=trigger_shape,
            response_shape=response_shape,
            weight_text=event.message.extract_plain_text(),
        )
    except (RuleError, ValueError) as exc:
        await _reject_study_error(
            matcher,
            state,
            locale,
            _study_error_plan_entry(exc, locale),
        )
        return
    await _finalize_study_submission(
        matcher,
        bot,
        event,
        result,
        locale=locale,
        source_event=source_event,
    )


async def _start_guided_study_with_trigger_image(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    arg: Message,
) -> None:
    from src.plugins.wordbank.handlers import build_message_shape_from_message
    from src.plugins.wordbank.services import wordbank_media_service, wordbank_service

    await wordbank_service.initialize()
    state["study_locale"] = locale
    shape = await build_message_shape_from_message(wordbank_media_service, arg)
    if shape.is_empty():
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.study.mode_prompt"),
        )
        return
    clear_interaction_errors(state)
    register_root_message(state, event)
    state["study_trigger_shape"] = shape
    state["study_trigger_preloaded"] = True
    await pause_with_message(
        matcher,
        message=tr(locale, "wordbank.guided.study.mode_prompt"),
    )


# endregion


# region 命令入口
# 以下 handler 共享同一个 matcher（study_command, priority=5），按注册顺序级联：
# 命中后 pause 终止整条链；不适用则直接 return，把事件交给下一个 handler。
#
#   #study <args> → 入口：带参直接提交 / 预填步骤
#   模式          → 消费 study_skip_mode_step 后跳过
#   分组范围      → 消费 study_skip_group_block_step 后跳过
#   触发/回答     → 按 *_preloaded / *_after_preloaded_trigger 分流
#   回答/权重     → 合并转发选择 / 记录回答
#   权重与提交    → 仅在 study_weight_pending 时执行
#
# 注意：*_skip_* 均为一次性令牌，命中即 pop；重排 handler 顺序等同于改动行为。
@study_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    arg: Message = CommandArg(),
) -> None:
    from src.plugins.wordbank.handlers import (
        extract_image_urls,
        fetch_image_bytes_from_message,
        handle_study_with_media_result,
    )
    from src.plugins.wordbank.services import wordbank_media_service, wordbank_service
    from src.plugins.wordbank.services.rules import RuleError

    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await _abort_study_on_revoke(matcher, event, locale)
    arg_text = arg.extract_plain_text()
    has_images = bool(extract_image_urls(arg))
    if not has_meaningful_text(arg_text):
        if has_images:
            await _start_guided_study_with_trigger_image(
                matcher,
                event,
                state,
                locale,
                arg,
            )
            return
        await wordbank_service.initialize()
        state["study_locale"] = locale
        register_root_message(state, event)
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.study.mode_prompt"),
        )
        return
    if await _start_guided_study_from_partial_args(
        matcher,
        event,
        state,
        locale,
        arg_text,
        has_images=has_images,
    ):
        return
    await wordbank_service.initialize()
    try:
        if not has_images:
            result = await handle_study_with_media_result(
                wordbank_service,
                wordbank_media_service,
                event=event,
                text=arg.extract_plain_text(),
                image_bytes=None,
                extra_image_bytes=(),
            )
        else:
            long_task = LongTaskRunner(
                LongTaskSpec(
                    task_name="study.media_submission",
                    source_kind="study_command",
                    prompt=tr(locale, "wordbank.add.processing_with_media"),
                    threshold_ms=800,
                ),
                sink=CompositeProgressSink(
                    LoggerProgressSink(),
                    MessageEventProgressSink(bot, event),
                ),
            )
            async with long_task:
                image_items = await fetch_image_bytes_from_message(
                    arg,
                    limit=2,
                    task=long_task,
                )
                result = await handle_study_with_media_result(
                    wordbank_service,
                    wordbank_media_service,
                    event=event,
                    text=arg.extract_plain_text(),
                    image_bytes=image_items[0] if image_items else None,
                    extra_image_bytes=image_items[1:],
                    task=long_task,
                )
                await long_task.advance("submitting")
    except (RuleError, ValueError) as exc:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=_study_error_plan_entry(exc, locale),
            source_kind="study_command",
        )
        return
    await _finalize_study_submission(
        matcher,
        bot,
        event,
        result,
        locale=locale,
    )


@study_command.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    from src.plugins.wordbank.handlers.commands import (
        parse_study_mode_choice,
    )
    from src.plugins.wordbank.services.rules import RuleError

    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    # 一次性跳过令牌：命中即消费，本次输入交给后续步骤。
    if state.get("study_skip_mode_step"):
        state.pop("study_skip_mode_step", None)
        return
    text = event.message.extract_plain_text()
    try:
        parse_study_mode_choice(text)
    except RuleError as exc:
        await _reject_study_error(
            matcher,
            state,
            locale,
            _study_error_plan_entry(exc, locale),
        )
        return
    clear_interaction_errors(state)
    state["study_trig_mode"] = text
    # 触发词已预载时需连同 shape 一起保留，否则重建后丢失预载结果。
    mode_keep_keys = (
        ("study_trigger_preloaded", "study_trigger_shape")
        if state.get("study_trigger_preloaded")
        else ("study_trigger_preloaded",)
    )
    _register_study_checkpoint(
        state,
        event,
        step_index=STUDY_STEP_MODE,
        locale=locale,
        snapshot=_copy_study_state(state, keep_keys=mode_keep_keys),
        cleanup_keys=STUDY_RECALL_PENDING_KEYS,
    )
    await pause_with_message(
        matcher,
        message=tr(locale, "wordbank.guided.study.group_block_prompt"),
    )


@study_command.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    from src.plugins.wordbank.handlers.commands import (
        parse_study_group_block_choice,
    )
    from src.plugins.wordbank.services.rules import RuleError

    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    # 一次性跳过令牌：命中即消费。
    if state.get("study_skip_group_block_step"):
        state.pop("study_skip_group_block_step", None)
        return
    text = event.message.extract_plain_text()
    try:
        parse_study_group_block_choice(text)
    except RuleError as exc:
        await _reject_study_error(
            matcher,
            state,
            locale,
            _study_error_plan_entry(exc, locale),
        )
        return
    clear_interaction_errors(state)
    state["study_group_block"] = text
    trigger_preloaded = bool(state.get("study_trigger_preloaded"))
    keep_keys = (
        (
            "study_trig_mode",
            "study_trigger_preloaded",
            "study_trigger_shape",
        )
        if trigger_preloaded
        else ("study_trig_mode", "study_trigger_preloaded")
    )
    _register_study_checkpoint(
        state,
        event,
        step_index=STUDY_STEP_GROUP_BLOCK,
        locale=locale,
        snapshot=_copy_study_state(state, keep_keys=keep_keys),
        cleanup_keys=STUDY_RECALL_PENDING_KEYS,
    )
    if trigger_preloaded:
        state["study_response_after_preloaded_trigger"] = True
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.study.response_prompt"),
        )
        return
    await pause_with_message(
        matcher,
        message=tr(locale, "wordbank.guided.study.trigger_prompt"),
    )


@study_command.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    if state.get("study_weight_pending"):
        return
    if state.get("study_weight_after_preloaded_trigger"):
        return
    if state.get("study_response_after_preloaded_trigger"):
        state.pop("study_response_after_preloaded_trigger", None)
        await _record_study_response(bot, matcher, event, state, locale)
        return
    await _record_study_trigger(matcher, event, state, locale)


@study_command.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    if state.get("study_weight_pending"):
        return
    if state.get("study_forward_response_pending"):
        await _record_study_forward_response_choice(
            bot,
            matcher,
            event,
            state,
            locale,
        )
        return
    if not state_value(state, "study_trigger_shape", MessageShape):
        return
    await _record_study_response(bot, matcher, event, state, locale)


@study_command.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    if not state.get("study_weight_pending"):
        return
    await _record_study_weight_and_finish(bot, matcher, event, state, locale)


# endregion


# region 撤回 notice（文件末）
@study_recall_notice.handle()
async def _(bot: Bot, matcher: Matcher, event: NoticeEvent) -> None:
    if not is_supported_recall_notice(event):
        return

    session = find_recall_session(
        study_command,
        cast(GroupRecallNoticeEvent | FriendRecallNoticeEvent, event),
    )
    if session is None:
        return

    locale = "zh-CN"
    checkpoint = session.checkpoint
    state = session.matcher_cls._default_state
    if "study_locale" in state:
        locale = _study_locale(state)

    await _cancel_study_resources(
        state,
        checkpoint.cleanup_keys
        if checkpoint is not None and not session.is_root_message
        else STUDY_RECALL_PENDING_KEYS,
    )
    session.matcher_cls.destroy()

    if session.is_root_message or checkpoint is None:
        await deliver_message_plan(
            bot,
            plan=DeliveryPlan(
                messages=(tr(locale, "interaction.cancelled"),),
                source_kind="study_notice",
            ),
            target=resolve_notice_delivery_target(event),
        )
        return

    rebuild_temp_matcher(
        session.matcher_cls,
        study_command,
        step_index=checkpoint.step_index,
        state=checkpoint.state_snapshot,
    )
    await deliver_message_plan(
        bot,
        plan=DeliveryPlan(
            messages=(checkpoint.prompt,),
            source_kind="study_notice",
        ),
        target=resolve_notice_delivery_target(event),
    )


# endregion
