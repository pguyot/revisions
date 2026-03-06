#!/usr/bin/env python3
"""Merge model exercises from model_exercises.json into dsd/data.json."""
import json
import pathlib
import sys
import tempfile

DATA_FILE = pathlib.Path("dsd/data.json")
MODEL_FILE = pathlib.Path("model_exercises.json")

SECTIONS = ["leseverstehen", "hoerverstehen"]
TEILE = ["teil1", "teil2", "teil3", "teil4", "teil5"]


def main() -> None:
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError) as e:
        print(f"Error reading {DATA_FILE}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(MODEL_FILE, encoding="utf-8") as f:
            model = json.load(f)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError) as e:
        print(f"Error reading {MODEL_FILE}: {e}", file=sys.stderr)
        sys.exit(1)

    # Check for existing model exercises to avoid duplicates
    existing_ids = set()
    for section in SECTIONS:
        for teil in TEILE:
            for ex in data.get(section, {}).get(teil, []):
                existing_ids.add(ex["id"])

    total = 0
    for section in SECTIONS:
        for teil in TEILE:
            for ex in model.get(section, {}).get(teil, []):
                if ex["id"] not in existing_ids:
                    data.setdefault(section, {}).setdefault(teil, []).append(ex)
                    total += 1
                else:
                    print(f"  Skipping duplicate: {ex['id']}")

    # Atomic write: write to temp file then rename
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=DATA_FILE.parent, suffix=".tmp", prefix=DATA_FILE.stem
    )
    try:
        with open(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        pathlib.Path(tmp_path).replace(DATA_FILE)
    except Exception:
        pathlib.Path(tmp_path).unlink(missing_ok=True)
        raise

    print(f"Added {total} model exercises")
    for section in SECTIONS:
        for teil in TEILE:
            exercises = data.get(section, {}).get(teil, [])
            m = sum(1 for e in exercises if e.get("source") == "Modellsatz")
            print(f"  {section}.{teil}: {len(exercises)} total ({m} Modellsatz)")


if __name__ == "__main__":
    main()
