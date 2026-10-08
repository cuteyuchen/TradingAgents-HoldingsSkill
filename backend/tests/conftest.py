"""Configure isolated storage before pytest imports application test modules."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_STORAGE = {
    "ADVISOR_DB_PATH": BACKEND_DIR / "data" / "advisor.db",
    "ADVISOR_ARTIFACTS_DIR": BACKEND_DIR / "data" / "artifacts",
    "ADVISOR_BACKUP_DIR": BACKEND_DIR / "data" / "backups",
}


def _configure_test_environment() -> dict[str, Path]:
    if "app.config" in sys.modules or "app.database" in sys.modules:
        raise pytest.UsageError("Application configuration was imported before test storage isolation.")

    configured: dict[str, Path] = {}
    for variable, default_path in DEFAULT_STORAGE.items():
        raw = os.environ.get(variable, "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        default_path = default_path.resolve()
        if path == default_path or variable != "ADVISOR_DB_PATH" and default_path in path.parents:
            raise pytest.UsageError(f"Refusing pytest storage in the application default: {variable}={path}")
        configured[variable] = path

    temporary_root = Path(tempfile.mkdtemp(prefix="advisor-pytest-")) if len(configured) < len(DEFAULT_STORAGE) else None
    for variable, basename in (
        ("ADVISOR_DB_PATH", "advisor-tests.db"),
        ("ADVISOR_ARTIFACTS_DIR", "artifacts"),
        ("ADVISOR_BACKUP_DIR", "backups"),
    ):
        path = configured.get(variable)
        if path is None:
            path = temporary_root / basename
            configured[variable] = path
        directory = path.parent if variable == "ADVISOR_DB_PATH" else path
        directory.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(path)

    os.environ["SCHEDULER_ENABLED"] = "false"
    os.environ.setdefault("ADVISOR_SQLITE_JOURNAL_MODE", "MEMORY")
    os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
    os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
    return configured


TEST_STORAGE = _configure_test_environment()


def pytest_report_header():
    return f"Isolated test database: {TEST_STORAGE['ADVISOR_DB_PATH']}"


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_sep("-", "Test storage retained for diagnostics")
    for variable, path in TEST_STORAGE.items():
        terminalreporter.write_line(f"{variable}={path}")
