from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
FORWARD_HELPERS = {
    SRC_ROOT / "lib" / "onebot_forward.py",
    SRC_ROOT / "lib" / "message_delivery.py",
}


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def test_runtime_forward_api_calls_are_centralized() -> None:
    banned_patterns = (
        "send_group_forward_msg",
        "send_private_forward_msg",
        "MessageSegment.node(",
        "MessageSegment.node_custom(",
    )
    violations: list[str] = []

    for path in _iter_python_files(SRC_ROOT):
        if path in FORWARD_HELPERS:
            continue
        content = path.read_text(encoding="utf-8")
        hits = [pattern for pattern in banned_patterns if pattern in content]
        if hits:
            violations.append(f"{path.relative_to(ROOT)} -> {', '.join(hits)}")

    assert not violations, (
        "Forward runtime path must be centralized in the forward delivery helpers:\n"
        + "\n".join(violations)
    )
