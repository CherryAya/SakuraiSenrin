from __future__ import annotations

from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace

import nonebot
import pytest

from src.lib.docs_assets import DocsAssetsError


@pytest.mark.parametrize("download_fails", [False, True])
def test_bot_checks_fonts_and_images_before_loading_plugins(
    monkeypatch: pytest.MonkeyPatch,
    download_fails: bool,
) -> None:
    events: list[str] = []

    class StopAtPluginLoading(Exception):
        pass

    def initialize_assets() -> None:
        events.append("images")
        if download_fails:
            raise DocsAssetsError("asset download failed")

    def load_plugins(*args: object) -> None:
        events.append("plugins")
        raise StopAtPluginLoading

    def no_op(*args: object, **kwargs: object) -> None:
        pass

    stubs: dict[str, dict[str, object]] = {
        "src.lib.docs_assets": {"init_docs_assets": initialize_assets},
        "src.lib.message_api_hooks": {"install_message_delivery_hooks": no_op},
        "src.lib.trace_log": {
            "configure_logging": lambda **kwargs: events.append("logging"),
            "shutdown_logging": no_op,
        },
        "src.scripts.install": {"init_fonts": lambda: events.append("fonts")},
        "src.services.db": {"init_db": no_op},
        "src.services.message_asset_startup": {
            "run_startup_message_asset_check": no_op
        },
        "src.services.backup_scheduler": {"install_backup_scheduler": no_op},
        "src.services.shard_archiver": {"install_shard_archiver_scheduler": no_op},
    }
    for name, attributes in stubs.items():
        module = ModuleType(name)
        for attribute, value in attributes.items():
            setattr(module, attribute, value)
        monkeypatch.setitem(sys.modules, name, module)
    driver = SimpleNamespace(
        register_adapter=no_op,
        on_startup=lambda fn: fn,
        on_shutdown=lambda fn: fn,
        on_bot_connect=lambda fn: fn,
    )
    monkeypatch.setattr(nonebot, "init", lambda: events.append("configuration"))
    monkeypatch.setattr(nonebot, "get_driver", lambda: driver)
    monkeypatch.setattr(nonebot, "load_plugins", load_plugins)

    expected = DocsAssetsError if download_fails else StopAtPluginLoading
    with pytest.raises(expected):
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "bot.py"))
    assert events == [
        "configuration",
        "logging",
        "fonts",
        "images",
        *([] if download_fails else ["plugins"]),
    ]
