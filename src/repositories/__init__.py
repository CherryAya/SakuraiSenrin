from src.lib.cache import blacklist_cache, group_cache, member_cache, user_cache

from .blacklist import BlacklistRepository
from .group import GroupRepository
from .member import MemberRepository
from .user import UserRepository

user_repo = UserRepository(user_cache)
group_repo = GroupRepository(group_cache)
member_repo = MemberRepository(member_cache)
blacklist_repo = BlacklistRepository(blacklist_cache)
__all__ = [
    "blacklist_repo",
    "group_repo",
    "member_repo",
    "user_repo",
]
