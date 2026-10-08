"""Phase K sanitized diagnostic bundle contracts."""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path

from app.config import settings
from app.system import diagnostics as diagnostics_module
from app.system.logging import configure_logging, tail_logs


def test_diagnostic_bundle_redacts_secrets(tmp_path: Path, monkeypatch):
    secret = "diagnostics-supersecret"
    monkeypatch.setattr(settings, "ADVISOR_TOKEN", secret)
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    configure_logging()
    logger = logging.getLogger("test_diagnostics")
    logger.error("injected token value: %s", secret)
    monkeypatch.setattr(
        diagnostics_module,
        "build_release_metadata",
        lambda db=None: {"app_version": "test", "git_sha": "UNKNOWN"},
    )
    monkeypatch.setattr(
        diagnostics_module,
        "operational_health",
        lambda db=None: {"status": "OK", "components": {}},
    )
    monkeypatch.setattr(
        diagnostics_module,
        "readiness",
        lambda db=None, detailed=False: {"status": "READY", "ready": True, "checks": {}},
    )
    monkeypatch.setattr(
        diagnostics_module,
        "_jobs_payload",
        lambda db: {"startup_recovery": {"counts": {}, "errors": []}, "recent": {}},
    )
    result = diagnostics_module.build_diagnostic_bundle(db=None)
    path = tmp_path / "backups" / "diagnostics" / result["filename"]
    assert path.is_file()
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert {"manifest.json", "release.json", "health.json", "readiness.json", "jobs.json", "logs.txt"} <= names
        logs_content = archive.read("logs.txt").decode("utf-8")
        combined = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    assert secret not in combined
    assert "logs.txt" in names
    assert logs_content
    assert result["contains_db"] is False
    assert result["contains_backup"] is False
    assert tail_logs(10)
