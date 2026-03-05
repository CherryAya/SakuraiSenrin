from pathlib import Path
import sys
import types
from types import SimpleNamespace

from nonebug import NONEBOT_INIT_KWARGS
import pytest

# Prevent importing src.plugins.water.__init__ during test collection.
# We only test submodules (handlers/services/repo), not plugin bootstrap side effects.
_root = Path(__file__).resolve().parents[1]
_water_pkg_path = _root / "src" / "plugins" / "water"


def _ensure_pkg(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(path)]  # type: ignore[attr-defined]
    sys.modules[name] = pkg


_ensure_pkg("src.plugins.water", _water_pkg_path)
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
            GITHUB_TOKEN="test-token",
            GITHUB_REPO="owner/repo",
            GITHUB_BRANCH="main",
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
        "GITHUB_TOKEN": "test-token",
        "GITHUB_REPO": "owner/repo",
        "GITHUB_BRANCH": "main",
        "COMMAND_START": {"#", "/"},
    }


@pytest.fixture(autouse=True)
def _disable_real_plugin_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid loading unrelated plugins when importing modules under test."""
    monkeypatch.setenv("ENVIRONMENT", "test")
