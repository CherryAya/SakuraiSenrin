"""时间戳工具：全仓统一时间来源，走本模块便于测试替换与审计。"""

from __future__ import annotations

import time


def get_current_time() -> int:
    return int(time.time())  # noqa: TID251
