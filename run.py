#!/usr/bin/env python3
"""Start the backend and frontend with active-environment and port discovery."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
LOCAL_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
APP_NAME = "My Doctor Viewer Full-Stack Services"
BACKEND_RELOAD = True


def in_active_environment() -> bool:
    return (
        sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or bool(os.environ.get("VIRTUAL_ENV"))
        or bool(os.environ.get("CONDA_PREFIX"))
    )


def select_runtime() -> Path:
    if in_active_environment():
        return Path(sys.executable)
    if not LOCAL_PYTHON.is_file():
        print("[RUN] No active environment or local .venv; running setup.py...")
        if subprocess.call([sys.executable, str(ROOT / "setup.py")], cwd=ROOT):
            raise RuntimeError("Setup failed")
    if not LOCAL_PYTHON.is_file():
        raise RuntimeError(f"Python environment was not found at {LOCAL_PYTHON}")
    return LOCAL_PYTHON


def load_env_file() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def requested_port(variable: str, default: int) -> int:
    value = os.environ.get(variable, str(default))
    try:
        port = int(value)
    except ValueError as error:
        raise RuntimeError(f"{variable} must be an integer, not {value!r}") from error
    if not 1 <= port <= 65535:
        raise RuntimeError(f"{variable} must be between 1 and 65535")
    return port


def available_port(start: int, reserved: set[int] | None = None) -> int:
    reserved = reserved or set()
    for port in range(start, 65536):
        if port in reserved:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"No available TCP port was found at or above {start}")


def merge_cors_origins(environment: dict[str, str], frontend_port: int) -> None:
    current = environment.get("CORS_ORIGINS", "")
    if current.strip() == "*":
        return
    origins = [item.strip() for item in current.split(",") if item.strip()]
    for origin in (
        f"http://localhost:{frontend_port}",
        f"http://127.0.0.1:{frontend_port}",
    ):
        if origin not in origins:
            origins.append(origin)
    environment["CORS_ORIGINS"] = ",".join(origins)


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    runtime = select_runtime()
    if Path(sys.executable).resolve() != runtime.resolve():
        return subprocess.call([str(runtime), str(Path(__file__).resolve()), *sys.argv[1:]])

    load_env_file()
    backend_port = available_port(requested_port("BACKEND_PORT", 8000))
    frontend_port = available_port(
        requested_port("FRONTEND_PORT", 5173), {backend_port}
    )
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://localhost:{frontend_port}"

    backend_env = os.environ.copy()
    merge_cors_origins(backend_env, frontend_port)
    backend_env["FRONTEND_URL"] = frontend_url
    backend_env["PUBLIC_BACKEND_URL"] = backend_url

    frontend_env = os.environ.copy()
    frontend_env["VITE_BACKEND_URL"] = backend_url

    print("=" * 60)
    print(f"Starting {APP_NAME}")
    print(f"Python:   {sys.executable}")
    print(f"Backend:  {backend_url} (API docs: {backend_url}/docs)")
    print(f"Frontend: {frontend_url}")
    print("=" * 60)

    backend_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(backend_port),
    ]
    if BACKEND_RELOAD:
        backend_command.append("--reload")

    npm = "npm.cmd" if os.name == "nt" else "npm"
    frontend_command = [
        npm,
        "run",
        "dev",
        "--",
        "--port",
        str(frontend_port),
        "--strictPort",
    ]

    backend_process: subprocess.Popen[bytes] | None = None
    frontend_process: subprocess.Popen[bytes] | None = None
    try:
        backend_process = subprocess.Popen(
            backend_command, cwd=BACKEND_DIR, env=backend_env
        )
        frontend_process = subprocess.Popen(
            frontend_command, cwd=FRONTEND_DIR, env=frontend_env
        )
        while True:
            for label, process in (
                ("Backend", backend_process),
                ("Frontend", frontend_process),
            ):
                status = process.poll()
                if status is not None:
                    print(f"[RUN] {label} exited with code {status}.")
                    return status
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[RUN] Stopping services...")
        return 0
    finally:
        stop(frontend_process)
        stop(backend_process)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"[RUN] Error: {error}", file=sys.stderr)
        raise SystemExit(1)

