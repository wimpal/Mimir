#!/usr/bin/env python3
"""End-to-end voice round-trip: mic (or file) → STT → chat → TTS → playback.

Usage:
  uv run python scripts/voice_roundtrip.py
  uv run python scripts/voice_roundtrip.py --audio path/to.wav
  uv run python scripts/voice_roundtrip.py --url http://127.0.0.1:8000 --locale nl

Requires: brain running, MIMIR_CLIENT_TOKEN in env, ffmpeg (+ ffplay optional), Piper voices.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent

# Household default: Focusrite interface (Windows Settings → Input). Override with
# MIMIR_VOICE_INPUT_DEVICE or --device.
DEFAULT_INPUT_DEVICE = "Analogue 1 + 2 (Focusrite USB Audio)"

# Load .env from repo root when run as a script.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass


def _token() -> str:
    for name in ("MIMIR_CLIENT_TOKEN", "MIMIR_AUTH_TOKEN"):
        val = os.environ.get(name, "").strip()
        if val:
            return val
    print("Set MIMIR_CLIENT_TOKEN in .env", file=sys.stderr)
    sys.exit(1)


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        print(f"Missing {name} on PATH", file=sys.stderr)
        sys.exit(1)
    return path


def list_dshow_devices(ffmpeg: str) -> None:
    subprocess.run(
        [ffmpeg, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        check=False,
    )


def _dshow_audio_devices(ffmpeg: str) -> list[str]:
    proc = subprocess.run(
        [ffmpeg, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (proc.stderr or "") + (proc.stdout or "")
    devices: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if "(audio)" not in line or "Alternative name" in line:
            continue
        start = line.find('"')
        end = line.rfind('"')
        if start >= 0 and end > start:
            devices.append(line[start + 1 : end])
    return devices


def _rank_dshow_devices(devices: list[str]) -> list[str]:
    """Order dshow inputs: Focusrite/analogue first; skip virtual mixers."""

    def score(name: str) -> tuple[int, str]:
        lower = name.lower()
        if "focusrite" in lower or "analogue" in lower:
            return (0, name)
        if "voicemeeter" in lower or "virtual" in lower or "cable" in lower:
            return (90, name)
        if "microphone" in lower or "mic" in lower:
            return (20, name)
        return (10, name)

    return sorted(devices, key=score)


def _default_input_device() -> str | None:
    env = os.environ.get("MIMIR_VOICE_INPUT_DEVICE", "").strip()
    if env:
        return env
    return DEFAULT_INPUT_DEVICE


def record_wav(ffmpeg: str, seconds: float, out: Path, device: str | None = None) -> None:
    candidates: list[str] = []
    chosen = device or _default_input_device()
    if chosen:
        candidates.append(chosen if chosen.startswith("audio=") else f"audio={chosen}")
    if device is None:
        listed = _rank_dshow_devices(_dshow_audio_devices(ffmpeg))
        for name in listed:
            spec = f"audio={name}"
            if spec not in candidates:
                candidates.append(spec)
    last_err: str | None = None
    for dev in candidates:
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "dshow",
            "-i",
            dev,
            "-t",
            str(seconds),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and out.is_file() and out.stat().st_size > 44:
            print(f"Recorded with {dev}")
            return
        last_err = proc.stderr[-500:] if proc.stderr else "record failed"
    print("Could not record from microphone. Try --audio or list devices:", file=sys.stderr)
    list_dshow_devices(ffmpeg)
    if last_err:
        print(last_err, file=sys.stderr)
    sys.exit(1)


def make_sine_wav(path: Path, seconds: float = 1.0) -> None:
    import struct

    rate = 16000
    n = int(rate * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            val = int(8000 * __import__("math").sin(2 * 3.14159 * 440 * i / rate))
            frames += struct.pack("<h", val)
        wf.writeframes(frames)


def main() -> int:
    parser = argparse.ArgumentParser(description="STT → chat → TTS round-trip probe")
    parser.add_argument("--url", default=os.environ.get("MIMIR_BRAIN_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--locale", choices=("nl", "en"), default="nl")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--audio", type=Path, help="Use existing wav instead of mic")
    parser.add_argument(
        "--device",
        help=(
            "ffmpeg dshow device name "
            f"(default: {DEFAULT_INPUT_DEVICE!r} or MIMIR_VOICE_INPUT_DEVICE)"
        ),
    )
    parser.add_argument("--message", help="Skip STT; send this chat message instead")
    parser.add_argument("--no-play", action="store_true")
    args = parser.parse_args()

    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    base = args.url.rstrip("/")

    with httpx.Client(timeout=180.0) as client:
        health = client.get(f"{base}/health")
        health.raise_for_status()
        print("health:", health.json().get("status"), health.json().get("voice"))

        tmp_dir = REPO_ROOT / "data" / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        transcript = args.message
        stt_ms = 0

        if transcript is None:
            if args.audio:
                wav_path = args.audio
            else:
                ffmpeg = _require_tool("ffmpeg")
                wav_path = tmp_dir / "ptt.wav"
                print(f"Recording {args.seconds}s… speak now.")
                record_wav(ffmpeg, args.seconds, wav_path, device=args.device)

            audio_bytes = wav_path.read_bytes()
            t0 = time.perf_counter()
            stt_resp = client.post(
                f"{base}/v1/stt",
                content=audio_bytes,
                headers={**headers, "Content-Type": "audio/wav"},
            )
            stt_ms = int((time.perf_counter() - t0) * 1000)
            if stt_resp.status_code != 200:
                print("STT failed:", stt_resp.status_code, stt_resp.text, file=sys.stderr)
                return 1
            stt_body = stt_resp.json()
            transcript = stt_body.get("text", "")
            print(f"STT ({stt_ms} ms): {transcript!r} lang={stt_body.get('language')}")

        t1 = time.perf_counter()
        chat_resp = client.post(
            f"{base}/v1/chat",
            json={"message": transcript},
            headers=headers,
        )
        chat_ms = int((time.perf_counter() - t1) * 1000)
        if chat_resp.status_code != 200:
            print("Chat failed:", chat_resp.status_code, chat_resp.text, file=sys.stderr)
            return 1
        reply = chat_resp.json().get("reply", "")
        print(f"Chat ({chat_ms} ms): {reply[:200]}{'…' if len(reply) > 200 else ''}")

        t2 = time.perf_counter()
        tts_resp = client.post(
            f"{base}/v1/tts",
            json={"text": reply, "locale": args.locale},
            headers=headers,
        )
        tts_ms = int((time.perf_counter() - t2) * 1000)
        if tts_resp.status_code != 200:
            print("TTS failed:", tts_resp.status_code, tts_resp.text, file=sys.stderr)
            return 1

        out_wav = tmp_dir / "reply.wav"
        out_wav.write_bytes(tts_resp.content)
        total_ms = stt_ms + chat_ms + tts_ms
        print(f"TTS ({tts_ms} ms): wrote {out_wav} ({len(tts_resp.content)} bytes)")
        print(f"Timings ms: stt={stt_ms} chat={chat_ms} tts={tts_ms} total={total_ms}")

        if not args.no_play:
            ffplay = shutil.which("ffplay")
            if ffplay:
                subprocess.run([ffplay, "-nodisp", "-autoexit", str(out_wav)], check=False)
            else:
                try:
                    os.startfile(str(out_wav))  # type: ignore[attr-defined]
                except OSError:
                    print(f"Play manually: {out_wav}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
