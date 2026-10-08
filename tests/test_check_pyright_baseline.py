from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "check_pyright_baseline.py"
)


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_pyright_baseline", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    baseline: dict[str, int],
    current: dict[str, int],
) -> None:
    """把 collect() 与基线路径都替换掉，避免真的跑一遍 pyright。"""
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"total": sum(baseline.values()), "counts": baseline}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(gate, "collect", lambda: current)


def test_ratchet_passes_when_counts_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        baseline={"src/a.py::reportArgumentType": 2},
        current={"src/a.py::reportArgumentType": 2},
    )

    assert gate.main(["check_pyright_baseline.py"]) == 0


def test_ratchet_passes_when_counts_shrink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        baseline={"src/a.py::reportArgumentType": 5},
        current={"src/a.py::reportArgumentType": 2},
    )

    assert gate.main(["check_pyright_baseline.py"]) == 0


def test_ratchet_fails_when_a_new_bucket_appears(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        baseline={},
        current={"src/a.py::reportArgumentType": 1},
    )

    assert gate.main(["check_pyright_baseline.py"]) == 1


def test_ratchet_fails_when_a_bucket_grows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        baseline={"src/a.py::reportArgumentType": 1},
        current={"src/a.py::reportArgumentType": 3},
    )

    assert gate.main(["check_pyright_baseline.py"]) == 1


def test_ratchet_ignores_line_number_shifts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """基线按 file::rule 聚合，插入代码导致的行号漂移不应算作新增。"""
    _install(
        monkeypatch,
        tmp_path,
        baseline={"src/a.py::reportArgumentType": 2},
        current={"src/a.py::reportArgumentType": 2},
    )

    assert gate.main(["check_pyright_baseline.py"]) == 0


def test_update_writes_current_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        baseline={"src/a.py::reportArgumentType": 9},
        current={"src/a.py::reportArgumentType": 4},
    )

    assert gate.main(["check_pyright_baseline.py", "--update"]) == 0

    written = json.loads(gate.BASELINE_PATH.read_text(encoding="utf-8"))
    assert written["counts"] == {"src/a.py::reportArgumentType": 4}
    assert written["total"] == 4


def test_missing_baseline_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gate, "BASELINE_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(gate, "collect", lambda: {})

    assert gate.main(["check_pyright_baseline.py"]) == 1


def test_shipped_baseline_matches_repository_layout() -> None:
    """仓库自带的基线必须存在、可解析，且 total 与明细自洽。"""
    assert gate.BASELINE_PATH.is_file()

    raw = json.loads(gate.BASELINE_PATH.read_text(encoding="utf-8"))
    assert raw["total"] == sum(raw["counts"].values())
    assert raw["counts"]
    # 键统一使用仓库相对的 POSIX 路径，避免平台差异导致基线失效。
    for key in raw["counts"]:
        path_part = key.split("::", 1)[0]
        assert "\\" not in path_part, key
        assert not path_part.startswith("/"), key
