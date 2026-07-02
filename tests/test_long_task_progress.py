from __future__ import annotations

import json
from pathlib import Path

from src.lib.long_task_progress import (
    LongTaskAuditTarget,
    build_long_task_progress_report,
    write_long_task_progress_endpoint,
)


def test_long_task_progress_report_detects_complete_and_legacy_candidates(
    tmp_path: Path,
) -> None:
    complete_file = tmp_path / "src" / "plugins" / "demo" / "complete.py"
    complete_file.parent.mkdir(parents=True, exist_ok=True)
    complete_file.write_text(
        "\n".join(
            [
                "from src.lib.long_task import (",
                "    LoggerProgressSink,",
                "    LongTaskRunner,",
                "    MessageEventProgressSink,",
                ")",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    legacy_file = tmp_path / "src" / "plugins" / "demo" / "legacy.py"
    legacy_file.write_text(
        'WAIT = "正在执行手动备份，请稍候..."\n',
        encoding="utf-8",
    )
    heavy_file = tmp_path / "src" / "plugins" / "demo" / "heavy.py"
    heavy_file.write_text(
        "\n".join(
            [
                "from src.lib.message_plan import (",
                "    ImageBytesBlock,",
                "    deliver_message_plan,",
                ")",
                "from src.lib.utils.img import QQAvatar",
                "async def run(matcher):",
                '    avatar = await QQAvatar.fetch_user("1")',
                "    await matcher.finish(ImageBytesBlock(avatar))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    targets = (
        LongTaskAuditTarget(
            slug="demo.complete",
            label="Demo Complete",
            path="src/plugins/demo/complete.py",
            category="plugin",
            description="completed long task integration",
            expect_logger_sink=True,
            expect_message_event_sink=True,
        ),
        LongTaskAuditTarget(
            slug="demo.partial",
            label="Demo Partial",
            path="src/plugins/demo/partial.py",
            category="plugin",
            description="missing file should be reported",
        ),
    )

    payload = build_long_task_progress_report(root=tmp_path, targets=targets)

    assert payload["summary"]["total_targets"] == 2
    assert payload["summary"]["complete_targets"] == 1
    assert payload["summary"]["missing_file_targets"] == 1
    assert payload["summary"]["legacy_wait_candidates"] == 1
    assert payload["summary"]["heavy_path_candidates"] == 1
    assert payload["targets"][0]["status"] == "complete"
    assert payload["targets"][1]["status"] == "missing_file"
    assert payload["legacy_wait_candidates"][0]["path"] == "src/plugins/demo/legacy.py"
    assert payload["heavy_path_candidates"][0]["path"] == "src/plugins/demo/heavy.py"
    assert payload["heavy_path_candidates"][0]["reasons"] == [
        "avatar_fetch",
        "image_render",
    ]


def test_long_task_progress_endpoint_writer_outputs_json(tmp_path: Path) -> None:
    target_file = tmp_path / "src" / "plugins" / "demo" / "runner.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(
        "from src.lib.long_task import LoggerProgressSink, LongTaskRunner\n",
        encoding="utf-8",
    )

    output = tmp_path / "long-task-progress.json"
    payload = write_long_task_progress_endpoint(
        output,
        root=tmp_path,
        targets=(
            LongTaskAuditTarget(
                slug="demo.runner",
                label="Demo Runner",
                path="src/plugins/demo/runner.py",
                category="plugin",
                description="runner target",
                expect_logger_sink=True,
            ),
        ),
    )

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["summary"]["complete_targets"] == 1
    assert saved == payload
