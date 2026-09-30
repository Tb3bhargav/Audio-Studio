"""Exercise NVIDIA Studio Voice's NVCF gRPC preview endpoint with silent WAV audio."""
from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import grpc

from main import (
    NVIDIA_API_KEY,
    NIM_TIMEOUT_SECONDS,
    NVCF_FUNCTION_ID,
    NVCF_GRPC_TARGET,
    EnhanceAudioRequest,
    EnhanceAudioResponse,
)


def requests_for(path: Path):
    with path.open("rb") as source:
        while data := source.read(64 * 1024):
            yield EnhanceAudioRequest(audio_stream_data=data)


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "silence.wav"
        with wave.open(str(input_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48_000)
            audio.writeframes(b"\0\0" * 48_000)

        metadata = (
            ("authorization", f"Bearer {NVIDIA_API_KEY}"),
            ("function-id", NVCF_FUNCTION_ID),
        )
        with grpc.secure_channel(NVCF_GRPC_TARGET, grpc.ssl_channel_credentials()) as channel:
            enhance_audio = channel.stream_stream(
                "/nvidia.ai4m.studiovoice.v1.StudioVoice/EnhanceAudio",
                request_serializer=EnhanceAudioRequest.SerializeToString,
                response_deserializer=EnhanceAudioResponse.FromString,
            )
            output = b"".join(
                response.audio_stream_data
                for response in enhance_audio(
                    requests_for(input_path),
                    metadata=metadata,
                    timeout=NIM_TIMEOUT_SECONDS,
                    wait_for_ready=True,
                )
                if response.HasField("audio_stream_data")
            )

    if len(output) <= 44 or not output.startswith(b"RIFF"):
        raise RuntimeError("Studio Voice returned an invalid WAV response.")
    print(f"SUCCESS: received {len(output)} bytes of enhanced WAV audio.")


if __name__ == "__main__":
    main()
