"""Background workflow implementations beyond medical-record ingestion.

Each workflow uses the same isolated job directory and lifecycle as the records
pipeline.  Outputs are review artefacts, never diagnoses or treatment advice.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import shutil
from pathlib import Path

from .. import db
from . import pipeline


SAFETY_NOTICE = (
    "AI-generated content is unverified and must be compared with the linked "
    "source by a qualified clinician. It is not medical advice."
)


def _artifact_index(outputs: Path) -> list[dict]:
    return [
        {
            "name": str(path.relative_to(outputs)).replace("\\", "/"),
            "size": path.stat().st_size,
            "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        }
        for path in sorted(outputs.rglob("*"))
        if path.is_file()
    ]


def _finish(runner: pipeline.JobRunner, summary: dict) -> None:
    result = {
        "summary": summary,
        "artifacts": _artifact_index(runner.outputs),
        "safety_notice": SAFETY_NOTICE,
    }
    zip_path = pipeline.stage_package(runner)
    db.set_status(
        runner.job_id,
        "completed",
        progress=100,
        zip_path=str(zip_path),
        result_json=json.dumps(result),
    )
    runner.log("Job completed.")


def _parse_model_json(text: str) -> dict:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    candidate = match.group(1) if match else text
    try:
        value = json.loads(candidate.strip())
        return value if isinstance(value, dict) else {"response": value}
    except json.JSONDecodeError:
        return {"raw_response": text, "json_valid": False}


def _run_imaging(runner: pipeline.JobRunner) -> dict:
    try:
        import numpy as np
        import pydicom
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Imaging dependencies are unavailable. Run setup again to install pydicom, numpy and Pillow."
        ) from exc

    images_dir = runner.outputs / "images"
    reports_dir = runner.outputs / "reports"
    images_dir.mkdir(exist_ok=True)
    reports_dir.mkdir(exist_ok=True)
    checkpoint_path = runner.work / "imaging_checkpoint.json"
    completed = set()
    if checkpoint_path.is_file():
        completed = set(json.loads(checkpoint_path.read_text(encoding="utf-8")))

    llm_ok, llm_reason = pipeline._ollama_status()
    manifest: list[dict] = []
    inputs = [p for p in sorted(runner.inputs.rglob("*")) if p.is_file()]
    if not inputs:
        raise RuntimeError("No DICOM files were uploaded")

    for index, source in enumerate(inputs, 1):
        runner.check_cancelled()
        safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.name).strip("_") or f"image_{index}"
        png_name = f"{safe_stem}.png"
        report_name = f"{safe_stem}.analysis.json"
        item = {
            "source_file": source.name,
            "source_url": f"/api/jobs/{runner.job_id}/source/{source.name}",
            "image": f"images/{png_name}",
            "report": f"reports/{report_name}",
        }
        if source.name in completed and (images_dir / png_name).is_file():
            item["status"] = "skipped_completed"
            manifest.append(item)
            runner.log(f"Skipping completed DICOM: {source.name}")
            continue
        try:
            dataset = pydicom.dcmread(str(source))
            pixels = dataset.pixel_array.astype(np.float32)
            while pixels.ndim > 2:
                pixels = pixels[0]
            low, high = float(pixels.min()), float(pixels.max())
            if high > low:
                pixels = (pixels - low) / (high - low) * 255
            image = Image.fromarray(pixels.astype(np.uint8)).convert("L")
            image = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
            png_path = images_dir / png_name
            image.save(png_path)

            report = {
                "review_status": "unverified",
                "unverified": True,
                "safety_notice": SAFETY_NOTICE,
                "source_file": source.name,
                "source_url": item["source_url"],
                "image_url": f"/api/jobs/{runner.job_id}/artifacts/images/{png_name}",
                "metadata": {
                    "modality": str(getattr(dataset, "Modality", "")),
                    "study_instance_uid": str(getattr(dataset, "StudyInstanceUID", "")),
                    "series_instance_uid": str(getattr(dataset, "SeriesInstanceUID", "")),
                    "instance_number": str(getattr(dataset, "InstanceNumber", "")),
                    "rows": int(getattr(dataset, "Rows", image.height)),
                    "columns": int(getattr(dataset, "Columns", image.width)),
                },
                "analysis_status": "not_run",
                "finding": None,
            }
            if llm_ok:
                encoded = base64.b64encode(png_path.read_bytes()).decode("ascii")
                try:
                    from ollama import chat

                    response = chat(
                        model=pipeline.config.OLLAMA_MODEL,
                        messages=[{
                            "role": "user",
                            "content": (
                                "Describe this medical image as an unverified draft for clinician review. "
                                "Return JSON with image_description, key_findings, potential_abnormalities, "
                                "conclusion, and identified_conditions. Do not give treatment advice."
                            ),
                            "images": [encoded],
                        }],
                    )
                    report["finding"] = _parse_model_json(response.message.content)
                    report["analysis_status"] = "ai_draft_unverified"
                except Exception as exc:  # PNG remains useful even when model analysis fails
                    report["analysis_status"] = "model_error"
                    report["analysis_error"] = str(exc)
                    runner.log(f"Model analysis failed for {source.name}: {exc}")
            else:
                report["analysis_note"] = llm_reason

            (reports_dir / report_name).write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            completed.add(source.name)
            checkpoint_path.write_text(json.dumps(sorted(completed), indent=2), encoding="utf-8")
            item["status"] = "processed"
            item["analysis_status"] = report["analysis_status"]
            runner.log(f"Converted DICOM {source.name} -> {png_name}")
        except Exception as exc:
            item["status"] = "unreadable"
            item["error"] = str(exc)
            runner.log(f"Unreadable DICOM {source.name}: {exc}")
        manifest.append(item)
        runner.progress(10 + int(index / len(inputs) * 80), "imaging")

    (runner.outputs / "imaging_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return {
        "input_count": len(inputs),
        "processed_count": sum(i["status"] in {"processed", "skipped_completed"} for i in manifest),
        "unreadable_count": sum(i["status"] == "unreadable" for i in manifest),
        "llm_enabled": llm_ok,
    }


def _run_genetics(runner: pipeline.JobRunner, options: dict) -> dict:
    gene = str(options.get("gene", "")).strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,31}", gene):
        raise RuntimeError("A valid gene symbol is required")
    args = [
        "--gene", gene,
        "--input-dir", str(runner.inputs),
        "--json",
        "--output", str(runner.outputs / "genetics_results.json"),
    ]
    if options.get("pass_only"):
        args.append("--pass-only")
    if options.get("include_no_clinvar", True):
        args.append("--include-no-clinvar")
    runner.progress(20, "gene lookup and VCF scan")
    rc = runner.run_script("query_gene_indels.py", *args)
    if rc != 0:
        raise RuntimeError(f"Genetics workflow exited with code {rc}; see logs for details")
    output = runner.outputs / "genetics_results.json"
    data = json.loads(output.read_text(encoding="utf-8")) if output.is_file() else {}
    wrapper = {
        "review_status": "professional_review_recommended",
        "safety_notice": (
            "Variant annotations can change and are not a diagnosis. Confirm genome build, sample quality, "
            "and clinical significance with a genetics professional."
        ),
        "source_files": [p.name for p in runner.inputs.iterdir() if p.is_file()],
        "gene": gene,
        "results": data,
    }
    output.write_text(json.dumps(wrapper, indent=2), encoding="utf-8")
    rows = data.get("variants", data.get("results", [])) if isinstance(data, dict) else []
    return {"gene": gene, "variant_count": len(rows) if isinstance(rows, list) else None}


def _run_research(runner: pipeline.JobRunner, options: dict) -> dict:
    source_type = options.get("source_type")
    urls = options.get("urls") or []
    if not urls:
        raise RuntimeError("At least one feed or ClinicalTrials.gov RSS URL is required")
    url_file = runner.work / "urls.txt"
    url_file.write_text("\n".join(urls) + "\n", encoding="utf-8")
    runner.progress(20, "download")
    if source_type == "rss":
        output = runner.outputs / "rss_feed.json"
        rc = runner.run_script("download_rss.py", str(url_file), "--output", str(output))
    elif source_type == "trials":
        output = runner.outputs / "clinical_trials.json"
        fields_ref = runner.work / "fields.txt"
        fields = "NCTId,BriefTitle,OverallStatus,Condition,InterventionName,LeadSponsorName,StudyType,Phase,StartDate,CompletionDate,BriefSummary"
        fields_ref.write_text(
            "https://clinicaltrials.gov/search?fields=" + fields + "\n", encoding="utf-8"
        )
        rc = runner.run_script(
            "download_all_studies.py",
            str(url_file),
            "--fields-from", str(fields_ref),
            "--output", str(output),
        )
    else:
        raise RuntimeError("source_type must be 'rss' or 'trials'")
    if rc != 0:
        raise RuntimeError(f"Research download was incomplete (exit code {rc}); see logs")
    data = json.loads(output.read_text(encoding="utf-8"))
    return {
        "source_type": source_type,
        "source_count": len(urls),
        "entry_count": data.get("entry_count", data.get("unique_study_count", data.get("study_count"))),
    }


def _condition_values(payload: object) -> list[tuple[str, list]]:
    rows: list[tuple[str, list]] = []
    if not isinstance(payload, dict):
        return rows
    keys = ("Identified Conditions (array of strings)", "identified_conditions", "conditions")
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            rows.append(("report", value))
            return rows
    for source, value in payload.items():
        if isinstance(value, dict):
            nested = _condition_values(value)
            rows.extend((str(source) if name == "report" else f"{source}/{name}", vals) for name, vals in nested)
    return rows


def _run_conditions(runner: pipeline.JobRunner) -> dict:
    summary: dict[str, dict] = {}
    failures = []
    inputs = [p for p in sorted(runner.inputs.iterdir()) if p.is_file()]
    for path in inputs:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for report, conditions in _condition_values(payload):
                source = path.name if report == "report" else f"{path.name}:{report}"
                for raw in conditions:
                    condition = str(raw).strip()
                    normalized = condition.casefold()
                    if not condition or "normal" in normalized or normalized.startswith("no ") or "no finding" in normalized:
                        continue
                    entry = summary.setdefault(condition, {"incident_count": 0, "source_files": []})
                    entry["incident_count"] += 1
                    if source not in entry["source_files"]:
                        entry["source_files"].append(source)
        except (OSError, json.JSONDecodeError) as exc:
            failures.append({"file": path.name, "error": str(exc)})
            runner.log(f"Could not read {path.name}: {exc}")
    ordered = dict(sorted(summary.items(), key=lambda item: (-item[1]["incident_count"], item[0])))
    output = {
        "review_status": "unverified_source_rollup",
        "safety_notice": SAFETY_NOTICE,
        "condition_count": len(ordered),
        "conditions": ordered,
        "input_errors": failures,
    }
    (runner.outputs / "conditions_summary.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {"condition_count": len(ordered), "input_count": len(inputs), "invalid_input_count": len(failures)}


def run_job(job_id: str) -> None:
    """Dispatch a queued job to the appropriate workflow."""
    job = db.get_job(job_id)
    if not job:
        return
    if job.get("job_type", "records") == "records":
        pipeline.run_job(job_id)
        return

    runner = pipeline.JobRunner(job_id, Path(job["temp_dir"]))
    try:
        db.set_status(job_id, "in_progress", error_message=None, completed_at=None)
        runner.progress(5, job["job_type"])
        if job["job_type"] == "imaging":
            summary = _run_imaging(runner)
        elif job["job_type"] == "genetics":
            summary = _run_genetics(runner, job.get("options", {}))
        elif job["job_type"] == "research":
            summary = _run_research(runner, job.get("options", {}))
        elif job["job_type"] == "conditions":
            summary = _run_conditions(runner)
        else:
            raise RuntimeError(f"Unknown workflow type: {job['job_type']}")
        runner.check_cancelled()
        runner.progress(95, "package")
        if runner.log_path.is_file():
            shutil.copy2(runner.log_path, runner.outputs / "logs.txt")
        _finish(runner, summary)
    except pipeline.JobCancelled:
        try:
            runner.log("Job cancelled by user.")
        except OSError:
            pass
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
        pipeline._unregister(job_id)
