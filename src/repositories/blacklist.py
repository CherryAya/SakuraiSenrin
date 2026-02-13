from src.database.core.ops import BlacklistOps
from src.database.instances import core_db
from src.lib.cache.field import BlacklistCacheItem
from src.lib.cache.impl import BlacklistCache


class BlacklistRepository:
    def __init__(self, cache: BlacklistCache) -> None:
        self.cache = cache

    async def warm_up(self) -> None:
        async with core_db.session(commit=False) as session:
            blacklists = await BlacklistOps(session).get_all()

        self.cache.set_batch(
            {
                self.cache._gen_key(
                    b.target_user_id,
                    b.group_id,
                ): BlacklistCacheItem(
                    expiry=b.ban_expiry,
                    reason=b.reason,
                )
                for b in blacklists
            },
        )
