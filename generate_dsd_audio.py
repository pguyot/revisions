#!/usr/bin/env python3
"""Generate TTS audio files for the DSD Hörverstehen exercises.

Reads dsd/data.json, extracts all spokenText entries, and generates MP3 files
using Piper TTS.  Dialogs (teil1 scenes, teil3 interviews) use distinct voices
for distinct speakers, matching gender where possible.

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
import re
import struct
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_FILE = pathlib.Path("dsd/data.json")
AUDIO_DIR = pathlib.Path("dsd/audio")
MANIFEST_FILE = AUDIO_DIR / "manifest.json"

# Piper voices
VOICE_MALE = "de_DE-thorsten-high"
VOICE_FEMALE = "de_DE-kerstin-low"

VOICE_HF_PATHS = {
    VOICE_MALE: "de/de_DE/thorsten/high",
    VOICE_FEMALE: "de/de_DE/kerstin/low",
}

PIPER_MODEL_DIR = pathlib.Path(tempfile.gettempdir()) / "piper-voices"

# Silence between dialog turns (seconds)
TURN_PAUSE_S = 0.6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def audio_filename(teil: str, ex_idx: int, sub_idx: int | None, text: str,
                   voice_key: str = "") -> str:
    """Stable filename based on content hash so we skip unchanged texts.

    We include a 'v2' marker and the voice_key (describing the speaker
    assignment) so files are regenerated when voice layout changes.
    """
    h = hashlib.sha256(("v2:" + voice_key + ":" + text).encode()).hexdigest()[:12]
    suffix = f"_{sub_idx}" if sub_idx is not None else ""
    return f"{teil}_ex{ex_idx}{suffix}_{h}.mp3"


def collect_spoken_texts(data: dict) -> list[tuple[str, int, int | None, str, dict]]:
    """Return list of (teil, ex_idx, sub_idx, spokenText, exercise) from data.json."""
    results: list[tuple[str, int, int | None, str, dict]] = []
    hv = data.get("hoerverstehen", {})
    for teil in sorted(hv):
        for i, ex in enumerate(hv[teil]):
            if "spokenText" in ex:
                results.append((teil, i, None, ex["spokenText"], ex))
            for j, scene in enumerate(ex.get("scenes", [])):
                if "spokenText" in scene:
                    results.append((teil, i, j, scene["spokenText"], ex))
            for j, ann in enumerate(ex.get("announcements", [])):
                if "spokenText" in ann:
                    results.append((teil, i, j, ann["spokenText"], ex))
    return results


# ---------------------------------------------------------------------------
# Dialog parsing
# ---------------------------------------------------------------------------

def split_dialog_turns(text: str) -> list[str]:
    """Split a dialog by em-dash / en-dash turn markers."""
    parts = re.split(r'\s*[—–]\s*', text)
    return [p.strip() for p in parts if p.strip()]


def split_interview_turns(text: str) -> list[tuple[str, str]]:
    """Split interview text into (speaker_label, text) pairs.

    Interviews use the format:
        Interviewer: question text\n\nName: answer text
    """
    segments: list[tuple[str, str]] = []
    # Split on speaker labels at start of line
    pattern = r'(?:^|\n\n)(\w+):\s*'
    parts = re.split(pattern, text.strip())
    # parts alternates: [preamble, label1, text1, label2, text2, ...]
    if parts[0].strip():
        segments.append(("", parts[0].strip()))
    for i in range(1, len(parts) - 1, 2):
        label = parts[i]
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if content:
            segments.append((label, content))
    return segments


def detect_interviewee_gender(exercise: dict) -> str:
    """Detect gender of interviewee from the intro text. Returns 'm' or 'f'."""
    intro = exercise.get("intro", "")
    # German feminine markers
    if re.search(r'einer\s+(Schülerin|Austauschschülerin|Studentin)', intro):
        return "f"
    if re.search(r'\bSie\s+(berichtet|erzählt|macht|arbeitet)', intro):
        return "f"
    # German masculine markers
    if re.search(r'einem\s+(Schüler|Austauschschüler|Student)', intro):
        return "m"
    if re.search(r'\bEr\s+(berichtet|erzählt|macht|arbeitet)', intro):
        return "m"
    # Fallback: try common name patterns
    female_names = {"Sarah", "Lena", "Julia", "Yuki", "Anna", "Lisa", "Marie", "Laura"}
    male_names = {"Max", "Tom", "Paul", "Tim", "Jonas", "Leon", "Lukas", "Felix"}
    for name in female_names:
        if name in intro:
            return "f"
    for name in male_names:
        if name in intro:
            return "m"
    return "m"  # default


# ---------------------------------------------------------------------------
# WAV manipulation
# ---------------------------------------------------------------------------

def read_wav(path: pathlib.Path) -> tuple[int, int, int, bytes]:
    """Read a WAV file, return (channels, sample_width, framerate, frames)."""
    import wave
    with wave.open(str(path), "rb") as wf:
        return wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.readframes(wf.getnframes())


def write_wav(path: pathlib.Path, channels: int, sample_width: int, framerate: int, frames: bytes) -> None:
    """Write a WAV file."""
    import wave
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(framerate)
        wf.writeframes(frames)


def make_silence_wav(duration_s: float, channels: int, sample_width: int, framerate: int) -> bytes:
    """Generate silence as raw WAV frames."""
    n_frames = int(framerate * duration_s)
    return b'\x00' * (n_frames * channels * sample_width)


def resample_wav_frames(frames: bytes, channels: int, sample_width: int,
                        src_rate: int, dst_rate: int) -> bytes:
    """Simple linear resampling of WAV frames."""
    if src_rate == dst_rate:
        return frames
    frame_size = channels * sample_width
    n_src = len(frames) // frame_size
    n_dst = int(n_src * dst_rate / src_rate)
    result = bytearray(n_dst * frame_size)

    if sample_width == 2:
        fmt = f"<{channels}h"
        out_fmt = fmt
    else:
        # Fallback for other widths: byte-level nearest neighbor
        for i in range(n_dst):
            src_i = int(i * src_rate / dst_rate)
            src_i = min(src_i, n_src - 1)
            result[i * frame_size:(i + 1) * frame_size] = frames[src_i * frame_size:(src_i + 1) * frame_size]
        return bytes(result)

    for i in range(n_dst):
        src_pos = i * src_rate / dst_rate
        src_i = int(src_pos)
        frac = src_pos - src_i
        src_i = min(src_i, n_src - 1)
        src_i2 = min(src_i + 1, n_src - 1)

        s1 = struct.unpack_from(fmt, frames, src_i * frame_size)
        s2 = struct.unpack_from(fmt, frames, src_i2 * frame_size)
        mixed = tuple(int(a + frac * (b - a)) for a, b in zip(s1, s2))
        struct.pack_into(out_fmt, result, i * frame_size, *mixed)

    return bytes(result)


# ---------------------------------------------------------------------------
# Audio generation
# ---------------------------------------------------------------------------

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
    print("  WARNING: neither ffmpeg nor lame found — saving as .wav")
    mp3_path = mp3_path.with_suffix(".wav")
    wav_path.rename(mp3_path)


def ensure_model(voice: str) -> pathlib.Path:
    """Ensure a Piper model is downloaded, return path to .onnx file."""
    model_path = PIPER_MODEL_DIR / f"{voice}.onnx"
    config_path = PIPER_MODEL_DIR / f"{voice}.onnx.json"
    if model_path.exists() and config_path.exists():
        return model_path

    PIPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    prefix = VOICE_HF_PATHS[voice]
    for fname in [f"{voice}.onnx", f"{voice}.onnx.json"]:
        url = f"{base}/{prefix}/{fname}"
        dest = PIPER_MODEL_DIR / fname
        if not dest.exists():
            print(f"  Downloading {fname} …")
            import urllib.request
            urllib.request.urlretrieve(url, dest)
    return model_path


def generate_wav(text: str, wav_path: pathlib.Path, model_path: pathlib.Path) -> bool:
    """Generate a WAV file for a single text segment. Returns True on success."""
    result = subprocess.run(
        ["piper", "--model", str(model_path), "--output_file", str(wav_path)],
        input=text, text=True, capture_output=True,
    )
    if result.returncode != 0:
        print(f"  ERROR: piper failed: {result.stderr}", file=sys.stderr)
        return False
    return True


def generate_single_voice(text: str, output_mp3: pathlib.Path, model_path: pathlib.Path) -> None:
    """Generate audio for a single text using one voice."""
    wav_path = output_mp3.with_suffix(".wav")
    if generate_wav(text, wav_path, model_path):
        wav_to_mp3(wav_path, output_mp3)
        if wav_path.exists() and output_mp3.exists():
            wav_path.unlink()


def generate_multi_voice(segments: list[tuple[str, pathlib.Path]], output_mp3: pathlib.Path,
                         target_rate: int = 22050) -> None:
    """Generate audio from multiple (text, model_path) segments and concatenate.

    Each segment is generated separately, then all WAV data is concatenated
    with brief pauses between turns.
    """
    all_frames: list[bytes] = []
    channels = 1
    sample_width = 2
    framerate = target_rate

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        for i, (text, model_path) in enumerate(segments):
            seg_wav = tmp / f"seg_{i}.wav"
            if not generate_wav(text, seg_wav, model_path):
                snippet = text[:60] + ("…" if len(text) > 60 else "")
                raise RuntimeError(
                    f"generate_wav failed for segment {i} "
                    f"(model={model_path.name}, text={snippet!r})"
                )
            ch, sw, fr, frames = read_wav(seg_wav)
            # Use first segment's properties as reference
            if i == 0:
                channels = ch
                sample_width = sw
                framerate = max(fr, target_rate)
            # Resample if needed
            if fr != framerate:
                frames = resample_wav_frames(frames, ch, sw, fr, framerate)
            all_frames.append(frames)
            # Add pause between turns (not after last)
            if i < len(segments) - 1:
                all_frames.append(make_silence_wav(TURN_PAUSE_S, channels, sample_width, framerate))

    if not all_frames:
        return

    combined_wav = output_mp3.with_suffix(".wav")
    write_wav(combined_wav, channels, sample_width, framerate, b''.join(all_frames))
    wav_to_mp3(combined_wav, output_mp3)
    if combined_wav.exists() and output_mp3.exists():
        combined_wav.unlink()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    spoken = collect_spoken_texts(data)
    print(f"Found {len(spoken)} spoken texts in {DATA_FILE}")

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure both voice models are available
    male_model = ensure_model(VOICE_MALE)
    female_model = ensure_model(VOICE_FEMALE)

    manifest: dict[str, str] = {}
    generated = 0
    skipped = 0

    for teil, ex_idx, sub_idx, text, exercise in spoken:
        # Determine voice plan and build segments before computing filename
        voice_key = VOICE_MALE  # default: single male voice
        segments: list[tuple[str, pathlib.Path]] | None = None

        if teil == "teil3" and "\n\n" in text:
            turns = split_interview_turns(text)
            if len(turns) > 1:
                interviewee_gender = detect_interviewee_gender(exercise)
                interviewer_model = female_model if interviewee_gender == "m" else male_model
                interviewee_model = male_model if interviewee_gender == "m" else female_model
                voice_key = f"interview:gender={interviewee_gender}"
                segments = []
                for label, content in turns:
                    if label.lower() == "interviewer":
                        segments.append((content, interviewer_model))
                    else:
                        segments.append((content, interviewee_model))

        if segments is None and teil == "teil1":
            turns_t1 = split_dialog_turns(text)
            if len(turns_t1) > 1:
                voice_key = f"dialog:alt-mf:{len(turns_t1)}"
                segments = []
                for i, turn_text in enumerate(turns_t1):
                    model = male_model if i % 2 == 0 else female_model
                    segments.append((turn_text, model))

        fname = audio_filename(teil, ex_idx, sub_idx, text, voice_key)
        key = f"{teil}/{ex_idx}" if sub_idx is None else f"{teil}/{ex_idx}/{sub_idx}"

        out_path = AUDIO_DIR / fname
        if out_path.exists():
            manifest[key] = fname
            skipped += 1
            continue

        print(f"  Generating {fname} …")

        try:
            if segments is not None:
                generate_multi_voice(segments, out_path)
            else:
                generate_single_voice(text, out_path, male_model)
        except RuntimeError as e:
            print(f"  ERROR: {e}", file=sys.stderr)

        if out_path.exists():
            manifest[key] = fname
            generated += 1
        else:
            print(f"  WARNING: failed to produce {fname}, skipping manifest entry")

    # Write manifest
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\nDone: {generated} generated, {skipped} skipped (already exist)")
    print(f"Manifest written to {MANIFEST_FILE}")


if __name__ == "__main__":
    main()
