from __future__ import annotations

import ast
from pathlib import Path
import sys


def _is_message_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "Message"
    if isinstance(func, ast.Attribute):
        return func.attr == "Message"
    return False


def _iter_violations(path: Path) -> list[tuple[int, int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    violations: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_message_call(node):
            continue
        if not node.args and not node.keywords:
            continue
        violations.append(
            (
                node.lineno,
                node.col_offset,
                "禁止使用 Message(...) 包裹消息，避免 message 注入；"
                "请改为手动组装 MessageSegment",
            )
        )
    return violations


def main(argv: list[str]) -> int:
    has_error = False
    output: list[str] = []
    for raw_path in argv[1:]:
        path = Path(raw_path)
        for line, col, message in _iter_violations(path):
            has_error = True
            output.append(f"{path}:{line}:{col}: {message}")
    if output:
        sys.stdout.write("\n".join(output) + "\n")
    return 1 if has_error else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
