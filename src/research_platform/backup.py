from __future__ import annotations

import json
import os
import shutil
import subprocess
from shutil import which
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from sqlalchemy.engine import make_url

from research_platform.core.config import Settings


class BackupError(RuntimeError):
    """Raised when the local backup or restore workflow cannot complete."""


@dataclass
class BackupResult:
    backup_dir: Path
    db_dump_path: Path
    data_copy_path: Path
    manifest_path: Path


class RestoreMode(str, Enum):
    FILES_ONLY = "files-only"
    DB_ONLY = "db-only"
    FULL = "full"


@dataclass
class BackupContents:
    backup_dir: Path
    manifest_path: Path
    db_dump_path: Path
    data_copy_path: Path


@dataclass
class RestorePlan:
    backup_dir: Path
    mode: RestoreMode
    db_dump_path: Path | None
    data_copy_path: Path | None
    target_database_url_redacted: str | None
    target_data_dir: Path | None
    pre_restore_backup_root: Path | None


@dataclass
class RestoreResult:
    mode: RestoreMode
    backup_dir: Path
    target_data_dir: Path | None
    data_rollback_dir: Path | None
    pre_restore_backup_dir: Path | None
    target_database_url_redacted: str | None


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"


@dataclass
class PreflightCheck:
    name: str
    status: CheckStatus
    details: str


@dataclass
class RestorePreflightResult:
    plan: RestorePlan | None
    checks: list[PreflightCheck]

    @property
    def ok(self) -> bool:
        return all(check.status != CheckStatus.FAIL for check in self.checks)


ProgressCallback = Callable[[str], None]


def run_backup(
    settings: Settings,
    target_root: Path | None = None,
    timestamp: datetime | None = None,
    progress: ProgressCallback | None = None,
) -> BackupResult:
    """Create a timestamped local backup containing a DB dump and copied data dir."""
    target_root = (target_root or settings.backup_target_dir).resolve()
    source_data_dir = settings.data_dir.resolve()
    _validate_backup_target(source_data_dir=source_data_dir, target_root=target_root)
    snapshot_time = timestamp or datetime.now(timezone.utc)
    backup_dir = target_root / snapshot_time.strftime("%Y%m%d-%H%M%S")
    db_dir = backup_dir / "db"
    data_copy_path = backup_dir / "data"
    db_dump_path = db_dir / "company_intelligence.sql"
    manifest_path = backup_dir / "manifest.json"

    if backup_dir.exists():
        raise BackupError(f"Backup directory already exists: {backup_dir}")

    db_dir.mkdir(parents=True, exist_ok=False)

    try:
        _emit_progress(progress, f"Creating backup in {backup_dir}")
        _emit_progress(progress, "Dumping PostgreSQL database")
        dump_database(
            database_url=settings.database_url,
            output_path=db_dump_path,
            pg_dump_path=settings.backup_pg_dump_path,
        )
        _emit_progress(progress, f"Copying data directory from {source_data_dir}")
        copy_data_dir(source_data_dir, data_copy_path)
        write_manifest(
            manifest_path=manifest_path,
            snapshot_time=snapshot_time,
            settings=settings,
            db_dump_path=db_dump_path,
            data_copy_path=data_copy_path,
        )
        _emit_progress(progress, "Backup completed successfully")
    except Exception as exc:
        try:
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
        except Exception as cleanup_exc:
            raise BackupError(
                f"Backup failed: {exc}. Cleanup of partial backup also failed: {cleanup_exc}"
            ) from exc
        raise

    return BackupResult(
        backup_dir=backup_dir,
        db_dump_path=db_dump_path,
        data_copy_path=data_copy_path,
        manifest_path=manifest_path,
    )


