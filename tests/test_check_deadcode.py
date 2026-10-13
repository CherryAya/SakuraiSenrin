"""``scripts/check_deadcode.py`` 的行为测试。

零引用代码要真的被拦下：该脚本的价值在于「能失败」；
标记机制确实能豁免。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_deadcode.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_deadcode", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate() -> ModuleType:
    return _load_gate()


def test_marker_prefix_constant_matches_documented_convention(gate: ModuleType) -> None:
    """约定字面量须与文档一致，避免文档与实现漂移。"""
    assert gate.MARKER_PREFIX == "# deadcode-keep:"


def test_keep_kinds_are_non_empty_and_kebab_case(gate: ModuleType) -> None:
    assert gate.KEEP_KINDS, "类别枚举不得为空"
    for kind in gate.KEEP_KINDS:
        assert kind == kind.lower()
        assert " " not in kind
        assert "_" not in kind, f"{kind} 应为 kebab-case"


def test_extract_marker_requires_line_start(gate: ModuleType) -> None:
    """标记独占行首：行内提及被视为散文。"""
    assert gate._extract_marker("# deadcode-keep: test-double | 理由") == (
        "test-double | 理由"
    )
    # 缩进仍算个数
    assert gate._extract_marker("    # deadcode-keep: orm-contract | 理由")
    # 行内提及被忽略
    assert gate._extract_marker("- 说明里提到 # deadcode-keep: 的用法") is None
    assert gate._extract_marker("# 普通注释") is None


def test_marker_before_finds_marker_on_preceding_line(gate: ModuleType) -> None:
    lines = [
        "def f() -> None:",
        "    ...",
    ]
    # lineno=2 即定义行；上一行是代码
    assert gate._marker_before(lines, 2) is None

    lines = [
        "# deadcode-keep: framework-hook | nonebot 回调",
        "async def _on_startup() -> None:",
    ]
    assert gate._marker_before(lines, 2) == "framework-hook | nonebot 回调"


def test_marker_before_stops_at_non_comment_line(gate: ModuleType) -> None:
    """标记与定义之间隔着代码即失效，防远端注释误豁免。"""
    lines = [
        "# deadcode-keep: public-api | 理由",
        "x = 1",
        "def f() -> None:",
    ]
    assert gate._marker_before(lines, 3) is None


def test_marker_before_stops_at_blank_line(gate: ModuleType) -> None:
    lines = [
        "# deadcode-keep: public-api | 理由",
        "",
        "def f() -> None:",
    ]
    assert gate._marker_before(lines, 3) is None


def test_marker_before_allows_multi_line_comment_block(gate: ModuleType) -> None:
    """标记可落在多行注释块的任一行（隔空行即断）。"""
    lines = [
        "# deadcode-keep: test-binding | 仅被 tests 引用",
        "# 补充说明：删除需同步迁移测试",
        "def helper() -> None:",
    ]
    assert gate._marker_before(lines, 3) == "test-binding | 仅被 tests 引用"


def test_validate_kind_rejects_unknown(gate: ModuleType) -> None:
    malformed: list[str] = []
    ok = gate._validate_kind("not-a-kind | 理由", "a.py", 1, malformed)

    assert ok is False
    assert malformed
    assert "未知类别" in malformed[0]


def test_validate_kind_accepts_known(gate: ModuleType) -> None:
    malformed: list[str] = []

    assert gate._validate_kind("manual-entry | 理由", "a.py", 1, malformed) is True
    assert malformed == []


def test_orphan_scan_excludes_nonebot_dynamic_dirs(gate: ModuleType) -> None:
    """plugins/hooks 由 nonebot 按目录加载，跳过入站 import 判定。"""
    orphans = gate.find_orphan_modules()

    assert not [p for p in orphans if "/plugins/" in p or "/hooks/" in p]


def test_gate_passes_on_current_repo(gate: ModuleType) -> None:
    """当前仓库应为绿灯（所有零引用模块已删除或已标记）。"""
    assert gate.main(["check_deadcode"]) == 0


def test_gate_reports_dead_package_and_its_members(
    gate: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整包零引用时，包与成员模块都要报出。"""
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "lib" / "orphan.py").write_text(
        "def never_used() -> None: ...\n", encoding="utf-8"
    )

    orphans = gate.find_orphan_modules()

    assert "src/lib/orphan.py" in orphans


def test_package_init_not_orphan_when_sibling_imported(
    gate: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子模块被引用时包自身照常通过（导入子模块会执行它）。"""
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "lib" / "alive.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "src" / "lib" / "dead.py").write_text(
        "def never_used() -> None: ...\n", encoding="utf-8"
    )
    (tmp_path / "src" / "consumer.py").write_text(
        "from src.lib.alive import VALUE\n", encoding="utf-8"
    )

    orphans = gate.find_orphan_modules()

    assert "src/lib/__init__.py" not in orphans
    assert "src/lib/dead.py" in orphans


def test_referenced_module_is_not_an_orphan(
    gate: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """被 import 的模块跳过孤儿判定，防误报噪声。"""
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "used.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "src" / "main.py").write_text(
        "from src.used import VALUE\n", encoding="utf-8"
    )

    orphans = gate.find_orphan_modules()

    assert "src/used.py" not in orphans
    assert "src/main.py" in orphans  # main.py 自身零引用


def test_gate_accepts_module_marked_in_docstring(
    gate: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模块 docstring 内的标记豁免整模块（CLI 入口的用法）。"""
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    (tmp_path / "src" / "tools").mkdir(parents=True)
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "tools" / "cli.py").write_text(
        '"""工具。\n\n# deadcode-keep: manual-entry | 人工执行的 CLI\n"""\n\n'
        "def main() -> None: ...\n",
        encoding="utf-8",
    )

    marked, malformed = gate._scan_markers()

    assert malformed == []
    assert "src/tools/cli.py::<module>" in marked
