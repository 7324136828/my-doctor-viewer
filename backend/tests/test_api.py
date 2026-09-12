"""End-to-end smoke test: upload a PDF, run the pipeline, download the zip.

Runs with ENABLE_LLM=false so no Ollama dependency is needed.
"""

import io
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ["JOBS_ROOT"] = str(Path(tempfile.mkdtemp(prefix="mdv_test_jobs_")))
os.environ["ENABLE_LLM"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from pypdf import PdfWriter  # noqa: E402
import numpy as np  # noqa: E402
from pydicom.dataset import FileDataset, FileMetaDataset  # noqa: E402
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid  # noqa: E402

from app.main import app  # noqa: E402


def _make_pdf() -> bytes:
    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(buf)
    return buf.getvalue()


def _make_dicom() -> bytes:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dataset = FileDataset("fixture.dcm", {}, file_meta=meta, preamble=b"\0" * 128)
    dataset.SOPClassUID = meta.MediaStorageSOPClassUID
    dataset.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    dataset.StudyInstanceUID = generate_uid()
    dataset.SeriesInstanceUID = generate_uid()
    dataset.Modality = "MR"
    dataset.Rows = 2
    dataset.Columns = 2
    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = "MONOCHROME2"
    dataset.BitsAllocated = 16
    dataset.BitsStored = 16
    dataset.HighBit = 15
    dataset.PixelRepresentation = 0
    dataset.PixelData = np.array([[0, 100], [200, 300]], dtype=np.uint16).tobytes()
    buffer = io.BytesIO()
    dataset.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


def test_convert_pdf_end_to_end():
    with TestClient(app) as client:
        resp = client.post(
            "/api/convert",
            files=[("files", ("sample.pdf", _make_pdf(), "application/pdf"))],
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]

        status = None
        for _ in range(120):
            job = client.get(f"/api/jobs/{job_id}").json()
            status = job["status"]
            if status in ("completed", "failed", "discarded"):
                break
            time.sleep(0.5)
        assert status == "completed", f"job ended as {status}: {job}"

        logs = client.get(f"/api/jobs/{job_id}/logs").json()["logs"]
        assert "extract" in logs

        dl = client.get(f"/api/jobs/{job_id}/download-zip")
        assert dl.status_code == 200
        names = zipfile.ZipFile(io.BytesIO(dl.content)).namelist()
        assert "extracted_text.txt" in names
        assert "metadata.json" in names


def test_rejects_non_pdf():
    with TestClient(app) as client:
        resp = client.post(
            "/api/convert",
            files=[("files", ("notes.txt", b"hello", "text/plain"))],
        )
        assert resp.status_code == 400


def test_text_records_produce_manifest_and_review_labels():
    with TestClient(app) as client:
        resp = client.post(
            "/api/workflows/records",
            files=[("files", ("visit.txt", b"Visit date: 2026-01-02\nRoutine follow-up.", "text/plain"))],
        )
        assert resp.status_code == 200, resp.text
        job = client.get(f"/api/jobs/{resp.json()['job_id']}").json()
        assert job["status"] == "completed"
        assert job["job_type"] == "records"
        assert job["result"]["summary"]["document_count"] == 1
        review = client.get(
            f"/api/jobs/{job['id']}/artifacts/review_manifest.json"
        )
        assert review.status_code == 200
        payload = review.json()
        assert payload["chunk_count"] == 1
        assert payload["summaries"][0]["review_status"] == "unverified"
        assert payload["summaries"][0]["source_url"].endswith("/visit.txt")


def test_condition_rollup_excludes_normal_findings():
    reports = {
        "scan-a": {"Identified Conditions (array of strings)": ["Cyst", "Normal anatomy"]},
        "scan-b": {"identified_conditions": ["Cyst", "No acute finding", "Inflammation"]},
    }
    with TestClient(app) as client:
        resp = client.post(
            "/api/workflows/conditions",
            files=[("files", ("reports.json", json.dumps(reports).encode(), "application/json"))],
        )
        assert resp.status_code == 200, resp.text
        job = client.get(f"/api/jobs/{resp.json()['job_id']}").json()
        assert job["status"] == "completed"
        output = client.get(
            f"/api/jobs/{job['id']}/artifacts/conditions_summary.json"
        ).json()
        assert output["conditions"]["Cyst"]["incident_count"] == 2
        assert "Normal anatomy" not in output["conditions"]
        assert "No acute finding" not in output["conditions"]


def test_imaging_logs_unreadable_files_without_failing_batch():
    with TestClient(app) as client:
        resp = client.post(
            "/api/workflows/imaging",
            files=[("files", ("broken.dcm", b"not a dicom", "application/dicom"))],
        )
        assert resp.status_code == 200, resp.text
        job = client.get(f"/api/jobs/{resp.json()['job_id']}").json()
        assert job["status"] == "completed"
        assert job["result"]["summary"]["unreadable_count"] == 1
        manifest = client.get(
            f"/api/jobs/{job['id']}/artifacts/imaging_manifest.json"
        ).json()
        assert manifest[0]["status"] == "unreadable"


def test_imaging_writes_png_and_source_linked_report():
    with TestClient(app) as client:
        resp = client.post(
            "/api/workflows/imaging",
            files=[("files", ("scan.dcm", _make_dicom(), "application/dicom"))],
        )
        assert resp.status_code == 200, resp.text
        job = client.get(f"/api/jobs/{resp.json()['job_id']}").json()
        assert job["status"] == "completed"
        assert job["result"]["summary"]["processed_count"] == 1
        assert client.get(f"/api/jobs/{job['id']}/artifacts/images/scan.dcm.png").status_code == 200
        report = client.get(
            f"/api/jobs/{job['id']}/artifacts/reports/scan.dcm.analysis.json"
        ).json()
        assert report["unverified"] is True
        assert report["source_url"].endswith("/source/scan.dcm")
        assert report["analysis_status"] == "not_run"


def test_artifact_path_cannot_escape_job_output():
    with TestClient(app) as client:
        resp = client.get("/api/jobs/not-a-job/artifacts/../../jobs.db")
        assert resp.status_code in {404, 422}
