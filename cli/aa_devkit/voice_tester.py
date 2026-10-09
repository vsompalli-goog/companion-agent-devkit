"""Voice Testing Environment & Telephony DSP Conditioning (Section 7.6: FR-5.1, FR-5.2, FR-5.3, FR-5.5).

Capabilities:
  - FR-5.1: Synthesizes caller and human-agent audio from a transcript using Google Cloud
    Text-to-Speech v1 REST with STOCK VOICES ONLY (strictly forbids voice cloning) and
    labels every output artifact as derived from customer data (`derivedFromCustomerData: true`).
  - Prosody Normalizer & Sentence Chunker:
      * `prepare_text_for_natural_tts`: Strips stage directions, markdown, and wrapping quotes.
      * `split_text_into_tts_chunks`: Splits long utterances on sentence/clause boundaries (`<=200` chars).
  - Pure-Python Telephony DSP Audio Conditioning:
      * `apply_telephone_bandpass`: G.711 PSTN bandpass filter (`190 Hz – 3100 Hz`) + soft-knee saturation.
      * `overlay_ambient_noise`: Mixes `call_center` or `white_noise` background profiles.
      * `generate_transfer_ringtone_pcm`: Dual-tone `440 Hz + 480 Hz` PSTN ringback tone generator.
      * `interleave_stereo_pcm`: Dual-channel stereo WAV export (Caller = Left, Agent = Right).
  - FR-5.2 & FR-5.3: Configurable per-customer streaming settings (codec, sample rate,
    chunk size/ms, cadence ms, network jitter ms, and endpointing config) for replaying
    synthetic audio in the customer's designated GCP project.
"""

from __future__ import annotations

from array import array
import base64
from dataclasses import asdict, dataclass
import datetime
import io
import json
import math
from pathlib import Path
import random
import re
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

ALLOWED_NOISE_PROFILES = ("none", "call_center", "white_noise")


@dataclass
class StreamingProfile:
    """Per-customer configurable voice streaming settings (FR-5.3)."""

    codec: str = "AUDIO_ENCODING_LINEAR_16"  # or "AUDIO_ENCODING_MULAW"
    sample_rate_hz: int = 24000
    chunk_ms: int = 100
    cadence_ms: int = 100
    jitter_ms: int = 0
    single_utterance_endpointing: bool = False
    telephone_filter: bool = False
    noise_profile: str = "none"
    stereo: bool = False
    include_transfer_ring: bool = False


def validate_stock_voice(voice_name: str) -> str:
    """Enforces FR-5.1: Only Google Cloud TTS stock voices are permitted (no voice cloning)."""
    clean = (voice_name or "").strip()
    if clean not in ALLOWED_STOCK_VOICES:
        raise ValueError(
            f"Voice '{clean}' is not in the approved stock voice catalog (FR-5.1 forbids voice cloning). "
            f"Allowed stock voices: {sorted(ALLOWED_STOCK_VOICES)}"
        )
    return clean


def prepare_text_for_natural_tts(text: str) -> str:
    """Cleans stage directions, markdown, and formatting artifacts before TTS synthesis."""
    if not text:
        return ""
    cleaned = text.strip()
    # Strip wrapping quotation marks
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ('"', "'", "\u201c", "\u201d"):
        cleaned = cleaned[1:-1].strip()
    # Remove bracketed or parenthesized stage directions like "(polite tone)" or "[sighs]"
    cleaned = re.sub(r"\(\s*(?:polite|confused|frustrated|rushed|adversarial)\s+tone\s*\)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\[(?:sighs|pauses|laughs|clears throat|coughing|inaudible)[^\]]*\]", "", cleaned, flags=re.I)
    # Strip markdown bold/italics/code backticks
    cleaned = re.sub(r"[*_`]+", "", cleaned)
    # Collapse multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def split_text_into_tts_chunks(text: str, max_chars: int = 200) -> list[str]:
    """Splits a cleaned utterance into sentence/clause-aligned chunks `<= max_chars`."""
    cleaned = prepare_text_for_natural_tts(text)
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    chunks: list[str] = []
    current = ""

    for sent in sentences:
        if not sent:
            continue
        if len(sent) > max_chars:
            # Split long sentence on clause boundaries (, ; :) or words
            clauses = re.split(r"(?<=[,;:])\s+", sent)
            for cl in clauses:
                if len(current) + len(cl) + 1 <= max_chars:
                    current = f"{current} {cl}".strip() if current else cl
                else:
                    if current:
                        chunks.append(current)
                    if len(cl) <= max_chars:
                        current = cl
                    else:
                        words = cl.split(" ")
                        current = ""
                        for w in words:
                            if len(current) + len(w) + 1 <= max_chars:
                                current = f"{current} {w}".strip() if current else w
                            else:
                                if current:
                                    chunks.append(current)
                                current = w
        elif len(current) + len(sent) + 1 <= max_chars:
            current = f"{current} {sent}".strip() if current else sent
        else:
            if current:
                chunks.append(current)
            current = sent

    if current:
        chunks.append(current)
    return chunks


