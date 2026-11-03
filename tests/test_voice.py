import io
import json
import math
import struct
import wave
from datetime import datetime

import httpx
import pytest

from signalhq.connectors.audio import turns_to_feedback
from signalhq.llm.errors import PermanentError, TransientError
from signalhq.llm.gateway import LLMGateway
from signalhq.llm.offline import OfflineHeuristicTransport
from signalhq.llm.voice import HttpVoiceTransport, build_request, inspect_wav, parse_transcript
from signalhq.repositories import LedgerRepo
from tests.conftest import make_settings

RESPONSE = {
    "sessionId": "s-1",
    "transcript": "Hi. My downloads vanished after the update and I want a refund.",
    "audioDurationMs": 2000,
    "turns": [
        {"turnId": "0", "startMs": 0, "endMs": 400, "transcript": "Hi.", "speaker": "A"},
        {"turnId": "1", "startMs": 400, "endMs": 2000,
         "transcript": "My downloads vanished after the update and I want a refund.", "speaker": "B"},
    ],
}


def tone_wav(seconds=1.0, rate=24000, channels=1, width=2) -> bytes:
    """A short sine tone, generated in memory as a test fixture."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) * channels
            for i in range(int(rate * seconds))
        )
        w.writeframes(frames)
    return buf.getvalue()


def test_wav_checks():
    info = inspect_wav(tone_wav())
    assert (info.channels, info.sample_rate, info.sample_width_bytes) == (1, 24000, 2)
    assert info.duration_s == pytest.approx(1.0)
    with pytest.raises(ValueError, match="mono"):
        inspect_wav(tone_wav(channels=2))
    with pytest.raises(ValueError, match="16 or 24 kHz"):
        inspect_wav(tone_wav(rate=44100, seconds=0.1))
    with pytest.raises(ValueError, match="RIFF"):
        inspect_wav(b"not audio")


def test_multipart_request_has_request_and_audio_parts():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["ctype"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(200, json=RESPONSE)

    transport = HttpVoiceTransport("https://api.example/v1/asr/transcribe", "k",
                                   client=httpx.Client(transport=httpx.MockTransport(handler)))
    wav = tone_wav(0.2)
    out = transport.transcribe(wav, build_request("muse-voice-transcribe-1.0", "DIARIZATION"), 5)
    assert out == RESPONSE
    assert seen["auth"] == "Bearer k"
    assert seen["ctype"].startswith("multipart/form-data")
    assert b'name="request"' in seen["body"] and b'name="audio"' in seen["body"]
    assert b'"mode": "DIARIZATION"' in seen["body"]
    assert wav[:44] in seen["body"]  # the RIFF header travels unchanged


@pytest.mark.parametrize("status,error", [(429, TransientError), (502, TransientError), (400, PermanentError)])
def test_http_errors_map_to_taxonomy(status, error):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text="no")))
    with pytest.raises(error):
        HttpVoiceTransport("https://x", "k", client=client).transcribe(tone_wav(0.1), {}, 5)


def test_parse_and_turns_become_call_items():
    transcript = parse_transcript(RESPONSE)
    assert transcript.audio_duration_ms == 2000 and len(transcript.turns) == 2
    items = list(turns_to_feedback(transcript, recording_id="rec1", recorded_at=datetime(2026, 10, 1)))
    assert len(items) == 1  # "Hi." is too short to be feedback
    assert items[0].source == "call" and items[0].external_id == "rec1:1"
    assert items[0].meta["speaker"] == "B"
    assert items[0].created_at == datetime(2026, 10, 1, 0, 0, 0, 400000)


class FakeVoice:
    def __init__(self):
        self.calls = 0

    def transcribe(self, wav, request, timeout_s):
        self.calls += 1
        return RESPONSE


def test_gateway_transcribe_is_cached_and_priced(db, tmp_path):
    settings = make_settings(tmp_path, offline=False, model_api_key="k")
    voice = FakeVoice()
    gw = LLMGateway(db, settings, OfflineHeuristicTransport(), voice)
    wav = tone_wav(2.0)
    assert gw.transcribe(wav, {"mode": "DIARIZATION"}) == RESPONSE
    assert gw.transcribe(wav, {"mode": "DIARIZATION"}) == RESPONSE
    assert voice.calls == 1
    with db.read() as s:
        summary = LedgerRepo(s).summary()
    assert summary["purposes"]["transcribe"]["cache_hits"] == 1
    assert summary["cost_usd"] == pytest.approx(2.0 / 3600 * 0.18)


def test_saved_transcript_round_trips(tmp_path):
    path = tmp_path / "call.transcript.json"
    path.write_text(json.dumps(RESPONSE))
    assert parse_transcript(json.loads(path.read_text())).session_id == "s-1"
