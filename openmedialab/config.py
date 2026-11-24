import os
from pathlib import Path


STORAGE_DIR = Path(os.environ.get("OPENMEDIALAB_STORAGE_DIR", "./data")).resolve()
MAX_MEDIA_BYTES = int(os.environ.get("OPENMEDIALAB_MAX_UPLOAD_BYTES", str(1024**3)))
MAX_FRAMES = int(os.environ.get("OPENMEDIALAB_MAX_FRAMES", "1000"))
ALLOWED_EXTENSIONS = {
    ".3gp", ".aac", ".avi", ".flac", ".m4a", ".mkv", ".mov", ".mp3",
    ".mp4", ".mpeg", ".mpg", ".oga", ".ogg", ".opus", ".wav", ".webm",
    ".wmv",
}
