"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-07 22:12:20
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 23:59:18
Description: 测试 matcher
"""

from nonebot.plugin import on_message

test_matcher = on_message(block=False)


@test_matcher.handle()
async def _() -> None:
    pass
