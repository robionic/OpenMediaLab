# Third-party components

OpenMediaLab's application code is MIT-licensed. This does not relicense bundled or separately installed tools, codecs, libraries, or model weights.

| Component | Use | License / notes |
|---|---|---|
| FFmpeg / FFprobe | Media probing, conversion, and frame extraction | FFmpeg is LGPL by default, but build configuration and enabled codecs can change applicable terms. Check the FFmpeg distribution you install. |
| yt-dlp | Downloading supported public video links | Unlicense; source websites and media remain subject to their own terms. |
| FastAPI, Uvicorn, HTTPX | Web/API runtime | See each upstream package's license. |
| faster-whisper | Optional speech/lyric transcription | MIT software license. Model cards and downloaded model weights have separate terms. |
| Demucs | Optional vocal/instrumental source separation | MIT software license. Downloaded model weights have separate terms. |
| librosa | Optional beat analysis | ISC license. |

This table is informational, not legal advice. Review the current upstream notices and the licenses for any models, codecs, or binaries you choose to install or distribute.