def build_restore_plan(
    settings: Settings,
    backup_dir: Path,
    mode: RestoreMode = RestoreMode.FULL,
    target_data_dir: Path | None = None,
    target_database_url: str | None = None,
    pre_restore_backup_root: Path | None = None,
) -> RestorePlan:
    """Validate a backup snapshot and describe what a restore would target."""
    contents = inspect_backup_contents(backup_dir, mode=mode)
    resolved_target_data_dir = None
    target_database_url_redacted = None

    if mode in {RestoreMode.FILES_ONLY, RestoreMode.FULL}:
        resolved_target_data_dir = (target_data_dir or settings.data_dir).resolve()
        _validate_restore_target_data_dir(
            backup_dir=contents.backup_dir,
            source_data_dir=contents.data_copy_path,
            target_data_dir=resolved_target_data_dir,
        )

    if mode in {RestoreMode.DB_ONLY, RestoreMode.FULL}:
        target_database_url_redacted = redact_database_url(
            target_database_url or settings.database_url
        )

    if pre_restore_backup_root is not None:
        resolved_pre_restore_backup_root = pre_restore_backup_root.resolve()
    else:
        resolved_pre_restore_backup_root = (settings.backup_target_dir / "pre-restore").resolve()

    return RestorePlan(
        backup_dir=contents.backup_dir,
        mode=mode,
        db_dump_path=contents.db_dump_path if mode in {RestoreMode.DB_ONLY, RestoreMode.FULL} else None,
        data_copy_path=contents.data_copy_path if mode in {RestoreMode.FILES_ONLY, RestoreMode.FULL} else None,
        target_database_url_redacted=target_database_url_redacted,
        target_data_dir=resolved_target_data_dir,
        pre_restore_backup_root=resolved_pre_restore_backup_root,
    )


def run_restore(
    settings: Settings,
    backup_dir: Path,
    mode: RestoreMode = RestoreMode.FULL,
    target_data_dir: Path | None = None,
    target_database_url: str | None = None,
    create_pre_restore_backup: bool = True,
    pre_restore_backup_root: Path | None = None,
    timestamp: datetime | None = None,
    progress: ProgressCallback | None = None,
) -> RestoreResult:
    """Restore files and/or the PostgreSQL database from a validated backup snapshot."""
    snapshot_time = timestamp or datetime.now(timezone.utc)
    plan = build_restore_plan(
        settings=settings,
        backup_dir=backup_dir,
        mode=mode,
        target_data_dir=target_data_dir,
        target_database_url=target_database_url,
        pre_restore_backup_root=pre_restore_backup_root,
    )

    pre_restore_backup_dir = None
    if create_pre_restore_backup:
        _emit_progress(progress, "Creating pre-restore safety backup")
        backup_settings = settings.model_copy(
            update={
                "data_dir": target_data_dir or settings.data_dir,
                "database_url": target_database_url or settings.database_url,
            }
        )
        pre_restore_backup_dir = run_backup(
            settings=backup_settings,
            target_root=plan.pre_restore_backup_root,
            timestamp=snapshot_time,
            progress=progress,
        ).backup_dir

    data_rollback_dir = None
    restored_files = False

    try:
        if plan.mode in {RestoreMode.FILES_ONLY, RestoreMode.FULL} and plan.data_copy_path and plan.target_data_dir:
            _emit_progress(progress, f"Restoring data directory to {plan.target_data_dir}")
            data_rollback_dir = restore_data_dir(
                source_dir=plan.data_copy_path,
                target_dir=plan.target_data_dir,
                snapshot_time=snapshot_time,
            )
            restored_files = True

        if plan.mode in {RestoreMode.DB_ONLY, RestoreMode.FULL} and plan.db_dump_path:
            _emit_progress(progress, "Validating database dump in a temporary database")
            validate_database_dump(
                database_url=target_database_url or settings.database_url,
                dump_path=plan.db_dump_path,
                psql_path=settings.backup_psql_path,
                snapshot_time=snapshot_time,
            )
            _emit_progress(progress, "Restoring database to live target")
            restore_database(
                database_url=target_database_url or settings.database_url,
                dump_path=plan.db_dump_path,
                psql_path=settings.backup_psql_path,
            )
        _emit_progress(progress, "Restore completed successfully")
    except Exception as exc:
        if restored_files and plan.target_data_dir:
            try:
                rollback_restored_data(target_dir=plan.target_data_dir, rollback_dir=data_rollback_dir)
            except Exception as rollback_exc:
                raise BackupError(
                    f"Restore failed: {exc}. File rollback also failed: {rollback_exc}"
                ) from exc
        raise

    return RestoreResult(
        mode=plan.mode,
        backup_dir=plan.backup_dir,
        target_data_dir=plan.target_data_dir,
        data_rollback_dir=data_rollback_dir,
        pre_restore_backup_dir=pre_restore_backup_dir,
        target_database_url_redacted=plan.target_database_url_redacted,
    )


