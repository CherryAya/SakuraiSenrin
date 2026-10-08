"""Wordbank passive message handling."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupMessageEvent,
    MessageEvent,
    NoticeEvent,
)

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.interaction import is_revoke_signal
from src.lib.message_plan import (
    AtRefBlock,
    FaceBlock,
    ImageBytesBlock,
    MessagePlanBlock,
    MessagePlanEntry,
    MessagePlanInput,
    RawMessageBlock,
    ReplyRefBlock,
    TextBlock,
    normalize_message_plan_entry,
)
from src.lib.utils.img import QQAvatar
from src.logger import logger
from src.plugins.wordbank.debug import elapsed_ms, log_perf, perf_start
from src.plugins.wordbank.message_model import (
    PLACEHOLDER_ACCOUNT,
    PLACEHOLDER_AVATAR,
    PLACEHOLDER_GROUP_CARD,
    PLACEHOLDER_NICKNAME,
    PLACEHOLDER_PROFILE_COMBO,
    MessageShape,
    format_at_fallback_text,
    format_event_summary_text,
    is_response_sender_target,
    is_safe_executable_at_target,
    shape_from_event,
    shape_from_message,
    shape_to_payload,
)
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.matching import SelectedMatch
from src.plugins.wordbank.services.media import MediaError, WordbankMediaService
from src.plugins.wordbank.services.rules import Role, RuleContext
from src.repositories import member_repo, user_repo

MAX_PASSIVE_IMAGES = 4
MAX_IMAGE_DOWNLOAD_BYTES = 4 * 1024 * 1024


@dataclass(slots=True, frozen=True)
class PassiveResponse:
    text: str
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    group_id: str
    user_id: str
    message_type: str
    response_shape: MessageShape | None = None


@dataclass(slots=True, frozen=True)
class PassiveImageRef:
    url: str
    name_hints: tuple[str, ...] = ()


def build_rule_context(event: MessageEvent | NoticeEvent) -> RuleContext:
    role: Role = "member"
    sender = getattr(event, "sender", None)
    sender_role = str(getattr(sender, "role", "") or "")
    if sender_role == "owner":
        role = "owner"
    elif sender_role == "admin":
        role = "admin"
    group_id = str(getattr(event, "group_id", "") or "")
    return RuleContext(
        group_id=group_id,
        user_id=str(getattr(event, "user_id", "")),
        message_type=(
            "group" if isinstance(event, GroupMessageEvent) or group_id else "private"
        ),
        sender_role=role,
    )


def extract_image_refs(event: MessageEvent) -> list[PassiveImageRef]:
    refs: list[PassiveImageRef] = []
    message = getattr(event, "original_message", None) or event.message
    for segment in message:
        if segment.type != "image":
            continue
        url = str(segment.data.get("url") or "").strip()
        if not url:
            continue
        name_hints: list[str] = [url]
        file_value = str(segment.data.get("file") or "").strip()
        if file_value:
            name_hints.append(file_value)
        url_name = Path(urlparse(url).path).name.strip()
        if url_name:
            name_hints.append(url_name)
        refs.append(PassiveImageRef(url=url, name_hints=tuple(name_hints)))
    return refs


def extract_image_urls(event: MessageEvent) -> list[str]:
    return [item.url for item in extract_image_refs(event)]


def build_message_match_shapes(
    event: MessageEvent,
    *,
    image_ids: dict[int, int],
) -> tuple[tuple[str, MessageShape], ...]:
    shapes: list[tuple[str, MessageShape]] = []
    seen_keys: set[str] = set()
    for source, message in (
        ("message", getattr(event, "message", None)),
        ("original_message", getattr(event, "original_message", None)),
    ):
        if message is None:
            continue
        shape = shape_from_message(
            message,
            image_ids=image_ids,
            preserve_blank_text=True,
        )
        if shape.is_empty():
            continue
        key = shape_to_payload(shape)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        shapes.append((source, shape))
    return tuple(shapes)


def build_passive_response(
    selected: SelectedMatch,
    *,
    context: RuleContext,
    message_type: str,
    event_trigger: str = "",
) -> PassiveResponse:
    return PassiveResponse(
        text=selected.response.text,
        trigger_group_id=selected.candidate.group.id,
        trigger_variant_id=selected.candidate.trigger.id,
        response_item_id=selected.response.id,
        group_id=context.group_id,
        user_id=context.user_id,
        message_type=message_type,
        response_shape=selected.response.message_shape,
    )


def build_event_triggers(
    event: MessageEvent | NoticeEvent,
    bot: Bot,
) -> tuple[str, ...]:
    if isinstance(event, MessageEvent):
        for message in (
            getattr(event, "message", None),
            getattr(event, "original_message", None),
        ):
            if message is None:
                continue
            for segment in message:
                if segment.type != "at":
                    continue
                target = str(segment.data.get("qq", ""))
                if target == str(bot.self_id):
                    return ("event:at", "event:mention")
        return ()

    notice_type = str(getattr(event, "notice_type", ""))
    sub_type = str(getattr(event, "sub_type", ""))
    if notice_type == "notify" and sub_type == "poke":
        target_id = str(getattr(event, "target_id", ""))
        if target_id != str(bot.self_id):
            return ()
        return ("event:poke",)
    if notice_type == "group_increase":
        if str(getattr(event, "user_id", "")) == str(bot.self_id):
            return (
                "event:bot_join",
                "event:join",
                "event:group_join",
                "event:group_increase",
            )
        return (
            "event:member_join",
            "event:join",
            "event:group_join",
            "event:group_increase",
        )
    if notice_type == "group_decrease":
        if str(getattr(event, "user_id", "")) == str(bot.self_id):
            return (
                "event:bot_leave",
                "event:leave",
                "event:group_leave",
                "event:group_decrease",
            )
        return (
            "event:member_leave",
            "event:leave",
            "event:group_leave",
            "event:group_decrease",
        )
    return ()


async def fetch_image_bytes(
    url: str,
    *,
    max_bytes: int = MAX_IMAGE_DOWNLOAD_BYTES,
    client: httpx.AsyncClient | None = None,
) -> bytes | None:
    start = perf_start()
    try:
        owned_client = client is None
        active_client = client or httpx.AsyncClient(timeout=5.0)
        try:
            async with active_client.stream("GET", url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    log_perf(
                        "passive.fetch_image_bytes.too_large",
                        start=start,
                        url=url,
                        content_length=content_length,
                        max_bytes=max_bytes,
                    )
                    return None

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        log_perf(
                            "passive.fetch_image_bytes.truncated",
                            start=start,
                            url=url,
                            downloaded_bytes=total,
                            max_bytes=max_bytes,
                        )
                        return None
                    chunks.append(chunk)
                data = b"".join(chunks)
                log_perf(
                    "passive.fetch_image_bytes.done",
                    start=start,
                    url=url,
                    bytes=len(data),
                    reused_client=not owned_client,
                )
                return data
        finally:
            if owned_client:
                await active_client.aclose()
    except Exception as exc:
        log_perf(
            "passive.fetch_image_bytes.failed",
            start=start,
            url=url,
            error=type(exc).__name__,
        )
        logger.warning(f"[Wordbank] image fetch skipped: {exc}")
        return None


async def resolve_message_image_ids(
    media_service: WordbankMediaService,
    image_refs: Sequence[PassiveImageRef],
) -> dict[int, int]:
    start = perf_start()
    canonical_ids: dict[int, int] = {}
    hint_hits = 0
    download_attempts = 0
    resolve_hits = 0
    active_refs = tuple(image_refs[:MAX_PASSIVE_IMAGES])
    shared_client: httpx.AsyncClient | None = None
    try:
        for image_ref in active_refs:
            hinted_canonical_id = media_service.resolve_canonical_id_from_hints(
                image_ref.name_hints,
            )
            if hinted_canonical_id is not None:
                hint_hits += 1
                canonical_ids[len(canonical_ids)] = hinted_canonical_id
                continue
            if shared_client is None:
                shared_client = httpx.AsyncClient(timeout=5.0)
            download_attempts += 1
            data = await fetch_image_bytes(image_ref.url, client=shared_client)
            if data is None:
                continue
            try:
                canonical_id = media_service.resolve_canonical_id(
                    data,
                    name_hints=image_ref.name_hints,
                )
            except MediaError as exc:
                logger.warning(f"[Wordbank] image match skipped: {exc}")
                continue
            if canonical_id is not None:
                resolve_hits += 1
                canonical_ids[len(canonical_ids)] = canonical_id
    finally:
        if shared_client is not None:
            await shared_client.aclose()
    log_perf(
        "passive.resolve_message_image_ids",
        start=start,
        refs=len(active_refs),
        resolved=len(canonical_ids),
        hint_hits=hint_hits,
        download_attempts=download_attempts,
        resolve_hits=resolve_hits,
    )
    return canonical_ids


async def handle_passive_message(
    bot: Bot,
    event: MessageEvent,
    service: WordbankService,
    media_service: WordbankMediaService,
) -> PassiveResponse | None:
    start = perf_start()
    if is_revoke_signal(event):
        log_perf(
            "passive.handle_message.skipped",
            start=start,
            reason="revoke_signal",
        )
        return None

    if str(event.user_id) == str(bot.self_id):
        log_perf(
            "passive.handle_message.skipped",
            start=start,
            reason="self_message",
        )
        return None

    context = build_rule_context(event)
    image_refs = extract_image_refs(event)
    resolve_start = perf_start()
    image_ids = await resolve_message_image_ids(media_service, image_refs)
    resolve_elapsed = elapsed_ms(resolve_start)
    message_shapes = build_message_match_shapes(event, image_ids=image_ids)
    primary_message_shape = (
        message_shapes[0][1]
        if message_shapes
        else shape_from_message(
            event.message,
            image_ids=image_ids,
            preserve_blank_text=True,
        )
    )
    selected: SelectedMatch | None = None
    message_match_elapsed = 0.0
    attempted_message_sources: list[str] = []
    for message_source, message_shape in message_shapes:
        attempted_message_sources.append(message_source)
        match_start = perf_start()
        selected = await service.match_message(
            message_shape,
            context=context,
            message_type="message",
        )
        message_match_elapsed += elapsed_ms(match_start)
        if selected is not None:
            log_perf(
                "passive.handle_message.matched",
                start=start,
                group_id=context.group_id or "-",
                user_id=context.user_id,
                image_refs=len(image_refs),
                resolved_images=len(image_ids),
                shape_atoms=len(message_shape.atoms),
                match_stage="message",
                message_source=message_source,
                message_attempts=len(attempted_message_sources),
                message_match_ms=f"{message_match_elapsed:.2f}",
                image_resolve_ms=f"{resolve_elapsed:.2f}",
                response_item_id=selected.response.id,
            )
            return build_passive_response(
                selected,
                context=context,
                message_type="message",
            )

    event_triggers = build_event_triggers(event, bot)
    event_match_count = 0
    if event_triggers:
        for event_trigger in event_triggers:
            event_match_count += 1
            match_start = perf_start()
            selected = await service.match_message(
                shape_from_event(event_trigger),
                context=context,
                message_type="event",
            )
            event_match_elapsed = elapsed_ms(match_start)
            if selected is not None:
                log_perf(
                    "passive.handle_message.matched",
                    start=start,
                    group_id=context.group_id or "-",
                    user_id=context.user_id,
                    image_refs=len(image_refs),
                    resolved_images=len(image_ids),
                    shape_atoms=len(primary_message_shape.atoms),
                    match_stage=event_trigger,
                    message_attempts=len(attempted_message_sources),
                    message_match_ms=f"{message_match_elapsed:.2f}",
                    event_match_ms=f"{event_match_elapsed:.2f}",
                    image_resolve_ms=f"{resolve_elapsed:.2f}",
                    response_item_id=selected.response.id,
                )
                return build_passive_response(
                    selected,
                    context=context,
                    message_type="event",
                    event_trigger=event_trigger,
                )
    log_perf(
        "passive.handle_message.miss",
        start=start,
        group_id=context.group_id or "-",
        user_id=context.user_id,
        image_refs=len(image_refs),
        resolved_images=len(image_ids),
        shape_atoms=len(primary_message_shape.atoms),
        message_attempts=len(attempted_message_sources),
        message_match_ms=f"{message_match_elapsed:.2f}",
        image_resolve_ms=f"{resolve_elapsed:.2f}",
        event_triggers=event_match_count,
    )
    return None


async def handle_passive_notice(
    bot: Bot,
    event: NoticeEvent,
    service: WordbankService,
) -> PassiveResponse | None:
    start = perf_start()
    if is_revoke_signal(event):
        log_perf(
            "passive.handle_notice.skipped",
            start=start,
            reason="revoke_signal",
        )
        return None

    event_triggers = build_event_triggers(event, bot)
    if not event_triggers:
        log_perf(
            "passive.handle_notice.skipped",
            start=start,
            reason="no_event_trigger",
        )
        return None

    context = build_rule_context(event)
    for event_trigger in event_triggers:
        match_start = perf_start()
        selected = await service.match_message(
            shape_from_event(event_trigger),
            context=context,
            message_type="event",
        )
        match_elapsed = elapsed_ms(match_start)
        if selected is not None:
            log_perf(
                "passive.handle_notice.matched",
                start=start,
                group_id=context.group_id or "-",
                user_id=context.user_id,
                event_trigger=event_trigger,
                event_match_ms=f"{match_elapsed:.2f}",
                response_item_id=selected.response.id,
            )
            return build_passive_response(
                selected,
                context=context,
                message_type="event",
                event_trigger=event_trigger,
            )
    log_perf(
        "passive.handle_notice.miss",
        start=start,
        group_id=context.group_id or "-",
        user_id=context.user_id,
        event_triggers=len(event_triggers),
    )
    return None


# ---------------------------------------------------------------------------
# 被动响应编译与投递
#
# 把 `PassiveResponse` 编译为可投递的消息规划，并统一 passive / notice 两条
# 入口的投递循环。原本这两份循环内联在 entry_runtime 的闭包里，逻辑重复。
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class PassivePokeAction:
    """待执行的被动戳一戳动作。"""

    target_id: str


@dataclass(slots=True, frozen=True)
class PassiveProfilePlaceholderData:
    """`[账号]` / `[昵称]` / `[群名片]` / `[xx]` 占位符的解析结果。"""

    account: str
    nickname: str
    group_card: str
    combo_text: str


@dataclass(slots=True, frozen=True)
class CompiledPassiveResponse:
    """编译后的被动响应：消息规划 + 图片埋点 + 后续动作。"""

    message: MessagePlanInput | None
    image_trace_fields: dict[str, object]
    post_actions: tuple[PassivePokeAction, ...] = ()


def message_segment_stats(message: MessagePlanInput) -> tuple[int, int]:
    """统计消息规划里的有效段数与图片段数。"""
    entry = normalize_message_plan_entry(message)
    segment_count = 0
    image_count = 0
    for block in entry.blocks:
        if isinstance(block, TextBlock):
            if block.text:
                segment_count += 1
            continue
        if isinstance(block, ImageBytesBlock):
            segment_count += 1
            image_count += 1
            continue
        if isinstance(block, ReplyRefBlock):
            if block.message_id.isdigit():
                segment_count += 1
            continue
        if isinstance(block, RawMessageBlock):
            raw_segments = list(block.message)
            segment_count += len(raw_segments)
            image_count += sum(1 for segment in raw_segments if segment.type == "image")
            continue
        segment_count += 1
    return (
        segment_count,
        image_count,
    )


def _image_payload_trace_fields(
    trace_fields: Mapping[str, object] | None,
) -> dict[str, object]:
    """从图片统计里挑出需要落日志的埋点字段。"""
    if trace_fields is None:
        return {}
    payload: dict[str, object] = {}
    for key in (
        "requested_image_ids",
        "loaded_image_ids",
        "loaded_image_sizes",
        "loaded_count",
        "missing_count",
        "image_total_bytes",
        "image_max_bytes",
    ):
        value = trace_fields.get(key)
        if value is not None:
            payload[key] = value
    return payload


def _resolve_passive_target_id(response: PassiveResponse, target_id: str) -> str:
    if is_response_sender_target(target_id):
        return str(response.user_id).strip()
    return str(target_id).strip()


async def _resolve_passive_profile_placeholder_data(
    response: PassiveResponse,
) -> PassiveProfilePlaceholderData:
    account = str(response.user_id).strip()
    group_id = str(response.group_id).strip()
    nickname_task = (
        user_repo.get_name_by_uid(account) if account else asyncio.sleep(0, result=None)
    )
    group_card_task = (
        member_repo.get_card_by_uid_gid(account, group_id)
        if account and group_id
        else asyncio.sleep(0, result=None)
    )
    nickname_value, group_card_value = await asyncio.gather(
        nickname_task,
        group_card_task,
    )
    nickname = str(nickname_value or "").strip() or account
    raw_group_card = str(group_card_value or "").strip()
    group_card = raw_group_card or nickname
    combo_text = nickname
    if raw_group_card and raw_group_card != nickname:
        combo_text += f"({raw_group_card})"
    combo_text += f"[{account}]"
    return PassiveProfilePlaceholderData(
        account=account,
        nickname=nickname,
        group_card=group_card,
        combo_text=combo_text,
    )


async def _render_profile_avatar(account: str) -> bytes | None:
    if not account:
        return None
    try:
        avatar = await QQAvatar.fetch_user(account, size=160)
        buffer = await asyncio.to_thread(avatar.save, "PNG")
        if hasattr(buffer, "getvalue"):
            return bytes(buffer.getvalue())
        if isinstance(buffer, (bytes, bytearray)):
            return bytes(buffer)
    except Exception as exc:
        logger.debug(
            f"[Wordbank] passive profile avatar skipped | user_id={account} error={exc}"
        )
    return None


async def _load_passive_image_bytes(
    shape: MessageShape,
    media_service: WordbankMediaService,
) -> dict[int, bytes | None]:
    image_ids = {
        atom.canonical_image_id
        for atom in shape.atoms
        if atom.kind == "image" and atom.canonical_image_id is not None
    }
    if not image_ids:
        return {}
    ordered_ids = sorted(image_ids)
    loaded = await asyncio.gather(
        *(
            media_service.load_canonical_storage_bytes(image_id)
            for image_id in ordered_ids
        )
    )
    return dict(zip(ordered_ids, loaded, strict=False))


def _passive_image_payload_stats(
    image_bytes_by_id: Mapping[int, bytes | None],
) -> dict[str, object]:
    requested_image_ids = tuple(sorted(image_bytes_by_id))
    loaded_pairs = tuple(
        (image_id, len(image_bytes))
        for image_id, image_bytes in sorted(image_bytes_by_id.items())
        if image_bytes is not None
    )
    loaded_count = len(loaded_pairs)
    image_total_bytes = sum(size for _, size in loaded_pairs)
    return {
        "requested_image_ids": requested_image_ids,
        "loaded_image_ids": tuple(image_id for image_id, _ in loaded_pairs),
        "loaded_image_sizes": tuple(size for _, size in loaded_pairs),
        "loaded_count": loaded_count,
        "missing_count": len(image_bytes_by_id) - loaded_count,
        "image_total_bytes": image_total_bytes,
        "image_max_bytes": max((size for _, size in loaded_pairs), default=0),
    }


def _log_passive_missing_images(
    *,
    locale: LocaleCode,
    image_bytes_by_id: Mapping[int, bytes | None],
    media_service: WordbankMediaService,
    response_item_id: int,
) -> None:
    missing_image_ids = tuple(
        image_id
        for image_id, image_bytes in sorted(image_bytes_by_id.items())
        if image_bytes is None
    )
    if not missing_image_ids:
        return
    details = [
        media_service.describe_canonical_image_state(image_id)
        for image_id in missing_image_ids
    ]
    logger.warning(
        "[Wordbank] image render fallback | "
        f"stage=compile_passive_response locale={locale} "
        f"missing_image_ids={missing_image_ids} "
        f"details={details} response_item_id={response_item_id}"
    )


async def compile_passive_response(
    response: PassiveResponse,
    *,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> CompiledPassiveResponse:
    """把被动响应编译为消息规划。

    `media_service` 必须显式传入：调用方持有 service 单例，避免在渲染层隐式
    依赖全局注册表，也便于测试替换。
    """
    start = perf_start()
    shape = response.response_shape
    if shape is None or shape.is_empty():
        text_value = response.text
        log_perf(
            "passive.build_passive_message.text_only",
            start=start,
            response_item_id=response.response_item_id,
        )
        if not text_value:
            return CompiledPassiveResponse(message=None, image_trace_fields={})
        return CompiledPassiveResponse(
            message=text_value,
            image_trace_fields={},
        )
    image_atom_count = sum(1 for atom in shape.atoms if atom.kind == "image")
    log_perf(
        "passive.build_passive_message.render_shape.begin",
        response_item_id=response.response_item_id,
        atom_count=len(shape.atoms),
        image_atom_count=image_atom_count,
    )
    image_bytes_by_id = await _load_passive_image_bytes(shape, media_service)
    payload_stats = _passive_image_payload_stats(image_bytes_by_id)
    _log_passive_missing_images(
        locale=locale,
        image_bytes_by_id=image_bytes_by_id,
        media_service=media_service,
        response_item_id=response.response_item_id,
    )
    log_perf(
        "passive.build_passive_message.render_shape.images_loaded",
        start=start,
        response_item_id=response.response_item_id,
        **payload_stats,
    )
    blocks: list[MessagePlanBlock] = []
    post_actions: list[PassivePokeAction] = []
    image_segments = 0
    profile_data: PassiveProfilePlaceholderData | None = None
    profile_avatar_bytes: bytes | None = None
    profile_avatar_loaded = False
    for atom in shape.atoms:
        if atom.kind == "text" and atom.text:
            blocks.append(TextBlock(atom.text))
            continue
        if atom.kind == "face" and atom.face_id is not None:
            blocks.append(FaceBlock(atom.face_id))
            continue
        if atom.kind == "at" and atom.target_id:
            resolved_target_id = _resolve_passive_target_id(response, atom.target_id)
            if resolved_target_id:
                if is_safe_executable_at_target(resolved_target_id):
                    blocks.append(AtRefBlock(resolved_target_id))
                else:
                    blocks.append(
                        TextBlock(format_at_fallback_text(resolved_target_id))
                    )
            continue
        if atom.kind == "image" and atom.canonical_image_id is not None:
            image_bytes = image_bytes_by_id.get(atom.canonical_image_id)
            if image_bytes is None:
                blocks.append(TextBlock(tr(locale, "wordbank.render.image_missing")))
                continue
            blocks.append(ImageBytesBlock(image_bytes))
            image_segments += 1
            continue
        if atom.kind == "placeholder" and atom.placeholder_name:
            if profile_data is None:
                profile_data = await _resolve_passive_profile_placeholder_data(response)
            if atom.placeholder_name == PLACEHOLDER_ACCOUNT:
                if profile_data.account:
                    blocks.append(TextBlock(profile_data.account))
                continue
            if atom.placeholder_name == PLACEHOLDER_NICKNAME:
                if profile_data.nickname:
                    blocks.append(TextBlock(profile_data.nickname))
                continue
            if atom.placeholder_name == PLACEHOLDER_GROUP_CARD:
                if profile_data.group_card:
                    blocks.append(TextBlock(profile_data.group_card))
                continue
            if atom.placeholder_name == PLACEHOLDER_PROFILE_COMBO:
                if profile_data.combo_text:
                    blocks.append(TextBlock(profile_data.combo_text))
                if not profile_avatar_loaded:
                    profile_avatar_bytes = await _render_profile_avatar(
                        profile_data.account
                    )
                    profile_avatar_loaded = True
                if profile_avatar_bytes is not None:
                    blocks.append(ImageBytesBlock(profile_avatar_bytes))
                    image_segments += 1
                continue
            if atom.placeholder_name == PLACEHOLDER_AVATAR:
                if not profile_avatar_loaded:
                    profile_avatar_bytes = await _render_profile_avatar(
                        profile_data.account
                    )
                    profile_avatar_loaded = True
                if profile_avatar_bytes is not None:
                    blocks.append(ImageBytesBlock(profile_avatar_bytes))
                    image_segments += 1
                continue
        if atom.kind == "event" and atom.event_name == "event:poke":
            resolved_target_id = _resolve_passive_target_id(response, atom.target_id)
            if resolved_target_id:
                post_actions.append(PassivePokeAction(target_id=resolved_target_id))
                continue
            logger.debug(
                "[Wordbank] passive poke skipped | "
                f"response_item_id={response.response_item_id} reason=empty_target"
            )
            continue
        if atom.kind == "event" and atom.event_name:
            blocks.append(
                TextBlock(format_event_summary_text(atom.event_name, atom.target_id))
            )
    message: MessagePlanInput | None = None
    if blocks:
        message = MessagePlanEntry(blocks=tuple(blocks))
    log_perf(
        "passive.build_passive_message.render_shape.segment_built",
        segments=len(blocks),
        image_segments=image_segments,
        post_action_count=len(post_actions),
        **payload_stats,
        response_item_id=response.response_item_id,
    )
    image_trace_fields = _image_payload_trace_fields(payload_stats)
    log_perf(
        "passive.build_passive_message.rendered_shape",
        start=start,
        response_item_id=response.response_item_id,
        atoms=len(shape.atoms),
        segments=len(blocks),
        post_action_count=len(post_actions),
        **image_trace_fields,
    )
    return CompiledPassiveResponse(
        message=message,
        image_trace_fields=image_trace_fields,
        post_actions=tuple(post_actions),
    )


async def build_passive_message(
    response: PassiveResponse,
    *,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> tuple[MessagePlanInput, dict[str, object]]:
    """兼容旧接口：返回 `(消息规划, 图片埋点)`。"""
    compiled = await compile_passive_response(
        response,
        locale=locale,
        media_service=media_service,
    )
    return (
        compiled.message or MessagePlanEntry(blocks=()),
        compiled.image_trace_fields,
    )


async def execute_passive_post_actions(
    bot: Bot,
    response: PassiveResponse,
    actions: tuple[PassivePokeAction, ...],
) -> None:
    if not actions:
        return
    # 暂时关闭主动戳一戳执行，保留被动事件匹配与正常消息投递不变。
    logger.info(
        "[Wordbank] passive poke disabled | "
        f"response_item_id={response.response_item_id} action_count={len(actions)}"
    )
    return
