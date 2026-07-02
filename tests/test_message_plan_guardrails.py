from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOTS = (
    ROOT / "src" / "plugins",
    ROOT / "src" / "hooks",
)
_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "check_message_delivery_guardrails",
    ROOT / "scripts" / "check_message_delivery_guardrails.py",
)
assert _SCRIPT_SPEC is not None
assert _SCRIPT_SPEC.loader is not None
_SCRIPT_MODULE = importlib.util.module_from_spec(_SCRIPT_SPEC)
sys.modules[_SCRIPT_SPEC.name] = _SCRIPT_MODULE
_SCRIPT_SPEC.loader.exec_module(_SCRIPT_MODULE)

iter_project_violations = _SCRIPT_MODULE.iter_project_violations
iter_violations_for_path = _SCRIPT_MODULE.iter_violations_for_path


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def test_plugin_layers_do_not_call_send_custom_forward_directly() -> None:
    banned_patterns = (
        "send_custom_forward(",
        "from src.lib.onebot_forward import send_custom_forward",
        "deliver_forward_messages(",
        "from src.lib.message_delivery import deliver_forward_messages",
    )
    violations: list[str] = []

    for root in PLUGIN_ROOTS:
        for path in _iter_python_files(root):
            content = path.read_text(encoding="utf-8")
            hits = [pattern for pattern in banned_patterns if pattern in content]
            if hits:
                violations.append(f"{path.relative_to(ROOT)} -> {', '.join(hits)}")

    assert not violations, (
        "Plugin-facing message flows must route through the shared delivery plan "
        "instead of calling send_custom_forward directly:\n" + "\n".join(violations)
    )


def test_plugin_layers_do_not_call_deliver_single_message_directly() -> None:
    banned_patterns = (
        "deliver_single_message(",
        "resolve_delivery_target(",
        "from src.lib.message_delivery import deliver_single_message",
        "from src.lib.message_delivery import resolve_delivery_target",
    )
    violations: list[str] = []

    for root in PLUGIN_ROOTS:
        for path in _iter_python_files(root):
            content = path.read_text(encoding="utf-8")
            hits = [pattern for pattern in banned_patterns if pattern in content]
            if hits:
                violations.append(f"{path.relative_to(ROOT)} -> {', '.join(hits)}")

    assert not violations, (
        "Plugin-facing message delivery must route through DeliveryPlan "
        "instead of direct single-message delivery:\n" + "\n".join(violations)
    )


def _scan_source(source: str) -> list[Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "sample.py"
        path.write_text(source, encoding="utf-8")
        return iter_violations_for_path(path)


def test_delivery_guardrail_blocks_matcher_send_and_finish_payloads() -> None:
    violations = _scan_source(
        "\n".join(
            [
                "async def f(matcher, ctx):",
                '    await matcher.send("x")',
                '    await matcher.finish("y")',
                "    await ctx.matcher.finish(message='z')",
            ]
        )
    )
    assert [item.code for item in violations] == ["MDG001", "MDG002", "MDG002"]


def test_delivery_guardrail_allows_bare_finish_and_interaction_controls() -> None:
    violations = _scan_source(
        "\n".join(
            [
                "async def f(matcher):",
                "    await matcher.finish()",
                '    await matcher.pause("prompt")',
                '    await matcher.reject("prompt")',
            ]
        )
    )
    assert not violations


def test_delivery_guardrail_blocks_direct_bot_sends() -> None:
    violations = _scan_source(
        "\n".join(
            [
                "async def f(bot, ctx):",
                '    await bot.send("x")',
                '    await bot.call_api("send_group_msg", group_id=1, message="x")',
                (
                    "    await ctx.bot.call_api("
                    '"send_private_forward_msg", user_id=1, messages=[]'
                    ")"
                ),
            ]
        )
    )
    assert [item.code for item in violations] == ["MDG003", "MDG004", "MDG004"]


def test_plugin_layers_have_no_direct_send_violations() -> None:
    violations = iter_project_violations()
    assert not violations, "\n".join(item.render() for item in violations)


def test_wordbank_plan_builders_do_not_use_render_shape_wrapper_outside_rendering() -> (
    None
):
    rendering_path = ROOT / "src" / "plugins" / "wordbank" / "handlers" / "rendering.py"
    violations: list[str] = []

    for path in _iter_python_files(ROOT / "src" / "plugins" / "wordbank"):
        if path == rendering_path:
            continue
        content = path.read_text(encoding="utf-8")
        if "render_shape_message(" in content:
            violations.append(str(path.relative_to(ROOT)))

    assert not violations, (
        "Wordbank rich rendering should compose from MessagePlanEntry builders "
        "instead of calling render_shape_message outside rendering.py:\n"
        + "\n".join(violations)
    )