def run_restore_preflight(
    settings: Settings,
    backup_dir: Path,
    mode: RestoreMode = RestoreMode.FULL,
    target_data_dir: Path | None = None,
    target_database_url: str | None = None,
    create_pre_restore_backup: bool = True,
    pre_restore_backup_root: Path | None = None,
    timestamp: datetime | None = None,
) -> RestorePreflightResult:
    """Run a cautious restore checklist before any live restore work begins."""
    checks: list[PreflightCheck] = []
    snapshot_time = timestamp or datetime.now(timezone.utc)

    try:
        plan = build_restore_plan(
            settings=settings,
            backup_dir=backup_dir,
            mode=mode,
            target_data_dir=target_data_dir,
            target_database_url=target_database_url,
            pre_restore_backup_root=pre_restore_backup_root,
        )
    except BackupError as exc:
        checks.append(
            PreflightCheck(
                name="Backup Snapshot And Targets",
                status=CheckStatus.FAIL,
                details=str(exc),
            )
        )
        return RestorePreflightResult(plan=None, checks=checks)

    checks.append(
        PreflightCheck(
            name="Backup Snapshot And Targets",
            status=CheckStatus.PASS,
            details="Backup layout and restore target paths look valid.",
        )
    )

    if plan.target_data_dir is not None:
        _append_directory_writability_check(
            checks=checks,
            name="Data Directory Parent Writable",
            directory=plan.target_data_dir.parent,
        )

    if create_pre_restore_backup and plan.pre_restore_backup_root is not None:
        _append_tool_check(
            checks=checks,
            name="pg_dump Available",
            command=settings.backup_pg_dump_path,
        )
        _append_directory_writability_check(
            checks=checks,
            name="Pre-Restore Backup Root Writable",
            directory=plan.pre_restore_backup_root,
        )

    if mode in {RestoreMode.DB_ONLY, RestoreMode.FULL}:
        _append_tool_check(
            checks=checks,
            name="psql Available",
            command=settings.backup_psql_path,
        )
        checks.extend(
            inspect_postgres_restore_capabilities(
                database_url=target_database_url or settings.database_url,
                psql_path=settings.backup_psql_path,
                snapshot_time=snapshot_time,
            )
        )

    return RestorePreflightResult(plan=plan, checks=checks)


def dump_database(database_url: str, output_path: Path, pg_dump_path: str = "pg_dump") -> None:
    """Create a plain SQL dump using pg_dump and a SQLAlchemy-style DATABASE_URL."""
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise BackupError("Backup currently supports PostgreSQL DATABASE_URL values only.")
    if not url.database:
        raise BackupError("DATABASE_URL must include a database name for backup.")

    cmd = [
        pg_dump_path,
        "--no-password",
        "--host",
        url.host or "localhost",
        "--port",
        str(url.port or 5432),
        "--username",
        url.username or "postgres",
        "--dbname",
        url.database,
        "--file",
        str(output_path),
    ]

    env = _postgres_env(url.password)
    _run_subprocess(
        cmd,
        env=env,
        missing_tool_message=f"pg_dump not found at '{pg_dump_path}'. Set BACKUP_PG_DUMP_PATH if needed.",
    )


