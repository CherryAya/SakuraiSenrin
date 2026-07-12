from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "check_matcher_bot_injection",
    ROOT / "scripts" / "check_matcher_bot_injection.py",
)
assert _SCRIPT_SPEC is not None
assert _SCRIPT_SPEC.loader is not None
_SCRIPT_MODULE = importlib.util.module_from_spec(_SCRIPT_SPEC)
sys.modules[_SCRIPT_SPEC.name] = _SCRIPT_MODULE
_SCRIPT_SPEC.loader.exec_module(_SCRIPT_MODULE)

iter_project_violations = _SCRIPT_MODULE.iter_project_violations
iter_violations_for_path = _SCRIPT_MODULE.iter_violations_for_path


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_guardrail_blocks_missing_bot_in_dynamic_dispatch() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _write_file(
            root / "src" / "plugins" / "demo" / "__init__.py",
            "\n".join(
                [
                    "from nonebot.adapters.onebot.v11 import Bot, MessageEvent",
                    "from nonebot.matcher import Matcher",
                    "from nonebot.typing import T_State",
                    "async def _finish(",
                    "    bot: Bot,",
                    "    matcher: Matcher,",
                    "    state: T_State,",
                    "    event: MessageEvent,",
                    ") -> None:",
                    "    return None",
                ]
            ),
        )
        entry_path = root / "src" / "plugins" / "demo" / "entry.py"
        _write_file(
            entry_path,
            "\n".join(
                [
                    "from nonebot import on_command",
                    "from nonebot.adapters.onebot.v11 import MessageEvent",
                    "from nonebot.matcher import Matcher",
                    "from nonebot.typing import T_State",
                    'demo = on_command("demo")',
                    "async def _call_dynamic(name: str, *args: object) -> None:",
                    "    from src.plugins import demo as demo_plugin",
                    "    target = getattr(demo_plugin, name)",
                    "    await target(*args)",
                    "@demo.handle()",
                    "async def _handler(",
                    "    matcher: Matcher,",
                    "    event: MessageEvent,",
                    "    state: T_State,",
                    ") -> None:",
                    '    await _call_dynamic("_finish", matcher, state, event)',
                ]
            ),
        )

        violations = iter_violations_for_path(
            entry_path,
            project_root=root,
        )

    assert [item.code for item in violations] == ["MBI001"]


def test_guardrail_allows_dynamic_dispatch_with_explicit_bot() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _write_file(
            root / "src" / "plugins" / "demo" / "__init__.py",
            "\n".join(
                [
                    "from nonebot.adapters.onebot.v11 import Bot, MessageEvent",
                    "from nonebot.matcher import Matcher",
                    "from nonebot.typing import T_State",
                    "async def _finish(",
                    "    bot: Bot,",
                    "    matcher: Matcher,",
                    "    state: T_State,",
                    "    event: MessageEvent,",
                    ") -> None:",
                    "    return None",
                ]
            ),
        )
        entry_path = root / "src" / "plugins" / "demo" / "entry.py"
        _write_file(
            entry_path,
            "\n".join(
                [
                    "from nonebot import on_command",
                    "from nonebot.adapters.onebot.v11 import Bot, MessageEvent",
                    "from nonebot.matcher import Matcher",
                    "from nonebot.typing import T_State",
                    'demo = on_command("demo")',
                    "async def _call_dynamic(name: str, *args: object) -> None:",
                    "    from src.plugins import demo as demo_plugin",
                    "    target = getattr(demo_plugin, name)",
                    "    await target(*args)",
                    "@demo.handle()",
                    "async def _handler(",
                    "    bot: Bot,",
                    "    matcher: Matcher,",
                    "    event: MessageEvent,",
                    "    state: T_State,",
                    ") -> None:",
                    '    await _call_dynamic("_finish", bot, matcher, state, event)',
                ]
            ),
        )

        violations = iter_violations_for_path(
            entry_path,
            project_root=root,
        )

    assert not violations


def test_guardrail_blocks_missing_bot_in_direct_helper_call() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        path = root / "src" / "plugins" / "demo" / "__init__.py"
        _write_file(
            path,
            "\n".join(
                [
                    "from nonebot import on_command",
                    "from nonebot.adapters.onebot.v11 import Bot, MessageEvent",
                    "from nonebot.matcher import Matcher",
                    "from nonebot.typing import T_State",
                    'demo = on_command("demo")',
                    "async def _finish(",
                    "    bot: Bot,",
                    "    matcher: Matcher,",
                    "    state: T_State,",
                    "    event: MessageEvent,",
                    ") -> None:",
                    "    return None",
                    "@demo.handle()",
                    "async def _handler(",
                    "    matcher: Matcher,",
                    "    event: MessageEvent,",
                    "    state: T_State,",
                    ") -> None:",
                    "    await _finish(matcher, state, event)",
                ]
            ),
        )

        violations = iter_violations_for_path(
            path,
            project_root=root,
        )

    assert [item.code for item in violations] == ["MBI001"]


def test_project_has_no_missing_bot_injection_violations() -> None:
    violations = iter_project_violations()
    assert not violations, "\n".join(item.render() for item in violations)
