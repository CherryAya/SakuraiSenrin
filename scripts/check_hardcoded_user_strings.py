"""i18n 硬编码文案守卫：盯住绕开 catalog 的用户可见 CJK 字面量。

只报流向用户可见出口（消息投递、计划文本块、告警模板）的字面量；命令别名、
匹配路由 token、解析 token、README 结构键与日志输出走注册/解析/日志路径，
逐条放行。
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import sys

CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")

# 字符串实参流向消息体的调用
TEXT_EMITTERS = frozenset(
    {
        "AlertTemplate",
        "build_text_plan_entry",
        "deliver_admin_notification_i18n",
        "deliver_message_plan",
        "deliver_single_message",
        "finish",
        "finish_with_message",
        "finish_i18n",
        "msg",
        "pause",
        "reject",
        "send",
        "send_i18n",
        "send_with_message",
        "text_message",
        "tr",
        "tr_template",
    }
)

# 携带文案的构造关键字，须走 i18n
TEXT_BLOCK_TYPES = frozenset({"MessagePlanEntry", "TextBlock", "MessagePlanText"})
TEXT_FIELD_NAMES = frozenset({"text"})

# 第二位置实参为 message key（走目录）的出口
KEYED_EMITTERS = frozenset({"tr", "tr_template"})

# 属展示文案（而非路由 token）的字面量集合名
DISPLAY_COLLECTION_NAMES = frozenset({"messages"})

LOGGER_NAMES = frozenset(
    {"debug", "error", "exception", "info", "log", "success", "trace", "warning"}
)

SKIPPED_DIR_NAMES = frozenset({"__pycache__", "locales"})
SKIPPED_PATH_PREFIXES = (
    "src/database/",
    "src/scripts/",
    "src/locales/",
    "tools/",
    "tests/",
    "scripts/",
)

# 用已本地化文案组装 plan/text 节点的辅助函数名
LOCALIZED_BUILDER_NAMES = frozenset(
    {
        "append_image_plan_entry",
        "build_image_plan_entry",
        "build_message_plan_entry",
        "render_message_plan_entry",
    }
)

VIOLATION_HINT = (
    "禁止硬编码用户可见文案；请改用 tr(locale, key, ...) "
    "并在 src/locales/{zh_cn,lzh,x_meme}.py 补齐对应键"
)


def _iter_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*.py")
                if not SKIPPED_DIR_NAMES & set(candidate.parts)
            )
        elif path.suffix == ".py":
            files.append(path)
    return files


def _is_skipped(path: Path) -> bool:
    return path.as_posix().startswith(SKIPPED_PATH_PREFIXES)


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _base_name(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _collect_docstring_ids(tree: ast.Module) -> set[int]:
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstring_ids.add(id(first.value))
    return docstring_ids


def _is_inside(tree_parents: dict[int, ast.AST], node: ast.AST) -> ast.AST | None:
    return tree_parents.get(id(node))


def _iter_violations(path: Path) -> list[tuple[int, int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except (SyntaxError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    docstrings = _collect_docstring_ids(tree)
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    violations: list[tuple[int, int, str]] = []

    def report(node: ast.AST, value: str) -> None:
        line_no = getattr(node, "lineno", 0)
        violations.append((line_no, getattr(node, "col_offset", 0), value))

    def has_cjk(value: str) -> bool:
        return bool(CJK_PATTERN.search(value))

    def check_expr(node: ast.AST) -> None:
        """把展示文案表达式里的 CJK 字面量逐条报出。"""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and has_cjk(node.value):
                report(node, node.value)
            return
        if isinstance(node, ast.JoinedStr):
            for value_node in node.values:
                check_expr(value_node)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            check_expr(node.left)
            check_expr(node.right)
            return
        if isinstance(node, ast.Call) and _base_name(_dotted_name(node.func)) in {
            "strip",
            "removeprefix",
            "removesuffix",
            "join",
            "format",
            "replace",
        }:
            for arg in node.args:
                check_expr(arg)
            return
        if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            for child in ast.iter_child_nodes(node):
                check_expr(child)

    def check_arg(node: ast.AST, *, emitter: str, position: int | None) -> None:
        if emitter in KEYED_EMITTERS and position is not None and position >= 1:
            return
        if position == 0 and emitter == "tr":
            return
        check_expr(node)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _dotted_name(node.func)
        name = _base_name(callee)

        if name in LOGGER_NAMES or name in LOCALIZED_BUILDER_NAMES:
            continue

        if callee in TEXT_BLOCK_TYPES or name in TEXT_BLOCK_TYPES:
            for keyword in node.keywords:
                if keyword.arg in TEXT_FIELD_NAMES:
                    check_expr(keyword.value)
            continue

        if name in TEXT_EMITTERS:
            for index, arg in enumerate(node.args):
                check_arg(arg, emitter=name, position=index)
            for keyword in node.keywords:
                if keyword.arg in DISPLAY_COLLECTION_NAMES:
                    check_expr(keyword.value)
            continue

        if name == "DeliveryPlan":
            for keyword in node.keywords:
                if keyword.arg in DISPLAY_COLLECTION_NAMES:
                    check_expr(keyword.value)
            continue

    return violations


def main(argv: list[str]) -> int:
    has_error = False
    output: list[str] = []
    for path in _iter_files(argv[1:]):
        if _is_skipped(path):
            continue
        for line, col, _message in _iter_violations(path):
            has_error = True
            output.append(f"{path}:{line}:{col + 1}: {VIOLATION_HINT}")
    if output:
        sys.stdout.write("\n".join(sorted(set(output))) + "\n")
    return 1 if has_error else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
