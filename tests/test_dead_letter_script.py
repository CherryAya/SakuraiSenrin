"""死信重放脚本的 handler 覆盖回归。

历史缺陷：trace writer 是懒建的模块私有实例（`trace_log._trace_writer`），
重放脚本用 `vars(module)` 扫描各 writer 模块时收不到它——trace 死信永远
匹配不到 handler，重放时只能打印"无重放 handler，暂跳过"，等于无法补偿。
"""

from __future__ import annotations

from scripts import dead_letter as dead_letter_script


def test_build_handlers_covers_lazily_created_trace_writer() -> None:
    """回归：handlers 必须包含 trace writer，否则其死信无法重放。"""
    from src.lib.trace_log import get_trace_writer

    handlers = dead_letter_script._build_handlers()

    trace_writer = get_trace_writer()
    assert trace_writer.worker_name in handlers
    assert handlers[trace_writer.worker_name] is trace_writer.flush_callback


def test_build_handlers_keeps_module_scan_results() -> None:
    """既有行为不能丢：模块级 writer（core/water/wordbank）仍要被收集。"""
    from src.lib.db.batch import BatchWriter

    handlers = dead_letter_script._build_handlers()

    from src.plugins.water.database import writers as water_writers

    module_writers = [
        value
        for value in vars(water_writers).values()
        if isinstance(value, BatchWriter)
    ]
    assert module_writers, "water writer 模块应至少有一个 writer"
    for writer in module_writers:
        assert writer.worker_name in handlers
