import csv
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from openmedialab.config import ALLOWED_EXTENSIONS, MAX_FRAMES, MAX_MEDIA_BYTES
from openmedialab.schemas import JobOptions
from openmedialab.urls import UnsafeMediaUrl, validate_media_url


class MediaProcessingError(RuntimeError):
    pass


def _run(command: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise MediaProcessingError(
            f"Required command not found: {command[0]}. Install FFmpeg and ensure it is on PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaProcessingError(f"Media command timed out: {command[0]}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1] if detail else f"Command exited with {result.returncode}."
        raise MediaProcessingError(f"{command[0]} failed: {message[:500]}")
    return result


def probe_media(source: Path) -> dict:
    result = _run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,format_name,size:stream=codec_type,codec_name,width,height",
            "-of", "json", str(source),
        ],
        timeout=120,
    )
    try:
        raw = json.loads(result.stdout)
        duration = float(raw.get("format", {}).get("duration", 0))
        streams = raw.get("streams", [])
    except (ValueError, TypeError) as exc:
        raise MediaProcessingError("FFprobe returned invalid media metadata.") from exc
    if duration <= 0 or duration > 24 * 60 * 60:
        raise MediaProcessingError("Media duration must be between 0 and 24 hours.")
    return {
        "duration": duration,
        "format": raw.get("format", {}).get("format_name", "unknown"),
        "streams": streams,
        "has_video": any(stream.get("codec_type") == "video" for stream in streams),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
    }


def _extract_wav(source: Path, destination: Path) -> None:
    _run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(source), "-map", "0:a:0",
         "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(destination)],
        timeout=3600,
    )


def _write_audio(source: Path, destination: Path, media_format: str) -> None:
    encoder = ["-c:a", "libmp3lame", "-q:a", "2"] if media_format == "mp3" else [
        "-c:a", "libvorbis", "-q:a", "5"
    ]
    _run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(source), "-map", "0:a:0",
         "-vn", *encoder, str(destination)],
        timeout=3600,
    )


