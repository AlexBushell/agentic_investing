from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from research_platform.backup import (
    BackupError,
    CheckStatus,
    PreflightCheck,
    RestoreMode,
    build_restore_plan,
    redact_database_url,
    run_backup,
    run_restore_preflight,
    run_restore,
)
from research_platform.core.config import Settings


def make_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sample.txt").write_text("hello", encoding="utf-8")
    return Settings(
        data_dir=data_dir,
        backup_target_dir=tmp_path / "backups",
        backup_pg_dump_path="pg_dump",
        backup_psql_path="psql",
        database_url="postgresql+psycopg://postgres:secret@localhost:5432/company_intelligence",
    )


def make_backup_snapshot(tmp_path: Path) -> Path:
    backup_dir = tmp_path / "snapshot" / "20260524-123045"
    (backup_dir / "db").mkdir(parents=True)
    (backup_dir / "data").mkdir()
    (backup_dir / "db" / "company_intelligence.sql").write_text("-- dump", encoding="utf-8")
    (backup_dir / "data" / "sample.txt").write_text("restored", encoding="utf-8")
    (backup_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return backup_dir


def make_partial_backup_snapshot(tmp_path: Path, *, include_db: bool, include_data: bool) -> Path:
    backup_dir = tmp_path / "partial-snapshot" / "20260524-123045"
    backup_dir.mkdir(parents=True)
    if include_db:
        (backup_dir / "db").mkdir()
        (backup_dir / "db" / "company_intelligence.sql").write_text("-- dump", encoding="utf-8")
    if include_data:
        (backup_dir / "data").mkdir()
        (backup_dir / "data" / "sample.txt").write_text("restored", encoding="utf-8")
    (backup_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return backup_dir


class TestRunBackup:
    def test_creates_timestamped_backup_with_manifest_and_data_copy(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        snapshot_time = datetime(2026, 5, 24, 12, 30, 45, tzinfo=timezone.utc)

        with patch("research_platform.backup.dump_database") as dump_database:
            result = run_backup(settings=settings, timestamp=snapshot_time)

        assert result.backup_dir == (tmp_path / "backups" / "20260524-123045")
        assert result.db_dump_path.exists() is False
        assert result.data_copy_path.exists()
        assert (result.data_copy_path / "sample.txt").read_text(encoding="utf-8") == "hello"
        assert result.manifest_path.exists()
        dump_database.assert_called_once()
        manifest = result.manifest_path.read_text(encoding="utf-8")
        assert "company_intelligence.sql" in manifest
        assert "***" in manifest

    def test_uses_explicit_target_when_provided(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        target = tmp_path / "google-drive-backups"

        with patch("research_platform.backup.dump_database"):
            result = run_backup(
                settings=settings,
                target_root=target,
                timestamp=datetime(2026, 5, 24, 12, 30, 45, tzinfo=timezone.utc),
            )

        assert result.backup_dir.parent == target.resolve()

    def test_cleans_up_partial_backup_if_copy_fails(self, tmp_path: Path):
        settings = make_settings(tmp_path)

        with patch("research_platform.backup.dump_database"), patch(
            "research_platform.backup.copy_data_dir",
            side_effect=BackupError("copy failed"),
        ):
            with pytest.raises(BackupError):
                run_backup(
                    settings=settings,
                    timestamp=datetime(2026, 5, 24, 12, 30, 45, tzinfo=timezone.utc),
                )

        assert not (tmp_path / "backups" / "20260524-123045").exists()

    def test_rejects_target_inside_data_dir(self, tmp_path: Path):
        settings = make_settings(tmp_path)

        with patch("research_platform.backup.dump_database"):
            with pytest.raises(BackupError, match="child of it"):
                run_backup(
                    settings=settings,
                    target_root=settings.data_dir / "backups",
                    timestamp=datetime(2026, 5, 24, 12, 30, 45, tzinfo=timezone.utc),
                )

    def test_rejects_target_that_contains_data_dir(self, tmp_path: Path):
        settings = make_settings(tmp_path)

        with patch("research_platform.backup.dump_database"):
            with pytest.raises(BackupError, match="parent of the data directory"):
                run_backup(
                    settings=settings,
                    target_root=tmp_path,
                    timestamp=datetime(2026, 5, 24, 12, 30, 45, tzinfo=timezone.utc),
                )

    def test_emits_progress_messages(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        messages: list[str] = []

        with patch("research_platform.backup.dump_database"):
            run_backup(
                settings=settings,
                timestamp=datetime(2026, 5, 24, 12, 30, 45, tzinfo=timezone.utc),
                progress=messages.append,
            )

        assert any("Dumping PostgreSQL database" in message for message in messages)
        assert any("Backup completed successfully" in message for message in messages)


class TestDumpDatabase:
    def test_passes_no_password_flag(self, tmp_path: Path):
        output_path = tmp_path / "dump.sql"

        with patch("research_platform.backup.subprocess.run") as subprocess_run:
            subprocess_run.return_value.returncode = 0
            subprocess_run.return_value.stderr = ""

            from research_platform.backup import dump_database

            dump_database(
                database_url="postgresql+psycopg://postgres:secret@localhost:5432/company_intelligence",
                output_path=output_path,
            )

        cmd = subprocess_run.call_args.args[0]
        assert "--no-password" in cmd


class TestBuildRestorePlan:
    def test_builds_full_restore_plan(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        backup_dir = make_backup_snapshot(tmp_path)

        plan = build_restore_plan(settings=settings, backup_dir=backup_dir, mode=RestoreMode.FULL)

        assert plan.mode == RestoreMode.FULL
        assert plan.db_dump_path == backup_dir / "db" / "company_intelligence.sql"
        assert plan.data_copy_path == backup_dir / "data"
        assert plan.target_data_dir == settings.data_dir.resolve()
        assert plan.target_database_url_redacted is not None

    def test_rejects_restore_target_inside_backup_dir(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        backup_dir = make_backup_snapshot(tmp_path)

        with pytest.raises(BackupError, match="child of it"):
            build_restore_plan(
                settings=settings,
                backup_dir=backup_dir,
                mode=RestoreMode.FILES_ONLY,
                target_data_dir=backup_dir / "data" / "live",
            )

    def test_files_only_plan_allows_backup_without_db_dump(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        backup_dir = make_partial_backup_snapshot(tmp_path, include_db=False, include_data=True)

        plan = build_restore_plan(
            settings=settings,
            backup_dir=backup_dir,
            mode=RestoreMode.FILES_ONLY,
        )

        assert plan.data_copy_path == backup_dir / "data"
        assert plan.db_dump_path is None

    def test_db_only_plan_allows_backup_without_data_dir(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        backup_dir = make_partial_backup_snapshot(tmp_path, include_db=True, include_data=False)

        plan = build_restore_plan(
            settings=settings,
            backup_dir=backup_dir,
            mode=RestoreMode.DB_ONLY,
        )

        assert plan.db_dump_path == backup_dir / "db" / "company_intelligence.sql"
        assert plan.data_copy_path is None


class TestRestorePreflight:
    def test_reports_successful_checks(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        backup_dir = make_backup_snapshot(tmp_path)

        with patch(
            "research_platform.backup._resolve_command",
            return_value="C:\\tools\\stub.exe",
        ), patch(
            "research_platform.backup.inspect_postgres_restore_capabilities",
            return_value=[
                PreflightCheck(
                    name="Target Database Connectivity",
                    status=CheckStatus.PASS,
                    details="connected",
                ),
                PreflightCheck(
                    name="Temporary Database Create/Drop",
                    status=CheckStatus.PASS,
                    details="temp db ok",
                ),
            ],
        ):
            result = run_restore_preflight(
                settings=settings,
                backup_dir=backup_dir,
                mode=RestoreMode.FULL,
            )

        assert result.ok is True
        assert result.plan is not None
        assert any(check.name == "Backup Snapshot And Targets" for check in result.checks)

    def test_reports_failure_when_backup_layout_invalid(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        missing_backup_dir = tmp_path / "missing-backup"

        result = run_restore_preflight(
            settings=settings,
            backup_dir=missing_backup_dir,
            mode=RestoreMode.FULL,
        )

        assert result.ok is False
        assert result.plan is None
        assert result.checks[0].status == CheckStatus.FAIL


class TestRunRestore:
    def test_creates_pre_restore_backup_and_restores_files(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        backup_dir = make_backup_snapshot(tmp_path)
        (settings.data_dir / "sample.txt").write_text("live", encoding="utf-8")

        with patch("research_platform.backup.run_backup") as backup_run, patch(
            "research_platform.backup.restore_database"
        ) as restore_database:
            backup_run.return_value.backup_dir = tmp_path / "backups" / "pre-restore" / "20260524-123045"
            result = run_restore(
                settings=settings,
                backup_dir=backup_dir,
                mode=RestoreMode.FILES_ONLY,
                timestamp=datetime(2026, 5, 24, 12, 30, 45, tzinfo=timezone.utc),
            )

        assert backup_run.called
        assert not restore_database.called
        assert (settings.data_dir / "sample.txt").read_text(encoding="utf-8") == "restored"
        assert result.data_rollback_dir is not None
        assert result.data_rollback_dir.exists()
        assert (result.data_rollback_dir / "sample.txt").read_text(encoding="utf-8") == "live"

    def test_pre_restore_backup_uses_restore_targets(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        backup_dir = make_backup_snapshot(tmp_path)
        target_data_dir = tmp_path / "alternate-data"
        target_data_dir.mkdir()
        (target_data_dir / "sample.txt").write_text("alt-live", encoding="utf-8")
        target_db_url = "postgresql+psycopg://postgres:override@localhost:5432/alternate_db"

        with patch("research_platform.backup.run_backup") as backup_run, patch(
            "research_platform.backup.validate_database_dump"
        ), patch(
            "research_platform.backup.restore_database"
        ):
            backup_run.return_value.backup_dir = tmp_path / "backups" / "pre-restore" / "20260524-123045"
            run_restore(
                settings=settings,
                backup_dir=backup_dir,
                mode=RestoreMode.FULL,
                target_data_dir=target_data_dir,
                target_database_url=target_db_url,
                timestamp=datetime(2026, 5, 24, 12, 30, 45, tzinfo=timezone.utc),
            )

        backup_settings = backup_run.call_args.kwargs["settings"]
        assert backup_settings.data_dir == target_data_dir
        assert backup_settings.database_url == target_db_url

    def test_validates_dump_in_temp_db_before_live_restore(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        backup_dir = make_backup_snapshot(tmp_path)

        with patch("research_platform.backup.run_backup") as backup_run, patch(
            "research_platform.backup.validate_database_dump"
        ) as validate_dump, patch(
            "research_platform.backup.restore_database"
        ) as restore_database, patch(
            "research_platform.backup._emit_progress"
        ):
            backup_run.return_value.backup_dir = tmp_path / "backups" / "pre-restore" / "20260524-123045"
            run_restore(
                settings=settings,
                backup_dir=backup_dir,
                mode=RestoreMode.DB_ONLY,
                timestamp=datetime(2026, 5, 24, 12, 30, 45, tzinfo=timezone.utc),
            )

        validate_dump.assert_called_once()
        restore_database.assert_called_once()
        assert validate_dump.call_args is not None
        assert restore_database.call_args is not None

    def test_rolls_back_files_if_database_restore_fails(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        backup_dir = make_backup_snapshot(tmp_path)
        (settings.data_dir / "sample.txt").write_text("live", encoding="utf-8")

        with patch("research_platform.backup.run_backup") as backup_run, patch(
            "research_platform.backup.validate_database_dump"
        ), patch(
            "research_platform.backup.restore_database",
            side_effect=BackupError("db restore failed"),
        ):
            backup_run.return_value.backup_dir = tmp_path / "backups" / "pre-restore" / "20260524-123045"
            with pytest.raises(BackupError, match="db restore failed"):
                run_restore(
                    settings=settings,
                    backup_dir=backup_dir,
                    mode=RestoreMode.FULL,
                    timestamp=datetime(2026, 5, 24, 12, 30, 45, tzinfo=timezone.utc),
                )

        assert (settings.data_dir / "sample.txt").read_text(encoding="utf-8") == "live"

    def test_raises_if_file_rollback_fails(self, tmp_path: Path):
        settings = make_settings(tmp_path)
        backup_dir = make_backup_snapshot(tmp_path)
        (settings.data_dir / "sample.txt").write_text("live", encoding="utf-8")

        with patch("research_platform.backup.run_backup") as backup_run, patch(
            "research_platform.backup.validate_database_dump"
        ), patch(
            "research_platform.backup.restore_database",
            side_effect=BackupError("db restore failed"),
        ), patch(
            "research_platform.backup.rollback_restored_data",
            side_effect=BackupError("rollback failed"),
        ):
            backup_run.return_value.backup_dir = tmp_path / "backups" / "pre-restore" / "20260524-123045"
            with pytest.raises(BackupError, match="File rollback also failed"):
                run_restore(
                    settings=settings,
                    backup_dir=backup_dir,
                    mode=RestoreMode.FULL,
                    timestamp=datetime(2026, 5, 24, 12, 30, 45, tzinfo=timezone.utc),
                )


class TestRedactDatabaseUrl:
    def test_hides_password(self):
        redacted = redact_database_url(
            "postgresql+psycopg://postgres:secret@localhost:5432/company_intelligence"
        )
        assert "secret" not in redacted
        assert "***" in redacted
