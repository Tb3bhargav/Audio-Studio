from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import grpc
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from studiovoice_pb2 import EnhanceAudioRequest, EnhanceAudioResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("studiovoice")

app = FastAPI(title="StudioVoice")
# ponytail: OS temp dir so Windows (no /tmp) and Linux both work; override with AUDIO_JOBS_DIR env for persistent storage.
UPLOAD_ROOT = Path(os.environ.get("AUDIO_JOBS_DIR", str(Path(tempfile.gettempdir()) / "audio_jobs")))
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Hardcoded for personal use only — do not push to a public repo
NVIDIA_API_KEY = "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"
NVCF_GRPC_TARGET = "grpc.nvcf.nvidia.com:443"
NVCF_FUNCTION_ID = "3f0aeba3-6d91-4465-b8cc-cc2aef355186"

MAX_DIRECT_BYTES = 32 * 1024 * 1024
MAX_DIRECT_DURATION_SECONDS = 360.0
CHUNK_SEGMENT_SECONDS = 300
CROSSFADE_SECONDS = 0.005
MAX_CONCURRENT_REQUESTS = 2
NIM_TIMEOUT_SECONDS = 300.0
JOB_CLEANUP_SECONDS = 24 * 60 * 60
TRANSIENT_BACKOFF_SECONDS = (1, 2, 4)
PLACEHOLDER_API_KEY = "nvapi-PUT_YOUR_REAL_KEY_HERE"
FORMAT_SPECS = {
    "wav": {"ext": "wav", "media_type": "audio/wav"},
    "mp3": {"ext": "mp3", "media_type": "audio/mpeg"},
    "aac": {"ext": "m4a", "media_type": "audio/mp4"},
    "mp4": {"ext": "mp4", "media_type": "audio/mp4"},
}


def ffmpeg_version() -> str:
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed or not on PATH.") from exc
    return result.stdout.splitlines()[0].strip() if result.stdout else "ffmpeg"


def ffprobe_duration_seconds(file_path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="ffprobe is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail="Unable to read audio duration.") from exc

    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Unable to read audio duration.") from exc


async def cleanup_job_later(job_dir: Path) -> None:
    try:
        await asyncio.sleep(JOB_CLEANUP_SECONDS)
        shutil.rmtree(job_dir, ignore_errors=True)
    except asyncio.CancelledError:
        return


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "ffmpeg": ffmpeg_version(),
        "nim_key_set": NVIDIA_API_KEY != PLACEHOLDER_API_KEY,
        "transport": "grpc",
        "grpc_target": NVCF_GRPC_TARGET,
    }


async def save_upload(upload: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as target:
        shutil.copyfileobj(upload.file, target)


def compute_job_id(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:32]


def convert_to_wav(input_path: Path, output_path: Path) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-ar",
        "48000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("ffmpeg convert failed: %s", exc.stderr.strip() if exc.stderr else "<no stderr>")
        raise HTTPException(status_code=400, detail="Unable to convert uploaded audio to WAV.") from exc


def split_wav_into_chunks(input_path: Path, job_dir: Path) -> list[Path]:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-f",
        "segment",
        "-segment_time",
        str(CHUNK_SEGMENT_SECONDS),
        "-ar",
        "48000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(job_dir / "chunk_%03d.wav"),
    ]
    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=400, detail="Unable to split audio into chunks.") from exc

    chunk_paths = sorted(job_dir.glob("chunk_*.wav"))
    if not chunk_paths:
        raise HTTPException(status_code=400, detail="Audio chunking produced no output.")
    return chunk_paths


def iter_audio_requests(chunk_path: Path):
    with chunk_path.open("rb") as source:
        while data := source.read(64 * 1024):
            yield EnhanceAudioRequest(audio_stream_data=data)


def enhance_chunk_sync(chunk_path: Path, output_path: Path) -> None:
    metadata = (
        ("authorization", f"Bearer {NVIDIA_API_KEY}"),
        ("function-id", NVCF_FUNCTION_ID),
    )
    channel = grpc.secure_channel(NVCF_GRPC_TARGET, grpc.ssl_channel_credentials())
    try:
        enhance_audio = channel.stream_stream(
            "/nvidia.ai4m.studiovoice.v1.StudioVoice/EnhanceAudio",
            request_serializer=EnhanceAudioRequest.SerializeToString,
            response_deserializer=EnhanceAudioResponse.FromString,
        )
        bytes_written = 0
        with output_path.open("wb") as destination:
            for response in enhance_audio(
                iter_audio_requests(chunk_path),
                metadata=metadata,
                timeout=NIM_TIMEOUT_SECONDS,
                wait_for_ready=True,
            ):
                if response.HasField("audio_stream_data"):
                    destination.write(response.audio_stream_data)
                    bytes_written += len(response.audio_stream_data)

        if not bytes_written:
            raise HTTPException(status_code=502, detail="NVIDIA Studio Voice returned no audio data.")
    finally:
        channel.close()


async def enhance_chunk(
    semaphore: asyncio.Semaphore,
    chunk_path: Path,
    job_id: str,
    chunk_number: int,
    chunk_total: int,
) -> Path:
    output_path = chunk_path.with_name(f"{chunk_path.stem}_enhanced.wav")

    for attempt, backoff_seconds in enumerate((0, *TRANSIENT_BACKOFF_SECONDS), start=1):
        start_time = time.perf_counter()
        try:
            async with semaphore:
                await asyncio.to_thread(enhance_chunk_sync, chunk_path, output_path)
            elapsed = time.perf_counter() - start_time
            logger.info("Job %s: enhanced chunk %d/%d in %.2fs", job_id, chunk_number, chunk_total, elapsed)
            return output_path
        except grpc.RpcError as exc:
            if exc.code() not in {
                grpc.StatusCode.DEADLINE_EXCEEDED,
                grpc.StatusCode.INTERNAL,
                grpc.StatusCode.RESOURCE_EXHAUSTED,
                grpc.StatusCode.UNAVAILABLE,
            }:
                raise HTTPException(
                    status_code=502,
                    detail=f"NVIDIA Studio Voice request failed: {exc.code().name}.",
                ) from exc
            if attempt >= 4:
                break
            await asyncio.sleep(backoff_seconds)
        except HTTPException:
            raise

    raise HTTPException(status_code=502, detail="NVIDIA Studio Voice request failed after retries.")


