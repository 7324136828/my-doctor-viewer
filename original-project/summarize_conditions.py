import json

MERGED_PATH = "merged.json"
OUTPUT_PATH = "conditions_summary.json"

with open(MERGED_PATH, "r", encoding="utf-8") as f:
    merged = json.load(f)

summary = {}

for filename, data in merged.items():
    conditions = data.get("Identified Conditions (array of strings)", [])
    for condition in conditions:
        if "Normal" in str(condition):
            continue
        if "No " in str(condition):
            continue
        if condition not in summary:
            summary[condition] = {"incident count": 0, "files": []}
        summary[condition]["incident count"] += 1
        summary[condition]["files"].append(filename)

sorted_summary = dict(sorted(summary.items(), key=lambda x: x[1]["incident count"], reverse=True))

with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(sorted_summary, f, indent=2, ensure_ascii=False)

print(f"Done. {len(summary)} unique conditions written to {OUTPUT_PATH}")
