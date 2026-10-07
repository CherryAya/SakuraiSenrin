"""Buffered writer 的运行态巡检与死信告警。

`BatchWriter` 在超过 `max_retries` 后把批次放进内存死信队列，但此前生产代码
没有任何消费方：一次持续性的写入失败会静默丢数据直到进程重启，且日志之外
没有可查询的状态。

这里提供三件事：

1. 汇总所有 writer 的运行态快照（对应 ES 的 _cluster/health）
2. 把死信暴露给运维脚本与告警
3. 关机前 drain + 落盘，避免正常退出时丢掉内存缓冲
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from src.lib.db.batch import BatchWriter, DeadLetterRecord
from src.lib.trace_log import log_trace_event
from src.logger import logger


class _WriterLike(Protocol):
    @property
    def worker_name(self) -> str: ...

    @property
    def health(self) -> Any: ...

    async def drain(self) -> None: ...

    def pop_dead_letters(self) -> tuple[DeadLetterRecord[Any], ...]: ...


@dataclass(slots=True, frozen=True)
class WriterHealthReport:
    """全部 writer 的巡检结果。"""

    degraded_writers: tuple[str, ...]
    dead_letter_batches: int
    dead_letter_items: int
    detail: tuple[tuple[str, Any], ...]

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded_writers)


def _collect_writers() -> list[_WriterLike]:
    """收集进程内所有已注册的 BatchWriter 实例。"""
    from src.plugins.water.database import writers as water_writers
    from src.plugins.wordbank.database import writers as wordbank_writers
    from src.services import writers as core_writers

    found: list[_WriterLike] = []
    for module in (core_writers, water_writers, wordbank_writers):
        for value in vars(module).values():
            if isinstance(value, BatchWriter):
                found.append(value)
    return found


def build_health_report() -> WriterHealthReport:
    """汇总所有 writer 的运行态，不消费死信。"""
    degraded: list[str] = []
    total_batches = 0
    total_items = 0
    detail: list[tuple[str, Any]] = []
    for writer in _collect_writers():
        health = writer.health
        detail.append((writer.worker_name, health))
        total_batches += health.dead_letter_batches
        total_items += health.dead_letter_items
        if health.is_degraded:
            degraded.append(writer.worker_name)
    return WriterHealthReport(
        degraded_writers=tuple(degraded),
        dead_letter_batches=total_batches,
        dead_letter_items=total_items,
        detail=tuple(detail),
    )


async def report_writer_health(*, alert: bool = True) -> WriterHealthReport:
    """巡检并（可选）上报降级状态。

    与 build_health_report 的区别：会写日志与 trace，供启动自检或定时任务调用。
    """
    report = build_health_report()
    if not report.is_degraded or not alert:
        return report
    logger.error(
        f"检测到 {len(report.degraded_writers)} 个 buffered writer 存在死信: "
        f"{', '.join(report.degraded_writers)}；"
        f"累计丢失 {report.dead_letter_items} 条（{report.dead_letter_batches} 批）。"
        "死信将在关机时落库到 sys_dead_letter；请尽快排查落库失败原因，"
        "并可用 scripts/dead_letter.py 重放。"
    )
    log_trace_event(
        event_name="batch_writer_degraded",
        source_kind="batch_writer",
        component="writer_health",
        status="dead_letter",
        summary=(
            f"{len(report.degraded_writers)} buffered writer(s) degraded with "
            f"{report.dead_letter_items} dead-lettered item(s)."
        ),
        level="ERROR",
        payload_json={
            "degraded_writers": list(report.degraded_writers),
            "dead_letter_batches": report.dead_letter_batches,
            "dead_letter_items": report.dead_letter_items,
        },
    )
    return report


async def drain_all_writers() -> None:
    """关机前把内存缓冲落盘，避免正常退出丢数据。"""
    for writer in _collect_writers():
        try:
            await writer.drain()
        except Exception as exc:
            logger.error(f"BatchWriter [{writer.worker_name}] drain 失败: {exc}")


async def flush_dead_letters() -> int:
    """把各 writer 的内存死信落库，返回落库条数。

    须在 drain_all_writers 之后调用：drain 会把暂存错误重抛，而新产生的死信要
    到这里才写入 sys_dead_letter，以便关机后仍可回溯与重放。
    """
    from src.services.dead_letter_store import persist_dead_letters

    total = 0
    for writer in _collect_writers():
        records = writer.pop_dead_letters()
        if not records:
            continue
        report = await persist_dead_letters(records)
        total += report.persisted
        if report.failed:
            logger.error(
                f"BatchWriter [{writer.worker_name}] 有 {report.failed} 条死信落库失败",
            )
    return total


def render_health_table() -> str:
    """渲染成对齐的文本表格，供运维脚本直接打印。"""
    report = build_health_report()
    if not report.detail:
        return "no buffered writers registered"
    header = (
        f"{'writer':<34}{'flush':>10}{'failed':>9}{'dead':>7}"
        f"{'cons_fail':>11}{'degraded':>10}"
    )
    lines = [header]
    for name, health in report.detail:
        lines.append(
            f"{name[:34]:<34}{health.total_flushed_items:>10}"
            f"{health.total_failed_items:>9}{health.dead_letter_batches:>7}"
            f"{health.consecutive_failures:>11}"
            f"{('YES' if health.is_degraded else '-'):>10}",
        )
    lines.append(f"{'TOTAL':<34}{'':>10}{'':>9}{report.dead_letter_batches:>7}")
    return "\n".join(lines)


__all__ = [
    "WriterHealthReport",
    "build_health_report",
    "drain_all_writers",
    "flush_dead_letters",
    "render_health_table",
    "report_writer_health",
]
