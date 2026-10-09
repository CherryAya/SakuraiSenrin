"""deadcode 门禁（ratchet / 只允许下降）。

背景：用户要求「扫全项目 deadcode；如果必须存在则做标记；默认不允许 deadcode」。
deadcode 有四种来源，本脚本各自处理：

1. **未被引用的模块**（整文件级）——vulture 查不出（它只在文件内部的符号引用图上
   判定，整模块无人引用时其内部符号互相引用，全都「看似被用」）。本脚本用 AST 做
   入站 import 分析。
2. **未被引用的函数/类/方法/属性**——vulture 出候选（阈值 60），人工核实后登记。
3. **框架按名调用**（nonebot 装饰器、ORM 列、Protocol 成员、字符串派发等）——
   vulture 必然误报，需显式标记豁免。
4. **测试契约绑定**（src 符号仅被 tests 消费）——同上。

两种豁免方式，二选一：

- 行内标记（推荐，就近说明理由）—— 形如 ``deadcode-keep`` 前缀 + 类别 + 原因
  （本文件为避免自我匹配，不写出完整字面量）:

      实际写法为「井号 + deadcode-keep: 类别 | 一句话原因」

  类别枚举见 KEEP_KINDS。标记须紧跟在被豁免的**定义行之前**（定义行前的注释块内，
  中间不隔空行）；整模块豁免则写在模块 docstring 内。
  模块级标记归类为 ``<file>::<module>``，定义级归类为 ``<file>::<name>``。

- 清单文件 ``deadcode-allowlist.toml``：用于批量豁免（如 ORM 列、测试替身），
  按 ``file::name`` 记录，附理由。

**门禁逻辑**：扫描 → 减去 allowlist → 减去行内标记 → 若仍有剩余即失败，
提示「删除或补 ``# deadcode-keep:`` 标记」。这把「默认不允许 deadcode，
必须存在则标记」变成可机械执行的硬规则。

用法::

    uv run python scripts/check_deadcode.py            # 校验
    uv run python scripts/check_deadcode.py --update   # 刷新基线（仅在确认收敛后）
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "deadcode-baseline.toml"

# 行内标记前缀；类别与原因以 | 分隔。
MARKER_PREFIX = "# deadcode-keep:"

# 允许的豁免类别。新增类别须同时更新 CLAUDE.md 的说明。
KEEP_KINDS = frozenset(
    {
        "framework-hook",  # nonebot/apscheduler 装饰器注册的回调
        "orm-contract",  # SQLAlchemy 列 / relationship / TypeDecorator 钩子
        "protocol-member",  # Protocol 结构化匹配成员（形参名须与第三方逐字一致）
        "data-contract",  # TypedDict / dataclass / 跨进程 manifest 的序列化字段
        "state-accessor",  # 枚举 is_xxx/can_xxx 访问器（pre-commit 强制走访问器）
        "test-double",  # 测试替身按属性名满足契约的成员
        "test-binding",  # src 符号仅被 tests 直接引用
        "module-protocol",  # __path__ / __getattr__ / __all__ 等模块协议
        "public-api",  # 有意保留的公共 API 面（附理由）
        "manual-entry",  # 供人工执行的脚本入口（__main__）
    },
)

# 扫描根与被排除项。plugins/hooks 由 nonebot 按目录动态加载，模块级不参与
# 「入站 import」判定（其内部符号另有 allowlist 覆盖）。
SCAN_ROOTS = ("src", "tests", "scripts")
SKIP_PARTS = frozenset({"__pycache__", ".venv", "node_modules"})


def _iter_py(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py") if not any(part in SKIP_PARTS for part in p.parts)
    )


def _module_name(path: Path) -> tuple[str, bool]:
    """返回 (点分模块名, 是否包的 __init__)。"""
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    is_init = parts[-1] == "__init__"
    if is_init:
        parts = parts[:-1]
    return ".".join(parts), is_init


def _collect_imports() -> set[str]:
    """收集全仓所有 import 目标（含 ``from X import Y`` 展开出的子模块）。"""
    imported: set[str] = set()
    files = [p for r in SCAN_ROOTS for p in _iter_py(ROOT / r)]
    bot = ROOT / "bot.py"
    if bot.exists():
        files.append(bot)
    for path in files:
        name, is_init = _module_name(path)
        pkg = name if is_init else (name.rsplit(".", 1)[0] if "." in name else "")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = pkg
                    for _ in range(node.level - 1):
                        base = base.rsplit(".", 1)[0] if "." in base else ""
                    target = f"{base}.{node.module}" if node.module else base
                else:
                    target = node.module or ""
                imported.add(target)
                for alias in node.names:
                    imported.add(f"{target}.{alias.name}" if target else alias.name)
    return imported


def _is_reachable(name: str, is_init: bool, imported: set[str]) -> bool:
    """判断模块是否有入站引用。

    规则：
    - 该模块自身被 import，或
    - 其**子模块**被 import（包因加载子模块而必然被执行），或
    - 它是包的 ``__init__``，且有任一兄弟子模块被 import
      （``src.lib.plugin_docs.render.demo`` 被导入时，
      ``src.lib.plugin_docs.render`` 这个包也被执行）。
    """
    if name in imported:
        return True
    if any(t.startswith(name + ".") for t in imported):
        return True
    if is_init:
        # 包内任一子模块被引用，即认为包被使用
        prefix = name + "."
        if any(t.startswith(prefix) for t in imported):
            return True
    return False


def find_orphan_modules() -> list[str]:
    """找出零入站引用的模块（排除 nonebot 动态加载的 plugins/hooks）。"""
    imported = _collect_imports()
    orphans: list[str] = []
    for path in _iter_py(ROOT / "src"):
        name, is_init = _module_name(path)
        # nonebot 按目录加载，其模块名不出现在静态 import 图中
        if name.startswith(("src.plugins", "src.hooks")):
            continue
        if _is_reachable(name, is_init, imported):
            continue
        orphans.append(path.relative_to(ROOT).as_posix())
    return sorted(orphans)


def _load_allowlist() -> dict[str, str]:
    """读取 allowlist（``file::name`` -> 理由）。"""
    if not BASELINE_PATH.exists():
        return {}
    raw = tomllib.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    entries: dict[str, str] = {}
    for section in ("allow", "orphan-modules"):
        for key, value in (raw.get(section) or {}).items():
            entries[key] = str(value)
    return entries


def _extract_marker(line: str) -> str | None:
    """从一行中提取标记内容；仅当该行**以标记开头**时才算数。

    刻意不匹配「行内提及」的场景（如本文档里的说明文字、或别处引用该约定的
    散文），否则会产生自我匹配的假阳性。约定即「标记独占行首」。
    """
    text = line.strip()
    if not text.startswith(MARKER_PREFIX):
        return None
    return text[len(MARKER_PREFIX) :].strip()


def _marker_before(lines: list[str], lineno: int) -> str | None:
    """取定义行上方的标记（允许跨行注释块，不隔空行）。

    ``lineno`` 为 1-based 定义行号。
    """
    idx = lineno - 2  # 上一行的 0-based 下标
    for _ in range(6):  # 最多向上看 6 行
        if idx < 0:
            return None
        raw = lines[idx]
        marker = _extract_marker(raw)
        if marker is not None:
            return marker
        if not raw.strip().startswith("#"):
            return None  # 遇到非注释行即停止
        idx -= 1
    return None


def _validate_kind(
    marker: str,
    rel: str,
    lineno: int,
    malformed: list[str],
) -> bool:
    """校验标记类别是否合法；非法则记入 malformed 并返回 False。"""
    kind = marker.split("|", 1)[0].strip()
    if kind in KEEP_KINDS:
        return True
    malformed.append(
        f"{rel}:{lineno} 未知类别 {kind!r}（允许："
        + ", ".join(sorted(KEEP_KINDS))
        + "）",
    )
    return False


def _scan_markers() -> tuple[dict[str, str], list[str]]:
    """扫描全仓豁免标记。

    两种位置都识别：

    - **模块级**：放在模块 docstring 里（用于整模块豁免，如 CLI 入口），
      归类为 ``<file>::<module>``；
    - **定义级**：放在函数/类定义行上方的注释块里。
    """
    marked: dict[str, str] = {}
    malformed: list[str] = []
    for root in SCAN_ROOTS:
        for path in _iter_py(ROOT / root):
            rel = path.relative_to(ROOT).as_posix()
            lines = path.read_text(encoding="utf-8").splitlines()
            try:
                tree = ast.parse("\n".join(lines))
            except SyntaxError:
                continue

            # 模块级标记：docstring 内独占行的标记
            doc = ast.get_docstring(tree, clean=False) or ""
            for offset, doc_line in enumerate(doc.splitlines()):
                marker = _extract_marker(doc_line)
                if marker is None:
                    continue
                line_no = (tree.body[0].lineno if tree.body else 1) + offset
                if _validate_kind(marker, rel, line_no, malformed):
                    marked[f"{rel}::<module>"] = marker
                break

            # 定义级标记
            nodes = list(tree.body) + [
                n
                for parent in ast.walk(tree)
                if isinstance(parent, ast.ClassDef)
                for n in parent.body
            ]
            for node in nodes:
                if not isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                ):
                    continue
                marker = _marker_before(lines, node.lineno)
                if marker is None:
                    continue
                if _validate_kind(marker, rel, node.lineno, malformed):
                    marked[f"{rel}::{node.name}"] = marker
    return marked, malformed


def _write_baseline(orphans: list[str], allow: dict[str, str]) -> None:
    lines = [
        "# deadcode 基线：已核实「必须存在」的豁免清单。",
        "# 由 scripts/check_deadcode.py --update 生成；新增条目请附理由。",
        "",
        "[orphan-modules]",
    ]
    lines += [
        f'"{p}" = {allow.get(p, "TODO: 说明为何必须存在")!r}'.replace("'", '"')
        for p in orphans
    ]
    lines += ["", "[allow]"]
    lines += [
        f'"{k}" = {v!r}'.replace("'", '"')
        for k, v in sorted(allow.items())
        if not k.startswith("src/")
    ]
    BASELINE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="deadcode 门禁（ratchet）")
    parser.add_argument("--update", action="store_true", help="刷新基线")
    parser.add_argument(
        "--show",
        action="store_true",
        help="只打印当前扫描结果，不做判定",
    )
    args = parser.parse_args(argv[1:])

    orphans = find_orphan_modules()
    marked, malformed = _scan_markers()
    allowlist = _load_allowlist()

    if args.show:
        sys.stdout.write(f"零引用模块（{len(orphans)}）：\n")
        for p in orphans:
            sys.stdout.write(f"  {p}\n")
        sys.stdout.write(f"行内标记（{len(marked)}）：\n")
        for k, v in sorted(marked.items()):
            sys.stdout.write(f"  {k}  [{v}]\n")
        return 0

    if malformed:
        for msg in malformed:
            sys.stdout.write(f"标记格式错误：{msg}\n")
        return 1

    if args.update:
        _write_baseline(orphans, allowlist)
        sys.stdout.write(
            f"已刷新基线：{len(orphans)} 个模块，{len(allowlist)} 条豁免\n",
        )
        return 0

    if not BASELINE_PATH.exists():
        sys.stdout.write(
            f"缺少基线文件 {BASELINE_PATH.name}；先执行 --update 生成。\n",
        )
        return 1

    # 门禁：零引用模块必须被豁免（在 allowlist 里，或有行内标记）
    unresolved = [
        p
        for p in orphans
        if p not in allowlist and not any(k.startswith(p + "::") for k in marked)
    ]

    if unresolved:
        sys.stdout.write(
            f"发现 {len(unresolved)} 个零引用模块，既未删除也未标记：\n",
        )
        for p in unresolved:
            sys.stdout.write(f"  {p}\n")
        sys.stdout.write(
            "\n默认不允许 deadcode。请二选一：\n"
            "  1. 删除该模块；\n"
            f"  2. 若是框架按名加载等必须存在的情形，加行内标记 "
            f"`{MARKER_PREFIX} <类别> | <原因>`，"
            f"或在 {BASELINE_PATH.name} 登记。\n",
        )
        return 1

    sys.stdout.write(
        f"deadcode 门禁通过：零引用模块 {len(orphans)}，"
        f"其中豁免 {len([p for p in orphans if p in allowlist])}、"
        f"行内标记 {len(marked)} 处。\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
