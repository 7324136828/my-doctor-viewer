"""Pydantic request/response schemas for the conversion API."""

from pydantic import BaseModel, Field


class JobCreateResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    id: str
    filename: str
    filenames: list[str]
    file_size: int
    status: str
    progress: int
    created_at: str
    completed_at: str | None = None
    error_message: str | None = None
    temp_dir: str | None = None
    zip_path: str | None = None
    job_type: str = "records"
    options_json: str = "{}"
    options: dict = Field(default_factory=dict)
    result_json: str | None = None
    result: dict | list | None = None


class JobLogsResponse(BaseModel):
    job_id: str
    logs: str


class DiscardResponse(BaseModel):
    job_id: str
    status: str
    detail: str


class ResearchRequest(BaseModel):
    source_type: str
    urls: list[str]


class ResumeResponse(BaseModel):
    job_id: str
    status: str
