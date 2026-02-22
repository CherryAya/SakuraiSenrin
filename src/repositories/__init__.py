"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 19:41:15
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-22 17:31:44
Description: repo 实现

`get_xxx` cache -> db -> 回填
"""

from src.lib.cache import blacklist_cache, group_cache, member_cache, user_cache

from .blacklist import BlacklistRepository
from .group import GroupRepository
from .invite import InviteRepository
from .member import MemberRepository
from .user import UserRepository

blacklist_repo = BlacklistRepository(blacklist_cache)
group_repo = GroupRepository(group_cache)
invite_repo = InviteRepository()
member_repo = MemberRepository(member_cache)
user_repo = UserRepository(user_cache)

__all__ = [
    "blacklist_repo",
    "group_repo",
    "invite_repo",
    "member_repo",
    "user_repo",
]
