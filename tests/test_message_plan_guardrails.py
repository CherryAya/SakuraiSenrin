from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOTS = (
    ROOT / "src" / "plugins",
    ROOT / "src" / "hooks",
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
