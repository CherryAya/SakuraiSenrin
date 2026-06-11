from collections.abc import Iterator
from pathlib import Path
import sys
import types
from types import SimpleNamespace

from nonebug import NONEBOT_INIT_KWARGS
import pytest
import pytest_asyncio

# Prevent importing plugin bootstrap modules during test collection.
# We only test submodules (handlers/services/repo), not NoneBot side effects.
_root = Path(__file__).resolve().parents[1]
_water_pkg_path = _root / "src" / "plugins" / "water"
_wordbank_pkg_path = _root / "src" / "plugins" / "wordbank"


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
            HTTP_PROXY=None,
            OBJECT_STORAGE_DEFAULT_PROVIDER=None,
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
    # Ensure src.config.GlobalConfig can be built in tests.
    config.stash[NONEBOT_INIT_KWARGS] = {
        "SUPERUSERS": {"1"},
        "IGNORED_USERS": set(),
        "MAIN_GROUP_ID": "10001",
        "OBJECT_STORAGE_DEFAULT_PROVIDER": None,
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
        "COMMAND_START": {"#", "/"},
    }


@pytest.fixture(autouse=True)
def _disable_real_plugin_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid loading unrelated plugins when importing modules under test."""
    monkeypatch.setenv("ENVIRONMENT", "test")


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
