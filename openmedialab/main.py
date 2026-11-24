import json
import mimetypes
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from openmedialab.config import ALLOWED_EXTENSIONS, MAX_MEDIA_BYTES, STORAGE_DIR
from openmedialab.jobs import create_job, read_job, serialize_job, update_job
from openmedialab.media import (
    MediaProcessingError,
    download_direct,
    download_platform,
    process_media,
)
from openmedialab.schemas import JobOptions, JobResponse, UrlJobRequest
from openmedialab.urls import UnsafeMediaUrl, validate_media_url


app = FastAPI(title="OpenMediaLab", version="0.1.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


def _run_job(job_id: str, job_dir: Path, options: JobOptions, source: Path | None, url: str | None):
    try:
        if source is None and url:
            update_job(job_dir, status="downloading", stage="Downloading source media")
            _, is_platform = validate_media_url(url)
            source = download_platform(url, job_dir) if is_platform else download_direct(url, job_dir)
        if source is None or not source.is_file():
            raise MediaProcessingError("The source media file could not be found.")
        update_job(job_dir, status="processing", stage="Inspecting source")

        def progress(stage: str):
            update_job(job_dir, status="processing", stage=stage)

        metadata, warnings = process_media(source, job_dir / "output", options, progress)
        update_job(job_dir, status="completed", stage="Completed", metadata=metadata, warnings=warnings)
    except Exception as exc:
        update_job(job_dir, status="failed", stage="Failed", error=str(exc)[:1000])


def _parse_options(value: str) -> JobOptions:
    try:
        return JobOptions.model_validate_json(value)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid processing options: {exc}") from exc


async def _save_upload(upload: UploadFile, destination_dir: Path) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Choose a supported audio or video file.")
    destination_dir.mkdir(parents=True, exist_ok=True)
    path = destination_dir / f"source{suffix}"
    total = 0
    try:
        with path.open("wb") as file:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_MEDIA_BYTES:
                    path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"The upload exceeds the {MAX_MEDIA_BYTES // (1024**2)} MiB limit.",
                    )
                file.write(chunk)
    finally:
        await upload.close()
    if not total:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return path


@app.post("/api/jobs/upload", response_model=JobResponse, status_code=202)
async def upload_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    options: str = Form(...),
):
    parsed_options = _parse_options(options)
    job_id, job_dir = create_job(None, parsed_options.model_dump())
    try:
        source = await _save_upload(file, job_dir)
    except Exception:
        (job_dir / "job.json").unlink(missing_ok=True)
        raise
    background_tasks.add_task(_run_job, job_id, job_dir, parsed_options, source, None)
    return serialize_job(read_job(job_id))


@app.post("/api/jobs/url", response_model=JobResponse, status_code=202)
def url_job(request: UrlJobRequest, background_tasks: BackgroundTasks):
    url = str(request.url)
    try:
        validate_media_url(url)
    except UnsafeMediaUrl as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job_id, job_dir = create_job(url, request.options.model_dump())
    background_tasks.add_task(_run_job, job_id, job_dir, request.options, None, url)
    return serialize_job(read_job(job_id))


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str):
    payload = read_job(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return serialize_job(payload)


@app.get("/api/jobs/{job_id}/source")
def get_source(job_id: str):
    payload = read_job(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    job_dir = STORAGE_DIR / "jobs" / job_id
    sources = [
        path for path in job_dir.glob("source.*")
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS
    ]
    if not sources:
        raise HTTPException(status_code=404, detail="Source media is not available yet.")
    source = sources[0]
    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return FileResponse(source, media_type=media_type)


@app.get("/api/jobs/{job_id}/files/{filename}")
def get_output_file(job_id: str, filename: str):
    payload = read_job(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if Path(filename).name != filename or filename.startswith("."):
        raise HTTPException(status_code=404, detail="Output file not found.")
    output = STORAGE_DIR / "jobs" / job_id / "output" / filename
    if not output.is_file():
        raise HTTPException(status_code=404, detail="Output file not found.")
    media_type = mimetypes.guess_type(output.name)[0] or "application/octet-stream"
    return FileResponse(output, media_type=media_type)
