"""
Audit JSON files against source TXT chunks.
1. Check JSON validity (and report files with JS comments)
2. For structured lab reports, check that key numeric values match
"""
import os
import json
import re

CHUNKS_DIR = "chunks"
JSON_DIR = "json"

# Known lab result formats found in these reports
LAB_VALUE_RE = re.compile(
    r"^([A-Z][A-Z ,/()\-\.]{2,60})\s*\n([\d\.]+)\s*\n([\d\.]+ [\d\.]+)",
    re.MULTILINE,
)

issues = []
fixes_needed = {}

txt_files = sorted(f for f in os.listdir(CHUNKS_DIR) if f.endswith(".txt"))
print(f"Checking {len(txt_files)} chunk files...\n")

for txt_file in txt_files:
    json_file = txt_file.replace(".txt", ".json")
    json_path = os.path.join(JSON_DIR, json_file)
    txt_path = os.path.join(CHUNKS_DIR, txt_file)

    if not os.path.exists(json_path):
        issues.append(f"[NO JSON]        {txt_file}")
        continue

    with open(txt_path, encoding="utf-8") as f:
        txt_content = f.read()
    with open(json_path, encoding="utf-8") as f:
        json_raw = f.read()

    # --- validity check ---
    has_comments = bool(re.search(r"//[^\n]*", json_raw))
    try:
        parsed = json.loads(json_raw)
        valid = True
    except json.JSONDecodeError:
        # strip JS comments and retry
        cleaned = re.sub(r"//[^\n]*", "", json_raw)
        try:
            parsed = json.loads(cleaned)
            valid = True
            issues.append(f"[JS COMMENTS]    {json_file}")
        except json.JSONDecodeError as e:
            valid = False
            issues.append(f"[INVALID JSON]   {json_file}: {e}")
            continue

    json_str = json_raw  # for substring search

    # --- content audit: find numeric lab values in txt missing from json ---
    file_missing = []
    for m in LAB_VALUE_RE.finditer(txt_content):
        name = m.group(1).strip()
        value = m.group(2).strip()
        ref = m.group(3).strip()
        if value not in json_str:
            file_missing.append((name, value, ref))

    if file_missing:
        fixes_needed[txt_file] = file_missing
        issues.append(f"[MISSING VALUES] {txt_file}: {len(file_missing)} value(s) not found in JSON")
        for name, val, ref in file_missing[:8]:
            issues.append(f"    {name}: {val}  (range {ref})")

print("\n".join(issues) if issues else "No issues found.")
print(f"\nTotal files with missing values: {len(fixes_needed)}")
