"""Environment-driven application settings.

All values can be overridden via environment variables (see .env.example).
No secrets live here — only operational configuration.
"""

import os
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
ORIGINAL_PROJECT_DIR = ROOT_DIR / "original-project"

APP_NAME = os.environ.get("APP_NAME", "My Doctor Viewer")

# Backend bind settings
BACKEND_HOST = os.environ.get("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))

# Isolated system-temp root: every job gets <JOBS_ROOT>/<uuid>/
JOBS_ROOT = Path(
    os.environ.get(
        "JOBS_ROOT",
        str(Path(tempfile.gettempdir()) / "my_doctor_viewer_jobs"),
    )
)

# Upload guard
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# LLM pipeline: "auto" (default) runs MedGemma stages only when the Ollama
# server is reachable; "true"/"false" force the behaviour.
ENABLE_LLM = os.environ.get("ENABLE_LLM", "auto").strip().lower()
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "MedGemma1.5:latest")

# How long finished job temp folders are kept before the startup sweep purges
JOB_TTL_HOURS = float(os.environ.get("JOB_TTL_HOURS", "24"))

# CORS — the Vite dev server runs on localhost:5173 by default
CORS_ORIGINS = [
    o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()
]
