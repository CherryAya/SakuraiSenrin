"""对外统一的逻辑 db 名，内部按月分片。

对位 Elasticsearch 的 alias -> index 关系：业务侧只认一个逻辑名（alias），
底层物理存储按月分片（index）。调用方不再需要自己把业务时间戳换算成分片键，
也不需要「先算出 ``2026_09`` 再反解回 datetime」——这两步绕圈正是历史时区
错位 bug 的温床。

设计约束：

* alias **不持有任何状态**，只持有对 SegmentStore 的引用与两个元信息
  （ops_class / time_field），因此上一轮的锁、inode 标记、ILM 全部复用。
* 时区口径只在 SegmentStore._to_local 一处实现，alias 不重复推导。
* 生命周期与运维方法（init / run_archiver_task / iter_backup_sources /
  shard_health）全部透传，alias 层完全隐形。
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
from src.lib.db.connectors import ColdPolicy, MomentLike, SegmentStore
from src.lib.db.ops import BaseOps
from src.lib.db.schema import PatchRegistry
from src.lib.trace_log import log_trace_event
from src.lib.types import JsonValue


class OpsFactory(Protocol):
    """「能用 ``AsyncSession`` 构造的 ops」结构化视图。

    仅用于 ``write_batch`` 内部构造 ops 那一处的 cast：``AliasStore`` 的 OpsT
    不带约束（原因见类头说明），静态期看不到「ops_class 可被 session 构造」，
    这里把该运行时事实写实。不把 OpsFactory 反过来做成 AliasStore 的约束——
    那会拦住 unbounded ``OpsT`` 的调用点（如 execute_batch_write）。

    真实实现是 ``BaseOps.__init__(self, session: AsyncSession)``。
    """

    def __init__(self, session: AsyncSession) -> None: ...


# 时间窗口边界的可选形态；比 MomentLike 少一档：窗口端点不接受分片键 / record_date
# 字符串。
type WindowBound = datetime | arrow.Arrow | int


def _payload_field(payload: object, field: str) -> object:
    """从批次元素按字段名取值（元素形态由调用方保证，见 write_batch）。

    批次元素在调用点是 TypedDict（运行时即 dict），但 ``write_batch`` 的
    PayloadT 不能绑 ``Mapping``——那样会排除 TypedDict；不绑则静态期看不到
    下标操作。这里用 ``Mapping`` 收口这一处动态取值，非映射形态直接抛
    TypeError，与原先 ``item[field]`` 的失败语义一致。
    """
    if not isinstance(payload, Mapping):
        raise TypeError(f"batch payload must be a mapping, got {type(payload)!r}")
    # isinstance 只把 object 收窄成 Mapping[Unknown, Unknown]；批次字段名恒为
    # str，此处把「键为 str、值为 object」写实，值仍保持未知，不放松到 Any。
    fields = cast("Mapping[str, object]", payload)
    return fields[field]


def _as_datetime(moment: object) -> datetime:
    """窗口边界转 datetime；不做取月（map_reduce 内部负责枚举分片）。

    与 ``_route_ctx`` 同理：入参声明为 ``object``，形态校验由下面逐条 isinstance
    收口，不合法的值照旧抛 TypeError。
    """
    if isinstance(moment, datetime):
        return moment
    if isinstance(moment, arrow.Arrow):
        return moment.datetime
    if isinstance(moment, int):
        return arrow.get(moment).datetime
    raise TypeError(f"unsupported window bound: {type(moment)!r}")


class AliasStore[OpsT]:
    """按时间维度自动路由的逻辑 db。

    ``store`` 是物理分片库；本类只负责把「业务时间」翻译成「目标分片」。

    这里刻意不约束 ``OpsT``（旧写法 ``OpsT: BaseOps[DeclarativeBase]``）：具体
    ops 都是 ``BaseOps[具体模型]``，而 ``BaseOps`` 的模型参数不变，
    ``BaseOps[TraceEventLog]`` 判不进 ``BaseOps[DeclarativeBase]``，于是
    ``AliasStore[TraceEventLogOps]`` 这类参数化会成片报
    reportInvalidTypeArguments。真实约束是「``ops_class`` 能用 ``AsyncSession``
    构造、实例可交给 ``method``」，运行时由构造参数保证。
    """

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

    # ------------------------------------------------------------------
    # 身份与配置（透传，便于 instances.py / 运维脚本统一处理）
    # ------------------------------------------------------------------

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
        """按 store 的分片格式解析分片键。

        与 SegmentStore._get_shard_key 同语义：直接对给定时刻按 fmt 格式化，
        不做时区换算（调用方传入的已是目标时区的墙钟时间）。
        """
        return moment.strftime(self.fmt)

    def to_local(self, moment: datetime | arrow.Arrow) -> arrow.Arrow:
        """归一化到 store 时区（供业务侧需要本地时间语义时使用）。"""
        return self._to_local(moment)

    def _to_local(self, moment: datetime | arrow.Arrow) -> arrow.Arrow:
        """store 时区归一化，与 SegmentStore._to_local 口径一致。

        ``arrow.get`` 对 naive datetime 也按 UTC 解释，返回的 Arrow 恒带 tzinfo，
        因此「无时区则就地打标」的分支不可达；等价于直接换算到 store 时区。
        """
        return arrow.get(moment).to(self.tz)

    def __repr__(self) -> str:
        return (
            f"AliasStore({self.namespace}/{self.prefix}, "
            f"time_field={self._time_field!r}, tz={self.tz!r})"
        )

    # ------------------------------------------------------------------
    # 路由：业务时间 -> 分片
    # ------------------------------------------------------------------

    def shard_key_for(self, moment: MomentLike) -> str:
        """把任意形式的时间（时间戳 / datetime / record_date）解析成分片键。"""
        return self._floor_month(self._route_ctx_arrow(moment)).strftime(self.fmt)

    def _route_ctx(self, moment: object) -> datetime:
        """归一化到「本分片月首零点」，交由 SegmentStore 统一处理时区。

        入参声明为 ``object``：调用方可能是分片键/record_date 字符串、时间戳，也
        可能直接把 payload dict 里取出的原始 JSON 值丢进来（见 ``write_batch``）。
        真正的形态校验由下面逐条 isinstance 收口，不合法的值照旧抛 TypeError。
        """
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
        """把分片键字符串反解为该月的月初；不是分片键则返回 None。

        用 strptime 而非 arrow.get：arrow 的 ISO 解析器无法处理 ``%Y_%m``
        这类含自定义分隔符的格式。
        """
        try:
            return arrow.get(datetime.strptime(raw, self.fmt))
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

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

        取代原先散落在各 repo 的「先按月分组、再把 route_key 反解回 datetime」
        两步走；分片口径只在这里与 SegmentStore 各实现一次。

        ``method`` 第二参声明为 ``list``（而非 ``Sequence``）：实现按分片把批次
        以 ``list`` 形态交给 ops，且实际的 ops 方法签名就是 ``list[...]``；
        ``Callable`` 参数逆变，声明成 ``Sequence`` 会拒绝真实方法，也会放过
        与调用形态不符的签名。
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
                    # OpsT 已省略约束（见类头说明）；此处由构造参数保证
                    # 「能用 session 构造」，静态期不可表达，显式 cast 收口。
                    ops = cast("type[OpsFactory]", self._ops_class)(session)
                    result = await method(ops, grouped)  # type: ignore[arg-type]
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

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

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
        """并发执行多次窗口扫描。

        取代原先 water 侧 ``asyncio.gather`` 直接打所有分片的裸并发写法：
        并发度在此收口，且每个窗口各自受 SegmentStore 的跨度上限保护。
        """
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
        """按显式分片键集合并发扫描。

        适用于「分片来自文件清单而非时间窗口」的场景（如 water 只扫已存在的
        归档分片）；并发度在此收口，取代原先的裸 asyncio.gather。
        """
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
        """同一窗口并发跑两个查询，只枚举并 hydrate 一遍分片。

        对应 ES 在一次 search 里取多个聚合；原先是两个独立的 map_reduce，
        各自枚举分片并各 hydrate 一遍，冷分片会被解压两次。
        """

        async def _both(session: AsyncSession) -> tuple[T1, T2]:
            return await first(session), await second(session)

        results = await self.scan(start, end, _both, cold_policy=cold_policy)
        return (
            [item[0] for item in results],
            [item[1] for item in results],
        )

        # ------------------------------------------------------------------

    # 生命周期与运维（透传，alias 层无状态）
    # ------------------------------------------------------------------

    async def init(self, base: type[DeclarativeBase]) -> None:
        await self._store.init(base)

    async def run_archiver_task(self) -> None:
        await self._store.run_archiver_task()

    async def flush_manifest(self) -> None:
        await self._store.flush_manifest()

    def reset_runtime_state(self) -> None:
        """清空内存态（schema 初始化标记、manifest 缓存、flush 任务）。

        分片文件被物理删除后必须调用，否则会沿用指向已失效 inode 的标记。

        这里刻意触碰物理 store 的私有实现细节：这些字段由 SegmentStore 独占
        维护，alias 层没有、也不应该有对应状态，transparent 透传是唯一入口。
        结构化的 Protocol 无法绕开 reportPrivateUsage（作用域按声明类判定），
        故在此显式豁免这四处直接访问。
        """
        self._store._initialized_shards.clear()  # type: ignore[attr-defined]
        self._store._manifest = None  # type: ignore[attr-defined]
        self._store._manifest_dirty = False  # type: ignore[attr-defined]
        self._store._manifest_flush_task = None  # type: ignore[attr-defined]

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
        """物理 store 上 ops 类的公开视图，供 RoutableStore 协议结构匹配。

        ops 类在运行时就是 BaseOps 的某个参数化子类；``BaseOps`` 的模型参数
        不变，无法用类型系统表达「上转型到具体模型」，故这里 cast 成基类参数化
        形态。该 cast 只用于跨层协议匹配，不参与任何业务判断。
        """
        return cast("type[BaseOps[DeclarativeBase]]", self._ops_class)

    @property
    def time_field_ref(self) -> str:
        return self._time_field

    def __getattr__(self, item: str) -> object:
        """不做隐式代理：私有成员与拼错的名字都直接报错。

        迁移期曾对全部属性无条件转发（``getattr(self._store, item)``），那会让
        ``_manifest`` / ``_initialized_shards`` 之类的实现细节被业务层静默穿透，
        拼错属性名也不会在开发期暴露。需要访问 store 时请显式用 ``.store``；
        需要跨层读取的只读属性已在上面逐个显式代理。
        """
        raise AttributeError(
            f"AliasStore 不做隐式代理（{item!r}）；如需访问物理 store 请用 .store",
        )


def build_alias[OpsT](
    store: SegmentStore,
    ops_class: type[OpsT],
    *,
    time_field: str,
) -> AliasStore[OpsT]:
    """便捷构造（保留给不使用类型推导的调用点）。"""
    return AliasStore(store, ops_class=ops_class, time_field=time_field)


__all__ = [
    "AliasStore",
    "build_alias",
]
