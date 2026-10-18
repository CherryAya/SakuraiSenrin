from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import nonebot
from nonebot.config import Config, DotEnvSettingsSource, SettingsError
from pydantic import BaseModel, ValidationError
import pytest


def test_support_groups_are_parsed_and_validated_from_dotenv(
    config_schema: type[BaseModel],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env.selected"
    env_file.write_text(
        "\n".join(
            [
                "SUPERUSERS=[]",
                "IGNORED_USERS=[]",
                "MAIN_GROUP_ID=",
                "GITHUB_TOKEN=",
                "GITHUB_REPO=",
                "GITHUB_BRANCH=master",
                'HELP_SUPPORT_GROUPS=\'[{"title":" 测试群 ","group_id":" 20001 ",'
                '"url":" https://example.invalid/group "}]\'',
                'ADMIN_NOTIFY_GROUP_IDS=["20001", " 20002 ", "20001", ""]',
            ]
        ),
        encoding="utf-8",
    )

    values = DotEnvSettingsSource(
        config_schema, env_file=env_file, env_file_encoding="utf-8"
    )()
    resolved = config_schema.model_validate(values).model_dump()

    assert resolved["HELP_SUPPORT_GROUPS"] == (
        {
            "title": "测试群",
            "group_id": "20001",
            "url": "https://example.invalid/group",
        },
    )
    assert resolved["ADMIN_NOTIFY_GROUP_IDS"] == ("20001", "20002")

    # 复用同一文件验证 NoneBot 原生配置 -> 插件配置的完整加载路径。
    runtime_config = Config(_env_file=env_file)
    monkeypatch.setattr(
        "nonebot.plugin.get_driver", lambda: SimpleNamespace(config=runtime_config)
    )
    runtime_resolved = nonebot.get_plugin_config(config_schema).model_dump()
    assert runtime_resolved["HELP_SUPPORT_GROUPS"] == resolved["HELP_SUPPORT_GROUPS"]
    assert (
        runtime_resolved["ADMIN_NOTIFY_GROUP_IDS"] == resolved["ADMIN_NOTIFY_GROUP_IDS"]
    )


def test_configuration_defaults_are_defined_by_the_current_model(
    config_schema: type[BaseModel], config_values: dict[str, object]
) -> None:
    resolved = config_schema.model_validate(config_values).model_dump()

    assert resolved["HELP_SUPPORT_GROUPS"] == ()
    assert resolved["ADMIN_NOTIFY_PRIVATE_ENABLED"] is True
    assert resolved["ADMIN_NOTIFY_GROUP_ENABLED"] is False
    assert resolved["ADMIN_NOTIFY_GROUP_IDS"] == ()
    assert resolved["DEBUG"] is False
    assert resolved["DEBUG_SQL_ECHO"] is False
    assert resolved["WORDBANK_MEDIA_REMOTE_SYNC_MODE"] == "deferred"
    assert "OBJECT_STORAGE_DEFAULT_PROVIDER" not in config_schema.model_fields


@pytest.mark.parametrize(
    "groups",
    [
        "not an array",
        [{"title": "测试群"}],
        [{"title": " ", "url": "https://example.invalid/group"}],
        [{"title": "测试群", "url": " "}],
        [
            {
                "title": "测试群",
                "group_id": 20001,
                "url": "https://example.invalid/group",
            }
        ],
        [
            {
                "title": "测试群",
                "url": "https://example.invalid/group",
                "old_name": "旧字段",
            }
        ],
    ],
)
def test_invalid_support_group_configuration_is_rejected(
    config_schema: type[BaseModel],
    config_values: dict[str, object],
    groups: object,
) -> None:
    with pytest.raises(ValidationError, match="HELP_SUPPORT_GROUPS"):
        config_schema.model_validate({**config_values, "HELP_SUPPORT_GROUPS": groups})


def test_invalid_support_group_json_is_not_replaced_with_defaults(
    config_schema: type[BaseModel], tmp_path: Path
) -> None:
    env_file = tmp_path / ".env.selected"
    env_file.write_text("HELP_SUPPORT_GROUPS=not-json\n", encoding="utf-8")

    with pytest.raises(SettingsError, match="help_support_groups"):
        DotEnvSettingsSource(
            config_schema, env_file=env_file, env_file_encoding="utf-8"
        )()


@pytest.mark.parametrize("group_ids", ['["20001"]', [20001], None])
def test_notification_groups_require_an_array_of_strings(
    config_schema: type[BaseModel],
    config_values: dict[str, object],
    group_ids: object,
) -> None:
    with pytest.raises(ValidationError, match="ADMIN_NOTIFY_GROUP_IDS"):
        config_schema.model_validate(
            {**config_values, "ADMIN_NOTIFY_GROUP_IDS": group_ids}
        )


def test_documentation_fingerprint_tracks_the_loaded_support_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import build_docs
    from src.config import config

    monkeypatch.setattr(build_docs, "_collect_build_input_paths", lambda: [])
    monkeypatch.setattr(config, "HELP_SUPPORT_GROUPS", ())
    empty_hash = build_docs._build_input_hash(columns=2)
    monkeypatch.setattr(
        config,
        "HELP_SUPPORT_GROUPS",
        (
            SimpleNamespace(
                title="测试群", group_id="20001", url="https://example.invalid/group"
            ),
        ),
    )
    configured_hash = build_docs._build_input_hash(columns=2)

    assert configured_hash != empty_hash
    monkeypatch.setenv("HELP_SUPPORT_GROUPS", "unused process value")
    assert build_docs._build_input_hash(columns=2) == configured_hash
