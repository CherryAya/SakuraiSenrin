from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOTS = (
    ROOT / "src" / "plugins",
    ROOT / "src" / "hooks",
)
PLAN_FIRST_PLUGIN_ROOTS = (
    ROOT / "src" / "plugins" / "wordbank",
    ROOT / "src" / "plugins" / "study",
)


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


def test_wordbank_and_study_do_not_call_deliver_single_message_directly() -> None:
    banned_patterns = (
        "deliver_single_message(",
        "resolve_delivery_target(",
        "from src.lib.message_delivery import deliver_single_message",
        "from src.lib.message_delivery import resolve_delivery_target",
    )
    violations: list[str] = []

    for root in PLAN_FIRST_PLUGIN_ROOTS:
        for path in _iter_python_files(root):
            content = path.read_text(encoding="utf-8")
            hits = [pattern for pattern in banned_patterns if pattern in content]
            if hits:
                violations.append(f"{path.relative_to(ROOT)} -> {', '.join(hits)}")

    assert not violations, (
        "Wordbank/study message delivery must route through DeliveryPlan "
        "instead of plugin-side direct single-message delivery:\n"
        + "\n".join(violations)
    )


def test_wordbank_plan_builders_do_not_use_render_shape_wrapper_outside_rendering() -> None:
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
