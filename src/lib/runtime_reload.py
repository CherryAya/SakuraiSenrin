"""运行时状态重载注册表。

备份恢复会重建数据库内容，各插件的进程内缓存在此之后必须失效重建。
插件在 bootstrap 阶段注册自己的重载回调，公共层只负责按注册顺序调用，
因此不需要在服务层硬编码任何插件名。

与 ``src.lib.backup.registry`` 同构：注册方是插件的 bootstrap，
消费方是公共层的编排流程。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

type RuntimeReloadCallback = Callable[[], Awaitable[None]]

_reload_callbacks: dict[str, RuntimeReloadCallback] = {}


def register_runtime_reload(name: str, callback: RuntimeReloadCallback) -> None:
    """注册一个运行时重载回调。同名重复注册会被后者覆盖。"""
    _reload_callbacks[name] = callback


def get_runtime_reload_callbacks() -> tuple[RuntimeReloadCallback, ...]:
    """返回已注册的回调，按注册顺序。"""
    return tuple(_reload_callbacks.values())


async def reload_registered_runtime_states() -> None:
    """依次执行全部已注册的重载回调。"""
    for callback in tuple(_reload_callbacks.values()):
        await callback()
