from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.lib import message_assets as message_asset_module
from src.lib.message_assets import MessageAsset, MessageAssetRepository


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeDb:
    """把 async_sessionmaker 包装成仓库期望的 write_session()/read_session() 接口。

    提交语义与 src.lib.db.manager.DatabaseManager.open 保持一致。
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def _session(self, *, commit: bool) -> Any:
        return _SessionCtx(self._session_factory, commit=commit)

    def write_session(self) -> Any:
        return self._session(commit=True)

    def read_session(self) -> Any:
        return self._session(commit=False)


class _SessionCtx:
    def __init__(self, session_factory: Any, *, commit: bool) -> None:
        self._session_factory = session_factory
        self._commit = commit
        self._session: Any = None

    async def __aenter__(self) -> Any:
        self._session = self._session_factory()
        return self._session

    async def __aexit__(self, exc_type: object, *_: object) -> None:
        assert self._session is not None
        try:
            if self._commit:
                await self._session.commit()
        finally:
            await self._session.close()


async def _build_session_factory(tmp_path: Path) -> Any:
    # 使用文件型 SQLite：内存库会为每条连接创建独立数据库，无法验证并发 upsert。
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'asset.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(MessageAsset.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


_COMMON_PAYLOAD: dict[str, Any] = {
    "content_hash": "a" * 64,
    "asset_kind": "forward_node",
    "source_kind": "test",
    "message_id": "1",
    "sender_bot_id": "bot",
    "origin_message_type": "group",
    "origin_target_id": "100",
    "message_shape_kind": "plain",
}


@pytest.mark.asyncio
async def test_upsert_asset_is_idempotent_for_same_key(tmp_path: Path) -> None:
    """同一 asset_key 重复写入不应抛 UNIQUE 冲突。"""
    engine, session_factory = await _build_session_factory(tmp_path)

    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                message_asset_module, "message_asset_db", _FakeDb(session_factory)
            )
            repo = MessageAssetRepository()

            payload = {**_COMMON_PAYLOAD, "asset_key": "a" * 64}
            first = await repo.upsert_asset(**payload)
            second = await repo.upsert_asset(**{**payload, "message_id": "2"})

            assert first.asset_key == second.asset_key == "a" * 64
            assert second.message_id == "2"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_upsert_does_not_raise_unique_violation(
    tmp_path: Path,
) -> None:
    """并发写入同一 asset_key 时，SQLite 原生 upsert 应吸收唯一键冲突。"""
    engine, session_factory = await _build_session_factory(tmp_path)

    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                message_asset_module, "message_asset_db", _FakeDb(session_factory)
            )
            repo = MessageAssetRepository()
            payload = {**_COMMON_PAYLOAD, "asset_key": "b" * 64}

            results = await asyncio.gather(
                repo.upsert_asset(**payload),
                repo.upsert_asset(**payload),
            )

            assert all(r.asset_key == "b" * 64 for r in results)

            async with session_factory() as session:
                count = (
                    await session.execute(
                        select(func.count())
                        .select_from(MessageAsset)
                        .where(MessageAsset.asset_key == "b" * 64)
                    )
                ).scalar_one()
            assert count == 1
    finally:
        await engine.dispose()
