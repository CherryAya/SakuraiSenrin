"""Water plugin package wrapper."""

from __future__ import annotations

import os

if os.getenv("SAKURAI_WATER_WORKER") != "1":
    from .entry_plugin import *  # noqa: F403
    from .entry_plugin import __plugin_meta__  # noqa: F401
