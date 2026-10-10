from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]


def test_water_plugin_registers_archive_scheduler_job() -> None:
    script = """
import nonebot

nonebot.init(
    superusers={"1"},
    SUPERUSERS={"1"},
    IGNORED_USERS=set(),
    MAIN_GROUP_ID="10001",
    GITHUB_TOKEN="test-token",
    GITHUB_REPO="owner/repo",
    GITHUB_BRANCH="main",
    command_start={"#", "＃", "井"},
    command_sep={"."},
)

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

plugin = nonebot.load_plugin("src.plugins.water")
assert plugin is not None

job = scheduler.get_job("water_message_archive")
assert job is not None
assert job.trigger.fields[5].expressions[0].first == 0
assert job.trigger.fields[6].expressions[0].first == 25

summary_job = scheduler.get_job("water_summary_archive")
assert summary_job is not None
assert summary_job.trigger.fields[5].expressions[0].first == 0
assert summary_job.trigger.fields[6].expressions[0].first == 35

report_job = scheduler.get_job("water_daily_report_push")
assert report_job is not None
assert report_job.trigger.fields[5].expressions[0].first == 0
assert report_job.trigger.fields[6].expressions[0].first == 40

import src.plugins.water as water_module

assert water_module.water_recorder.priority < water_module.water_query.priority
assert water_module.water_recorder.block is False
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


def test_water_worker_mode_import_does_not_register_scheduler_jobs() -> None:
    script = """
import os
import nonebot

os.environ["SAKURAI_WATER_WORKER"] = "1"
nonebot.init(
    superusers={"1"},
    SUPERUSERS={"1"},
    IGNORED_USERS=set(),
    MAIN_GROUP_ID="10001",
    GITHUB_TOKEN="test-token",
    GITHUB_REPO="owner/repo",
    GITHUB_BRANCH="main",
    command_start={"#", "＃", "井"},
    command_sep={"."},
)

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler
from src.plugins.water.database import water_repo

assert water_repo is not None
assert scheduler.get_job("water_message_archive") is None
assert scheduler.get_job("water_summary_archive") is None
assert scheduler.get_job("water_daily_report_push") is None
"""
    env = dict(os.environ)
    env["SAKURAI_WATER_WORKER"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, output


def test_water_runtime_reload_reinitializes_plugin() -> None:
    script = """
import asyncio
import nonebot

nonebot.init(
    superusers={"1"},
    SUPERUSERS={"1"},
    IGNORED_USERS=set(),
    MAIN_GROUP_ID="10001",
    GITHUB_TOKEN="test-token",
    GITHUB_REPO="owner/repo",
    GITHUB_BRANCH="main",
    command_start={"#", "＃", "井"},
    command_sep={"."},
)

nonebot.require("nonebot_plugin_apscheduler")
import src.plugins.water as water_module
from src.plugins.water.database import water_repo
from src.plugins.water.services import matrix_suggestion

calls = []

async def _fake_init_all_tables():
    calls.append("init_all_tables")

async def _fake_warm_first():
    calls.append("warm_first")

async def _fake_warm_matrix():
    calls.append("warm_matrix")

water_repo.init_all_tables = _fake_init_all_tables
water_repo.warm_up_group_matrix_cache = _fake_warm_matrix
(
    matrix_suggestion.matrix_suggestion_service.warm_up_first_record_cache
) = _fake_warm_first

async def main():
    await water_module.initialize_water_plugin()
    first = list(calls)
    calls.clear()
    await water_module.initialize_water_plugin()
    assert calls == [], f"未复位时应早退，实际 {calls}"

    water_module.reset_water_plugin_initialized()
    await water_module.initialize_water_plugin()
    assert calls == ["init_all_tables", "warm_first", "warm_matrix"], (
        f"复位后应重新初始化，实际 {calls}"
    )
    assert first == ["init_all_tables", "warm_first", "warm_matrix"]

    # 恢复重载入口必须自行复位后重新初始化
    calls.clear()
    await water_module.reload_water_runtime_state()
    assert calls == ["init_all_tables", "warm_first", "warm_matrix"], (
        f"reload 入口应复位并重新初始化，实际 {calls}"
    )

asyncio.run(main())
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
