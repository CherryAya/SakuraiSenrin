"""时间戳工具。

全仓统一的时间来源。禁止直接调用 time.time()/datetime.now()，
统一走本模块，便于测试替换与审计（见 pyproject 的 TID251 禁用清单）。
"""

from __future__ import annotations

import time


def get_current_time() -> int:
    return int(time.time())  # noqa: TID251