def _pcm16_to_samples(pcm_bytes: bytes) -> array:
    """Unpacks little-endian 16-bit signed PCM bytes into an `array('h')`."""
    even_len = len(pcm_bytes) - (len(pcm_bytes) % 2)
    samples = array("h")
    samples.frombytes(pcm_bytes[:even_len])
    return samples


def _samples_to_pcm16(samples: array) -> bytes:
    return samples.tobytes()


def apply_telephone_bandpass(
    pcm_bytes: bytes,
    sample_rate_hz: int = 24000,
    low_cut_hz: float = 190.0,
    high_cut_hz: float = 3100.0,
) -> bytes:
    """Applies a G.711 PSTN telephone bandpass filter (`190 Hz – 3100 Hz`) and soft-knee saturation."""
    if not pcm_bytes or len(pcm_bytes) < 4:
        return pcm_bytes

    samples = _pcm16_to_samples(pcm_bytes)
    dt = 1.0 / max(1, sample_rate_hz)

    # First-order RC high-pass filter (~190 Hz)
    rc_hp = 1.0 / (2.0 * math.pi * low_cut_hz)
    alpha_hp = rc_hp / (rc_hp + dt)

    # First-order RC low-pass filter (~3100 Hz)
    rc_lp = 1.0 / (2.0 * math.pi * high_cut_hz)
    alpha_lp = dt / (rc_lp + dt)

    out = array("h", [0] * len(samples))
    hp_prev_y = 0.0
    hp_prev_x = 0.0
    lp_prev_y = 0.0

    for i, x_int in enumerate(samples):
        x = float(x_int) / 32768.0
        # High-pass step
        hp_y = alpha_hp * (hp_prev_y + x - hp_prev_x)
        hp_prev_x = x
        hp_prev_y = hp_y
        # Low-pass step
        lp_y = lp_prev_y + alpha_lp * (hp_y - lp_prev_y)
        lp_prev_y = lp_y
        # Soft-knee telephony saturation
        saturated = math.tanh(lp_y * 1.35) * 0.92
        clamped = max(-32767, min(32767, int(saturated * 32767.0)))
        out[i] = clamped

    return _samples_to_pcm16(out)


def overlay_ambient_noise(
    pcm_bytes: bytes,
    noise_profile: str = "call_center",
    noise_level: float = 0.025,
    sample_rate_hz: int = 24000,
    seed: int = 42,
) -> bytes:
    """Mixes synthetic background noise (`call_center` or `white_noise`) into 16-bit PCM audio."""
    prof = (noise_profile or "none").strip().lower()
    if prof == "none" or not pcm_bytes or len(pcm_bytes) < 4:
        return pcm_bytes

    samples = _pcm16_to_samples(pcm_bytes)
    rng = random.Random(seed)
    out = array("h", [0] * len(samples))

    babble_state = 0.0
    for i, x_int in enumerate(samples):
        white = rng.uniform(-1.0, 1.0)
        if prof == "call_center":
            # Low-passed speech-band babble + subtle hum modulation
            babble_state = 0.88 * babble_state + 0.12 * white
            hum = 0.25 * math.sin(2.0 * math.pi * 120.0 * (i / max(1, sample_rate_hz)))
            noise_val = (babble_state + hum) * noise_level * 32767.0
        else:
            noise_val = white * noise_level * 32767.0

        mixed = max(-32767, min(32767, int(x_int + noise_val)))
        out[i] = mixed

    return _samples_to_pcm16(out)