def restore_database(database_url: str, dump_path: Path, psql_path: str = "psql") -> None:
    """Restore a plain SQL dump into PostgreSQL after clearing the public schema."""
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise BackupError("Restore currently supports PostgreSQL DATABASE_URL values only.")
    if not url.database:
        raise BackupError("DATABASE_URL must include a database name for restore.")
    if not dump_path.exists():
        raise BackupError(f"Database dump not found: {dump_path}")

    base_cmd = [
        psql_path,
        "--no-password",
        "--host",
        url.host or "localhost",
        "--port",
        str(url.port or 5432),
        "--username",
        url.username or "postgres",
        "--dbname",
        url.database,
        "--set",
        "ON_ERROR_STOP=1",
    ]

    env = _postgres_env(url.password)
    _run_subprocess(
        base_cmd + ["--command", "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"],
        env=env,
        missing_tool_message=f"psql not found at '{psql_path}'. Set BACKUP_PSQL_PATH if needed.",
    )
    _run_subprocess(
        base_cmd + ["--file", str(dump_path)],
        env=env,
        missing_tool_message=f"psql not found at '{psql_path}'. Set BACKUP_PSQL_PATH if needed.",
    )


def validate_database_dump(
    database_url: str,
    dump_path: Path,
    psql_path: str = "psql",
    snapshot_time: datetime | None = None,
) -> None:
    """Prove the SQL dump can be loaded by restoring it into a temporary database first."""
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise BackupError("Restore currently supports PostgreSQL DATABASE_URL values only.")
    if not url.database:
        raise BackupError("DATABASE_URL must include a database name for restore.")
    if not dump_path.exists():
        raise BackupError(f"Database dump not found: {dump_path}")

    temp_db_name = _temporary_database_name(url.database, snapshot_time or datetime.now(timezone.utc))
    maintenance_cmd = _psql_base_cmd(url, psql_path=psql_path, database="postgres")
    temp_cmd = _psql_base_cmd(url, psql_path=psql_path, database=temp_db_name)
    env = _postgres_env(url.password)

    try:
        _run_subprocess(
            maintenance_cmd + ["--command", f"CREATE DATABASE {_quote_identifier(temp_db_name)};"],
            env=env,
            missing_tool_message=f"psql not found at '{psql_path}'. Set BACKUP_PSQL_PATH if needed.",
        )
        _run_subprocess(
            temp_cmd + ["--file", str(dump_path)],
            env=env,
            missing_tool_message=f"psql not found at '{psql_path}'. Set BACKUP_PSQL_PATH if needed.",
        )
    finally:
        _drop_database(url=url, database_name=temp_db_name, psql_path=psql_path)


def inspect_postgres_restore_capabilities(
    database_url: str,
    psql_path: str,
    snapshot_time: datetime,
) -> list[PreflightCheck]:
    """Check PostgreSQL connectivity and restore-relevant capabilities."""
    checks: list[PreflightCheck] = []
    url = make_url(database_url)

    if not url.drivername.startswith("postgresql"):
        return [
            PreflightCheck(
                name="Target Database Connectivity",
                status=CheckStatus.FAIL,
                details="Only PostgreSQL restore targets are supported.",
            )
        ]

    try:
        metadata = _inspect_postgres_role_metadata(url)
    except BackupError as exc:
        return [
            PreflightCheck(
                name="Target Database Connectivity",
                status=CheckStatus.FAIL,
                details=str(exc),
            )
        ]

    checks.append(
        PreflightCheck(
            name="Target Database Connectivity",
            status=CheckStatus.PASS,
            details=(
                f"Connected as {metadata['current_user']} to {url.database}; "
                f"superuser={metadata['rolsuper']}, createdb={metadata['rolcreatedb']}."
            ),
        )
    )

    if metadata["rolsuper"] or metadata["owns_public"]:
        checks.append(
            PreflightCheck(
                name="Live Schema Reset Capability",
                status=CheckStatus.PASS,
                details="Role appears able to drop and recreate the public schema.",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                name="Live Schema Reset Capability",
                status=CheckStatus.WARN,
                details=(
                    "Role is not a superuser and does not appear to own public. "
                    "Live schema reset may still fail."
                ),
            )
        )

    try:
        _check_maintenance_database_access(url)
        checks.append(
            PreflightCheck(
                name="Maintenance Database Connectivity",
                status=CheckStatus.PASS,
                details="Connected to the postgres maintenance database successfully.",
            )
        )
    except BackupError as exc:
        checks.append(
            PreflightCheck(
                name="Maintenance Database Connectivity",
                status=CheckStatus.FAIL,
                details=str(exc),
            )
        )
        return checks

    try:
        probe_temp_database_capability(
            database_url=database_url,
            psql_path=psql_path,
            snapshot_time=snapshot_time,
        )
        checks.append(
            PreflightCheck(
                name="Temporary Database Create/Drop",
                status=CheckStatus.PASS,
                details="Successfully created and dropped a temporary database.",
            )
        )
    except BackupError as exc:
        checks.append(
            PreflightCheck(
                name="Temporary Database Create/Drop",
                status=CheckStatus.FAIL,
                details=str(exc),
            )
        )

    return checks


