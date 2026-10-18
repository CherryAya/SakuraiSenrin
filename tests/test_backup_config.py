from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from nonebot.config import DotEnvSettingsSource
from pydantic import BaseModel
import pytest

from src.services import backup as backup_module
from src.services.backup import BackupPlan, BackupService, ResticConfig


@pytest.mark.parametrize("old_environment", ["production", "development", "custom"])
def test_current_repository_is_not_overridden_by_legacy_configuration(
    config_schema: type[BaseModel],
    old_environment: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
                "GITHUB_BRANCH=main",
                "BACKUP_RESTIC_REPOSITORY=s3:https://selected.invalid/current",
                "BACKUP_RESTIC_PASSWORD='  exact password  '",
                "R2_ACCESS_KEY_ID=test-key",
                "R2_SECRET_ACCESS_KEY=test-secret",
                "BACKUP_REQUIRE_RESTIC=false",
                f"APP_ENV={old_environment}",
                "BACKUP_REMOTE_PROFILE=prod",
                "BACKUP_PROD_RESTIC_REPOSITORY=must-not-be-used",
                "BACKUP_PROD_RESTIC_PASSWORD=old-password",
                "BACKUP_DEV_RESTIC_REPOSITORY=must-not-be-used-either",
                "BACKUP_DEV_RESTIC_PASSWORD=another-old-password",
                "BACKUP_PROFILES_JSON=not-valid-json",
            ]
        ),
        encoding="utf-8",
    )
    values = DotEnvSettingsSource(
        config_schema, env_file=env_file, env_file_encoding="utf-8"
    )()
    resolved = config_schema.model_validate(values)
    assert "APP_ENV" not in config_schema.model_fields
    assert not any(
        key.startswith(("BACKUP_PROD_", "BACKUP_DEV_"))
        or key in {"BACKUP_REMOTE_PROFILE", "BACKUP_PROFILES_JSON"}
        for key in config_schema.model_fields
    )
    monkeypatch.setattr(backup_module, "config", resolved)
    monkeypatch.setattr(
        backup_module, "ensure_backup_database_registrations_loaded", lambda: None
    )
    monkeypatch.setattr(backup_module, "get_registered_backup_databases", tuple)

    service = backup_module.build_backup_service_from_config()

    assert service.restic == ResticConfig(
        repository="s3:https://selected.invalid/current",
        password="  exact password  ",
        access_key_id="test-key",
        secret_access_key="test-secret",
        require_restic=False,
    )


def test_disabled_backup_allows_empty_credentials(
    config_schema: type[BaseModel],
) -> None:
    resolved = config_schema.model_validate(
        {
            "SUPERUSERS": set(),
            "IGNORED_USERS": set(),
            "MAIN_GROUP_ID": "",
            "GITHUB_TOKEN": "",
            "GITHUB_REPO": "",
            "GITHUB_BRANCH": "main",
        }
    )
    assert resolved.model_dump()["BACKUP_ENABLED"] is False
    assert resolved.model_dump()["BACKUP_RESTIC_REPOSITORY"] is None
    assert resolved.model_dump()["BACKUP_RESTIC_PASSWORD"] is None


@pytest.mark.parametrize("operation", ["backup", "snapshots", "restore"])
@pytest.mark.parametrize(
    ("repository", "password", "error"),
    [(None, "password", "repository"), ("repo", None, "password")],
)
async def test_missing_credentials_fail_before_external_io(
    operation: str,
    repository: str | None,
    password: str | None,
    error: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = AsyncMock()
    monkeypatch.setattr(backup_module.asyncio, "create_subprocess_exec", process)
    monkeypatch.setattr(backup_module, "dispatch_backup_event", AsyncMock())
    service = BackupService(
        databases=(),
        local_root=tmp_path / "backup",
        restic=ResticConfig(
            repository=repository, password=password, require_restic=False
        ),
    )

    if operation == "backup":
        pending = service.run(BackupPlan(enabled=True))
    elif operation == "snapshots":
        pending = service.list_snapshots()
    else:
        pending = service.restore(snapshot="latest", target=tmp_path / "restore")

    with pytest.raises(RuntimeError, match=f"backup {error} is not configured"):
        await pending

    process.assert_not_awaited()
    assert not service.local_root.exists()
    assert not (tmp_path / "restore").exists()


async def test_missing_restic_fails_before_creating_restore_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(backup_module.shutil, "which", lambda command: None)
    service = BackupService(
        databases=(),
        local_root=tmp_path / "backup",
        restic=ResticConfig(repository="repo", password="password"),
    )

    with pytest.raises(RuntimeError, match="restic command is not installed"):
        await service.restore(snapshot="latest", target=tmp_path / "restore")

    assert not (tmp_path / "restore").exists()
