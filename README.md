# StudioVoice

StudioVoice is a minimal FastAPI backend that accepts an audio upload, converts it to 48kHz mono 16-bit PCM WAV with ffmpeg, streams it to NVIDIA Maxine Studio Voice over NVIDIA Cloud Functions (NVCF) gRPC/TLS, and returns the enhanced audio. Large files are split into time-based chunks and reassembled automatically.

## Requirements

- Python 3.11
- ffmpeg installed and available on PATH
- A hardcoded NVIDIA API key in `main.py` for personal use only

## Get an NVIDIA API key

1. Sign in to NVIDIA and create or use an API key for NIM access.
2. Paste the key directly into the `NVIDIA_API_KEY` line in `main.py`.
3. Do not push this setup to a public repo.

## Install ffmpeg

Use your platform package manager.

### Windows

```powershell
winget install Gyan.FFmpeg
```

### macOS

```bash
brew install ffmpeg
```

### Ubuntu / Debian

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

## Install Python dependencies

Create and activate a virtual environment, then install the app requirements:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
uvicorn main:app --reload --port 8000
```

## Health check

```powershell
curl http://127.0.0.1:8000/healthz
```

The response includes the detected ffmpeg version and whether the hardcoded `NVIDIA_API_KEY` is configured.

## NVIDIA Studio Voice endpoint

The app uses NVIDIA's Studio Voice preview gRPC service, not an HTTP base URL:

- Target: `grpc.nvcf.nvidia.com:443`
- TLS: enabled
- Function ID: `3f0aeba3-6d91-4465-b8cc-cc2aef355186`

The required NVCF `authorization` and `function-id` metadata are supplied automatically.

## Output format

Use the `format` query parameter on `POST /api/enhance` to choose the download format:

- `wav` for WAV output
- `mp3` for MP3 output
- `aac` for AAC in an `.m4a` container
- `mp4` for audio-only MP4 output

If `format` is omitted, the API returns WAV by default.

Example:

```powershell
curl -X POST "http://127.0.0.1:8000/api/enhance?format=mp3" ^
  -F "file=@test.wav" ^
  --output out.mp3
```

## Test upload

Generate a small test MP3:

```bash
ffmpeg -f lavfi -i "sine=frequency=440:duration=2" test.mp3
```

Upload it to the API:

```bash
curl -X POST "http://127.0.0.1:8000/api/enhance" ^
  -F "file=@test.mp3" ^
  --output enhanced.wav
```

On macOS or Linux, replace the line continuations with `\`.

The returned file should be a WAV file that plays in VLC, QuickTime, or any standard audio player.
