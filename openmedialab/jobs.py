import json
import threading
import uuid
from pathlib import Path

from openmedialab.config import STORAGE_DIR


_lock = threading.Lock()


def create_job(source_url: str | None, options: dict) -> tuple[str, Path]:
    job_id = str(uuid.uuid4())
    job_dir = STORAGE_DIR / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    payload = {
        "id": job_id,
        "status": "queued",
        "stage": "Waiting to start",
        "source_url": source_url,
        "options": options,
        "error": None,
        "warnings": [],
        "metadata": {},
        "files": [],
    }
    with _lock:
        _write(job_dir, payload)
    return job_id, job_dir


def _write(job_dir: Path, payload: dict) -> None:
    temporary = job_dir / ".job.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(job_dir / "job.json")


def update_job(job_dir: Path, **updates) -> dict:
    with _lock:
        path = job_dir / "job.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(updates)
        _write(job_dir, payload)
        return payload


def read_job(job_id: str) -> dict | None:
    if not _valid_id(job_id):
        return None
    path = STORAGE_DIR / "jobs" / job_id / "job.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _valid_id(job_id: str) -> bool:
    try:
        return str(uuid.UUID(job_id)) == job_id
    except ValueError:
        return False


def serialize_job(payload: dict) -> dict:
    job_dir = STORAGE_DIR / "jobs" / payload["id"]
    output_dir = job_dir / "output"
    files = []
    if payload["status"] == "completed" and output_dir.is_dir():
        for path in sorted(output_dir.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                files.append({
                    "name": path.name,
                    "size": path.stat().st_size,
                    "url": f"/api/jobs/{payload['id']}/files/{path.name}",
                })
    return {
        key: payload.get(key)
        for key in ("id", "status", "stage", "error", "warnings", "metadata", "source_url")
    } | {"files": files}