def generate_transfer_ringtone_pcm(
    duration_sec: float = 2.0,
    sample_rate_hz: int = 24000,
) -> bytes:
    """Generates a North American PSTN dual-tone (`440 Hz + 480 Hz`) ringback PCM buffer."""
    total_samples = max(1, int(duration_sec * sample_rate_hz))
    out = array("h", [0] * total_samples)
    for i in range(total_samples):
        t = i / max(1, sample_rate_hz)
        # Standard cadence: active during first 1.5s, silence for 0.5s
        if (t % 2.0) <= 1.5:
            val = 0.18 * (math.sin(2.0 * math.pi * 440.0 * t) + math.sin(2.0 * math.pi * 480.0 * t))
            out[i] = max(-32767, min(32767, int(val * 32767.0)))
        else:
            out[i] = 0
    return _samples_to_pcm16(out)


def interleave_stereo_pcm(left_pcm: bytes, right_pcm: bytes) -> bytes:
    """Interleaves two 16-bit mono PCM buffers into a 2-channel stereo 16-bit PCM buffer (Caller=Left, Agent=Right)."""
    left_samples = _pcm16_to_samples(left_pcm)
    right_samples = _pcm16_to_samples(right_pcm)
    max_len = max(len(left_samples), len(right_samples))
    if len(left_samples) < max_len:
        left_samples.extend([0] * (max_len - len(left_samples)))
    if len(right_samples) < max_len:
        right_samples.extend([0] * (max_len - len(right_samples)))

    stereo = array("h", [0] * (max_len * 2))
    for i in range(max_len):
        stereo[2 * i] = left_samples[i]
        stereo[2 * i + 1] = right_samples[i]
    return _samples_to_pcm16(stereo)


def _generate_offline_speech_like_pcm(text: str, role: str, sample_rate_hz: int = 24000) -> bytes:
    """Generates a deterministic speech-cadence harmonic tone for offline/unit-test synthesis."""
    word_count = max(2, len((text or "").split()))
    duration_sec = min(3.5, max(0.4, word_count * 0.18))
    total_samples = int(duration_sec * sample_rate_hz)
    f0 = 195.0 if role == "END_USER" else 135.0
    out = array("h", [0] * total_samples)
    for i in range(total_samples):
        t = i / max(1, sample_rate_hz)
        syllable_env = 0.55 + 0.45 * math.sin(2.0 * math.pi * 4.2 * t)
        sig = 0.25 * syllable_env * (
            0.65 * math.sin(2.0 * math.pi * f0 * t)
            + 0.35 * math.sin(2.0 * math.pi * (f0 * 2.0) * t)
        )
        out[i] = max(-32767, min(32767, int(sig * 32767.0)))
    return _samples_to_pcm16(out)


