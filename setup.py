#!/usr/bin/env python3
"""Cross-platform automated setup orchestrator.

Validates prerequisites, creates the .venv virtual environment, installs
backend Python dependencies and frontend npm dependencies, and initialises
.env from .env.example. Invoked by setup.bat / setup.sh.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
VENV_DIR = ROOT_DIR / ".venv"


def log(msg: str) -> None:
    print(f"\n[SETUP] {msg}", flush=True)


def check_prerequisites() -> None:
    log("Checking prerequisites...")
    if sys.version_info < (3, 10):
        sys.exit("Error: Python 3.10 or higher is required.")
    if not shutil.which("npm"):
        sys.exit("Error: Node.js and npm are required. Please install Node.js.")
    print(f"  Python {sys.version.split()[0]}  |  npm found")


def create_virtualenv() -> Path:
    log(f"Configuring Python virtual environment at {VENV_DIR}...")
    if not VENV_DIR.exists():
        import venv

        venv.create(VENV_DIR, with_pip=True)

    if os.name == "nt":
        py_bin = VENV_DIR / "Scripts" / "python.exe"
    else:
        py_bin = VENV_DIR / "bin" / "python"
    return py_bin


def install_backend(py_bin: Path) -> None:
    log("Installing backend dependencies...")
    pip_cmd = [str(py_bin), "-m", "pip"]
    subprocess.check_call([*pip_cmd, "install", "--upgrade", "pip"])
    req_file = BACKEND_DIR / "requirements.txt"
    if req_file.exists():
        subprocess.check_call([*pip_cmd, "install", "-r", str(req_file)])


def install_frontend() -> None:
    log("Installing frontend dependencies...")
    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    subprocess.check_call([npm_cmd, "install"], cwd=str(FRONTEND_DIR))


def setup_env() -> None:
    log("Setting up environment configuration...")
    env_example = ROOT_DIR / ".env.example"
    env_target = ROOT_DIR / ".env"
    if env_example.exists() and not env_target.exists():
        shutil.copy(env_example, env_target)
        print("Created .env from .env.example")


def main() -> None:
    check_prerequisites()
    py_bin = create_virtualenv()
    install_backend(py_bin)
    install_frontend()
    setup_env()
    log("Setup completed successfully!")
    print("Start the app with run.bat (Windows) or ./run.sh (Unix/macOS).")


if __name__ == "__main__":
    main()