def copy_data_dir(source_dir: Path, destination_dir: Path) -> None:
    """Copy the local data directory into the backup snapshot."""
    source_dir = source_dir.resolve()
    if not source_dir.exists():
        raise BackupError(f"Data directory does not exist: {source_dir}")
    if not source_dir.is_dir():
        raise BackupError(f"Data path is not a directory: {source_dir}")

    shutil.copytree(source_dir, destination_dir)


def restore_data_dir(source_dir: Path, target_dir: Path, snapshot_time: datetime) -> Path | None:
    """Replace the target data directory with the backup copy, preserving a rollback snapshot."""
    source_dir = source_dir.resolve()
    target_dir = target_dir.resolve()

    if not source_dir.exists() or not source_dir.is_dir():
        raise BackupError(f"Restore source data directory is invalid: {source_dir}")
    if target_dir.exists() and not target_dir.is_dir():
        raise BackupError(f"Restore target data path is not a directory: {target_dir}")

    rollback_dir = None
    if target_dir.exists():
        rollback_dir = target_dir.with_name(
            f"{target_dir.name}.pre_restore.{snapshot_time.strftime('%Y%m%d-%H%M%S')}"
        )
        if rollback_dir.exists():
            raise BackupError(f"Rollback directory already exists: {rollback_dir}")
        target_dir.rename(rollback_dir)

    try:
        shutil.copytree(source_dir, target_dir)
    except Exception:
        if rollback_dir and rollback_dir.exists() and not target_dir.exists():
            rollback_dir.rename(target_dir)
        raise

    return rollback_dir


def rollback_restored_data(target_dir: Path, rollback_dir: Path | None) -> None:
    """Restore the previous data directory if a later restore step fails."""
    if target_dir.exists():
        shutil.rmtree(target_dir)
    if rollback_dir and rollback_dir.exists():
        rollback_dir.rename(target_dir)


