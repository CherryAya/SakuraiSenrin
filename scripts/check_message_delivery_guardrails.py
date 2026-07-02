from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOTS = (
    ROOT / "src" / "plugins",
    ROOT / "src" / "hooks",
)
FORBIDDEN_SEND_APIS = {
    "send_msg",
    "send_group_msg",
    "send_private_msg",
    "send_group_forward_msg",
    "send_private_forward_msg",
}


@dataclass(slots=True, frozen=True)
class GuardrailViolation:
    path: Path
    line: int
    col: int
    code: str
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}:{self.col}: {self.code} {self.message}"


def _attribute_path(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attribute_path(node.value)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"
    return None


def _is_matcher_receiver(path: str | None) -> bool:
    return path == "matcher" or bool(path and path.endswith(".matcher"))


def _is_bot_receiver(path: str | None) -> bool:
    return path == "bot" or bool(path and path.endswith(".bot"))


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def iter_violations_for_path(path: Path) -> list[GuardrailViolation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    violations: list[GuardrailViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue

        method_name = node.func.attr
        receiver_path = _attribute_path(node.func.value)

        if method_name == "send" and _is_matcher_receiver(receiver_path):
            violations.append(
                GuardrailViolation(
                    path=path,
                    line=node.lineno,
                    col=node.col_offset,
                    code="MDG001",
                    message=(
                        "插件与 hook 不得直接调用 matcher.send(...)；"
                        "请改用 DeliveryPlan + deliver_message_plan(...)"
                    ),
                )
            )
            continue

        if (
            method_name == "finish"
            and _is_matcher_receiver(receiver_path)
            and (node.args or node.keywords)
        ):
            violations.append(
                GuardrailViolation(
                    path=path,
                    line=node.lineno,
                    col=node.col_offset,
                    code="MDG002",
                    message=(
                        "插件与 hook 不得通过 matcher.finish(...) 直接发送消息；"
                        "请先走 DeliveryPlan，再调用 matcher.finish()"
                    ),
                )
            )
            continue

        if method_name == "send" and _is_bot_receiver(receiver_path):
            violations.append(
                GuardrailViolation(
                    path=path,
                    line=node.lineno,
                    col=node.col_offset,
                    code="MDG003",
                    message=(
                        "插件与 hook 不得直接调用 bot.send(...)；"
                        "请改用 DeliveryPlan + deliver_message_plan(...)"
                    ),
                )
            )
            continue

        if method_name != "call_api" or not _is_bot_receiver(receiver_path):
            continue
        if not node.args:
            continue
        api_name = _literal_string(node.args[0])
        if api_name not in FORBIDDEN_SEND_APIS:
            continue
        violations.append(
            GuardrailViolation(
                path=path,
                line=node.lineno,
                col=node.col_offset,
                code="MDG004",
                message=(
                    "插件与 hook 不得直接调用 send_* API；"
                    "请改用 DeliveryPlan + deliver_message_plan(...)"
                ),
            )
        )
    return violations


def iter_project_violations() -> list[GuardrailViolation]:
    violations: list[GuardrailViolation] = []
    for root in PLUGIN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if path.is_file():
                violations.extend(iter_violations_for_path(path))
    return violations


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        violations: list[GuardrailViolation] = []
        for raw_path in argv[1:]:
            violations.extend(iter_violations_for_path(Path(raw_path)))
    else:
        violations = iter_project_violations()
    if violations:
        sys.stdout.write("\n".join(item.render() for item in violations) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
