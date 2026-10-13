"""water 插件入口包：显式转发 entry_plugin 的公开符号。"""

from __future__ import annotations

import os

if os.getenv("SAKURAI_WATER_WORKER") != "1":
    from .entry_plugin import __plugin_meta__ as __plugin_meta__
    from .entry_plugin import clear_water_query_cooldowns as clear_water_query_cooldowns
    from .entry_plugin import description as description
    from .entry_plugin import initialize_water_plugin as initialize_water_plugin
    from .entry_plugin import name as name
    from .entry_plugin import reload_water_runtime_state as reload_water_runtime_state
    from .entry_plugin import (
        reset_water_plugin_initialized as reset_water_plugin_initialized,
    )
    from .entry_plugin import water_achievement as water_achievement
    from .entry_plugin import water_admin as water_admin
    from .entry_plugin import water_merge as water_merge
    from .entry_plugin import water_notice as water_notice
    from .entry_plugin import water_profile as water_profile
    from .entry_plugin import water_query as water_query
    from .entry_plugin import water_query_cooldown as water_query_cooldown
    from .entry_plugin import water_recorder as water_recorder

    __all__ = [
        "__plugin_meta__",
        "clear_water_query_cooldowns",
        "description",
        "initialize_water_plugin",
        "name",
        "reload_water_runtime_state",
        "reset_water_plugin_initialized",
        "water_achievement",
        "water_admin",
        "water_merge",
        "water_notice",
        "water_profile",
        "water_query",
        "water_query_cooldown",
        "water_recorder",
    ]
