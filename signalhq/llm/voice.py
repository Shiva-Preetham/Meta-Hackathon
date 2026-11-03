"""Muse Voice Transcribe client: WAV checks, conversion, multipart request, response parsing.

Verified facts (CLAUDE.md §3): ``POST {base}/asr/transcribe`` as ``multipart/form-data``
with a ``request`` part (JSON settings) and an ``audio`` part (WAV); bearer auth; audio
must be RIFF/WAVE, mono, 16-bit PCM at 16 or 24 kHz, at most 32 MB and 10 minutes; the
response has ``sessionId``, ``transcript``, ``audioDurationMs`` and ``turns[]`` with
``turnId, startMs, endMs, transcript, speaker``.

NOT verified: the JSON field names inside the ``request`` part. The docs pages could not be
reached from the build environment, so ``build_request`` uses best-guess names that are
easy to change in one place. See PROGRESS.md ("Needs you").
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from signalhq.llm.errors import PermanentError, TransientError

MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_AUDIO_SECONDS = 10 * 60
ALLOWED_RATES = (16_000, 24_000)


@dataclass(frozen=True)
class WavInfo:
    channels: int
    sample_width_bytes: int
    sample_rate: int
    duration_s: float


@dataclass(frozen=True)
class Turn:
    turn_id: str
    start_ms: int
    end_ms: int
    transcript: str
    speaker: str | None


@dataclass(frozen=True)
class Transcript:
    session_id: str
    transcript: str
    audio_duration_ms: int
    turns: list[Turn]


def inspect_wav(data: bytes) -> WavInfo:
    """Validate the audio format rules. Raises ``ValueError`` with the reason."""
    if len(data) > MAX_BODY_BYTES:
        raise ValueError(f"audio is {len(data)} bytes; the limit is {MAX_BODY_BYTES}")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            info = WavInfo(
                channels=wav.getnchannels(),
                sample_width_bytes=wav.getsampwidth(),
                sample_rate=wav.getframerate(),
                duration_s=wav.getnframes() / float(wav.getframerate()),
            )
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"not a RIFF/WAVE PCM file: {exc}") from exc
    if info.channels != 1:
        raise ValueError(f"audio must be mono, got {info.channels} channels")
    if info.sample_width_bytes != 2:
        raise ValueError(f"audio must be 16-bit PCM, got {8 * info.sample_width_bytes}-bit")
    if info.sample_rate not in ALLOWED_RATES:
        raise ValueError(f"sample rate must be 16 or 24 kHz, got {info.sample_rate} Hz")
    if info.duration_s > MAX_AUDIO_SECONDS:
        raise ValueError(f"audio is {info.duration_s:.0f} s; the limit is {MAX_AUDIO_SECONDS} s")
    return info


def convert_to_wav(src: Path, dst: Path) -> Path:
    """``ffmpeg -i in.m4a -ac 1 -ar 24000 -sample_fmt s16 out.wav`` (CLAUDE.md §3)."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed; it is needed to convert audio to WAV")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "24000", "-sample_fmt", "s16", str(dst)]
    subprocess.run(cmd, check=True)
    return dst


def build_request(model: str, mode: str, language: str = "en") -> dict[str, Any]:
    """Settings for the ``request`` part. Field names are UNVERIFIED (see module doc)."""
    return {"model": model, "mode": mode, "language": language}


def parse_transcript(payload: dict[str, Any]) -> Transcript:
    turns = [
        Turn(
            turn_id=str(t.get("turnId", i)),
            start_ms=int(t.get("startMs", 0)),
            end_ms=int(t.get("endMs", 0)),
            transcript=str(t.get("transcript", "")).strip(),
            speaker=None if t.get("speaker") is None else str(t.get("speaker")),
        )
        for i, t in enumerate(payload.get("turns") or [])
    ]
    return Transcript(
        session_id=str(payload.get("sessionId", "")),
        transcript=str(payload.get("transcript", "")),
        audio_duration_ms=int(payload.get("audioDurationMs", 0)),
        turns=turns,
    )


class VoiceTransport(Protocol):
    def transcribe(self, wav: bytes, request: dict[str, Any], timeout_s: float) -> dict[str, Any]: ...


class HttpVoiceTransport:
    """Multipart POST with httpx. ``client`` is injectable so tests can mock the network."""

    def __init__(self, url: str, api_key: str, client: Any | None = None) -> None:
        import httpx

        self.url = url
        self.api_key = api_key
        self._client = client or httpx.Client()

    def transcribe(self, wav: bytes, request: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        import httpx

        files = {
            "request": (None, json.dumps(request), "application/json"),
            "audio": ("audio.wav", wav, "audio/wav"),
        }
        try:
            response = self._client.post(
                self.url,
                files=files,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=timeout_s,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = response.headers.get("retry-after")
            raise TransientError(
                f"HTTP {response.status_code}: {response.text[:300]}",
                retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
                status=response.status_code,
            )
        if response.status_code >= 400:
            raise PermanentError(f"HTTP {response.status_code}: {response.text[:500]}")
        return response.json()
