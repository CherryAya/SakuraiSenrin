from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (
    ROOT / "src" / "plugins",
    ROOT / "src" / "hooks",
)
SOURCE_ROOT = ROOT / "src"
HANDLER_DECORATORS = {"handle"}


@dataclass(frozen=True)
class FunctionSpec:
    module_name: str
    name: str
    first_param_name: str | None
    requires_bot_first_param: bool
    dispatcher_target_module: str | None = None


@dataclass(frozen=True)
class GuardrailViolation:
    path: Path
    line: int
    col: int
    code: str
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}:{self.col}: {self.code} {self.message}"


def _module_name_for_path(project_root: Path, path: Path) -> str:
    relative = path.relative_to(project_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _attribute_path(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attribute_path(node.value)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"
    return None


def _annotation_name(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    return _attribute_path(node)


def _looks_like_bot_annotation(annotation: str | None) -> bool:
    if annotation is None:
        return False
    return annotation.rsplit(".", 1)[-1] == "Bot"


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        return _attribute_path(node.func)
    return _attribute_path(node)


def _is_handler_function(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    for decorator in node.decorator_list:
        name = _decorator_name(decorator)
        if name is None:
            continue
        if name.rsplit(".", 1)[-1] in HANDLER_DECORATORS:
            return True
    return False


def _iter_import_aliases(nodes: list[ast.stmt]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
            continue
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        for alias in node.names:
            full_name = f"{node.module}.{alias.name}"
            aliases[alias.asname or alias.name] = full_name
    return aliases


def _function_import_aliases(
    node: ast.AsyncFunctionDef | ast.FunctionDef,
) -> dict[str, str]:
    aliases = _iter_import_aliases(list(node.body))
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)):
            continue
        if isinstance(child, ast.Import):
            for alias in child.names:
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(child, ast.ImportFrom) and child.module is not None:
            for alias in child.names:
                full_name = f"{child.module}.{alias.name}"
                aliases[alias.asname or alias.name] = full_name
    return aliases


def _dispatcher_target_module(
    node: ast.AsyncFunctionDef | ast.FunctionDef,
) -> str | None:
    if not node.args.args:
        return None
    dispatcher_param = node.args.args[0].arg
    aliases = _function_import_aliases(node)
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if not isinstance(child.func, ast.Name) or child.func.id != "getattr":
            continue
        if len(child.args) < 2:
            continue
        receiver, key = child.args[0], child.args[1]
        if not isinstance(key, ast.Name) or key.id != dispatcher_param:
            continue
        receiver_path = _attribute_path(receiver)
        if receiver_path is None:
            continue
        return aliases.get(receiver_path, receiver_path)
    return None


def _function_specs_for_module(
    project_root: Path,
    path: Path,
    tree: ast.Module,
) -> dict[str, FunctionSpec]:
    module_name = _module_name_for_path(project_root, path)
    specs: dict[str, FunctionSpec] = {}
    for node in tree.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        positional_args = [*node.args.posonlyargs, *node.args.args]
        first_arg = positional_args[0] if positional_args else None
        first_annotation = _annotation_name(first_arg.annotation) if first_arg else None
        specs[node.name] = FunctionSpec(
            module_name=module_name,
            name=node.name,
            first_param_name=first_arg.arg if first_arg else None,
            requires_bot_first_param=(
                first_arg is not None and _looks_like_bot_annotation(first_annotation)
            ),
            dispatcher_target_module=_dispatcher_target_module(node),
        )
    return specs


def _build_function_index(project_root: Path) -> dict[str, dict[str, FunctionSpec]]:
    index: dict[str, dict[str, FunctionSpec]] = {}
    for path in sorted((project_root / "src").rglob("*.py")):
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        index[_module_name_for_path(project_root, path)] = _function_specs_for_module(
            project_root,
            path,
            tree,
        )
    return index


def _iter_body_calls(node: ast.AsyncFunctionDef | ast.FunctionDef) -> list[ast.Call]:
    calls: list[ast.Call] = []

    def visit(body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)):
                continue
            for child in ast.iter_child_nodes(stmt):
                if isinstance(child, ast.Call):
                    calls.append(child)
                if isinstance(child, ast.stmt):
                    visit([child])
                else:
                    for grandchild in ast.walk(child):
                        if isinstance(grandchild, ast.Call):
                            calls.append(grandchild)

    visit(list(node.body))
    seen: set[int] = set()
    ordered: list[ast.Call] = []
    for call in calls:
        marker = id(call)
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(call)
    return ordered


def _collect_bot_names(node: ast.AsyncFunctionDef | ast.FunctionDef) -> set[str]:
    names = {"bot"}
    for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
        if arg.arg == "bot":
            names.add(arg.arg)
            continue
        if _looks_like_bot_annotation(_annotation_name(arg.annotation)):
            names.add(arg.arg)
    return names


def _looks_like_bot_value(node: ast.AST, bot_names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in bot_names
    if isinstance(node, ast.Attribute):
        path = _attribute_path(node)
        return bool(path and path.endswith(".bot"))
    if isinstance(node, ast.Call):
        func_name = _attribute_path(node.func)
        if func_name in {"cast", "typing.cast"} and len(node.args) >= 2:
            return _looks_like_bot_value(node.args[1], bot_names)
    return False


def _resolve_direct_callee(
    call: ast.Call,
    module_name: str,
    function_index: dict[str, dict[str, FunctionSpec]],
) -> tuple[FunctionSpec, str] | None:
    if not isinstance(call.func, ast.Name):
        return None
    spec = function_index.get(module_name, {}).get(call.func.id)
    if spec is None or not spec.requires_bot_first_param:
        return None
    return spec, f"{module_name}.{spec.name}"


def _resolve_dynamic_callee(
    call: ast.Call,
    module_name: str,
    function_index: dict[str, dict[str, FunctionSpec]],
) -> tuple[FunctionSpec, str] | None:
    if not isinstance(call.func, ast.Name):
        return None
    dispatcher = function_index.get(module_name, {}).get(call.func.id)
    if dispatcher is None or dispatcher.dispatcher_target_module is None:
        return None
    if not call.args:
        return None
    target_name_node = call.args[0]
    if not isinstance(target_name_node, ast.Constant):
        return None
    if not isinstance(target_name_node.value, str):
        return None
    spec = function_index.get(dispatcher.dispatcher_target_module, {}).get(
        target_name_node.value
    )
    if spec is None or not spec.requires_bot_first_param:
        return None
    return spec, f"{spec.module_name}.{spec.name}"


def _call_supplies_bot(
    call: ast.Call,
    spec: FunctionSpec,
    *,
    is_dynamic: bool,
    bot_names: set[str],
) -> bool:
    positional_offset = 1 if is_dynamic else 0
    if len(call.args) > positional_offset:
        return _looks_like_bot_value(call.args[positional_offset], bot_names)
    if spec.first_param_name is None:
        return False
    for keyword in call.keywords:
        if keyword.arg == spec.first_param_name:
            return _looks_like_bot_value(keyword.value, bot_names)
    return False


def _scan_path(
    path: Path,
    project_root: Path,
    function_index: dict[str, dict[str, FunctionSpec]],
) -> list[GuardrailViolation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    module_name = _module_name_for_path(project_root, path)
    violations: list[GuardrailViolation] = []
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not _is_handler_function(node):
            continue
        bot_names = _collect_bot_names(node)
        for call in _iter_body_calls(node):
            resolved = _resolve_direct_callee(call, module_name, function_index)
            is_dynamic = False
            if resolved is None:
                resolved = _resolve_dynamic_callee(call, module_name, function_index)
                is_dynamic = resolved is not None
            if resolved is None:
                continue
            spec, target_name = resolved
            if _call_supplies_bot(
                call,
                spec,
                is_dynamic=is_dynamic,
                bot_names=bot_names,
            ):
                continue
            violations.append(
                GuardrailViolation(
                    path=path,
                    line=call.lineno,
                    col=call.col_offset,
                    code="MBI001",
                    message=(
                        "matcher handler 调用需要 Bot 的 helper 时必须显式传递 bot；"
                        f"当前调用未正确为 {target_name} 提供 bot 参数"
                    ),
                )
            )
    return violations


def iter_violations_for_path(
    path: Path,
    *,
    project_root: Path = ROOT,
    function_index: dict[str, dict[str, FunctionSpec]] | None = None,
) -> list[GuardrailViolation]:
    if function_index is None:
        function_index = _build_function_index(project_root)
    return _scan_path(path, project_root, function_index)


def iter_project_violations(
    *,
    project_root: Path = ROOT,
) -> list[GuardrailViolation]:
    function_index = _build_function_index(project_root)
    violations: list[GuardrailViolation] = []
    for root in SCAN_ROOTS:
        scan_root = project_root / root.relative_to(ROOT)
        if not scan_root.exists():
            continue
        for path in sorted(scan_root.rglob("*.py")):
            if path.is_file():
                violations.extend(_scan_path(path, project_root, function_index))
    return violations


def main(argv: list[str]) -> int:
    function_index = _build_function_index(ROOT)
    if len(argv) > 1:
        violations: list[GuardrailViolation] = []
        for raw_path in argv[1:]:
            violations.extend(
                iter_violations_for_path(
                    Path(raw_path),
                    function_index=function_index,
                )
            )
    else:
        violations = iter_project_violations()
    if violations:
        sys.stdout.write("\n".join(item.render() for item in violations) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
