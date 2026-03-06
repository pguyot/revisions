#!/usr/bin/env python3
"""Merge model exercises from model_exercises.json into dsd/data.json."""
import json
import pathlib

DATA_FILE = pathlib.Path("dsd/data.json")
MODEL_FILE = pathlib.Path("model_exercises.json")

with open(DATA_FILE, encoding="utf-8") as f:
    data = json.load(f)

with open(MODEL_FILE, encoding="utf-8") as f:
    model = json.load(f)

# Check for existing model exercises to avoid duplicates
existing_ids = set()
for section in ["leseverstehen", "hoerverstehen"]:
    for teil in ["teil1", "teil2", "teil3", "teil4", "teil5"]:
        for ex in data[section][teil]:
            existing_ids.add(ex["id"])

total = 0
for section in ["leseverstehen", "hoerverstehen"]:
    for teil in ["teil1", "teil2", "teil3", "teil4", "teil5"]:
        for ex in model[section][teil]:
            if ex["id"] not in existing_ids:
                data[section][teil].append(ex)
                total += 1
            else:
                print(f"  Skipping duplicate: {ex['id']}")

with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"Added {total} model exercises")
for section in ["leseverstehen", "hoerverstehen"]:
    for teil in ["teil1", "teil2", "teil3", "teil4", "teil5"]:
        m = sum(1 for e in data[section][teil] if e.get("source") == "Modellsatz")
        print(f"  {section}.{teil}: {len(data[section][teil])} total ({m} Modellsatz)")
