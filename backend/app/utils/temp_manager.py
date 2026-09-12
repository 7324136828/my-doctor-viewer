"""System temp-directory lifecycle for conversion jobs.

Every job executes inside an isolated folder under the OS temp dir:

    <JOBS_ROOT>/<job_uuid>/
    ├── inputs/    staged PDF uploads
    ├── work/      pipeline scratch space (input/, chunks/, output/, json/, ...)
    ├── outputs/   user-facing converted artefacts
    └── archive/   conversion_output.zip
"""

import shutil
import time
from pathlib import Path

from .. import config

SUBDIRS = ("inputs", "work", "outputs", "archive")


def create_job_dir(job_id: str) -> Path:
    job_dir = config.JOBS_ROOT / job_id
    for sub in SUBDIRS:
        (job_dir / sub).mkdir(parents=True, exist_ok=True)
    return job_dir


def purge_job_dir(job_dir: str | Path) -> None:
    path = Path(job_dir)
    root = Path(config.JOBS_ROOT).resolve()
    # Safety: only ever delete inside the managed jobs root.
    if root not in path.resolve().parents:
        raise ValueError(f"Refusing to purge path outside jobs root: {path}")
    shutil.rmtree(path, ignore_errors=True)


def sweep_expired_jobs(known_dirs: list[str]) -> int:
    """Delete job folders older than JOB_TTL_HOURS. Returns count removed."""
    root = Path(config.JOBS_ROOT)
    if not root.is_dir():
        return 0
    cutoff = time.time() - config.JOB_TTL_HOURS * 3600
    removed = 0
    for child in root.iterdir():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