def write_manifest(
    manifest_path: Path,
    snapshot_time: datetime,
    settings: Settings,
    db_dump_path: Path,
    data_copy_path: Path,
) -> None:
    """Write a small manifest so the backup is self-describing."""
    manifest = {
        "created_at_utc": snapshot_time.astimezone(timezone.utc).isoformat(),
        "database_url_redacted": redact_database_url(settings.database_url),
        "db_dump_file": db_dump_path.name,
        "data_dir_name": data_copy_path.name,
        "source_data_dir": str(settings.data_dir.resolve()),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def redact_database_url(database_url: str) -> str:
    """Hide the password while keeping the connection target visible in metadata."""
    url = make_url(database_url)
    return url.render_as_string(hide_password=True)


def inspect_backup_contents(
    backup_dir: Path,
    mode: RestoreMode = RestoreMode.FULL,
) -> BackupContents:
    """Validate the expected backup layout and return the discovered file paths."""
    backup_dir = backup_dir.resolve()
    manifest_path = backup_dir / "manifest.json"
    db_dump_path = backup_dir / "db" / "company_intelligence.sql"
    data_copy_path = backup_dir / "data"

    if not backup_dir.exists() or not backup_dir.is_dir():
        raise BackupError(f"Backup directory does not exist: {backup_dir}")
    if not manifest_path.exists():
        raise BackupError(f"Backup manifest not found: {manifest_path}")
    if mode in {RestoreMode.DB_ONLY, RestoreMode.FULL} and not db_dump_path.exists():
        raise BackupError(f"Backup SQL dump not found: {db_dump_path}")
    if mode in {RestoreMode.FILES_ONLY, RestoreMode.FULL} and (
        not data_copy_path.exists() or not data_copy_path.is_dir()
    ):
        raise BackupError(f"Backup data directory not found: {data_copy_path}")

    return BackupContents(
        backup_dir=backup_dir,
        manifest_path=manifest_path,
        db_dump_path=db_dump_path,
        data_copy_path=data_copy_path,
    )


def _validate_backup_target(source_data_dir: Path, target_root: Path) -> None:
    """Reject recursive or overlapping source/backup directory layouts."""
    if target_root == source_data_dir or _is_relative_to(target_root, source_data_dir):
        raise BackupError(
            "Backup target cannot be the data directory or a child of it. "
            "Choose a folder outside DATA_DIR."
        )
    if _is_relative_to(source_data_dir, target_root):
        raise BackupError(
            "Backup target cannot be a parent of the data directory. "
            "Choose a folder that does not contain DATA_DIR."
        )


def _validate_restore_target_data_dir(
    backup_dir: Path,
    source_data_dir: Path,
    target_data_dir: Path,
) -> None:
    """Reject restore targets that would overwrite the backup snapshot or recurse into it."""
    if target_data_dir == backup_dir or _is_relative_to(target_data_dir, backup_dir):
        raise BackupError(
            "Restore target data directory cannot be the backup directory or a child of it."
        )
    if target_data_dir == source_data_dir:
        raise BackupError(
            "Restore target data directory cannot be the backup snapshot data directory."
        )


def _is_relative_to(path: Path, other: Path) -> bool:
    """Backport Path.is_relative_to for straightforward path containment checks."""
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def _postgres_env(password: str | None) -> dict[str, str]:
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    return env


def _emit_progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _append_tool_check(checks: list[PreflightCheck], name: str, command: str) -> None:
    resolved = _resolve_command(command)
    if resolved is None:
        checks.append(
            PreflightCheck(
                name=name,
                status=CheckStatus.FAIL,
                details=f"Command not found: {command}",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                name=name,
                status=CheckStatus.PASS,
                details=f"Resolved command path: {resolved}",
            )
        )


def _append_directory_writability_check(
    checks: list[PreflightCheck],
    name: str,
    directory: Path,
) -> None:
    target = directory.resolve()
    probe_root = _nearest_existing_parent(target)
    if probe_root is None:
        checks.append(
            PreflightCheck(
                name=name,
                status=CheckStatus.FAIL,
                details=f"Directory does not exist and no existing parent was found: {target}",
            )
        )
        return

    if os.access(probe_root, os.W_OK):
        checks.append(
            PreflightCheck(
                name=name,
                status=CheckStatus.PASS,
                details=f"Writable location confirmed: {probe_root}",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                name=name,
                status=CheckStatus.FAIL,
                details=f"Location is not writable: {probe_root}",
            )
        )


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while True:
        if current.exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _psql_base_cmd(url, psql_path: str, database: str) -> list[str]:
    return [
        psql_path,
        "--no-password",
        "--host",
        url.host or "localhost",
        "--port",
        str(url.port or 5432),
        "--username",
        url.username or "postgres",
        "--dbname",
        database,
        "--set",
        "ON_ERROR_STOP=1",
    ]


def _resolve_command(command: str) -> str | None:
    candidate = Path(command)
    if candidate.exists():
        return str(candidate.resolve())
    return which(command)


def _connect_params(url, database: str | None = None) -> dict[str, object]:
    return {
        "dbname": database or url.database,
        "user": url.username or "postgres",
        "password": url.password,
        "host": url.host or "localhost",
        "port": url.port or 5432,
    }


def _inspect_postgres_role_metadata(url) -> dict[str, object]:
    import psycopg

    try:
        with psycopg.connect(**_connect_params(url)) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      current_user,
                      r.rolsuper,
                      r.rolcreatedb,
                      EXISTS (
                        SELECT 1
                        FROM pg_namespace n
                        WHERE n.nspname = 'public'
                          AND pg_catalog.pg_get_userbyid(n.nspowner) = current_user
                      ) AS owns_public
                    FROM pg_roles r
                    WHERE r.rolname = current_user
                    """
                )
                row = cur.fetchone()
    except Exception as exc:
        raise BackupError(f"Could not connect to target database {url.database}: {exc}") from exc

    if row is None:
        raise BackupError("Could not inspect PostgreSQL role metadata for the current user.")

    return {
        "current_user": row[0],
        "rolsuper": bool(row[1]),
        "rolcreatedb": bool(row[2]),
        "owns_public": bool(row[3]),
    }


def _check_maintenance_database_access(url) -> None:
    import psycopg

    try:
        with psycopg.connect(**_connect_params(url, database="postgres")):
            return
    except Exception as exc:
        raise BackupError(f"Could not connect to maintenance database 'postgres': {exc}") from exc


def probe_temp_database_capability(
    database_url: str,
    psql_path: str,
    snapshot_time: datetime,
) -> None:
    """Create and drop a lightweight temp database to verify restore prerequisites."""
    url = make_url(database_url)
    temp_db_name = _temporary_database_name(url.database or "restore", snapshot_time)
    maintenance_cmd = _psql_base_cmd(url, psql_path=psql_path, database="postgres")
    env = _postgres_env(url.password)

    try:
        _run_subprocess(
            maintenance_cmd + ["--command", f"CREATE DATABASE {_quote_identifier(temp_db_name)};"],
            env=env,
            missing_tool_message=f"psql not found at '{psql_path}'. Set BACKUP_PSQL_PATH if needed.",
        )
    except BackupError as exc:
        raise BackupError(f"Could not create a temporary database for restore validation: {exc}") from exc
    finally:
        _drop_database(url=url, database_name=temp_db_name, psql_path=psql_path)


def _drop_database(url, database_name: str, psql_path: str) -> None:
    env = _postgres_env(url.password)
    maintenance_cmd = _psql_base_cmd(url, psql_path=psql_path, database="postgres")
    _run_subprocess(
        maintenance_cmd + [
            "--command",
            "SELECT pg_terminate_backend(pid) "
            f"FROM pg_stat_activity WHERE datname = '{database_name}' AND pid <> pg_backend_pid(); "
            f"DROP DATABASE IF EXISTS {_quote_identifier(database_name)};",
        ],
        env=env,
        missing_tool_message=f"psql not found at '{psql_path}'. Set BACKUP_PSQL_PATH if needed.",
        allow_cleanup_failure=True,
    )


def _temporary_database_name(base_name: str, snapshot_time: datetime) -> str:
    suffix = snapshot_time.strftime("%Y%m%d%H%M%S")
    prefix = f"{base_name}_restore_tmp_"
    max_base_len = max(1, 63 - len(prefix) - len(suffix))
    safe_base = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in base_name)
    return f"{safe_base[:max_base_len]}_restore_tmp_{suffix}"


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _run_subprocess(
    cmd: list[str],
    env: dict[str, str],
    missing_tool_message: str,
    allow_cleanup_failure: bool = False,
) -> None:
    """Run a subprocess and raise a concise BackupError on failure."""
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    except FileNotFoundError as exc:
        if allow_cleanup_failure:
            return
        raise BackupError(missing_tool_message) from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        if allow_cleanup_failure:
            return
        raise BackupError(f"Command failed with exit code {completed.returncode}: {stderr}")
