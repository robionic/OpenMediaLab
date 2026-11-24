import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from openmedialab import jobs
from openmedialab import main
from openmedialab.main import app
from openmedialab.media import _srt_time, _vtt_time
from openmedialab.schemas import JobOptions
from openmedialab.urls import UnsafeMediaUrl, validate_media_url


def test_options_reject_empty_operations():
    with pytest.raises(ValidationError, match="Select at least one"):
        JobOptions(
            extract_audio=False,
            transcribe=False,
            separate_sources=False,
            detect_beats=False,
            extract_frames=False,
            video_formats=[],
            extract_subtitles=False,
        )


def test_transcription_options_require_an_audio_format():
    with pytest.raises(ValidationError, match="at least one audio format"):
        JobOptions(audio_formats=[])


def test_subtitle_timestamps_are_formatted():
    assert _srt_time(61.234) == "00:01:01,234"
    assert _vtt_time(61.234) == "00:01:01.234"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/audio.mp3", "http://127.0.0.1/a.mp4"])
def test_rejects_non_http_or_private_urls(url):
    with pytest.raises(UnsafeMediaUrl):
        validate_media_url(url)


def test_app_exposes_processing_routes():
    routes = {route.path for route in app.routes}
    assert {"/", "/api/jobs/upload", "/api/jobs/url", "/api/jobs/{job_id}"} <= routes


def test_home_page_is_served():
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "OpenMediaLab" in response.text


def test_url_api_rejects_private_addresses():
    response = TestClient(app).post(
        "/api/jobs/url",
        json={
            "url": "http://127.0.0.1/example.mp4",
            "options": {"extract_audio": True},
        },
    )
    assert response.status_code == 422
    assert "public internet" in response.json()["detail"]


def test_uploaded_source_can_be_played_inline(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(jobs, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(main, "_run_job", lambda *args: None)
    client = TestClient(app)
    response = client.post(
        "/api/jobs/upload",
        data={"options": JobOptions().model_dump_json()},
        files={"file": ("clip.mp4", b"sample media", "video/mp4")},
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    preview = client.get(f"/api/jobs/{job_id}/source")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "video/mp4"
    assert "content-disposition" not in preview.headers
