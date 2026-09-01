"""Run the Phase O.1 browser acceptance suite in a fresh local environment.

The runner owns every runtime path it creates.  It never points the application
at the developer's default database or Docker volume.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
ARTIFACTS = ROOT / "output" / "playwright" / "acceptance"


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(url: str, process: subprocess.Popen[str], *, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"service exited with code {process.returncode}: {url}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001 - startup polling
            last_error = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for {url}: {last_error}")


def _terminate(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        # npm.cmd leaves Vite's node child alive unless the whole process tree is killed.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _run_checked(command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path) -> None:
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for child in ARTIFACTS.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    backend_port = _port()
    frontend_port = _port()
    npm = "npm.cmd" if os.name == "nt" else "npm"
    node_binary = os.environ.get("PLAYWRIGHT_NODE_BIN", "").strip()
    if node_binary:
        vite_command = [node_binary, str(FRONTEND / "node_modules" / "vite" / "bin" / "vite.js")]
        playwright_command = [node_binary, str(FRONTEND / "node_modules" / "playwright" / "cli.js")]
    else:
        vite_command = [npm, "run", "dev", "--"]
        playwright_command = ["npx.cmd" if os.name == "nt" else "npx", "--no-install", "playwright"]

    with tempfile.TemporaryDirectory(prefix="phase-o1-acceptance-") as runtime:
        runtime_path = Path(runtime)
        db_path = runtime_path / "advisor.db"
        artifacts_path = runtime_path / "artifacts"
        backup_path = runtime_path / "backups"
        static_path = runtime_path / "static"
        facts_path = runtime_path / "facts.json"
        for path in (artifacts_path, backup_path, static_path):
            path.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update({
            "PYTHONPATH": str(BACKEND),
            "ADVISOR_DB_PATH": str(db_path),
            "ADVISOR_ARTIFACTS_DIR": str(artifacts_path),
            "ADVISOR_BACKUP_DIR": str(backup_path),
            "ADVISOR_STATIC_DIR": str(static_path),
            "ACCEPTANCE_MODE": "true",
            "ACCEPTANCE_TRADE_DATE": "2026-08-21",
            "ACCEPTANCE_NOW_UTC": "2026-08-21T06:00:00+00:00",
            "APP_ENV": "acceptance",
            "APP_SECRET_KEY": "phase-o1-acceptance-secret-key-32-bytes",
            "ADVISOR_TOKEN": "phase-o1-acceptance-legacy-token",
            "ALLOW_REGISTRATION": "true",
            "ADVISOR_SQLITE_JOURNAL_MODE": "DELETE",
            "SCHEDULER_ENABLED": "false",
            "REALTIME_MONITOR_ENABLED": "false",
            "CALENDAR_SYNC_ENABLED": "false",
            "SECURITY_MASTER_SYNC_ENABLED": "false",
            "BACKUP_SCHEDULE_ENABLED": "false",
            "HOLDINGS_SKILL_DIR": str(ROOT / "skill" / "tradingagents-holdings-advisor"),
            "VITE_BACKEND_URL": f"http://127.0.0.1:{backend_port}",
            "PLAYWRIGHT_BASE_URL": f"http://127.0.0.1:{frontend_port}",
            "PLAYWRIGHT_FACTS_FILE": str(facts_path),
            "PLAYWRIGHT_ARTIFACT_DIR": str(ARTIFACTS),
            "PLAYWRIGHT_OUTPUT_DIR": str(ARTIFACTS / "test-results"),
            "PLAYWRIGHT_REPORT_DIR": str(ARTIFACTS / "playwright-report"),
        })
        preload = ROOT / "scripts" / "node_webcrypto.cjs"
        existing_node_options = env.get("NODE_OPTIONS", "").strip()
        env["NODE_OPTIONS"] = f"{existing_node_options} --require={preload}".strip()

        logs = {
            "alembic": ARTIFACTS / "alembic.log",
            "seed": ARTIFACTS / "seed.log",
            "backend": ARTIFACTS / "backend.log",
            "frontend": ARTIFACTS / "frontend.log",
            "playwright": ARTIFACTS / "playwright.log",
        }
        backend_process: subprocess.Popen[str] | None = None
        frontend_process: subprocess.Popen[str] | None = None
        backend_log = None
        frontend_log = None
        exit_code = 1
        try:
            _run_checked([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, log_path=logs["alembic"])
            _run_checked([sys.executable, str(ROOT / "scripts" / "acceptance_seed.py"), "--output", str(facts_path)], cwd=ROOT, env=env, log_path=logs["seed"])

            backend_log = logs["backend"].open("w", encoding="utf-8")
            backend_process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(backend_port), "--log-level", "warning"],
                cwd=BACKEND, env=env, stdout=backend_log, stderr=subprocess.STDOUT, text=True,
            )
            _wait(f"http://127.0.0.1:{backend_port}/healthz/live", backend_process)

            frontend_log = logs["frontend"].open("w", encoding="utf-8")
            frontend_process = subprocess.Popen(
                [*vite_command, "--host", "127.0.0.1", "--port", str(frontend_port)],
                cwd=FRONTEND, env=env, stdout=frontend_log, stderr=subprocess.STDOUT, text=True,
            )
            _wait(f"http://127.0.0.1:{frontend_port}/", frontend_process)

            with logs["playwright"].open("w", encoding="utf-8") as log:
                result = subprocess.run(
                    [*playwright_command, "test", "--project=acceptance", "--config=playwright.config.ts"],
                    cwd=FRONTEND, env=env, stdout=log, stderr=subprocess.STDOUT, check=False,
                )
            exit_code = result.returncode
        except Exception as exc:  # noqa: BLE001 - preserve diagnostics for CI
            (ARTIFACTS / "runner-error.txt").write_text(str(exc), encoding="utf-8")
            exit_code = 1
        finally:
            _terminate(frontend_process)
            _terminate(backend_process)
            if frontend_log is not None:
                frontend_log.close()
            if backend_log is not None:
                backend_log.close()
            if facts_path.exists():
                shutil.copy2(facts_path, ARTIFACTS / "facts.json")

        if exit_code:
            print(f"Phase O.1 acceptance failed; artifacts: {ARTIFACTS}", file=sys.stderr)
        else:
            print(f"Phase O.1 acceptance passed; artifacts: {ARTIFACTS}")
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
