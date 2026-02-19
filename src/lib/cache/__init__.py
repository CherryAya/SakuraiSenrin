"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-26 00:35:26
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 22:35:39
Description: 全局缓存类
"""

from .impl import (
    BlacklistCache,
    BlacklistCacheItem,
    GroupCache,
    GroupCacheItem,
    MemberCache,
    MemberCacheItem,
    UserCache,
    UserCacheItem,
)

blacklist_cache = BlacklistCache()
group_cache = GroupCache()
member_cache = MemberCache()
user_cache = UserCache()

__all__ = [
    "BlacklistCacheItem",
    "GroupCacheItem",
    "MemberCacheItem",
    "UserCacheItem",
    "blacklist_cache",
    "group_cache",
    "member_cache",
    "user_cache",
]
