"""
Fix all JSON issues found by audit_json.py:
1. Strip JS-style // comments
2. Report remaining invalid JSON files for manual inspection
"""
import os
import json
import re

JSON_DIR = "json"

COMMENT_FILES = [
    "MyChart - Test Details 2.json",
    "MyChart - Test Details 6.json",
    "NYU Langone Health MyChart - Past Visit Details 47573647538.json",
    "NYU Langone Health MyChart - Test Details 4573885949.json",
    "NYU Langone Health MyChart - Test Details 4756388345.json",
    "NYU Langone Health MyChart - Test Details 47572824.json",
    "NYU Langone Health MyChart - Test Details 4775127364.json",
    "NYU Langone Health MyChart - Test Details 5738388484.json",
    "NYU Langone Health MyChart - Test Details 573885833.json",
    "labreport.json",
]

print("=== Fixing JS-comment files ===")
for fname in COMMENT_FILES:
    fpath = os.path.join(JSON_DIR, fname)
    if not os.path.exists(fpath):
        print(f"  [SKIP] {fname} not found")
        continue
    with open(fpath, encoding="utf-8") as f:
        raw = f.read()
    # Strip // comments (not inside strings — simple approach, safe for these files)
    cleaned = re.sub(r"\s*//[^\n]*", "", raw)
    try:
        parsed = json.loads(cleaned)
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(parsed, f, indent=2, ensure_ascii=False)
        print(f"  [FIXED] {fname}")
    except json.JSONDecodeError as e:
        print(f"  [STILL INVALID] {fname}: {e}")

print()
print("=== Checking still-invalid JSON files ===")
INVALID_FILES = [
    "GetPDFaspx 1_10.json",
    "GetPDFaspx 1_2.json",
    "Immunization.json",
    "MyChart - Past Visit Details 7.json",
    "NYU Langone Health MyChart - Past Visit Details 457833617734.json",
    "NYU Langone Health MyChart - Past Visit Details 4747838328.json",
    "NYU Langone Health MyChart - Past Visit Details 478584383.json",
    "Scan - RADIOLOGY HERITAGE DOCUMENT - Jul 26, 2019.json",
    "YN Labs 5-28-26_3.json",
]
for fname in INVALID_FILES:
    fpath = os.path.join(JSON_DIR, fname)
    if not os.path.exists(fpath):
        print(f"  [SKIP] {fname}")
        continue
    with open(fpath, encoding="utf-8") as f:
        raw = f.read()
    print(f"\n--- {fname} ---")
    # Print around the error area
    try:
        json.loads(raw)
        print("  Now valid!")
    except json.JSONDecodeError as e:
        # show context around error
        lines = raw.splitlines()
        err_line = e.lineno - 1
        start = max(0, err_line - 3)
        end = min(len(lines), err_line + 4)
        for i, line in enumerate(lines[start:end], start + 1):
            marker = " >>> " if i == e.lineno else "     "
            print(f"{marker}{i:4}: {line}")
        print(f"  Error: {e}")
