from collections.abc import AsyncIterator, Iterator
import os
from pathlib import Path
import runpy
import sys
import types
from types import SimpleNamespace
from typing import cast

import nonebot
from nonebug import NONEBOT_INIT_KWARGS
from pydantic import BaseModel
import pytest
import pytest_asyncio

# sentry 在导入前关：init() 全局生效且异步派发
# 失败告警的 task 会落到后续用例 loop
# 造成连锁失败。
os.environ.setdefault("SAKURAI_DISABLE_SENTRY_NOTIFY", "1")
# 部分测试入口在收集阶段初始化 NoneBot，提前隔离开发/生产环境。
os.environ["ENVIRONMENT"] = "test"

# 收集阶段拦截插件引导：只测子模块
_root = Path(__file__).resolve().parents[1]
_water_pkg_path = _root / "src" / "plugins" / "water"
_wordbank_pkg_path = _root / "src" / "plugins" / "wordbank"
_TEST_SUPPORT_GROUPS = (
    {
        "title": "反馈群 A",
        "group_id": "10001",
        "url": "https://example.invalid/group-a",
    },
    {
        "title": "反馈群 B",
        "group_id": "10002",
        "url": "https://example.invalid/group-b",
    },
)


def _ensure_pkg(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(path)]  # type: ignore[attr-defined]
    sys.modules[name] = pkg


_ensure_pkg("src.plugins.water", _water_pkg_path)
_ensure_pkg("src.plugins.wordbank", _wordbank_pkg_path)
if "src.config" not in sys.modules:
    config_module = types.ModuleType("src.config")
    setattr(
        config_module,
        "config",
        SimpleNamespace(
            SUPERUSERS={"1"},
            IGNORED_USERS=set(),
            MAIN_GROUP_ID="10001",
            HELP_SUPPORT_GROUPS=tuple(
                SimpleNamespace(**group) for group in _TEST_SUPPORT_GROUPS
            ),
            ADMIN_NOTIFY_PRIVATE_ENABLED=True,
            ADMIN_NOTIFY_GROUP_ENABLED=False,
            ADMIN_NOTIFY_GROUP_IDS=(),
            DEBUG=False,
            DEV_TEST_GROUPS=set(),
            DEV_TEST_USERS=set(),
            DEBUG_SQL_ECHO=False,
            HTTP_PROXY=None,
            R2_ACCOUNT_ID=None,
            R2_ACCESS_KEY_ID=None,
            R2_SECRET_ACCESS_KEY=None,
            R2_BUCKET=None,
            R2_PUBLIC_BASE_URL=None,
            R2_ENDPOINT=None,
            GITHUB_TOKEN="test-token",
            GITHUB_REPO="owner/repo",
            GITHUB_BRANCH="main",
            WORDBANK_MEDIA_PROVIDER="local",
            WORDBANK_MEDIA_CACHE_ENABLED=True,
            WORDBANK_MEDIA_CACHE_ROOT="./data/wordbank/media_cache",
            WORDBANK_MEDIA_CACHE_MAX_BYTES=512 * 1024 * 1024,
            WORDBANK_MEDIA_CACHE_TRIM_TO_BYTES=460 * 1024 * 1024,
            WORDBANK_MEDIA_CACHE_MAX_FILES=5_000,
            WORDBANK_MEDIA_REMOTE_REQUIRED=False,
            WORDBANK_MEDIA_REMOTE_SYNC_MODE="deferred",
            WORDBANK_MEDIA_MIGRATION_BATCH_SIZE=200,
            BACKUP_ENABLED=False,
            BACKUP_RESTIC_REPOSITORY=None,
            BACKUP_RESTIC_PASSWORD=None,
            BACKUP_LOCAL_ROOT="./data/backup",
            BACKUP_CRON_HOUR=3,
            BACKUP_CRON_MINUTE=20,
            BACKUP_RETENTION_DAILY=7,
            BACKUP_RETENTION_WEEKLY=4,
            BACKUP_RETENTION_MONTHLY=6,
            BACKUP_REQUIRE_RESTIC=True,
            SAUCENAO_KEY=None,
            ASCII2D_KEY=None,
            SENTRY_DSN=None,
        ),
    )
    sys.modules["src.config"] = config_module