def _transcribe(wav_path: Path, output_dir: Path, model_name: str) -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise MediaProcessingError(
            'Transcription requires the optional AI dependencies. Install with: pip install -e ".[ai]"'
        ) from exc

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(wav_path), vad_filter=True)
    rows = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            rows.append((float(segment.start), float(segment.end), text))
    (output_dir / "transcript.txt").write_text(
        "\n".join(text for _, _, text in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    with (output_dir / "transcript.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["start_seconds", "end_seconds", "text"])
        writer.writerows(rows)
    (output_dir / "subtitles.srt").write_text(
        "".join(
            f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n\n"
            for index, (start, end, text) in enumerate(rows, 1)
        ),
        encoding="utf-8",
    )
    (output_dir / "subtitles.vtt").write_text(
        "WEBVTT\n\n"
        + "".join(
            f"{_vtt_time(start)} --> {_vtt_time(end)}\n{text}\n\n"
            for start, end, text in rows
        ),
        encoding="utf-8",
    )
    if not getattr(info, "language", None):
        raise MediaProcessingError("Whisper could not determine the transcript language.")


def _timestamp(seconds: float, separator: str) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    seconds_part, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{seconds_part:02}{separator}{millis:03}"


def _srt_time(seconds: float) -> str:
    return _timestamp(seconds, ",")


def _vtt_time(seconds: float) -> str:
    return _timestamp(seconds, ".")


def _separate_sources(wav_path: Path, output_dir: Path, formats: list[str]) -> None:
    try:
        import demucs  # noqa: F401
    except ImportError as exc:
        raise MediaProcessingError(
            'Source separation requires the optional AI dependencies. Install with: pip install -e ".[ai]"'
        ) from exc
    separation_dir = output_dir / ".separated"
    _run(
        [
            sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
            "-o", str(separation_dir), str(wav_path),
        ],
        timeout=7200,
    )
    stems = separation_dir / "htdemucs" / wav_path.stem
    if not (stems / "vocals.wav").is_file() or not (stems / "no_vocals.wav").is_file():
        raise MediaProcessingError("Demucs finished but did not produce both expected audio stems.")
    for stem, label in (("vocals.wav", "vocals"), ("no_vocals.wav", "instrumental")):
        for media_format in formats:
            _write_audio(
                stems / stem,
                output_dir / f"{label}.{media_format}",
                media_format,
            )


def _detect_beats(wav_path: Path, output_dir: Path) -> None:
    try:
        import librosa
    except ImportError as exc:
        raise MediaProcessingError(
            'Beat detection requires the optional AI dependencies. Install with: pip install -e ".[ai]"'
        ) from exc
    try:
        audio, sample_rate = librosa.load(str(wav_path), sr=None, mono=True)
        tempo, beat_frames = librosa.beat.beat_track(y=audio, sr=sample_rate)
        tempo_value = float(tempo[0] if getattr(tempo, "ndim", 0) else tempo)
        times = librosa.frames_to_time(beat_frames, sr=sample_rate).tolist()
    except Exception as exc:
        raise MediaProcessingError(f"Beat analysis failed: {exc}") from exc
    with (output_dir / "beats.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["beat_number", "time_seconds", "estimated_bpm"])
        writer.writerows((index, f"{time:.3f}", f"{tempo_value:.2f}") for index, time in enumerate(times, 1))
    (output_dir / "beats.txt").write_text(
        f"Estimated tempo: {tempo_value:.2f} BPM\n"
        + "\n".join(f"{time:.3f}" for time in times),
        encoding="utf-8",
    )


def _extract_frames(source: Path, output_dir: Path, duration: float, interval: float) -> None:
    count = min(int(duration / interval) + 1, MAX_FRAMES)
    if count <= 0:
        raise MediaProcessingError("No frames fall within the selected extraction interval.")
    frame_dir = output_dir / ".frames"
    frame_dir.mkdir()
    _run(
        [
            "ffmpeg", "-nostdin", "-y", "-i", str(source),
            "-vf", f"fps=1/{interval:g}", "-frames:v", str(count),
            str(frame_dir / "frame-%06d.jpg"),
        ],
        timeout=3600,
    )
    frames = sorted(frame_dir.glob("frame-*.jpg"))
    if not frames:
        raise MediaProcessingError("FFmpeg did not extract any video frames.")
    with (output_dir / "frames.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["frame_number", "time_seconds", "filename"])
        writer.writerows((index, f"{(index - 1) * interval:.3f}", frame.name) for index, frame in enumerate(frames, 1))
    with zipfile.ZipFile(output_dir / "frames.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for frame in frames:
            archive.write(frame, frame.name)


def _export_video(source: Path, destination: Path, media_format: str) -> None:
    if media_format == "mp4":
        codecs = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac"]
    else:
        codecs = ["-c:v", "libvpx-vp9", "-deadline", "good", "-crf", "32", "-b:v", "0",
                  "-c:a", "libopus"]
    _run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
         *codecs, str(destination)],
        timeout=7200,
    )


def _extract_subtitles(source: Path, output_dir: Path, warnings: list[str]) -> None:
    probe = _run(
        ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
         "stream=index:stream_tags=language", "-of", "json", str(source)],
        timeout=120,
    )
    try:
        streams = json.loads(probe.stdout).get("streams", [])
    except ValueError as exc:
        raise MediaProcessingError("FFprobe returned invalid subtitle metadata.") from exc
    if not streams:
        warnings.append("No embedded subtitle tracks were found in the source.")
        return
    srt_path = output_dir / "embedded-subtitles.srt"
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(source), "-map", "0:s:0", "-c:s", "srt", str(srt_path)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode:
        warnings.append("An embedded subtitle track was found but could not be converted to SRT.")
        return
    vtt = ["WEBVTT\n\n"]
    for block in re.split(r"\r?\n\r?\n", srt_path.read_text(encoding="utf-8", errors="replace")):
        lines = block.splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            vtt.append(lines[1].replace(",", ".") + "\n" + "\n".join(lines[2:]) + "\n\n")
    (output_dir / "embedded-subtitles.vtt").write_text("".join(vtt), encoding="utf-8")


def _save_response(response: httpx.Response, destination: Path) -> None:
    total = 0
    try:
        with destination.open("wb") as file:
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_MEDIA_BYTES:
                    raise MediaProcessingError("The remote media exceeds the configured size limit.")
                file.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if not total:
        destination.unlink(missing_ok=True)
        raise MediaProcessingError("The source URL returned an empty file.")


def process_media(source: Path, output_dir: Path, options: JobOptions, progress) -> tuple[dict, list[str]]:
    metadata = probe_media(source)
    warnings: list[str] = []
    if not metadata["has_audio"] and any(
        (options.extract_audio, options.transcribe, options.separate_sources, options.detect_beats)
    ):
        raise MediaProcessingError("The selected audio operations need an audio stream, but none was found.")
    if not metadata["has_video"] and any(
        (options.extract_frames, options.video_formats, options.extract_subtitles)
    ):
        raise MediaProcessingError("The selected video operations need a video stream, but none was found.")
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / ".working-audio.wav"
    needs_audio = any(
        (options.extract_audio, options.transcribe, options.separate_sources, options.detect_beats)
    )
    try:
        if needs_audio:
            progress("Extracting audio")
            _extract_wav(source, wav_path)
        if options.extract_audio:
            progress("Exporting audio")
            for media_format in options.audio_formats:
                _write_audio(wav_path, output_dir / f"audio.{media_format}", media_format)
        if options.transcribe:
            progress(f"Transcribing with Whisper ({options.whisper_model})")
            _transcribe(wav_path, output_dir, options.whisper_model)
        if options.separate_sources:
            progress("Separating vocals and instrumental")
            _separate_sources(wav_path, output_dir, options.source_formats)
        if options.detect_beats:
            progress("Detecting beats")
            _detect_beats(wav_path, output_dir)
        if options.extract_frames:
            progress("Extracting video frames")
            if metadata["duration"] / options.frame_interval > MAX_FRAMES:
                warnings.append(f"Frame extraction was capped at {MAX_FRAMES} frames.")
            _extract_frames(source, output_dir, metadata["duration"], options.frame_interval)
        for media_format in options.video_formats:
            progress(f"Exporting video as {media_format.upper()}")
            _export_video(source, output_dir / f"video.{media_format}", media_format)
        if options.extract_subtitles:
            progress("Extracting embedded subtitles")
            _extract_subtitles(source, output_dir, warnings)
        metadata.pop("streams", None)
        return metadata, warnings
    finally:
        wav_path.unlink(missing_ok=True)
        for temporary_dir in (output_dir / ".frames", output_dir / ".separated"):
            if temporary_dir.is_dir():
                shutil.rmtree(temporary_dir)


def download_direct(url: str, destination_dir: Path) -> Path:
    current_url = url
    with httpx.Client(
        follow_redirects=False, timeout=httpx.Timeout(30, read=90), trust_env=False
    ) as client:
        for _ in range(6):
            _, is_video_site = validate_media_url(current_url)
            if is_video_site:
                raise UnsafeMediaUrl("Platform links must be downloaded with the platform adapter.")
            with client.stream("GET", current_url, headers={"Range": "bytes=0-0"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise MediaProcessingError("The source redirected without a destination.")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code not in {200, 206}:
                    response.raise_for_status()
                    raise MediaProcessingError(
                        f"The source URL returned unexpected HTTP status {response.status_code}."
                    )
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_MEDIA_BYTES:
                    raise MediaProcessingError("The remote media exceeds the configured size limit.")
                if content_type.startswith("text/") or content_type == "application/json":
                    raise MediaProcessingError("The URL returned a web page or document, not media.")
                suffix = Path(urlsplit(current_url).path).suffix.lower()
                if suffix not in ALLOWED_EXTENSIONS:
                    suffix = mimetypes.guess_extension(content_type) or ""
                if suffix not in ALLOWED_EXTENSIONS:
                    raise MediaProcessingError("The direct URL must point to a supported audio or video media file.")
                if response.status_code == 206:
                    response.close()
                    stream = client.stream("GET", current_url)
                else:
                    stream = None
                if stream is None:
                    destination = destination_dir / f"source{suffix}"
                    _save_response(response, destination)
                    return destination
            with stream as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_MEDIA_BYTES:
                    raise MediaProcessingError("The remote media exceeds the configured size limit.")
                destination = destination_dir / f"source{suffix}"
                _save_response(response, destination)
                return destination
    raise MediaProcessingError("The source URL redirected too many times.")


def download_platform(url: str, destination_dir: Path) -> Path:
    try:
        import yt_dlp
    except ImportError as exc:
        raise MediaProcessingError("Install yt-dlp with `pip install -e .` to process YouTube/Vimeo links.") from exc
    destination_dir.mkdir(parents=True, exist_ok=True)
    def enforce_size_limit(status: dict) -> None:
        if status.get("status") == "downloading" and status.get("downloaded_bytes", 0) > MAX_MEDIA_BYTES:
            raise MediaProcessingError("The downloaded media exceeds the configured size limit.")

    options = {
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": str(destination_dir / "source.%(ext)s"),
        "noplaylist": True,
        "max_filesize": MAX_MEDIA_BYTES,
        "socket_timeout": 30,
        "retries": 3,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "progress_hooks": [enforce_size_limit],
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([url])
    except Exception as exc:
        for path in destination_dir.glob("source.*"):
            if path.is_file():
                path.unlink()
        if isinstance(exc, MediaProcessingError):
            raise
        raise MediaProcessingError(f"Video-site download failed: {exc}") from exc
    candidates = [
        path for path in destination_dir.glob("source.*")
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS
    ]
    if not candidates:
        raise MediaProcessingError("The video-site downloader did not produce a supported media file.")
    source = max(candidates, key=lambda path: path.stat().st_size)
    if source.stat().st_size > MAX_MEDIA_BYTES:
        source.unlink()
        raise MediaProcessingError("The downloaded media exceeds the configured size limit.")
    return source
