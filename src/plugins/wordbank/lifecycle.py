"""wordbank 初始化生命周期（无 NoneBot driver / scheduler 副作用，可安全早期导入）。"""

from __future__ import annotations

from src.plugins.wordbank.debug import elapsed_ms, log_perf, perf_start

from .services import wordbank_media_service, wordbank_service

_wordbank_initialized = False


async def initialize_wordbank_plugin() -> None:
    global _wordbank_initialized
    if _wordbank_initialized:
        log_perf("plugin.initialize.cached", initialized=True)
        return
    start = perf_start()
    service_start = perf_start()
    await wordbank_service.initialize()
    service_ms = elapsed_ms(service_start)
    media_start = perf_start()
    await wordbank_media_service.rebuild_cache()
    media_ms = elapsed_ms(media_start)
    _wordbank_initialized = True
    log_perf(
        "plugin.initialize.done",
        start=start,
        service_initialize_ms=f"{service_ms:.2f}",
        media_rebuild_ms=f"{media_ms:.2f}",
    )


def reset_wordbank_initialized() -> None:
    """复位初始化标记，供运行时重载流程强制重新初始化。"""
    global _wordbank_initialized
    _wordbank_initialized = False