def pytest_configure(config: pytest.Config) -> None:
    # nonebug 原样转交 kwargs 给 nonebot
    # pydantic 按字段名精确匹配程序化 kwargs：
    # 小写 superusers 才进真实字段
    # 生产 .env 查找会同时填好两者；这里也双写。
    config.stash[NONEBOT_INIT_KWARGS] = {
        # 明确隔离项目环境文件，测试配置只来自以下字段。
        "_env_file": (os.devnull,),
        "superusers": {"1"},
        "SUPERUSERS": {"1"},
        "IGNORED_USERS": set(),
        "MAIN_GROUP_ID": "10001",
        "HELP_SUPPORT_GROUPS": _TEST_SUPPORT_GROUPS,
        "ADMIN_NOTIFY_PRIVATE_ENABLED": True,
        "ADMIN_NOTIFY_GROUP_ENABLED": False,
        "ADMIN_NOTIFY_GROUP_IDS": (),
        "DEBUG": False,
        "DEV_TEST_GROUPS": set(),
        "DEV_TEST_USERS": set(),
        "DEBUG_SQL_ECHO": False,
        "R2_ACCOUNT_ID": None,
        "R2_ACCESS_KEY_ID": None,
        "R2_SECRET_ACCESS_KEY": None,
        "R2_BUCKET": None,
        "R2_PUBLIC_BASE_URL": None,
        "R2_ENDPOINT": None,
        "GITHUB_TOKEN": "test-token",
        "GITHUB_REPO": "owner/repo",
        "GITHUB_BRANCH": "main",
        "WORDBANK_MEDIA_PROVIDER": "local",
        "WORDBANK_MEDIA_CACHE_ENABLED": True,
        "WORDBANK_MEDIA_CACHE_ROOT": "./data/wordbank/media_cache",
        "WORDBANK_MEDIA_CACHE_MAX_BYTES": 512 * 1024 * 1024,
        "WORDBANK_MEDIA_CACHE_TRIM_TO_BYTES": 460 * 1024 * 1024,
        "WORDBANK_MEDIA_CACHE_MAX_FILES": 5_000,
        "WORDBANK_MEDIA_REMOTE_REQUIRED": False,
        "WORDBANK_MEDIA_REMOTE_SYNC_MODE": "deferred",
        "WORDBANK_MEDIA_MIGRATION_BATCH_SIZE": 200,
        "BACKUP_ENABLED": False,
        "BACKUP_RESTIC_REPOSITORY": None,
        "BACKUP_RESTIC_PASSWORD": None,
        "BACKUP_LOCAL_ROOT": "./data/backup",
        "BACKUP_CRON_HOUR": 3,
        "BACKUP_CRON_MINUTE": 20,
        "BACKUP_RETENTION_DAILY": 7,
        "BACKUP_RETENTION_WEEKLY": 4,
        "BACKUP_RETENTION_MONTHLY": 6,
        "BACKUP_REQUIRE_RESTIC": True,
        "command_start": {"#", "/"},
    }
    # 入口测试在收集阶段注册 matcher，须先使用同一份测试配置。
    nonebot.init(**config.stash[NONEBOT_INIT_KWARGS])


@pytest.fixture
def config_schema(monkeypatch: pytest.MonkeyPatch) -> type[BaseModel]:
    # 只加载真实配置模型，测试不会读取本地账号、启动机器人或插件。
    with monkeypatch.context() as loader:
        loader.setattr(nonebot, "get_plugin_config", lambda model: None)
        namespace = runpy.run_path(str(_root / "src/config.py"))
    return cast("type[BaseModel]", namespace["GlobalConfig"])


@pytest.fixture
def config_values() -> dict[str, object]:
    return {
        "SUPERUSERS": set(),
        "IGNORED_USERS": set(),
        "MAIN_GROUP_ID": "",
        "GITHUB_TOKEN": "",
        "GITHUB_REPO": "",
        "GITHUB_BRANCH": "master",
    }


@pytest.fixture(autouse=True)
def _disable_real_plugin_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拦截插件加载：测试只导入被测模块。"""
    monkeypatch.setenv("ENVIRONMENT", "test")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_test_db_engines() -> AsyncIterator[None]:
    from src.lib.db.manager import db_manager

    yield
    await db_manager.dispose_all()


@pytest.fixture(scope="session", autouse=True)
def _use_test_db_root(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Path]:
    from src.lib.db import connectors as connectors_module

    root = tmp_path_factory.mktemp("db-root")
    original_root = connectors_module.GLOBAL_DB_ROOT
    connectors_module.GLOBAL_DB_ROOT = root
    try:
        yield root
    finally:
        connectors_module.GLOBAL_DB_ROOT = original_root


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _init_test_databases(_use_test_db_root: Path) -> None:
    from src.services.db import init_db

    _ = _use_test_db_root
    await init_db()
