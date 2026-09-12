"""
Fix complex JSON issues that can't be done with simple string replacement:
1. Immunization.json  - strip all JS comments and truncate extra text after closing brace
2. NYU Langone... 4747838328.json - escape unescaped " inside string value
3. NYU Langone... 478584383.json  - fix bare "non-smoker" keys in object
4. YN Labs 5-28-26_3.json  - replace empty template with real content from source
"""
import os, json, re

JSON_DIR = "json"

# ── 1. Immunization.json ──────────────────────────────────────────────────────
path = os.path.join(JSON_DIR, "Immunization.json")
with open(path, encoding="utf-8") as f:
    raw = f.read()

# Strip JS-style // comments
cleaned = re.sub(r"\s*//[^\n]*", "", raw)

# Parse first complete JSON value, ignoring trailing text
import json as _json
decoder = _json.JSONDecoder()
parsed, _ = decoder.raw_decode(cleaned.strip())
with open(path, "w", encoding="utf-8") as f:
    _json.dump(parsed, f, indent=2, ensure_ascii=False)
print("[FIXED] Immunization.json")

# ── 2. NYU ... 4747838328.json — escape " inside string ─────────────────────
path = os.path.join(JSON_DIR, "NYU Langone Health MyChart - Past Visit Details 4747838328.json")
with open(path, encoding="utf-8") as f:
    raw = f.read()

# The problematic substring is the unescaped " after 5' 10 and the ? for ²
fixed = raw.replace(
    '1.778 m (5\' 10"), BMI: 22.74 kg/m?',
    '1.778 m (5\' 10\\"), BMI: 22.74 kg/m\u00b2'
)
try:
    parsed = _json.loads(fixed)
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(parsed, f, indent=2, ensure_ascii=False)
    print("[FIXED] NYU ... 4747838328.json")
except _json.JSONDecodeError as e:
    print(f"[STILL BROKEN] NYU ... 4747838328.json: {e}")
    # Show context
    lines = fixed.splitlines()
    start = max(0, e.lineno - 3)
    end = min(len(lines), e.lineno + 4)
    for i, line in enumerate(lines[start:end], start + 1):
        print(f"  {'>>>' if i == e.lineno else '   '} {i}: {line}")

# ── 3. NYU ... 478584383.json — fix bare "non-smoker" in objects ─────────────
path = os.path.join(JSON_DIR, "NYU Langone Health MyChart - Past Visit Details 478584383.json")
with open(path, encoding="utf-8") as f:
    raw = f.read()

# Replace standalone "non-smoker" entries (no colon follows) with a proper key-value
fixed = re.sub(r'"non-smoker"(\s*\n)', r'"is_non_smoker": true\1', raw)
# Replace standalone "paternal_grandfather" entry
fixed = re.sub(r'"paternal_grandfather"(\s*\n)', r'"affected_relative": "paternal_grandfather"\1', fixed)
try:
    parsed = _json.loads(fixed)
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(parsed, f, indent=2, ensure_ascii=False)
    print("[FIXED] NYU ... 478584383.json")
except _json.JSONDecodeError as e:
    print(f"[STILL BROKEN] NYU ... 478584383.json: {e}")
    lines = fixed.splitlines()
    start = max(0, e.lineno - 3)
    end = min(len(lines), e.lineno + 4)
    for i, line in enumerate(lines[start:end], start + 1):
        print(f"  {'>>>' if i == e.lineno else '   '} {i}: {line}")

# ── 4. YN Labs 5-28-26_3.json — replace empty template with real content ─────
path = os.path.join(JSON_DIR, "YN Labs 5-28-26_3.json")
content = {
    "name_of_patient": "Yuzhe Ni",
    "doctor_name": "A KING",
    "date_of_visit": "05/28/2026",
    "properties": {
        "patient_demographics": {
            "dob": "28-OCT-1996"
        },
        "clinical_background": {
            "description": "This chunk contains pages 4-11 of the Labcorp 500 PLUS Carrier Panel (No XL) report "
                           "(Invitae #: RQ3159607). It lists the complete set of genes and reference transcripts "
                           "evaluated in the carrier screen. No abnormal findings are reported in this section; "
                           "all listed genes were evaluated per the panel methodology."
        },
        "significant_test_results": {
            "carrier_panel_gene_list": {
                "test": "Labcorp 500 PLUS Carrier Panel (No XL)",
                "panel_id": "Invitae #: RQ3159607",
                "lab": "Labcorp Genetics, 1400 16th Street, San Francisco, CA 94103",
                "lab_director": "Jeana DaRe, PhD, FACMG",
                "pages_covered": "Pages 4-11 of 11",
                "note": "Full gene/transcript list evaluated. No additional pathogenic variants identified beyond GCDH (reported in chunk 1)."
            }
        },
        "visit_notes": "Gene panel reference list pages from the Labcorp Carrier Screen report. Clinical findings reported in YN Labs 5-28-26_1.json.",
        "care_plans_and_next_steps": None
    }
}
with open(path, "w", encoding="utf-8") as f:
    _json.dump(content, f, indent=2, ensure_ascii=False)
print("[FIXED] YN Labs 5-28-26_3.json (replaced empty template)")
