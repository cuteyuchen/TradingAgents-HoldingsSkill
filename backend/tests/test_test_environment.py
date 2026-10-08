"""Exercise collection-time isolation without importing any application module."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
CONFTEST_PATH = Path(__file__).with_name("conftest.py")
COLLECTION_ENVIRONMENT = {
    variable: os.environ[variable]
    for variable in ("ADVISOR_DB_PATH", "ADVISOR_ARTIFACTS_DIR", "ADVISOR_BACKUP_DIR", "SCHEDULER_ENABLED")
}


def _bootstrap(overrides=None, *, preimported=False):
    environment = os.environ.copy()
    for variable in COLLECTION_ENVIRONMENT:
        environment.pop(variable, None)
    environment.update(overrides or {})
    script = (
        "import json, os, runpy, sys, types\n"
        + ("sys.modules['app.config'] = types.ModuleType('app.config')\n" if preimported else "")
        + "runpy.run_path(sys.argv[1])\n"
        "print(json.dumps({key: os.environ[key] for key in "
        "('ADVISOR_DB_PATH', 'ADVISOR_ARTIFACTS_DIR', 'ADVISOR_BACKUP_DIR', 'SCHEDULER_ENABLED', 'ADVISOR_SQLITE_JOURNAL_MODE')}))\n"
        "assert not any(key == 'app' or key.startswith('app.') for key in sys.modules)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script, str(CONFTEST_PATH)],
        cwd=BACKEND_DIR, env=environment, text=True, capture_output=True, check=False,
    )


def test_storage_is_initialized_before_this_module_is_collected():
    assert Path(COLLECTION_ENVIRONMENT["ADVISOR_DB_PATH"]).is_absolute()
    assert Path(COLLECTION_ENVIRONMENT["ADVISOR_DB_PATH"]).resolve() != (BACKEND_DIR / "data" / "advisor.db").resolve()
    assert COLLECTION_ENVIRONMENT["SCHEDULER_ENABLED"] == "false"


def test_missing_environment_creates_retained_isolated_paths_without_app_imports():
    first = _bootstrap()
    second = _bootstrap()
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    first_paths = json.loads(first.stdout)
    second_paths = json.loads(second.stdout)
    database = Path(first_paths["ADVISOR_DB_PATH"])

    assert database.parent.name.startswith("advisor-pytest-")
    assert database.parent.is_dir()
    assert not database.exists()
    assert Path(first_paths["ADVISOR_ARTIFACTS_DIR"]).is_dir()
    assert Path(first_paths["ADVISOR_BACKUP_DIR"]).is_dir()
    assert first_paths["ADVISOR_DB_PATH"] != second_paths["ADVISOR_DB_PATH"]
    assert first_paths["SCHEDULER_ENABLED"] == "false"


def test_explicit_isolated_storage_is_preserved_and_scheduler_is_disabled(tmp_path):
    expected = {
        "ADVISOR_DB_PATH": str(tmp_path / "integration.db"),
        "ADVISOR_ARTIFACTS_DIR": str(tmp_path / "exports"),
        "ADVISOR_BACKUP_DIR": str(tmp_path / "backups"),
        "SCHEDULER_ENABLED": "true",
        "ADVISOR_SQLITE_JOURNAL_MODE": "WAL",
    }
    child = _bootstrap(expected)
    assert child.returncode == 0, child.stderr
    actual = json.loads(child.stdout)

    for variable in ("ADVISOR_DB_PATH", "ADVISOR_ARTIFACTS_DIR", "ADVISOR_BACKUP_DIR"):
        assert Path(actual[variable]) == Path(expected[variable]).resolve()
    assert actual["SCHEDULER_ENABLED"] == "false"
    assert actual["ADVISOR_SQLITE_JOURNAL_MODE"] == "WAL"
    assert not Path(actual["ADVISOR_DB_PATH"]).exists()


@pytest.mark.parametrize("variable,path", [
    ("ADVISOR_DB_PATH", "data/advisor.db"),
    ("ADVISOR_DB_PATH", str(BACKEND_DIR / "data" / "advisor.db")),
    ("ADVISOR_ARTIFACTS_DIR", str(BACKEND_DIR / "data" / "artifacts")),
    ("ADVISOR_ARTIFACTS_DIR", str(BACKEND_DIR / "data" / "artifacts" / "test")),
    ("ADVISOR_BACKUP_DIR", str(BACKEND_DIR / "data" / "backups")),
])
def test_default_user_storage_is_rejected_before_application_import(variable, path):
    child = _bootstrap({variable: path})

    assert child.returncode != 0
    assert "Refusing pytest storage in the application default" in child.stderr
    assert not child.stdout


def test_preimported_application_config_is_rejected():
    child = _bootstrap(preimported=True)

    assert child.returncode != 0
    assert "Application configuration was imported before test storage isolation" in child.stderr
