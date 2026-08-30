"""Local microphone capture for TUI voice input (ffmpeg dshow on Windows)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_INPUT_DEVICE = "Analogue 1 + 2 (Focusrite USB Audio)"
MAX_RECORD_SECONDS = 30.0
MIN_RECORD_SECONDS = 0.35
MIN_WAV_BYTES = 1000

# ASCII labels — Windows console fonts often lack emoji / Nerd Font glyphs.
MIC_LABEL_RECORD = "Stop"


class AudioCaptureError(RuntimeError):
    """Mic unavailable or recording failed."""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def default_input_device() -> str:
    env = os.environ.get("MIMIR_VOICE_INPUT_DEVICE", "").strip()
    return env or DEFAULT_INPUT_DEVICE


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
    def score(name: str) -> tuple[int, str]:
        lower = name.lower()
        if "focusrite" in lower or "analogue" in lower:
            return (0, name)
        if "voicemeeter" in lower or "virtual" in lower:
            return (90, name)
        if "microphone" in lower or "mic" in lower:
            return (20, name)
        return (10, name)

    return sorted(devices, key=score)


def _record_candidates(device: str | None) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    candidates: list[str] = []
    chosen = (device or default_input_device()).strip()
    if chosen:
        candidates.append(chosen if chosen.startswith("audio=") else f"audio={chosen}")
    for name in _rank_dshow_devices(_dshow_audio_devices(ffmpeg)):
        spec = f"audio={name}"
        if spec not in candidates:
            candidates.append(spec)
    return candidates


def _stop_ffmpeg(proc: subprocess.Popen[bytes], *, graceful: bool) -> None:
    if proc.poll() is not None:
        return
    if graceful and proc.stdin is not None:
        try:
            proc.stdin.write(b"q")
            proc.stdin.flush()
            proc.stdin.close()
        except OSError:
            proc.terminate()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


class AudioCapture:
    """Start/stop ffmpeg recording to a wav file."""

    def __init__(self, *, device: str | None = None) -> None:
        self._device = device
        self._proc: subprocess.Popen[bytes] | None = None
        self._path: Path | None = None
        self._ffmpeg = shutil.which("ffmpeg")
        self._started_at: float | None = None
        self._device_label: str | None = None

    @property
    def is_recording(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, out_path: Path) -> None:
        if self.is_recording:
            raise AudioCaptureError("Already recording.")
        if not self._ffmpeg:
            raise AudioCaptureError("ffmpeg is not on PATH.")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        last_err = ""
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        for dev in _record_candidates(self._device):
            cmd = [
                self._ffmpeg,
                "-y",
                "-f",
                "dshow",
                "-audio_buffer_size",
                "50",
                "-i",
                dev,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(out_path),
            ]
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=flags,
                )
            except OSError as exc:
                last_err = str(exc)
                continue
            # Give ffmpeg a moment to open the device; fail fast if it exits immediately.
            time.sleep(0.15)
            if proc.poll() is not None:
                err = (proc.stderr.read() if proc.stderr else b"").decode(
                    "utf-8", errors="replace"
                )
                last_err = err[-400:] if err else "ffmpeg exited immediately"
                continue
            self._proc = proc
            self._path = out_path
            self._started_at = time.monotonic()
            self._device_label = dev
            return
        raise AudioCaptureError(
            last_err or "Could not open microphone. Set MIMIR_VOICE_INPUT_DEVICE."
        )

    def stop(self) -> bytes:
        if self._proc is None or self._path is None:
            raise AudioCaptureError("Not recording.")
        proc = self._proc
        path = self._path
        started = self._started_at
        self._proc = None
        self._path = None
        self._started_at = None
        self._device_label = None
        if started is not None and (time.monotonic() - started) < MIN_RECORD_SECONDS:
            _stop_ffmpeg(proc, graceful=False)
            raise AudioCaptureError(
                "Recording too short — speak, then click Stop."
            )
        _stop_ffmpeg(proc, graceful=True)
        if not path.is_file() or path.stat().st_size < MIN_WAV_BYTES:
            raise AudioCaptureError(
                "Recording was empty. Check Windows Sound → Input is the Focusrite."
            )
        return path.read_bytes()

    def cancel(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        path = self._path
        self._proc = None
        self._path = None
        self._started_at = None
        self._device_label = None
        _stop_ffmpeg(proc, graceful=False)
        if path is not None and path.is_file():
            path.unlink(missing_ok=True)
