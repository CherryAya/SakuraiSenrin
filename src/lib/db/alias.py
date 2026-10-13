"""对外统一的逻辑 db 名，内部按月分片，对位 ES 的 alias -> index。

alias 只持有对 SegmentStore 的引用与 ops_class / time_field 元信息；
时区归一与生命周期方法全部透传物理 store。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

import arrow
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from src.lib.db.backup import BackupSource
from src.lib.db.connectors import (
    ColdPolicy,
    MomentLike,
    SegmentArchiveReport,
    SegmentStore,
)
from src.lib.db.ops import BaseOps
from src.lib.db.schema import PatchRegistry
from src.lib.trace_log import log_trace_event
from src.lib.types import JsonValue


class OpsFactory(Protocol):
    """「能用 AsyncSession 构造的 ops」结构化视图，作 OpsT 上界。"""

    def __init__(self, session: AsyncSession) -> None: ...


# 时间窗口边界的可选形态，比 MomentLike 少字符串两档
type WindowBound = datetime | arrow.Arrow | int


def _payload_field(payload: object, field: str) -> object:
    """从批次元素按字段名取值；非映射形态抛 TypeError。"""
    if not isinstance(payload, Mapping):
        raise TypeError(f"batch payload must be a mapping, got {type(payload)!r}")
    fields = cast("Mapping[str, object]", payload)
    return fields[field]


def _as_datetime(moment: object) -> datetime:
    """窗口边界转 datetime；取月由 map_reduce 负责。"""
    if isinstance(moment, datetime):
        return moment
    if isinstance(moment, arrow.Arrow):
        return moment.datetime
    if isinstance(moment, int):
        return arrow.get(moment).datetime
    raise TypeError(f"unsupported window bound: {type(moment)!r}")


class AliasStore[OpsT: OpsFactory]:
    """按时间维度自动路由的逻辑 db：业务时间 -> 目标分片。"""

    def __init__(
        self,
        store: SegmentStore,
        *,
        ops_class: type[OpsT],
        time_field: str,
    ) -> None:
        self._store = store
        self._ops_class = ops_class
        self._time_field = time_field

    # --- 身份与配置（透传）---

    @property
    def namespace(self) -> str:
        return self._store.namespace

    @property
    def prefix(self) -> str:
        return self._store.prefix

    @property
    def fmt(self) -> str:
        return self._store.fmt

    @property
    def tz(self) -> str:
        return self._store.tz

    @property
    def cold_policy(self) -> ColdPolicy:
        return self._store.cold_policy

    @property
    def patch_registry(self) -> PatchRegistry:
        return self._store.patch_registry

    @patch_registry.setter
    def patch_registry(self, registry: PatchRegistry) -> None:
        self._store.patch_registry = registry

    @property
    def base_dir(self) -> Path:
        return self._store.base_dir

    def shard_file_for(self, moment: MomentLike) -> Path:
        """解析业务时间对应的物理分片文件路径（转发到物理 store）。"""
        return self._store.shard_file_for(moment)

    @property
    def manifest_path(self) -> Path:
        return self._store.manifest_path

    @property
    def active_window_months(self) -> int:
        return self._store.active_window_months

    @property
    def retention_months(self) -> int:
        return self._store.retention_months

    @property
    def warm_budget_mb(self) -> int:
        return self._store.warm_budget_mb

    @property
    def warm_ttl_seconds(self) -> int:
        return self._store.warm_ttl_seconds

    def get_shard_key(self, moment: datetime) -> str:
        """按 store 的分片格式解析分片键，时刻按目标时区墙钟处理。"""
        return moment.strftime(self.fmt)

    def to_local(self, moment: datetime | arrow.Arrow) -> arrow.Arrow:
        """归一化到 store 时区（供业务侧需要本地时间语义时使用）。"""
        return self._to_local(moment)

    def _to_local(self, moment: datetime | arrow.Arrow) -> arrow.Arrow:
        """store 时区归一化，与 SegmentStore._to_local 口径一致。"""
        return arrow.get(moment).to(self.tz)

    def __repr__(self) -> str:
        return (
            f"AliasStore({self.namespace}/{self.prefix}, "
            f"time_field={self._time_field!r}, tz={self.tz!r})"
        )

    # --- 路由：业务时间 -> 分片 ---

    def shard_key_for(self, moment: MomentLike) -> str:
        """把任意形式的时间（时间戳 / datetime / record_date）解析成分片键。"""
        return self._floor_month(self._route_ctx_arrow(moment)).strftime(self.fmt)

    def _route_ctx(self, moment: object) -> datetime:
        """归一化到「本分片月首零点」，形态校验由 isinstance 逐条收口。"""
        return self._floor_month(self._route_ctx_arrow(moment)).datetime

    def _route_ctx_arrow(self, moment: object) -> arrow.Arrow:
        """把任意形态的时间归一化成 arrow，再取月首零点。"""
        if isinstance(moment, arrow.Arrow):
            return moment
        if isinstance(moment, datetime):
            return arrow.get(moment)
        if isinstance(moment, str):
            # 已是分片键（store.fmt 形态），直接反解
            shard = self._parse_shard_key(moment)
            if shard is not None:
                return shard
            # record_date 形态，如 "20260901"
            return arrow.get(moment, "YYYYMMDD")
        if isinstance(moment, int):
            # 时间戳；小数值形态按 record_date 解析（YYYYMMDD）
            if 10_000_000 < moment < 100_000_000:
                return arrow.get(str(moment), "YYYYMMDD")
            return arrow.get(moment)
        raise TypeError(f"unsupported time value: {type(moment)!r}")

    def _floor_month(self, value: arrow.Arrow) -> arrow.Arrow:
        """先归一化到 store 时区再取月首零点（供分片键推导使用）。"""
        return self._to_local(value).replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    def _parse_shard_key(self, raw: str) -> arrow.Arrow | None:
        """把分片键字符串反解为该月的月初；其余形态返回 None。

        用 strptime：arrow 的 ISO 解析器认 %Y_%m 这类自定义分隔符。
        """
        try:
            return arrow.get(datetime.strptime(raw, self.fmt))
        except ValueError:
            return None

    # --- 写入 ---

    def write_session_for(
        self,
        moment: MomentLike,
    ) -> AbstractAsyncContextManager[AsyncSession]:
        """按业务时间路由的写会话（替代手动计算 time_ctx）。"""
        return self._store.write_session(time_ctx=self._route_ctx(moment))

    def read_session_for(
        self,
        moment: MomentLike,
        *,
        cold_policy: ColdPolicy | None = None,
    ) -> AbstractAsyncContextManager[AsyncSession]:
        """按业务时间路由的读会话。"""
        return self._store.read_session(
            time_ctx=self._route_ctx(moment),
            cold_policy=cold_policy,
        )

    async def write_batch[PayloadT](
        self,
        payloads: Sequence[PayloadT],
        *,
        method: Callable[[OpsT, list[PayloadT]], Awaitable[int | None]],
        time_field: str | None = None,
        emit_trace: bool = True,
    ) -> int:
        """批量写入，内部按月分组路由（对应 ES 的 _bulk）。

        method 第二参取 list 形态，与 ops 方法签名一致。
        """
        if not payloads:
            return 0

        field = time_field or self._time_field
        route_map: dict[str, list[PayloadT]] = defaultdict(list)
        route_ctx_map: dict[str, datetime] = {}
        for item in payloads:
            route_ctx = self._floor_month(
                self._route_ctx_arrow(_payload_field(item, field))
            )
            shard_key = route_ctx.strftime(self.fmt)
            route_map[shard_key].append(item)
            route_ctx_map[shard_key] = route_ctx.datetime
        total = 0
        for shard_key, grouped in route_map.items():
            trace_id: str | None = None
            if emit_trace:
                trace_id = log_trace_event(
                    event_name="route_batch",
                    source_kind="segment_write",
                    component=f"{self.namespace}.{self.prefix}",
                    status="started",
                    summary=(
                        f"[{self._ops_class.__name__}] routing batch to "
                        f"shard {shard_key}."
                    ),
                    shard_key=shard_key,
                    batch_size=len(grouped),
                    payload_json={"ops_class": self._ops_class.__name__},
                )
            try:
                async with self._store.write_session(
                    time_ctx=route_ctx_map[shard_key],
                ) as session:
                    result = await method(self._ops_class(session), grouped)
                total += int(result) if isinstance(result, int) else 0
            except Exception as exc:
                if emit_trace:
                    log_trace_event(
                        event_name="route_batch",
                        source_kind="segment_write",
                        component=f"{self.namespace}.{self.prefix}",
                        status="failed",
                        summary=(
                            f"[{self._ops_class.__name__}] batch write to "
                            f"shard {shard_key} failed."
                        ),
                        level="ERROR",
                        trace_id=trace_id,
                        shard_key=shard_key,
                        batch_size=len(grouped),
                        payload_json={
                            "ops_class": self._ops_class.__name__,
                            "error": repr(exc),
                        },
                    )
                raise
            if emit_trace:
                log_trace_event(
                    event_name="route_batch",
                    source_kind="segment_write",
                    component=f"{self.namespace}.{self.prefix}",
                    status="success",
                    summary=(
                        f"[{self._ops_class.__name__}] batch written to "
                        f"shard {shard_key}."
                    ),
                    trace_id=trace_id,
                    shard_key=shard_key,
                    batch_size=len(grouped),
                    payload_json={"ops_class": self._ops_class.__name__},
                )
        return total

    async def write_one[PayloadT](
        self,
        payload: PayloadT,
        *,
        method: Callable[[OpsT, list[PayloadT]], Awaitable[int | None]],
        time_field: str | None = None,
        emit_trace: bool = False,
    ) -> int:
        """单条写入，按数据自身时间戳路由。"""
        return await self.write_batch(
            [payload],
            method=method,
            time_field=time_field,
            emit_trace=emit_trace,
        )

    # --- 读取 ---

    async def scan[T](
        self,
        start: WindowBound,
        end: WindowBound,
        query_func: Callable[[AsyncSession], Awaitable[T]],
        *,
        cold_policy: ColdPolicy | None = None,
    ) -> list[T]:
        """按时间窗口扫描（分片枚举与并发限流由 SegmentStore 负责）。"""
        return await self._store.map_reduce(
            _as_datetime(start),
            _as_datetime(end),
            query_func,
            cold_policy=cold_policy,
        )

    async def scan_many[T](
        self,
        windows: Sequence[tuple[WindowBound, WindowBound]],
        query_func: Callable[[AsyncSession], Awaitable[T]],
        *,
        cold_policy: ColdPolicy | None = None,
        concurrency: int = 4,
    ) -> list[T]:
        """并发执行多次窗口扫描，并发度在此收口。"""
        if not windows:
            return []
        semaphore = asyncio.Semaphore(concurrency)

        async def _run(window: tuple[WindowBound, WindowBound]) -> list[T]:
            async with semaphore:
                return await self.scan(*window, query_func, cold_policy=cold_policy)

        nested = await asyncio.gather(*(_run(w) for w in windows))
        return [item for group in nested for item in group]

    async def scan_shards[T](
        self,
        shard_keys: Sequence[str],
        query_func: Callable[[AsyncSession], Awaitable[T]],
        *,
        cold_policy: ColdPolicy | None = None,
        concurrency: int | None = None,
    ) -> list[T]:
        """按显式分片键集合并发扫描，适用于分片来自文件清单的场景。"""
        if not shard_keys:
            return []
        limit = concurrency or self._store.map_reduce_concurrency
        semaphore = asyncio.Semaphore(max(1, limit))

        async def _run(shard_key: str) -> T | None:
            async with semaphore:
                try:
                    async with self.read_session_for(
                        shard_key,
                        cold_policy=cold_policy,
                    ) as session:
                        return await query_func(session)
                except FileNotFoundError:
                    if cold_policy == ColdPolicy.SKIP or (
                        cold_policy is None and self.cold_policy == ColdPolicy.SKIP
                    ):
                        return None
                    raise

        results = await asyncio.gather(*(_run(key) for key in shard_keys))
        return [item for item in results if item is not None]

    async def scan_pair[T1, T2](
        self,
        start: WindowBound,
        end: WindowBound,
        first: Callable[[AsyncSession], Awaitable[T1]],
        second: Callable[[AsyncSession], Awaitable[T2]],
        *,
        cold_policy: ColdPolicy | None = None,
    ) -> tuple[list[T1], list[T2]]:
        """同一窗口并发跑两个查询，只枚举并 hydrate 一遍分片。"""

        async def _both(session: AsyncSession) -> tuple[T1, T2]:
            return await first(session), await second(session)

        results = await self.scan(start, end, _both, cold_policy=cold_policy)
        return (
            [item[0] for item in results],
            [item[1] for item in results],
        )

    # --- 生命周期与运维（透传）---

    async def init(self, base: type[DeclarativeBase]) -> None:
        await self._store.init(base)

    async def run_archiver_task(self) -> SegmentArchiveReport:
        return await self._store.run_archiver_task()

    async def flush_manifest(self) -> None:
        await self._store.flush_manifest()

    def reset_runtime_state(self) -> None:
        """清空内存态（schema 标记、manifest 缓存、flush 任务），实现在物理 store。"""
        self._store.reset_runtime_state()

    def shard_health(self) -> list[dict[str, JsonValue]]:
        return self._store.shard_health()

    def iter_backup_sources(self) -> list[BackupSource]:
        return self._store.iter_backup_sources()

    @property
    def store(self) -> SegmentStore:
        """物理分片库，仅在确实需要绕过 alias 时使用。"""
        return self._store

    @property
    def ops_class_ref(self) -> type[BaseOps[DeclarativeBase]]:
        """物理 store 上 ops 类的公开视图，供 RoutableStore 协议结构匹配。"""
        return cast("type[BaseOps[DeclarativeBase]]", self._ops_class)

    @property
    def time_field_ref(self) -> str:
        return self._time_field

    def __getattr__(self, item: str) -> object:
        """拒绝隐式代理：私有成员与拼错的名字都直接报错。

        需要访问 store 时请显式用 .store。
        """
        raise AttributeError(
            f"AliasStore 不做隐式代理（{item!r}）；如需访问物理 store 请用 .store",
        )


def build_alias[OpsT: OpsFactory](
    store: SegmentStore,
    ops_class: type[OpsT],
    *,
    time_field: str,
) -> AliasStore[OpsT]:
    """便捷构造：给手写类型参数的调用点用。"""
    return AliasStore(store, ops_class=ops_class, time_field=time_field)


__all__ = [
    "AliasStore",
    "build_alias",
]
