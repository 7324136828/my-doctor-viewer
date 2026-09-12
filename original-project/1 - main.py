import json
import os
import re

INPUT_DIR = "input"
OUTPUT_DIR = "output"
MERGED_PATH = "merged.json"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Extract JSON from .txt files ---
for filename in sorted(os.listdir(INPUT_DIR)):
    if not filename.endswith(".txt"):
        continue

    filepath = os.path.join(INPUT_DIR, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if not match:
        print(f"No JSON block found in {filename}, skipping.")
        continue

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in {filename}: {e}, skipping.")
        continue

    out_filename = os.path.splitext(filename)[0] + ".json"
    out_path = os.path.join(OUTPUT_DIR, out_filename)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Extracted: {out_filename}")

print("Extraction done.")

# --- Merge JSON files into merged.json ---
files = sorted(f for f in os.listdir(OUTPUT_DIR) if f.endswith(".json"))
print(f"Merging {len(files)} files...")

merged = {}
errors = []

for fname in files:
    fpath = os.path.join(OUTPUT_DIR, fname)
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged[fname] = data
    except Exception as e:
        errors.append((fname, str(e)))
        print(f"  Error: {fname} — {e}")

with open(MERGED_PATH, "w", encoding="utf-8") as f:
    json.dump(merged, f, indent=2, ensure_ascii=False)

print(f"\nDone. {len(merged)} records written to merged.json")
if errors:
    print(f"{len(errors)} files skipped due to errors.")
