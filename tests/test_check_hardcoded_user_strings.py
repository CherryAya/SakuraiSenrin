from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "check_hardcoded_user_strings.py"
)


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_hardcoded_user_strings", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    return path


def _violations(source: str, tmp_path: Path) -> list[tuple[int, int, str]]:
    path = _write(tmp_path, source)
    return gate._iter_violations(path)


def test_gate_flags_raw_text_in_delivery_plan(tmp_path: Path) -> None:
    violations = _violations(
        """
from src.lib.message_plan import DeliveryPlan

plan = DeliveryPlan(
    messages=("群成员全量同步已结束。",),
    source_kind="x",
)
""",
        tmp_path,
    )

    assert [value for _, _, value in violations] == ["群成员全量同步已结束。"]


def test_gate_flags_raw_text_in_text_block(tmp_path: Path) -> None:
    violations = _violations(
        """
from src.lib.message_plan import TextBlock

block = TextBlock(text="已封禁")
""",
        tmp_path,
    )

    assert [value for _, _, value in violations] == ["已封禁"]


def test_gate_flags_raw_fstring_passed_to_finish(tmp_path: Path) -> None:
    violations = _violations(
        """
def handle(matcher, state) -> None:
    matcher.finish(f"共 {state.total} 个群")
""",
        tmp_path,
    )

    assert [value for _, _, value in violations] == ["共 ", " 个群"]


def test_gate_allows_tr_key_and_params(tmp_path: Path) -> None:
    violations = _violations(
        """
from src.lib.i18n.runtime import tr

text = tr("zh-CN", "admin.group.banned")
text = tr(locale, "admin.group.status", status="已授权")
""",
        tmp_path,
    )

    assert violations == []


def test_gate_allows_command_aliases(tmp_path: Path) -> None:
    violations = _violations(
        """
from nonebot import on_command

ALIASES = ("添加词条", "学习")
matcher = on_command("wordbank", aliases={"添加词条"})
""",
        tmp_path,
    )

    assert violations == []


def test_gate_allows_input_token_comparisons(tmp_path: Path) -> None:
    violations = _violations(
        """
def is_approve(text: str) -> bool:
    return text in {"通过", "同意", "批准"}


def is_scope(text: str) -> bool:
    return text in {"本群", "全局"}
""",
        tmp_path,
    )

    assert violations == []


def test_gate_allows_logger_output(tmp_path: Path) -> None:
    violations = _violations(
        """
logger.warning("冷库跳过: " + name)
logger.error(f"释放 connection: {url}")
""",
        tmp_path,
    )

    assert violations == []


def test_gate_allows_docstrings(tmp_path: Path) -> None:
    violations = _violations(
        '''
def build() -> str:
    """构造异常消息模板，用于提示用户输入错误。"""
    return "ok"
''',
        tmp_path,
    )

    assert violations == []


def test_gate_allows_localized_builder_call(tmp_path: Path) -> None:
    violations = _violations(
        """
from src.lib.message_plan import append_image_plan_entry

entry = append_image_plan_entry(build_text_plan_entry(title), image_bytes)
""",
        tmp_path,
    )

    assert violations == []


def test_gate_skips_database_and_scripts_paths(tmp_path: Path) -> None:
    path = _write(tmp_path, 'VALUE = "用户忽略自身消息开关"\n')

    assert gate._is_skipped(Path("src/database/core/tables.py")) is True
    assert gate._is_skipped(Path("src/scripts/install.py")) is True
    assert gate._is_skipped(Path("src/locales/zh_cn.py")) is True
    assert gate._is_skipped(path) is False


def test_gate_tolerates_syntax_errors(tmp_path: Path) -> None:
    path = _write(tmp_path, "def broken(:\n")

    assert gate._iter_violations(path) == []


def test_gate_returns_zero_for_clean_tree() -> None:
    root = Path(__file__).resolve().parents[1]

    assert gate.main(["check", str(root / "src")]) == 0


@pytest.mark.parametrize(
    "source",
    [
        "plan = DeliveryPlan(messages=('已结束',), source_kind='x')",
        "block = TextBlock(text='已封禁')",
    ],
)
def test_gate_reports_line_numbers(source: str, tmp_path: Path) -> None:
    violations = _violations(source, tmp_path)

    assert violations
    assert all(line >= 1 for line, _, _ in violations)
