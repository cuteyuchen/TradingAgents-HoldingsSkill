"""Phase K verified backup, checksum, restore drill, and guard contracts."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.config import settings
from app.system import backup as backup_module
from app.system.backup import (
    BackupError,
    RestoreError,
    create_backup,
    ensure_pre_upgrade_backup,
    offline_restore,
    requires_pre_upgrade_backup,
    restore_drill,
    validate_backup_for_restore,
    verify_backup,
)
from app.system.startup import run_pre_upgrade_guard


def _seed_db(path: Path, *, revision: str = "20260827_0017") -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        conn.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (revision,))
        for table in (
            "users",
            "portfolios",
            "portfolio_snapshots",
            "analysis_jobs",
            "candidate_runs",
            "market_score_snapshots",
            "parameter_set_versions",
            "decision_memories",
            "daily_operational_runs",
        ):
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, code TEXT, side TEXT)")
        conn.execute("INSERT INTO trades (code, side) VALUES ('600000', 'BUY')")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def backup_env(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "advisor.db"
    backup_dir = tmp_path / "backups"
    _seed_db(db_path)
    monkeypatch.setattr(settings, "DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(
        backup_module,
        "_active_parameter_values",
        lambda: {"active_parameter_set_version": "v1", "active_parameter_set_hash": "hash"},
    )
    return tmp_path


def test_wal_backup_is_consistent_and_verified(backup_env):
    manifest = create_backup(reason="MANUAL")
    assert manifest["quick_check_result"] == "ok"
    assert manifest["sha256"]
    backup_file = backup_env / "backups" / manifest["filename"]
    assert backup_file.is_file()
    verified = verify_backup(manifest["backup_id"])
    assert verified["verified"] is True
    conn = sqlite3.connect(str(backup_file))
    try:
        assert conn.execute("SELECT side FROM trades WHERE code='600000'").fetchone()[0] == "BUY"
    finally:
        conn.close()


def test_partial_backup_never_becomes_success(backup_env, monkeypatch):
    real_quick_check = backup_module._quick_check

    def fail_partial(path):
        if ".partial" in Path(path).name:
            return {"ok": False, "result": "injected_failure"}
        return real_quick_check(path)

    monkeypatch.setattr(backup_module, "_quick_check", fail_partial)
    with pytest.raises(BackupError):
        create_backup(reason="MANUAL")
    assert not list((backup_env / "backups").glob("*.sqlite"))
    assert not list((backup_env / "backups").glob("*.partial"))


def test_checksum_tamper_fails_verification(backup_env):
    manifest = create_backup(reason="MANUAL")
    backup_file = backup_env / "backups" / manifest["filename"]
    with backup_file.open("ab") as handle:
        handle.write(b"x")
    verified = verify_backup(manifest["backup_id"])
    assert verified["verified"] is False
    assert verified["checksum_matches"] is False


def test_restore_drill_does_not_touch_production_db(backup_env):
    manifest = create_backup(reason="MANUAL")
    before = (backup_env / "advisor.db").read_bytes()
    result = restore_drill(manifest["backup_id"])
    assert result["status"] == "PASS"
    assert result["production_db_untouched"] is True
    assert (backup_env / "advisor.db").read_bytes() == before
    assert not list((backup_env / "backups" / "drills").glob("*.sqlite"))


def test_restore_rejects_backup_ahead_of_code_head(backup_env):
    manifest = create_backup(reason="MANUAL")
    backup_file = backup_env / "backups" / manifest["filename"]
    conn = sqlite3.connect(str(backup_file))
    try:
        conn.execute("UPDATE alembic_version SET version_num='20990101_9999'")
        conn.commit()
    finally:
        conn.close()
    checksum = hashlib.sha256(backup_file.read_bytes()).hexdigest()
    manifest["sha256"] = checksum
    manifest["source_db_revision"] = "20990101_9999"
    (backup_env / "backups" / f"{manifest['backup_id']}.json").write_text(
        __import__("json").dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(RestoreError, match="BACKUP_REVISION_AHEAD"):
        validate_backup_for_restore(manifest["backup_id"])


def test_pre_upgrade_guard_fails_closed(backup_env, monkeypatch):
    monkeypatch.setattr(
        "app.system.startup.ensure_pre_upgrade_backup",
        lambda db=None: (_ for _ in ()).throw(BackupError("injected_backup_failure")),
    )
    with pytest.raises(RuntimeError, match="PRE_UPGRADE_BACKUP_FAILED"):
        run_pre_upgrade_guard()


def test_pre_upgrade_backup_requirement(backup_env):
    assert requires_pre_upgrade_backup() is True
    conn = sqlite3.connect(str(backup_env / "advisor.db"))
    try:
        conn.execute("UPDATE alembic_version SET version_num='20260828_0018'")
        conn.commit()
    finally:
        conn.close()
    assert requires_pre_upgrade_backup() is False


def test_offline_restore_requires_confirmation(backup_env):
    manifest = create_backup(reason="MANUAL")
    with pytest.raises(RestoreError, match="restore_requires_confirmation"):
        offline_restore(
            backup_id=manifest["backup_id"],
            target=str(backup_env / "restored.db"),
            confirmed=False,
        )


def test_backup_lock_rejects_concurrent_backup(backup_env, monkeypatch):
    backup_module._BACKUP_LOCK.acquire()
    try:
        with pytest.raises(BackupError, match="BACKUP_ALREADY_RUNNING"):
            create_backup(reason="MANUAL")
    finally:
        backup_module._BACKUP_LOCK.release()
