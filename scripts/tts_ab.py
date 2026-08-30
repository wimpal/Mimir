#!/usr/bin/env python3
"""A/B render Piper voices and prosody settings for operator listening tests.

Usage:
  uv run python scripts/tts_ab.py
  uv run python scripts/tts_ab.py --locale nl --voices nl_NL-pim-medium nl_NL-alex-medium

Outputs WAV files under data/tmp/tts_ab/ with real-time factor (RTF) per render.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PHRASES: dict[str, list[str]] = {
    "nl": [
        "Kaas is toegevoegd aan de boodschappenlijst, sir.",
        "Morgen wordt het achttien graden Celsius in Utrecht.",
        "We hebben vorige maand tweehonderd euro aan boodschappen uitgegeven.",
    ],
    "en": [
        "Cheese has been added to the shopping list, sir.",
        "Tomorrow it will be eighteen degrees Celsius in Utrecht.",
    ],
}

VOICE_CATALOG: dict[str, dict[str, str]] = {
    "nl_NL-pim-medium": {
        "locale": "nl",
        "base": "https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_NL/pim/medium",
    },
    "nl_NL-alex-medium": {
        "locale": "nl",
        "base": "https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_NL/alex/medium",
    },
    "nl_NL-ronnie-medium": {
        "locale": "nl",
        "base": "https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_NL/ronnie/medium",
    },
    "nl_BE-nathalie-medium": {
        "locale": "nl",
        "base": "https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_BE/nathalie/medium",
    },
    "nl_BE-rdh-medium": {
        "locale": "nl",
        "base": "https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_BE/rdh/medium",
    },
    "en_US-lessac-medium": {
        "locale": "en",
        "base": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium",
    },
}

PARAM_SETS: dict[str, dict[str, float]] = {
    "default": {
        "length_scale": 1.05,
        "noise_scale": 0.667,
        "noise_w_scale": 0.75,
        "volume": 1.0,
        "sentence_silence_s": 0.14,
    },
    "smooth": {
        "length_scale": 1.08,
        "noise_scale": 0.55,
        "noise_w_scale": 0.65,
        "volume": 1.0,
        "sentence_silence_s": 0.18,
    },
    "fast": {
        "length_scale": 0.98,
        "noise_scale": 0.667,
        "noise_w_scale": 0.75,
        "volume": 1.0,
        "sentence_silence_s": 0.10,
    },
}


def _voice_path(voices_dir: Path, name: str) -> Path:
    return voices_dir / f"{name}.onnx"


def main() -> int:
    parser = argparse.ArgumentParser(description="Piper voice A/B renders")
    parser.add_argument("--locale", choices=("nl", "en"), default="nl")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "tmp" / "tts_ab")
    parser.add_argument("--voices", nargs="*", help="Voice model names (see VOICE_CATALOG)")
    parser.add_argument("--params", nargs="*", default=["default", "smooth"])
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT))
    from brain.voice.text import prepare_text_for_speech
    from brain.voice.tts import PiperEngine, TtsSynthesisSettings

    voices_dir = REPO_ROOT / "data" / "voices"
    if args.voices:
        voice_names = list(args.voices)
    else:
        voice_names = [
            n
            for n, meta in VOICE_CATALOG.items()
            if meta["locale"] == args.locale
        ]

    args.out.mkdir(parents=True, exist_ok=True)
    phrases = PHRASES[args.locale]

    for voice_name in voice_names:
        path = _voice_path(voices_dir, voice_name)
        if not path.is_file():
            print(f"skip missing voice: {path} (run download_voice_models.ps1)", file=sys.stderr)
            continue
        locale = VOICE_CATALOG.get(voice_name, {}).get("locale", args.locale)
        for param_name in args.params:
            params = PARAM_SETS.get(param_name, PARAM_SETS["default"])
            engine = PiperEngine(
                {locale: path},
                synthesis=TtsSynthesisSettings(
                    length_scale=params["length_scale"],
                    noise_scale=params["noise_scale"],
                    noise_w_scale=params["noise_w_scale"],
                    volume=params["volume"],
                    sentence_silence_s=params["sentence_silence_s"],
                    normalize="peak",
                    fade_ms=5.0,
                ),
            )
            for idx, phrase in enumerate(phrases):
                speech = prepare_text_for_speech(phrase, locale=locale)  # type: ignore[arg-type]
                t0 = time.perf_counter()
                wav = engine.synthesize(speech, locale=locale)  # type: ignore[arg-type]
                elapsed = time.perf_counter() - t0
                import wave
                import io

                with wave.open(io.BytesIO(wav), "rb") as wf:
                    frames = wf.getnframes()
                    rate = wf.getframerate()
                    duration = frames / float(rate) if rate else 0.0
                rtf = elapsed / duration if duration > 0 else 0.0
                out = args.out / f"{voice_name}__{param_name}__{idx}.wav"
                out.write_bytes(wav)
                print(
                    f"{out.name}: rtf={rtf:.2f} synth={elapsed*1000:.0f}ms "
                    f"audio={duration*1000:.0f}ms"
                )

    print(f"\nRendered to: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
