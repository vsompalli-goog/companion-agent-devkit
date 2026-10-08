"""Voice Testing Environment for Companion Agent (Section 7.6: FR-5.1, FR-5.2, FR-5.3, FR-5.5).

Capabilities:
  - FR-5.1: Synthesizes caller and human-agent audio from a transcript using Google Cloud
    Text-to-Speech v1 REST with STOCK VOICES ONLY (strictly forbids voice cloning) and
    labels every output artifact as derived from customer data (`derivedFromCustomerData: true`).
  - FR-5.2 & FR-5.3: Configurable per-customer streaming settings (codec, sample rate,
    chunk size/ms, cadence ms, network jitter ms, and endpointing config) for replaying
    synthetic audio in the customer's designated GCP project.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import datetime
import io
import json
from pathlib import Path
import random
import time
from typing import Any, Optional
import wave

from aa_devkit.client import CompanionAgentRestClient

# Strictly stock voices only (no voice cloning allowed per FR-5.1)
ALLOWED_STOCK_VOICES = {
    "en-US-Journey-F",
    "en-US-Journey-D",
    "en-US-Journey-O",
    "en-US-Neural2-F",
    "en-US-Neural2-D",
    "en-US-Standard-C",
    "en-US-Standard-D",
    "en-US-Chirp3-HD-Aoede",
    "en-US-Chirp3-HD-Puck",
}


@dataclass
class StreamingProfile:
    """Per-customer configurable voice streaming settings (FR-5.3)."""

    codec: str = "AUDIO_ENCODING_LINEAR_16"  # or "AUDIO_ENCODING_MULAW"
    sample_rate_hz: int = 24000
    chunk_ms: int = 100
    cadence_ms: int = 100
    jitter_ms: int = 0
    single_utterance_endpointing: bool = False


def validate_stock_voice(voice_name: str) -> str:
    """Enforces FR-5.1: Only Google Cloud TTS stock voices are permitted (no voice cloning)."""
    clean = (voice_name or "").strip()
    if clean not in ALLOWED_STOCK_VOICES:
        raise ValueError(
            f"Voice '{clean}' is not in the approved stock voice catalog (FR-5.1 forbids voice cloning). "
            f"Allowed stock voices: {sorted(ALLOWED_STOCK_VOICES)}"
        )
    return clean


def synthesize_transcript_audio(
    client: CompanionAgentRestClient,
    turns: list[dict[str, Any]],
    output_dir: str | Path,
    caller_voice: str = "en-US-Journey-F",
    agent_voice: str = "en-US-Journey-D",
    streaming_profile: Optional[StreamingProfile] = None,
) -> dict[str, Any]:
    """Synthesizes turn-by-turn WAV audio from a transcript using stock voices only (FR-5.1)."""
    validate_stock_voice(caller_voice)
    validate_stock_voice(agent_voice)
    prof = streaming_profile or StreamingProfile()

    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    tts_encoding = "MULAW" if "MULAW" in prof.codec.upper() else "LINEAR16"
    synthesized_turns: list[dict[str, Any]] = []

    for idx, turn in enumerate(turns, start=1):
        role = str(turn.get("role") or "END_USER").upper()
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        voice_name = caller_voice if role == "END_USER" else agent_voice

        tts_req = {
            "input": {"text": text},
            "voice": {"languageCode": "en-US", "name": voice_name},
            "audioConfig": {
                "audioEncoding": tts_encoding,
                "sampleRateHertz": prof.sample_rate_hz,
            },
        }
        resp = client.request(
            "POST",
            "https://texttospeech.googleapis.com/v1/text:synthesize",
            json_body=tts_req,
        )
        audio_b64 = resp.get("audioContent") or ""
        audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""

        wav_filename = f"turn_{idx:03d}_{role.lower()}.wav"
        wav_path = out_dir / wav_filename
        if audio_bytes.startswith(b"RIFF"):
            wav_path.write_bytes(audio_bytes)
        else:
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(1 if tts_encoding == "MULAW" else 2)
                wf.setframerate(prof.sample_rate_hz)
                wf.writeframes(audio_bytes)

        synthesized_turns.append(
            {
                "turn_id": turn.get("turn_id", idx),
                "role": role,
                "text": text,
                "stockVoice": voice_name,
                "voiceCloningUsed": False,
                "derivedFromCustomerData": True,
                "audioFile": wav_filename,
            }
        )

    manifest = {
        "schemaVersion": "1.0",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "derivedFromCustomerData": True,
        "voiceCloningUsed": False,
        "privacyNotice": "Synthetic audio derived from customer transcript using stock TTS voices only (FR-5.1).",
        "projectId": client.project_id,
        "streamingProfile": asdict(prof),
        "turns": synthesized_turns,
    }
    (out_dir / "voice_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def chunk_audio_bytes(
    raw_pcm: bytes,
    sample_rate_hz: int = 24000,
    bytes_per_sample: int = 2,
    chunk_ms: int = 100,
) -> list[bytes]:
    """Splits raw PCM bytes into fixed-duration chunks matching customer streaming settings (FR-5.3)."""
    if not raw_pcm:
        return []
    bytes_per_chunk = max(
        bytes_per_sample,
        int((sample_rate_hz * bytes_per_sample * max(10, chunk_ms)) / 1000),
    )
    return [raw_pcm[i : i + bytes_per_chunk] for i in range(0, len(raw_pcm), bytes_per_chunk)]


def replay_voice_manifest(
    client: CompanionAgentRestClient,
    manifest_dir: str | Path,
    profile_name: str,
    simulate_pacing: bool = False,
) -> dict[str, Any]:
    """Replays synthesized audio turns in the customer's project via Dialogflow :analyzeContent (FR-5.2)."""
    base_dir = Path(manifest_dir).expanduser().resolve()
    manifest = json.loads((base_dir / "voice_manifest.json").read_text(encoding="utf-8"))
    prof_dict = manifest.get("streamingProfile") or {}
    prof = StreamingProfile(**prof_dict)

    parent = f"projects/{client.project_id}/locations/{client.location}"
    conv_resp = client.request(
        "POST",
        f"v2beta1/{parent}/conversations",
        json_body={"conversationProfile": profile_name},
    )
    conv_name = conv_resp["name"]
    end_user_part = client.request(
        "POST", f"v2beta1/{conv_name}/participants", json_body={"role": "END_USER"}
    )["name"]
    human_agent_part = client.request(
        "POST", f"v2beta1/{conv_name}/participants", json_body={"role": "HUMAN_AGENT"}
    )["name"]

    replay_results: list[dict[str, Any]] = []
    for t in manifest.get("turns") or []:
        wav_path = base_dir / t["audioFile"]
        raw_bytes = wav_path.read_bytes()
        # Strip WAV header if present when sending raw PCM
        if raw_bytes.startswith(b"RIFF"):
            with wave.open(io.BytesIO(raw_bytes), "rb") as wf:
                pcm_bytes = wf.readframes(wf.getnframes())
        else:
            pcm_bytes = raw_bytes

        chunks = chunk_audio_bytes(
            pcm_bytes,
            sample_rate_hz=prof.sample_rate_hz,
            bytes_per_sample=1 if "MULAW" in prof.codec.upper() else 2,
            chunk_ms=prof.chunk_ms,
        )

        if simulate_pacing and chunks:
            for _ in chunks[:-1]:
                jitter = random.uniform(-prof.jitter_ms, prof.jitter_ms) if prof.jitter_ms > 0 else 0.0
                sleep_sec = max(0.0, (prof.cadence_ms + jitter) / 1000.0)
                time.sleep(min(sleep_sec, 0.05))

        part = human_agent_part if t.get("role") == "HUMAN_AGENT" else end_user_part
        t0 = time.perf_counter()
        resp = client.request(
            "POST",
            f"v2beta1/{part}:analyzeContent",
            json_body={
                "audioInput": {
                    "config": {
                        "audioEncoding": prof.codec,
                        "sampleRateHertz": prof.sample_rate_hz,
                        "languageCode": "en-US",
                        "singleUtterance": prof.single_utterance_endpointing,
                    },
                    "audio": base64.b64encode(pcm_bytes).decode("ascii"),
                }
            },
            timeout=45,
        )
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        replay_results.append(
            {
                "turn_id": t.get("turn_id"),
                "role": t.get("role"),
                "chunks_framed": len(chunks),
                "analyze_audio_ms": elapsed_ms,
                "recognized_transcript": (resp.get("message") or {}).get("content", ""),
            }
        )

    return {
        "conversation_name": conv_name,
        "derivedFromCustomerData": True,
        "streamingProfile": asdict(prof),
        "turns_replayed": len(replay_results),
        "results": replay_results,
    }
