"""FastAPI gateway for the local medical-data review workflows."""

import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import config, db
from .schemas.job import (
    DiscardResponse,
    JobCreateResponse,
    JobLogsResponse,
    JobStatusResponse,
    ResearchRequest,
    ResumeResponse,
)
from .services import pipeline, workflows
from .utils import temp_manager

@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    db.init_db(config.JOBS_ROOT / "jobs.db")
    removed = temp_manager.sweep_expired_jobs([])
    if removed:
        print(f"[startup] Purged {removed} expired job folder(s) from {config.JOBS_ROOT}")
    yield


app = FastAPI(title=f"{config.APP_NAME} API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    llm_ok, reason = pipeline._ollama_status()
    return {"status": "ok", "llm_enabled": llm_ok, "llm_detail": reason or None}


async def _create_upload_job(
    background_tasks: BackgroundTasks,
    files: list[UploadFile],
    job_type: str,
    allowed_extensions: set[str] | None,
    options: dict | None = None,
) -> JobCreateResponse:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    job_id = str(uuid.uuid4())
    job_dir = temp_manager.create_job_dir(job_id)
    inputs_dir = job_dir / "inputs"

    total_size = 0
    filenames: list[str] = []
    try:
        for f in files:
            safe = pipeline._safe_name(f.filename or f"uploaded_{job_type}")
            extension = Path(safe).suffix.lower()
            if allowed_extensions is not None and extension not in allowed_extensions:
                expected = ", ".join(sorted(allowed_extensions))
                raise HTTPException(status_code=400, detail=f"Unsupported file {safe}; expected {expected}")
            dest = inputs_dir / safe
            counter = 2
            while dest.exists():
                dest = inputs_dir / f"{Path(safe).stem}_{counter}{Path(safe).suffix}"
                counter += 1
            safe = dest.name
            with open(dest, "wb") as out:
                shutil.copyfileobj(f.file, out)
            size = dest.stat().st_size
            if size > config.MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"{safe} exceeds {config.MAX_UPLOAD_MB} MB limit",
                )
            total_size += size
            filenames.append(safe)
    except Exception:
        temp_manager.purge_job_dir(job_dir)
        raise

    db.create_job(
        job_id=job_id,
        filenames=filenames,
        file_size=total_size,
        temp_dir=str(job_dir),
        job_type=job_type,
        options=options,
    )
    background_tasks.add_task(workflows.run_job, job_id)
    return JobCreateResponse(job_id=job_id, status="queued")


@app.post("/api/convert", response_model=JobCreateResponse)
async def start_conversion(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
) -> JobCreateResponse:
    return await _create_upload_job(background_tasks, files, "records", {".pdf"})


@app.post("/api/workflows/records", response_model=JobCreateResponse)
async def start_records(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
) -> JobCreateResponse:
    return await _create_upload_job(background_tasks, files, "records", {".pdf", ".txt"})


@app.post("/api/workflows/imaging", response_model=JobCreateResponse)
async def start_imaging(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
) -> JobCreateResponse:
    # DICOM files frequently have .dcm, .dicom, .ima, or no extension.
    return await _create_upload_job(background_tasks, files, "imaging", None)


@app.post("/api/workflows/genetics", response_model=JobCreateResponse)
async def start_genetics(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    gene: str = Form(...),
    pass_only: bool = Form(False),
    include_no_clinvar: bool = Form(True),
) -> JobCreateResponse:
    return await _create_upload_job(
        background_tasks,
        files,
        "genetics",
        {".vcf"},
        {"gene": gene, "pass_only": pass_only, "include_no_clinvar": include_no_clinvar},
    )


@app.post("/api/workflows/conditions", response_model=JobCreateResponse)
async def start_conditions(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
) -> JobCreateResponse:
    return await _create_upload_job(background_tasks, files, "conditions", {".json"})


@app.post("/api/workflows/research", response_model=JobCreateResponse)
def start_research(
    request: ResearchRequest,
    background_tasks: BackgroundTasks,
) -> JobCreateResponse:
    if request.source_type not in {"rss", "trials"}:
        raise HTTPException(status_code=400, detail="source_type must be rss or trials")
    urls = [url.strip() for url in request.urls if url.strip()]
    if not urls or any(not url.startswith(("http://", "https://")) for url in urls):
        raise HTTPException(status_code=400, detail="Provide one or more HTTP(S) URLs")
    job_id = str(uuid.uuid4())
    job_dir = temp_manager.create_job_dir(job_id)
    db.create_job(
        job_id=job_id,
        filenames=[f"{request.source_type} sources ({len(urls)})"],
        file_size=sum(len(url.encode("utf-8")) for url in urls),
        temp_dir=str(job_dir),
        job_type="research",
        options={"source_type": request.source_type, "urls": urls},
    )
    background_tasks.add_task(workflows.run_job, job_id)
    return JobCreateResponse(job_id=job_id, status="queued")


@app.get("/api/jobs", response_model=list[JobStatusResponse])
def list_jobs() -> list[dict]:
    return db.list_jobs()


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> dict:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/logs", response_model=JobLogsResponse)
def get_job_logs(job_id: str) -> JobLogsResponse:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobLogsResponse(job_id=job_id, logs=pipeline.read_logs(job))


@app.post("/api/jobs/{job_id}/discard", response_model=DiscardResponse)
def discard_job(job_id: str) -> DiscardResponse:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "discarded":
        raise HTTPException(status_code=400, detail="Job is already discarded")

    pipeline.request_cancel(job_id)

    job_dir = Path(job["temp_dir"])
    if job_dir.is_dir():
        temp_manager.purge_job_dir(job_dir)

    db.set_status(job_id, "discarded", zip_path=None, result_json=None)
    return DiscardResponse(
        job_id=job_id, status="discarded", detail="Temporary folder purged"
    )


@app.post("/api/jobs/{job_id}/resume", response_model=ResumeResponse)
def resume_job(
    job_id: str,
    background_tasks: BackgroundTasks,
) -> ResumeResponse:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in {"failed"}:
        raise HTTPException(status_code=400, detail="Only failed jobs can be resumed")
    if not Path(job["temp_dir"]).is_dir():
        raise HTTPException(status_code=410, detail="Temporary job data has expired")
    db.update_job(job_id, status="queued", error_message=None, completed_at=None, progress=0)
    background_tasks.add_task(workflows.run_job, job_id)
    return ResumeResponse(job_id=job_id, status="queued")


def _job_file(job_id: str, area: str, relative_path: str) -> Path:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    root = (Path(job["temp_dir"]) / area).resolve()
    candidate = (root / relative_path).resolve()
    if root != candidate and root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return candidate


@app.get("/api/jobs/{job_id}/artifacts/{artifact_path:path}")
def get_artifact(job_id: str, artifact_path: str) -> FileResponse:
    return FileResponse(_job_file(job_id, "outputs", artifact_path))


@app.get("/api/jobs/{job_id}/source/{source_path:path}")
def get_source(job_id: str, source_path: str) -> FileResponse:
    return FileResponse(_job_file(job_id, "inputs", source_path))


@app.get("/api/jobs/{job_id}/download-zip")
def download_zip(job_id: str) -> FileResponse:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job is not completed")

    zip_path = Path(job["zip_path"] or "")
    if not zip_path.is_file():
        raise HTTPException(status_code=404, detail="ZIP archive not found")

    stem = Path(job["filenames"][0]).stem if job["filenames"] else job.get("job_type", "output")
    return FileResponse(
        path=zip_path,
        filename=f"{stem}_{job.get('job_type', 'records')}_output.zip",
        media_type="application/zip",
    )
