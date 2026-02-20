"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 23:19:49
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-21 01:50:22
Description: 插件入口
"""

from pathlib import Path

import nonebot

sub_plugins = nonebot.load_plugins(str(Path(__file__).parent.resolve()))
