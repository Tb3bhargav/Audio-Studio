# StudioVoice Agent Notes

Minimal FastAPI + vanilla JS audio enhancer for NVIDIA Maxine Studio Voice.

## Run

See [README.md](README.md) for setup, ffmpeg install, and run commands.

Quick start:
```powershell
uvicorn main:app --reload --port 8000
```

## Hard constraints

- `NVIDIA_API_KEY` is hardcoded in `main.py` for personal use only. Do not push to a public repo.
- ffmpeg and ffprobe must be installed and on PATH.
- CORS is locked to `localhost:8000` and `127.0.0.1:8000`.

## Architecture

- Backend: `main.py` (FastAPI). Upload → WAV conversion → chunking → NIM enhancement → crossfade reassembly → optional transcode.
- Frontend: `frontend/` (vanilla JS, no build step). Served via `StaticFiles`.
- Output formats: `wav`, `mp3`, `aac`, `mp4` via `?format=` on `POST /api/enhance`.

## Conventions

- Keep changes minimal. Reuse existing helpers in `main.py` instead of adding new ones.
- No new dependencies unless unavoidable.
- Frontend changes stay in `frontend/`; backend changes stay in `main.py`.
