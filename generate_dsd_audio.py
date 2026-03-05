#!/usr/bin/env python3
"""Generate TTS audio files for the DSD Hörverstehen exercises.

Reads dsd/data.json, extracts all spokenText entries, and generates MP3 files
using Piper TTS with the Thorsten-Voice (high quality German voice).

Audio files are saved to dsd/audio/ with content-hash filenames so unchanged
texts are never re-generated.  The script also writes dsd/audio/manifest.json
mapping each (teil, exercise, sub-index) to its audio filename, which the
game HTML uses for playback.

Requirements:
    pip install piper-tts

Usage:
    python generate_dsd_audio.py
"""

import hashlib
import json
import pathlib
import subprocess
import sys

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_FILE = pathlib.Path("dsd/data.json")
AUDIO_DIR = pathlib.Path("dsd/audio")
MANIFEST_FILE = AUDIO_DIR / "manifest.json"

# Piper model — must be downloaded separately (see README or download from
# https://huggingface.co/rhasspy/piper-voices/tree/main/de/de_DE/thorsten/high)
PIPER_MODEL = "de_DE-thorsten-high"
PIPER_MODEL_DIR = pathlib.Path("/tmp/piper-voices")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def audio_filename(teil: str, ex_idx: int, sub_idx: int | None, text: str) -> str:
    """Stable filename based on content hash so we skip unchanged texts."""
    h = hashlib.sha256(text.encode()).hexdigest()[:12]
    suffix = f"_{sub_idx}" if sub_idx is not None else ""
    return f"{teil}_ex{ex_idx}{suffix}_{h}.mp3"


def collect_spoken_texts(data: dict) -> list[tuple[str, int, int | None, str]]:
    """Return list of (teil, ex_idx, sub_idx, spokenText) from data.json."""
    results = []
    hv = data.get("hoerverstehen", {})
    for teil in sorted(hv):
        for i, ex in enumerate(hv[teil]):
            if "spokenText" in ex:
                results.append((teil, i, None, ex["spokenText"]))
            for j, scene in enumerate(ex.get("scenes", [])):
                if "spokenText" in scene:
                    results.append((teil, i, j, scene["spokenText"]))
            for j, ann in enumerate(ex.get("announcements", [])):
                if "spokenText" in ann:
                    results.append((teil, i, j, ann["spokenText"]))
    return results


def wav_to_mp3(wav_path: pathlib.Path, mp3_path: pathlib.Path) -> None:
    """Convert WAV to MP3 using ffmpeg or lame, whichever is available."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_path), "-q:a", "2", str(mp3_path)],
            check=True, capture_output=True,
        )
        return
    except FileNotFoundError:
        pass
    try:
        subprocess.run(
            ["lame", "--quiet", "-V", "2", str(wav_path), str(mp3_path)],
            check=True, capture_output=True,
        )
        return
    except FileNotFoundError:
        pass
    # Fallback: keep as WAV (rename)
    print("  WARNING: neither ffmpeg nor lame found — saving as .wav")
    mp3_path = mp3_path.with_suffix(".wav")
    wav_path.rename(mp3_path)


def ensure_model() -> pathlib.Path:
    """Ensure the Piper model is downloaded, return path to .onnx file."""
    model_path = PIPER_MODEL_DIR / f"{PIPER_MODEL}.onnx"
    config_path = PIPER_MODEL_DIR / f"{PIPER_MODEL}.onnx.json"
    if model_path.exists() and config_path.exists():
        return model_path

    PIPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    prefix = "de/de_DE/thorsten/high"
    for fname in [f"{PIPER_MODEL}.onnx", f"{PIPER_MODEL}.onnx.json"]:
        url = f"{base}/{prefix}/{fname}"
        dest = PIPER_MODEL_DIR / fname
        if not dest.exists():
            print(f"  Downloading {fname} …")
            import urllib.request
            urllib.request.urlretrieve(url, dest)
    return model_path


def generate_audio(text: str, output_mp3: pathlib.Path, model_path: pathlib.Path) -> None:
    """Generate audio for a single text using Piper TTS."""
    wav_path = output_mp3.with_suffix(".wav")
    result = subprocess.run(
        ["piper", "--model", str(model_path), "--output_file", str(wav_path)],
        input=text, text=True, capture_output=True,
    )
    if result.returncode != 0:
        print(f"  ERROR: piper failed: {result.stderr}", file=sys.stderr)
        return
    wav_to_mp3(wav_path, output_mp3)
    # Clean up intermediate WAV if MP3 was produced
    if wav_path.exists() and output_mp3.exists():
        wav_path.unlink()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    spoken = collect_spoken_texts(data)
    print(f"Found {len(spoken)} spoken texts in {DATA_FILE}")

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    model_path = ensure_model()

    # Build manifest mapping key → filename
    manifest: dict[str, str] = {}
    generated = 0
    skipped = 0

    for teil, ex_idx, sub_idx, text in spoken:
        fname = audio_filename(teil, ex_idx, sub_idx, text)
        key = f"{teil}/{ex_idx}" if sub_idx is None else f"{teil}/{ex_idx}/{sub_idx}"
        manifest[key] = fname

        out_path = AUDIO_DIR / fname
        if out_path.exists():
            skipped += 1
            continue

        print(f"  Generating {fname} …")
        generate_audio(text, out_path, model_path)
        generated += 1

    # Write manifest
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\nDone: {generated} generated, {skipped} skipped (already exist)")
    print(f"Manifest written to {MANIFEST_FILE}")


if __name__ == "__main__":
    main()
