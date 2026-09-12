"""Conversion pipeline adapter.

Stages uploaded PDFs inside an isolated system-temp job folder and pipes them
through the preserved ``original-project`` scripts:

    extract     PDF -> text            (pypdf, in-process)
    preprocess  text -> chunks         (process_medgemma.py preprocess)
    summarize   chunks -> LLM output   (process_medgemma.py process, Ollama)
    extractjson output -> json/        (extract_json.py)
    categorize  json/ -> metadata.json (categorize_json.py)
    trends      json/ -> csv/pdf/png   (plot_test_trends.py)
    package     outputs -> zip         (zipfile, in-process)

The original scripts resolve their data paths relative to cwd or
``os.path.dirname(__file__)``, so each script is copied into the job's ``work/``
directory and executed as a subprocess with ``cwd=work`` — the original source
tree is never touched.

When the Ollama/MedGemma backend is unavailable (``ENABLE_LLM``), the pipeline
still produces the deterministic artefacts (extracted text, chunk manifest,
document index) and marks the job completed with a notice.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from .. import config, db
from ..utils import temp_manager

# Original-project scripts invoked per job (copied into the job work dir).
SCRIPT_PREPROCESS = "process_medgemma.py"
SCRIPT_EXTRACT_JSON = "extract_json.py"
SCRIPT_CATEGORIZE = "categorize_json.py"
SCRIPT_TRENDS = "plot_test_trends.py"

# job_id -> currently running Popen (for discard/terminate)
_ACTIVE_PROCS: dict[str, subprocess.Popen] = {}
_CANCEL_FLAGS: dict[str, threading.Event] = {}
_REGISTRY_LOCK = threading.Lock()


class JobCancelled(Exception):
    pass


def _register(job_id: str) -> threading.Event:
    flag = threading.Event()
    with _REGISTRY_LOCK:
        _CANCEL_FLAGS[job_id] = flag
    return flag


def _unregister(job_id: str) -> None:
    with _REGISTRY_LOCK:
        _CANCEL_FLAGS.pop(job_id, None)
        _ACTIVE_PROCS.pop(job_id, None)


def request_cancel(job_id: str) -> None:
    """Signal a running job to abort and terminate its active subprocess."""
    with _REGISTRY_LOCK:
        flag = _CANCEL_FLAGS.get(job_id)
        proc = _ACTIVE_PROCS.get(job_id)
    if flag:
        flag.set()
    if proc and proc.poll() is None:
        proc.terminate()


class JobRunner:
    """Runs one job's stages, streaming logs and progress into the job store."""

    def __init__(self, job_id: str, job_dir: Path):
        self.job_id = job_id
        self.job_dir = job_dir
        self.inputs = job_dir / "inputs"
        self.work = job_dir / "work"
        self.outputs = job_dir / "outputs"
        self.archive = job_dir / "archive"
        self.log_path = self.work / "logs.txt"
        self.cancel_flag = _register(job_id)

    # -- logging / progress -------------------------------------------------
    def log(self, msg: str) -> None:
        line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def progress(self, pct: int, stage: str) -> None:
        db.update_job(self.job_id, progress=pct)
        self.log(f"--- stage: {stage} ({pct}%) ---")

    def check_cancelled(self) -> None:
        if self.cancel_flag.is_set():
            raise JobCancelled()

    def run_script(self, script_name: str, *args: str) -> int:
        """Copy an original-project script into work/ and run it there."""
        src = config.ORIGINAL_PROJECT_DIR / script_name
        if not src.is_file():
            raise FileNotFoundError(f"original-project script missing: {src}")
        shutil.copy2(src, self.work / script_name)

        cmd = [sys.executable, script_name, *args]
        self.log(f"$ {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            cwd=self.work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        with _REGISTRY_LOCK:
            _ACTIVE_PROCS[self.job_id] = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.log(line.rstrip())
                if self.cancel_flag.is_set():
                    proc.terminate()
                    raise JobCancelled()
            return proc.wait()
        finally:
            with _REGISTRY_LOCK:
                _ACTIVE_PROCS.pop(self.job_id, None)


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    """Strip directories and unsafe characters from an uploaded filename."""
    name = name.replace("\\", "/").split("/")[-1] or "document.pdf"
    return "".join(c for c in name if c.isalnum() or c in " ._-()[]").strip() or "document.pdf"


def stage_extract(runner: JobRunner) -> list[dict]:
    """PDF/TXT -> normalized text inputs and an auditable document index."""
    input_dir = runner.work / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted(
        path for path in runner.inputs.iterdir()
        if path.is_file() and path.suffix.lower() in {".pdf", ".txt"}
    )
    if not sources:
        raise RuntimeError("No PDF or text files staged in inputs/")

    index = []
    combined = []
    for source in sources:
        runner.log(f"Reading {source.name} ...")
        if source.suffix.lower() == ".pdf":
            reader = PdfReader(str(source))
            pages = []
            for i, page in enumerate(reader.pages, 1):
                try:
                    pages.append(page.extract_text() or "")
                except Exception as exc:  # keep going on a bad page
                    runner.log(f"  page {i}: extraction error: {exc}")
                    pages.append("")
            text = "\n\n".join(pages).strip()
            page_count = len(reader.pages)
        else:
            text = source.read_text(encoding="utf-8-sig", errors="replace").strip()
            page_count = None
        txt_path = input_dir / f"{source.stem}.txt"
        txt_path.write_text(text, encoding="utf-8")
        combined.append(f"===== {source.name} =====\n{text}")
        index.append({
            "file": source.name,
            "source_url": f"/api/jobs/{runner.job_id}/source/{source.name}",
            "text_file": txt_path.name,
            "pages": page_count,
            "chars": len(text),
        })
        detail = f"{page_count} page(s), " if page_count is not None else ""
        runner.log(f"  {source.name}: {detail}{len(text)} chars")

    (runner.outputs / "extracted_text.txt").write_text(
        "\n\n".join(combined), encoding="utf-8"
    )
    (runner.work / "pdf_index.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8"
    )
    return index


def stage_fallback_outputs(runner: JobRunner, index: list[dict], reason: str) -> None:
    """Deterministic outputs when the LLM stages are skipped."""
    manifest_path = runner.work / "manifest.json"
    chunks = []
    if manifest_path.is_file():
        chunks = json.loads(manifest_path.read_text(encoding="utf-8"))

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "llm_enabled": False,
        "skip_reason": reason,
        "documents": index,
        "chunks": [
            {
                "source_file": c.get("source_file"),
                "chunk_file": c.get("chunk_file"),
                "chunk_index": c.get("chunk_index"),
                "total_chunks": c.get("total_chunks"),
            }
            for c in chunks
        ],
        "files": {},
    }
    (runner.outputs / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    (runner.outputs / "NOTICE_llm_disabled.txt").write_text(
        "Structured clinical summarization (MedGemma via Ollama) was not run:\n"
        f"  {reason}\n\n"
        "Set ENABLE_LLM=true with a running Ollama server that has the "
        f"{config.OLLAMA_MODEL} model to enable the full pipeline.\n",
        encoding="utf-8",
    )
    runner.log(f"LLM stages skipped: {reason}")


def stage_llm_pipeline(runner: JobRunner) -> None:
    """Run the original-project LLM chain inside work/."""
    rc = runner.run_script(SCRIPT_PREPROCESS, "preprocess")
    if rc != 0:
        raise RuntimeError(f"preprocess exited with code {rc}")

    runner.progress(45, "summarize")
    rc = runner.run_script(SCRIPT_PREPROCESS, "process")
    if rc != 0:
        raise RuntimeError(f"MedGemma process exited with code {rc}")

    runner.progress(65, "extract_json")
    rc = runner.run_script(SCRIPT_EXTRACT_JSON)
    if rc != 0:
        raise RuntimeError(f"extract_json exited with code {rc}")

    runner.progress(78, "categorize")
    rc = runner.run_script(SCRIPT_CATEGORIZE)
    if rc != 0:
        raise RuntimeError(f"categorize_json exited with code {rc}")

    runner.progress(88, "trends")
    rc = runner.run_script(SCRIPT_TRENDS, "--all")
    if rc != 0:
        runner.log(f"plot_test_trends exited with code {rc}; continuing without report")


def stage_collect(runner: JobRunner) -> None:
    """Copy user-facing artefacts from work/ into outputs/."""
    work = runner.work
    json_dir = work / "json"
    if json_dir.is_dir():
        dest = runner.outputs / "json"
        dest.mkdir(exist_ok=True)
        for f in sorted(json_dir.glob("*.json")):
            shutil.copy2(f, dest / f.name)
    for name in ("metadata.json", "manifest.json",
                 "test_trends.csv", "test_trends.pdf", "test_trends.png"):
        src = work / name
        if src.is_file():
            shutil.copy2(src, runner.outputs / name)
    if runner.log_path.is_file():
        shutil.copy2(runner.log_path, runner.outputs / "logs.txt")


def stage_review_manifest(runner: JobRunner) -> dict:
    """Validate extracted summaries and label all model output as unverified."""
    manifest_path = runner.work / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else []
    source_by_stem = {
        Path(item.get("chunk_file", "")).stem: item.get("source_file")
        for item in manifest
        if item.get("chunk_file")
    }
    json_dir = runner.outputs / "json"
    validation = []
    for stem, source in source_by_stem.items():
        path = json_dir / f"{stem}.json"
        entry = {
            "chunk": stem,
            "source_file": source,
            "source_url": f"/api/jobs/{runner.job_id}/source/{source}",
            "json_file": f"json/{stem}.json",
            "valid": False,
            "review_status": "unverified",
        }
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload["_review"] = {
                        "status": "unverified",
                        "source_file": source,
                        "source_url": entry["source_url"],
                        "notice": "AI-generated summary; compare every finding with the source. Not medical advice.",
                    }
                    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                entry["valid"] = True
            except json.JSONDecodeError as exc:
                entry["error"] = f"Invalid JSON: {exc}"
        else:
            entry["error"] = "No valid JSON was extracted for this chunk"
        validation.append(entry)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "safety_notice": "All AI-generated summaries are unverified and require source review.",
        "chunk_count": len(validation),
        "valid_count": sum(item["valid"] for item in validation),
        "invalid_or_missing_count": sum(not item["valid"] for item in validation),
        "summaries": validation,
    }
    (runner.outputs / "review_manifest.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report


def stage_package(runner: JobRunner) -> Path:
    """Zip everything in outputs/ into archive/conversion_output.zip."""
    zip_path = runner.archive / "conversion_output.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(runner.outputs.rglob("*")):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(runner.outputs))
    runner.log(f"Packaged {zip_path.name} ({zip_path.stat().st_size} bytes)")
    return zip_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _ollama_status() -> tuple[bool, str]:
    """Return (available, reason) for the MedGemma LLM backend."""
    if config.ENABLE_LLM in ("false", "0", "no", "off"):
        return False, "ENABLE_LLM is disabled"
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=2):
            pass
    except OSError:
        reason = "Ollama server not reachable on 127.0.0.1:11434"
        if config.ENABLE_LLM == "true":
            reason += " (ENABLE_LLM=true but server is down)"
        return False, reason
    try:
        import ollama

        models = [m.model for m in ollama.list().models]
        if config.OLLAMA_MODEL not in models and not any(
            m.split(":")[0] == config.OLLAMA_MODEL.split(":")[0] for m in models
        ):
            return False, f"Ollama reachable but model '{config.OLLAMA_MODEL}' not pulled"
    except Exception as exc:
        return False, f"Ollama check failed: {exc}"
    return True, ""


