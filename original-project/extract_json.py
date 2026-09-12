import os
import re
import json

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
JSON_DIR = os.path.join(os.path.dirname(__file__), "json")
CHECKPOINT_FILE = os.path.join(os.path.dirname(__file__), "checkpoint.json")

os.makedirs(JSON_DIR, exist_ok=True)

with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
    checkpoint = json.load(f)

removed = []
extracted = []

for fname in os.listdir(OUTPUT_DIR):
    if not fname.endswith(".txt"):
        continue

    fpath = os.path.join(OUTPUT_DIR, fname)
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()

    # Find all ```json ... ``` blocks (last occurrence)
    matches = list(re.finditer(r"```json\s*(.*?)```", content, re.DOTALL))

    if not matches:
        # No JSON found — remove from checkpoint
        base = fname  # checkpoint uses the .txt filename
        if base in checkpoint.get("processed", []):
            checkpoint["processed"].remove(base)
            removed.append(base)
            print(f"[NO JSON] Removed from checkpoint: {base}")
        else:
            print(f"[NO JSON] Not in checkpoint, skipping: {base}")
        continue

    last_match = matches[-1]
    json_text = last_match.group(1).strip()

    # Validate JSON
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as e:
        print(f"[INVALID JSON] {fname}: {e}")
        # Still write the raw text so nothing is lost
        parsed = None

    out_name = os.path.splitext(fname)[0] + ".json"
    out_path = os.path.join(JSON_DIR, out_name)

    if parsed is not None:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(parsed, f, indent=2, ensure_ascii=False)
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json_text)

    extracted.append(fname)
    print(f"[OK] {fname} -> {out_name}")

# Save updated checkpoint
with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
    json.dump(checkpoint, f, indent=2, ensure_ascii=False)

print(f"\nDone. Extracted: {len(extracted)}, Removed from checkpoint: {len(removed)}")
if removed:
    print("Removed entries:", removed)
