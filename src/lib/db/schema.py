from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PatchBase(DeclarativeBase):
    pass


class AppliedSchemaPatch(PatchBase):
    __tablename__ = "_schema_patch"

    patch_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    applied_at: Mapped[int] = mapped_column(Integer, nullable=False)


PatchFunc = Callable[[AsyncSession], Awaitable[None]]


@dataclass(slots=True, frozen=True)
class SchemaPatch:
    patch_id: str
    apply: PatchFunc


@dataclass(slots=True)
class PatchRegistry:
    patches: list[SchemaPatch] = field(default_factory=list)

    def register(self, patch: SchemaPatch) -> None:
        self.patches.append(patch)

    async def apply_all(self, session: AsyncSession, now_ts: int) -> None:
        result = await session.execute(select(AppliedSchemaPatch.patch_id))
        applied = set(result.scalars().all())
        for patch in self.patches:
            if patch.patch_id in applied:
                continue
            await patch.apply(session)
            session.add(AppliedSchemaPatch(patch_id=patch.patch_id, applied_at=now_ts))