def build_crossfaded_output(enhanced_chunks: list[Path], final_path: Path) -> None:
    if len(enhanced_chunks) == 1:
        shutil.copyfile(enhanced_chunks[0], final_path)
        return

    ffmpeg_inputs: list[str] = []
    filter_parts: list[str] = []
    previous_label = "0:a"

    for index, chunk_path in enumerate(enhanced_chunks):
        ffmpeg_inputs.extend(["-i", str(chunk_path)])
        if index == 0:
            continue
        output_label = f"x{index}"
        filter_parts.append(
            f"[{previous_label}][{index}:a]acrossfade=d={CROSSFADE_SECONDS}:c1=tri:c2=tri[{output_label}]"
        )
        previous_label = output_label

    command = [
        "ffmpeg",
        "-y",
        *ffmpeg_inputs,
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        f"[{previous_label}]",
        "-c:a",
        "pcm_s16le",
        str(final_path),
    ]
    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail="Unable to reassemble enhanced audio chunks.") from exc


def transcode_final_audio(final_wav: Path, cache_dir: Path, job_id: str, download_format: str) -> Path:
    spec = FORMAT_SPECS[download_format]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_path = cache_dir / f"studiovoice_{job_id}.{spec['ext']}"

    if cached_path.exists():
        logger.info("Job %s: served cached %s", job_id, download_format)
        return cached_path

    if download_format == "wav":
        shutil.copyfile(final_wav, cached_path)
    else:
        command = ["ffmpeg", "-y", "-i", str(final_wav)]
        if download_format == "mp3":
            command.extend(["-c:a", "libmp3lame", "-q:a", "0"])
        elif download_format == "aac":
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        elif download_format == "mp4":
            command.extend(["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"])
        command.append(str(cached_path))
        try:
            subprocess.run(command, capture_output=True, text=True, check=True)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail="ffmpeg is not installed or not on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            raise HTTPException(status_code=500, detail="Unable to transcode enhanced audio.") from exc

    logger.info("Job %s: transcoded to %s (%d bytes)", job_id, download_format, cached_path.stat().st_size)
    return cached_path


@app.post("/api/enhance")
async def enhance_audio(
    file: UploadFile = File(...),
    download_format: str = Query(default="wav", alias="format"),
) -> FileResponse:
    if download_format not in FORMAT_SPECS:
        raise HTTPException(status_code=400, detail="Invalid format. Use one of: wav, mp3, aac, mp4.")

    original_name = Path(file.filename or "upload").name
    upload_id = uuid.uuid4().hex
    job_dir = UPLOAD_ROOT / upload_id
    job_dir.mkdir(parents=True, exist_ok=True)
    asyncio.create_task(cleanup_job_later(job_dir))

    input_path = job_dir / original_name
    wav_path = job_dir / "converted.wav"

    try:
        await save_upload(file, input_path)
        job_id = compute_job_id(input_path)
        job_dir = UPLOAD_ROOT / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        asyncio.create_task(cleanup_job_later(job_dir))

        stable_input_path = job_dir / original_name
        stable_wav_path = job_dir / "converted.wav"
        shutil.copyfile(input_path, stable_input_path)
        input_path = stable_input_path
        wav_path = stable_wav_path

        convert_to_wav(input_path, wav_path)

        size_bytes = wav_path.stat().st_size
        duration_seconds = ffprobe_duration_seconds(wav_path)
        size_mb = size_bytes / (1024 * 1024)

        if size_bytes <= MAX_DIRECT_BYTES and duration_seconds <= MAX_DIRECT_DURATION_SECONDS:
            chunk_paths = [wav_path]
        else:
            chunk_paths = split_wav_into_chunks(wav_path, job_dir)
        logger.info("Job %s: split into %d chunks", job_id, len(chunk_paths))

        chunk_list_path = job_dir / "chunk_list.txt"
        with chunk_list_path.open("w", encoding="utf-8", newline="\n") as handle:
            for chunk_path in chunk_paths:
                handle.write(f"file '{chunk_path.name}'\n")

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        enhanced_chunks = await asyncio.gather(
            *[
                enhance_chunk(semaphore, chunk_path, job_id, index + 1, len(chunk_paths))
                for index, chunk_path in enumerate(chunk_paths)
            ]
        )

        final_path = job_dir / "final.wav"
        build_crossfaded_output(list(enhanced_chunks), final_path)
        logger.info("Job %s: reassembled", job_id)

        cache_dir = job_dir / "cache"
        output_path = transcode_final_audio(final_path, cache_dir, job_id, download_format)
        spec = FORMAT_SPECS[download_format]
        download_name = f"studiovoice_{job_id}.{spec['ext']}"
        headers = {"Content-Disposition": f'attachment; filename="{download_name}"'}

        return FileResponse(
            path=output_path,
            media_type=spec["media_type"],
            filename=download_name,
            headers=headers,
        )
    finally:
        await file.close()


BASE_DIR = Path(__file__).resolve().parent  # ponytail: absolute dirs so the server works no matter the launch cwd; upgrade path: env override if ever containerized.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/", StaticFiles(directory=str(BASE_DIR / "frontend"), html=True), name="frontend")
