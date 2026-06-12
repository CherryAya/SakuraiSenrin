from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]


def test_wordbank_plugin_loads_without_duplicate_command_prefix_warnings() -> None:
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
plugin = nonebot.load_plugin("src.plugins.wordbank")
assert plugin is not None

from nonebot.rule import TrieRule

assert TrieRule.prefix.longest_prefix("#wordbank.add 晚安").key == "#wordbank.add"
assert TrieRule.prefix.longest_prefix("#wordbank.del 1").key == "#wordbank.del"
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


def test_wordbank_plugin_registers_archive_scheduler_job() -> None:
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

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

plugin = nonebot.load_plugin("src.plugins.wordbank")
assert plugin is not None

job = scheduler.get_job("wordbank_event_archive")
assert job is not None
assert job.trigger.fields[5].expressions[0].first == 0
assert job.trigger.fields[6].expressions[0].first == 30
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
