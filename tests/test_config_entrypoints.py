from __future__ import annotations

from collections.abc import Callable
import importlib
import inspect
from types import ModuleType

import nonebot
import pytest

ENTRYPOINTS = [
    ("scripts.dead_letter", ["dead_letter.py", "--list"], "_stub_plugin_packages"),
    ("scripts.writer_health", ["writer_health.py", "--json"], "_stub_plugin_packages"),
    ("scripts.db_shard_status", ["db_shard_status.py", "--json"], "_load_stores"),
    ("scripts.build_docs", ["build_docs.py", "build"], "build"),
    ("scripts.docs_assets", ["docs_assets.py", "pull"], "_run"),
    ("scripts.archive_event_shards", ["archive_event_shards.py"], "archive_targets"),
    ("scripts.query_trace_logs", ["query_trace_logs.py"], "log_db.store.init"),
    ("scripts.clear_help_cache", ["clear_help_cache.py"], "count_all_cache"),
]


async def _run_entrypoint(module: ModuleType) -> None:
    result = getattr(module, "main")()
    if inspect.isawaitable(result):
        await result


def _replace_load_step(
    module: ModuleType,
    path: str,
    monkeypatch: pytest.MonkeyPatch,
    callback: Callable[..., None],
) -> None:
    owner: object = module
    *parents, attribute = path.split(".")
    for name in parents:
        owner = getattr(owner, name)
    monkeypatch.setattr(owner, attribute, callback)


@pytest.mark.parametrize(("module_name", "argv", "load_step"), ENTRYPOINTS)
async def test_entrypoint_initializes_configuration_before_loading(
    module_name: str,
    argv: list[str],
    load_step: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(module_name)
    events: list[str] = []

    class StopBeforeIo(Exception):
        pass

    def initialize() -> None:
        events.append("configuration")

    def stop(*args: object, **kwargs: object) -> None:
        events.append("load")
        raise StopBeforeIo

    monkeypatch.setattr(nonebot, "init", initialize)
    _replace_load_step(module, load_step, monkeypatch, stop)
    monkeypatch.setattr("sys.argv", argv)

    with pytest.raises(StopBeforeIo):
        await _run_entrypoint(module)

    assert events == ["configuration", "load"]


@pytest.mark.parametrize(("module_name", "argv", "load_step"), ENTRYPOINTS)
async def test_configuration_initialization_failure_is_propagated(
    module_name: str,
    argv: list[str],
    load_step: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(module_name)
    loaded = False

    def initialize() -> None:
        raise ValueError("invalid current configuration")

    def load(*args: object, **kwargs: object) -> None:
        nonlocal loaded
        loaded = True

    monkeypatch.setattr(nonebot, "init", initialize)
    _replace_load_step(module, load_step, monkeypatch, load)
    monkeypatch.setattr("sys.argv", argv)

    with pytest.raises(ValueError, match="invalid current configuration"):
        await _run_entrypoint(module)

    assert loaded is False


@pytest.mark.parametrize(
    ("module_name", "argv", "load_step"),
    [entry for entry in ENTRYPOINTS if entry[0] != "scripts.clear_help_cache"],
)
async def test_entrypoint_help_does_not_load_configuration(
    module_name: str,
    argv: list[str],
    load_step: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(module_name)

    def initialize() -> None:
        raise AssertionError("help must work before loading configuration")

    monkeypatch.setattr(nonebot, "init", initialize)
    monkeypatch.setattr("sys.argv", [argv[0], "--help"])

    with pytest.raises(SystemExit) as error:
        await _run_entrypoint(module)

    assert error.value.code == 0
