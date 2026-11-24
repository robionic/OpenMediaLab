from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class JobOptions(BaseModel):
    extract_audio: bool = True
    audio_formats: list[Literal["mp3", "ogg"]] = Field(default_factory=lambda: ["mp3"])
    transcribe: bool = False
    separate_sources: bool = False
    source_formats: list[Literal["mp3", "ogg"]] = Field(default_factory=lambda: ["mp3"])
    detect_beats: bool = False
    extract_frames: bool = False
    frame_interval: float = Field(default=1.0, ge=0.25, le=60)
    video_formats: list[Literal["mp4", "webm"]] = Field(default_factory=list)
    extract_subtitles: bool = False
    whisper_model: Literal["tiny", "base", "small", "medium", "large-v3"] = "small"

    @field_validator("audio_formats", "source_formats", "video_formats")
    @classmethod
    def remove_duplicate_formats(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def require_an_operation(self):
        if not any(
            (
                self.extract_audio,
                self.transcribe,
                self.separate_sources,
                self.detect_beats,
                self.extract_frames,
                self.video_formats,
                self.extract_subtitles,
            )
        ):
            raise ValueError("Select at least one extraction or export operation.")
        if self.extract_audio and not self.audio_formats:
            raise ValueError("Select at least one audio format.")
        if self.separate_sources and not self.source_formats:
            raise ValueError("Select at least one source-separation format.")
        return self


class UrlJobRequest(BaseModel):
    url: HttpUrl
    options: JobOptions


class OutputFile(BaseModel):
    name: str
    size: int
    url: str


class JobResponse(BaseModel):
    id: str
    status: Literal["queued", "downloading", "processing", "completed", "failed"]
    stage: str
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    files: list[OutputFile] = Field(default_factory=list)
    source_url: str | None = None
