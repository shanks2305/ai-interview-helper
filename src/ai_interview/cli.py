from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from ai_interview.paths import desktop_dir, project_root


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ai-interview",
        description="Start the interview copilot API and desktop mic app.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--api-only",
        action="store_true",
        help="Run the FastAPI server only (no Electron window).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload the API on Python file changes (API-only or nested API process).",
    )
    args = parser.parse_args(argv)

    os.environ.setdefault("AI_INTERVIEW_ROOT", str(project_root()))

    if args.api_only:
        _run_api(host=args.host, port=args.port, reload=args.reload)
        return

    _run_app(host=args.host, port=args.port, reload=args.reload)


def _run_api(*, host: str, port: int, reload: bool) -> None:
    import uvicorn

    uvicorn.run(
        "ai_interview.main:app",
        host=host,
        port=port,
        reload=reload,
        factory=False,
    )


def _run_app(*, host: str, port: int, reload: bool) -> None:
    health = f"http://{host}:{port}/health"
    api: subprocess.Popen[bytes] | None = None
    started_api = False

    if not _healthy(health):
        api_cmd = [
            sys.executable,
            "-m",
            "ai_interview",
            "--api-only",
            "--host",
            host,
            "--port",
            str(port),
        ]
        if reload:
            api_cmd.append("--reload")
        api = subprocess.Popen(
            api_cmd,
            cwd=project_root(),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        started_api = True
        _wait_for_health(health, process=api)

    electron = _ensure_electron()
    electron_proc = subprocess.Popen(
        [str(electron), str(desktop_dir())],
        cwd=desktop_dir(),
        env={**os.environ, "AI_INTERVIEW_API": f"http://{host}:{port}"},
    )
    try:
        electron_proc.wait()
    except KeyboardInterrupt:
        electron_proc.terminate()
        try:
            electron_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            electron_proc.kill()
    finally:
        if started_api and api is not None and api.poll() is None:
            api.terminate()
            try:
                api.wait(timeout=8)
            except subprocess.TimeoutExpired:
                api.kill()


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _wait_for_health(url: str, process: subprocess.Popen[bytes], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"API process exited with code {process.returncode} before becoming healthy.")
        if _healthy(url):
            return
        time.sleep(0.2)
    raise SystemExit(f"API did not become healthy at {url}")


def _ensure_electron() -> Path:
    desktop = desktop_dir()
    if not (desktop / "package.json").is_file():
        raise SystemExit(f"Desktop app not found at {desktop}. Use --api-only to run the server.")

    binary = desktop / "node_modules" / ".bin" / ("electron.cmd" if os.name == "nt" else "electron")
    if binary.is_file():
        return binary

    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm is required once to install Electron. Install Node.js, then retry.")

    print("Installing desktop dependencies (first run)…")
    subprocess.run([npm, "install"], cwd=desktop, check=True)
    if not binary.is_file():
        raise SystemExit(f"Electron did not install at {binary}")
    return binary
