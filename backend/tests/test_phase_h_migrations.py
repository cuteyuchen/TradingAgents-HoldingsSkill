"""Phase H migration lineage and deployed-upgrade regression coverage."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.system.release import code_head_revision


def _upgrade(backend_dir: Path, database_path: Path, revision: str) -> None:
    env = os.environ.copy()
    env["ADVISOR_DB_PATH"] = str(database_path)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_deployed_phase_h_0015_upgrades_to_current_head(tmp_path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "phase_h_deployed.db"

    _upgrade(backend_dir, database_path, "20260826_0014")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "daily_operational_checkpoints" not in tables
        assert "operating_notifications" not in tables

    _upgrade(backend_dir, database_path, "20260826_0015")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("20260826_0015",)

    _upgrade(backend_dir, database_path, "head")
    _upgrade(backend_dir, database_path, "head")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (code_head_revision(),)
        for table, columns in {
            "daily_review_runs": {"lease_expires_at", "attempt_count"},
            "daily_operational_checkpoints": {"lease_expires_at"},
            "operating_notifications": {"lease_expires_at"},
        }.items():
            actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            assert columns <= actual
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {
            "parameter_set_versions",
            "parameter_change_proposals",
            "parameter_governance_events",
        } <= tables
        assert {
            "security_lifecycle_events",
            "security_trading_status_daily",
            "security_classification_daily",
            "security_valuation_daily",
            "fundamental_reports",
            "etf_metadata_history",
            "price_basis_metadata",
            "historical_data_sync_runs",
        } <= tables
        assert {
            "live_decision_observations",
            "live_quote_observations",
            "shadow_accounts",
            "shadow_order_intents",
            "shadow_fills",
            "shadow_ledger_entries",
            "shadow_daily_snapshots",
            "live_decision_outcomes",
            "decision_actual_alignments",
        } <= tables
