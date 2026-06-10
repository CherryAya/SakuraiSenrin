from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]


def test_remove_plugin_loads_with_command_aliases() -> None:
    script = """
import nonebot

nonebot.init(
    SUPERUSERS={"1"},
    IGNORED_USERS=set(),
    MAIN_GROUP_ID="10001",
    GITHUB_TOKEN="test-token",
    GITHUB_REPO="owner/repo",
    GITHUB_BRANCH="main",
    WORDBANK_MEDIA_PROVIDER="local",
    command_start={"#", "＃", "井"},
    command_sep={"."},
)
plugin = nonebot.load_plugin("src.plugins.remove")
assert plugin is not None

from nonebot.rule import TrieRule

assert TrieRule.prefix.longest_prefix("#remove").key == "#remove"
assert TrieRule.prefix.longest_prefix("#退群").key == "#退群"
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, output
    assert "Duplicated prefix rule" not in output