def run_job(job_id: str) -> None:
    """Background-task entry point: run all stages for a job."""
    job = db.get_job(job_id)
    if not job:
        return
    job_dir = Path(job["temp_dir"])
    runner = JobRunner(job_id, job_dir)
    try:
        db.set_status(job_id, "in_progress")
        runner.progress(5, "extract")
        index = stage_extract(runner)
        runner.check_cancelled()

        runner.progress(30, "preprocess")
        llm_ok, reason = _ollama_status()
        if llm_ok:
            stage_llm_pipeline(runner)
        else:
            # Still chunk the text so the manifest/index are meaningful.
            rc = runner.run_script(SCRIPT_PREPROCESS, "preprocess")
            if rc != 0:
                runner.log(f"preprocess exited {rc}; continuing")
            stage_fallback_outputs(runner, index, reason)

        runner.check_cancelled()
        runner.progress(95, "package")
        stage_collect(runner)
        review = stage_review_manifest(runner)
        zip_path = stage_package(runner)
        artifacts = [
            {
                "name": str(path.relative_to(runner.outputs)).replace("\\", "/"),
                "size": path.stat().st_size,
            }
            for path in sorted(runner.outputs.rglob("*")) if path.is_file()
        ]
        result = {
            "summary": {
                "document_count": len(index),
                "chunk_count": review["chunk_count"],
                "valid_summary_count": review["valid_count"],
                "needs_review_count": review["invalid_or_missing_count"],
                "llm_enabled": llm_ok,
            },
            "artifacts": artifacts,
            "safety_notice": review["safety_notice"],
        }
        db.set_status(
            job_id,
            "completed",
            progress=100,
            zip_path=str(zip_path),
            result_json=json.dumps(result),
        )
        runner.log("Job completed.")
    except JobCancelled:
        try:
            runner.log("Job cancelled by user.")
        except OSError:
            pass  # the discard endpoint may already have purged the folder
        if (db.get_job(job_id) or {}).get("status") != "discarded":
            db.set_status(job_id, "discarded", error_message="Cancelled by user")
    except Exception as exc:
        try:
            runner.log(f"Job failed: {exc}")
        except OSError:
            pass
        if (db.get_job(job_id) or {}).get("status") != "discarded":
            db.set_status(job_id, "failed", error_message=str(exc))
    finally:
        _unregister(job_id)


def read_logs(job: dict) -> str:
    log_path = Path(job["temp_dir"]) / "work" / "logs.txt"
    if log_path.is_file():
        return log_path.read_text(encoding="utf-8", errors="replace")
    return ""
