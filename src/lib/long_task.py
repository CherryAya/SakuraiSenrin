"""Generic long-task progress reporting helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import monotonic
from types import TracebackType
from typing import Protocol, Self

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.matcher import Matcher

from src.lib.message_plan import (
    DeliveryPlan,
    MessagePlanInput,
    deliver_message_plan,
    render_message_plan_input,
)
from src.logger import logger

LongTaskStage = str
LongTaskEventKind = str


@dataclass(slots=True, frozen=True)
class LongTaskSpec:
    task_name: str
    source_kind: str
    prompt: MessagePlanInput | None = None
    threshold_ms: int = 800
    progress_min_interval_ms: int = 700
    allow_progress_updates: bool = False


@dataclass(slots=True, frozen=True)
class LongTaskEvent:
    kind: LongTaskEventKind
    spec: LongTaskSpec
    stage: LongTaskStage
    elapsed_ms: float
    message: MessagePlanInput | None = None
    current: int | None = None
    total: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


class LongTaskSink(Protocol):
    async def handle_event(self, event: LongTaskEvent) -> None: ...


class NoOpProgressSink:
    async def handle_event(self, event: LongTaskEvent) -> None:
        _ = event


class LoggerProgressSink:
    async def handle_event(self, event: LongTaskEvent) -> None:
        details = [
            f"task={event.spec.task_name}",
            f"kind={event.kind}",
            f"stage={event.stage}",
            f"elapsed_ms={event.elapsed_ms:.2f}",
        ]
        if event.current is not None and event.total is not None:
            details.append(f"progress={event.current}/{event.total}")
        if event.metadata:
            details.extend(
                f"{key}={value}" for key, value in sorted(event.metadata.items())
            )
        logger.debug("[LongTask] " + " ".join(details))


class CompositeProgressSink:
    def __init__(self, *sinks: LongTaskSink) -> None:
        self._sinks = tuple(sinks)

    async def handle_event(self, event: LongTaskEvent) -> None:
        for sink in self._sinks:
            await sink.handle_event(event)


class MessageEventProgressSink:
    def __init__(
        self,
        bot: Bot,
        event: MessageEvent,
        *,
        progress_source_kind: str = "long_task",
    ) -> None:
        self._bot = bot
        self._event = event
        self._progress_source_kind = progress_source_kind

    async def handle_event(self, event: LongTaskEvent) -> None:
        if event.message is None:
            return
        if event.kind not in {"prompt", "progress"}:
            return
        await deliver_message_plan(
            self._bot,
            plan=DeliveryPlan(
                messages=(event.message,),
                source_kind=event.spec.source_kind or self._progress_source_kind,
                allow_asset_reuse=False,
            ),
            event=self._event,
        )


class MatcherProgressSink:
    def __init__(self, matcher: Matcher) -> None:
        self._matcher = matcher

    async def handle_event(self, event: LongTaskEvent) -> None:
        if event.message is None:
            return
        if event.kind not in {"prompt", "progress"}:
            return
        await self._matcher.send(render_message_plan_input(event.message))


class LongTaskRunner:
    def __init__(self, spec: LongTaskSpec, *, sink: LongTaskSink) -> None:
        self._spec = spec
        self._sink = sink
        self._started_at = 0.0
        self._prompt_task: asyncio.Task[None] | None = None
        self._finished = False
        self._prompt_sent = False
        self._last_progress_stage = ""
        self._last_progress_at = 0.0

    async def __aenter__(self) -> Self:
        self._started_at = monotonic()
        await self._emit("start", "queued")
        self._prompt_task = asyncio.create_task(self._emit_prompt_when_due())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        _ = exc_type, tb
        if exc is not None:
            await self.fail(
                message=None,
                metadata={"error": type(exc).__name__},
            )
            return
        await self.finish()

    @property
    def prompt_sent(self) -> bool:
        return self._prompt_sent

    async def advance(
        self,
        stage: LongTaskStage,
        *,
        message: MessagePlanInput | None = None,
        current: int | None = None,
        total: int | None = None,
        metadata: Mapping[str, object] | None = None,
        force_message: bool = False,
    ) -> None:
        progress_message = None
        if (
            message is not None
            and self._prompt_sent
            and (
                force_message
                or (
                    self._spec.allow_progress_updates
                    and self._should_emit_progress_message(stage)
                )
            )
        ):
            progress_message = message
        await self._emit(
            "progress",
            stage,
            message=progress_message,
            current=current,
            total=total,
            metadata=metadata,
        )

    async def finish(
        self,
        *,
        message: MessagePlanInput | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        await self._cancel_prompt_task()
        await self._emit(
            "finish",
            "done",
            message=message,
            metadata=metadata,
        )

    async def fail(
        self,
        *,
        message: MessagePlanInput | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        await self._cancel_prompt_task()
        await self._emit(
            "fail",
            "failed",
            message=message,
            metadata=metadata,
        )

    async def _emit_prompt_when_due(self) -> None:
        threshold_ms = max(0, self._spec.threshold_ms)
        if threshold_ms > 0:
            await asyncio.sleep(threshold_ms / 1000)
        if self._finished or self._spec.prompt is None:
            return
        self._prompt_sent = True
        await self._emit(
            "prompt",
            "queued",
            message=self._spec.prompt,
        )

    async def _cancel_prompt_task(self) -> None:
        if self._prompt_task is None:
            return
        self._prompt_task.cancel()
        try:
            await self._prompt_task
        except asyncio.CancelledError:
            pass
        self._prompt_task = None

    def _should_emit_progress_message(self, stage: LongTaskStage) -> bool:
        now = monotonic()
        if stage != self._last_progress_stage:
            self._last_progress_stage = stage
            self._last_progress_at = now
            return True
        min_interval = max(0, self._spec.progress_min_interval_ms) / 1000
        if now - self._last_progress_at >= min_interval:
            self._last_progress_at = now
            return True
        return False

    async def _emit(
        self,
        kind: LongTaskEventKind,
        stage: LongTaskStage,
        *,
        message: MessagePlanInput | None = None,
        current: int | None = None,
        total: int | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        await self._sink.handle_event(
            LongTaskEvent(
                kind=kind,
                spec=self._spec,
                stage=stage,
                elapsed_ms=(monotonic() - self._started_at) * 1000,
                message=message,
                current=current,
                total=total,
                metadata=metadata or {},
            )
        )