def synthesize_transcript_audio(
    client: Optional[CompanionAgentRestClient],
    turns: list[dict[str, Any]],
    output_dir: str | Path,
    caller_voice: str = "en-US-Journey-F",
    agent_voice: str = "en-US-Journey-D",
    streaming_profile: Optional[StreamingProfile] = None,
    offline_fallback: bool = False,
) -> dict[str, Any]:
    """Synthesizes turn-by-turn WAV audio from a transcript using stock voices only (FR-5.1).

    Supports prosody normalization, sentence-aligned TTS chunking (`<=200` chars),
    optional PSTN telephone bandpass filtering (`--telephone-filter`), ambient noise
    overlay (`--noise-profile`), transfer ringtone (`--include-transfer-ring`), and
    full-call stereo WAV export (`--stereo`).
    """
    validate_stock_voice(caller_voice)
    validate_stock_voice(agent_voice)
    prof = streaming_profile or StreamingProfile()

    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    tts_encoding = "MULAW" if "MULAW" in prof.codec.upper() else "LINEAR16"
    synthesized_turns: list[dict[str, Any]] = []

    stereo_left_timeline = bytearray()
    stereo_right_timeline = bytearray()

    if prof.include_transfer_ring:
        ring_pcm = generate_transfer_ringtone_pcm(duration_sec=1.6, sample_rate_hz=prof.sample_rate_hz)
        ring_wav = out_dir / "turn_000_transfer_ring.wav"
        with wave.open(str(ring_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(prof.sample_rate_hz)
            wf.writeframes(ring_pcm)
        stereo_left_timeline.extend(ring_pcm)
        stereo_right_timeline.extend(ring_pcm)

    for idx, turn in enumerate(turns, start=1):
        role = str(turn.get("role") or "END_USER").upper()
        raw_text = str(turn.get("text") or "").strip()
        if not raw_text:
            continue
        cleaned_text = prepare_text_for_natural_tts(raw_text)
        tts_chunks = split_text_into_tts_chunks(cleaned_text, max_chars=200)
        voice_name = caller_voice if role == "END_USER" else agent_voice

        combined_pcm = bytearray()
        for chunk_text in tts_chunks:
            if client is not None and not offline_fallback:
                tts_req = {
                    "input": {"text": chunk_text},
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
                chunk_bytes = base64.b64decode(audio_b64) if audio_b64 else b""
                if chunk_bytes.startswith(b"RIFF"):
                    with wave.open(io.BytesIO(chunk_bytes), "rb") as wf:
                        chunk_bytes = wf.readframes(wf.getnframes())
            else:
                chunk_bytes = _generate_offline_speech_like_pcm(
                    chunk_text, role=role, sample_rate_hz=prof.sample_rate_hz
                )
            combined_pcm.extend(chunk_bytes)

        pcm_bytes = bytes(combined_pcm)
        if tts_encoding == "LINEAR16":
            if prof.telephone_filter:
                pcm_bytes = apply_telephone_bandpass(pcm_bytes, sample_rate_hz=prof.sample_rate_hz)
            if prof.noise_profile and prof.noise_profile != "none":
                pcm_bytes = overlay_ambient_noise(
                    pcm_bytes,
                    noise_profile=prof.noise_profile,
                    sample_rate_hz=prof.sample_rate_hz,
                    seed=idx,
                )

        wav_filename = f"turn_{idx:03d}_{role.lower()}.wav"
        wav_path = out_dir / wav_filename
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(1 if tts_encoding == "MULAW" else 2)
            wf.setframerate(prof.sample_rate_hz)
            wf.writeframes(pcm_bytes)

        if prof.stereo and tts_encoding == "LINEAR16":
            silence = b"\x00" * len(pcm_bytes)
            if role == "END_USER":
                stereo_left_timeline.extend(pcm_bytes)
                stereo_right_timeline.extend(silence)
            else:
                stereo_left_timeline.extend(silence)
                stereo_right_timeline.extend(pcm_bytes)

        synthesized_turns.append(
            {
                "turn_id": turn.get("turn_id", idx),
                "role": role,
                "text": raw_text,
                "normalizedTtsText": cleaned_text,
                "ttsChunkCount": len(tts_chunks),
                "stockVoice": voice_name,
                "voiceCloningUsed": False,
                "derivedFromCustomerData": True,
                "telephoneFilterApplied": prof.telephone_filter,
                "noiseProfile": prof.noise_profile,
                "audioFile": wav_filename,
            }
        )

    stereo_file: Optional[str] = None
    if prof.stereo and stereo_left_timeline:
        stereo_pcm = interleave_stereo_pcm(bytes(stereo_left_timeline), bytes(stereo_right_timeline))
        stereo_file = "full_call_stereo.wav"
        with wave.open(str(out_dir / stereo_file), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(prof.sample_rate_hz)
            wf.writeframes(stereo_pcm)

    manifest = {
        "schemaVersion": "1.0",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "derivedFromCustomerData": True,
        "voiceCloningUsed": False,
        "privacyNotice": "Synthetic audio derived from customer transcript using stock TTS voices only (FR-5.1).",
        "projectId": client.project_id if client is not None else "offline-local",
        "streamingProfile": asdict(prof),
        "stereoFullCallFile": stereo_file,
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
    # Filter to valid StreamingProfile fields
    valid_fields = {f for f in StreamingProfile.__dataclass_fields__}
    prof = StreamingProfile(**{k: v for k, v in prof_dict.items() if k in valid_fields})

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
