"""
Plot trends of lab test values (ALT, LDL, glucose, ...) over time.

Data flow:
  metadata.json         -> which json/ files are test results, and each file's clinical date
  json/<file>           -> the actual analyte values inside properties.significant_test_results

significant_test_results has no fixed shape across the MedGemma outputs. Observed forms:
  * list of records:  {test_name|parameter, result|value|result_value, reference_range|range|normal_range, units}
  * panel dict:       {"cardiac_profile": {"hdl": "57.3 mg/dL", ...}, ...}
  * single-test dict: {test_name, results: [...]}
  * qualitative:      {finding: "...", organ: "..."}  (no number -> skipped)
So extraction is a recursive walk that emits (analyte_name, raw_value_string) pairs,
canonicalizes the name against a synonym table, and pulls the first plausible number.

Outputs:
  test_trends.csv        long format: date, analyte, value, unit, flag, reference_range, ...
  test_trends.pdf        report: overview grid + one detail page per analyte, with the
                         normal (reference) range shaded and out-of-range points marked
  test_trends.png        the overview grid as a single image (quick view)

Usage:
  python plot_test_trends.py                     # default panel of common analytes
  python plot_test_trends.py --analytes ALT LDL  # only these
  python plot_test_trends.py --list              # list every analyte found, with count, then exit
  python plot_test_trends.py --all               # plot every analyte with >=2 dated points
  python plot_test_trends.py --min-points 3      # require at least N points to plot an analyte
  python plot_test_trends.py --no-png            # skip the PNG, write only CSV + PDF
"""
import argparse
import csv
import json
import os
import re
from collections import defaultdict, Counter
from datetime import date, datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_pdf import PdfPages

BASE = os.path.dirname(os.path.abspath(__file__))
JSON_DIR = os.path.join(BASE, "json")
METADATA_FILE = os.path.join(BASE, "metadata.json")
CSV_OUT = os.path.join(BASE, "test_trends.csv")
PNG_OUT = os.path.join(BASE, "test_trends.png")
PDF_OUT = os.path.join(BASE, "test_trends.pdf")

BAND_COLOR = "#16a34a"   # normal-range shading
LINE_COLOR = "#2563eb"   # trend line / in-range points
OOR_COLOR = "#ea580c"    # out-of-range points
SUSPECT_COLOR = "#dc2626"

# Only these primary categories carry quantitative results.
TEST_CATEGORIES = {"lab_result", "genetic_screen"}

NAME_KEYS = ("test_name", "parameter", "name", "test", "analyte", "component", "panel_name")
VALUE_KEYS = ("result_value", "measured_value", "value", "result")
# keys that are metadata, never an analyte, when a dict is read panel-style
META_KEYS = {
    "collection_date", "collected_date", "result_date", "report_date", "date", "date_time",
    "panel_name", "panel", "specimen", "specimen_type", "status", "result_status",
    "ordering_doctor", "ordering_provider", "provider", "units", "unit", "uom",
    "reference_range", "reference_interval", "ref_interval", "reference", "range",
    "normal_range", "normal_value", "interpretive_ranges", "previous_result",
    "interpretation", "notes", "note", "method", "flag", "lab", "resulting_lab",
    "test_name", "parameter", "name", "test", "analyte", "component",
    "result", "value", "result_value", "measured_value", "results",
    "threshold", "category", "type", "condition", "summary", "finding", "organ",
    "variant_details", "gene", "mutation_type", "zygosity", "classification",
}

UNIT_KEYS = ("units", "unit", "uom")
REF_KEYS = ("reference_range", "reference_interval", "ref_interval", "normal_range",
            "range", "reference", "interpretive_ranges", "normal_value")
ITEM_DATE_KEYS = ("collection_date", "collected_date", "date", "result_date")

# ---------------------------------------------------------------------------
# Analyte canonicalization. Maps many observed spellings to one display label.
# Matched case-insensitively; longest key wins so "ldl cholesterol" beats "ldl".
# ---------------------------------------------------------------------------
SYNONYMS = {
    # liver
    "alt": "ALT", "alanine aminotransferase": "ALT", "alanine aminotransferase (alt)": "ALT",
    "sgpt": "ALT", "sgpt_alt": "ALT", "sgpt alt": "ALT", "alt (sgpt)": "ALT",
    "ast": "AST", "aspartate aminotransferase": "AST", "aspartate aminotransferase (ast)": "AST",
    "sgot": "AST", "sgot_ast": "AST", "sgot ast": "AST", "ast (sgot)": "AST",
    "alkaline phosphatase": "Alkaline Phosphatase", "alk phos": "Alkaline Phosphatase",
    "bilirubin total": "Total Bilirubin", "total bilirubin": "Total Bilirubin",
    "bilirubin, total": "Total Bilirubin", "t bili": "Total Bilirubin",
    "bilirubin direct": "Direct Bilirubin", "direct bilirubin": "Direct Bilirubin",
    "albumin": "Albumin", "protein, total": "Total Protein", "total protein": "Total Protein",
    "globulin": "Globulin", "globulin, total": "Globulin",
    "gamma glutamyl transferase": "GGT", "ggt": "GGT",
    # lipids
    "ldl": "LDL", "ldl cholesterol": "LDL", "ldl-c": "LDL", "ldl chol": "LDL",
    "ldl cholesterol calc": "LDL", "ldl calculated": "LDL", "ldl chol calc": "LDL",
    "ldl cholesterol, calculated": "LDL", "ldl cholesterol calc (nih)": "LDL",
    "ldl chol calc (nih)": "LDL", "vldl": "VLDL", "vldl cholesterol": "VLDL",
    "vldl cholesterol cal": "VLDL", "cholesterol/hdl ratio": "Cholesterol/HDL Ratio",
    "hdl": "HDL", "hdl cholesterol": "HDL", "hdl-c": "HDL",
    "cholesterol": "Total Cholesterol", "total cholesterol": "Total Cholesterol",
    "cholesterol, total": "Total Cholesterol", "chol": "Total Cholesterol",
    "triglycerides": "Triglycerides", "trig": "Triglycerides", "triglyceride": "Triglycerides",
    # metabolic
    "glucose": "Glucose", "fasting glucose": "Glucose",
    "hemoglobin a1c": "HbA1c", "hba1c": "HbA1c", "a1c": "HbA1c", "hemoglobin a1c (hba1c)": "HbA1c",
    "bun": "BUN", "urea nitrogen": "BUN", "blood urea nitrogen": "BUN",
    "creatinine": "Creatinine", "egfr": "eGFR", "gfr": "eGFR",
    "sodium": "Sodium", "potassium": "Potassium", "chloride": "Chloride",
    "carbon dioxide": "CO2", "co2": "CO2", "bicarbonate": "CO2",
    "calcium": "Calcium", "anion gap": "Anion Gap",
    # CBC
    "wbc": "WBC", "white blood cell count": "WBC", "white blood cell": "WBC",
    "wbc count": "WBC", "leukocytes": "WBC",
    "rbc": "RBC", "red blood cell count": "RBC", "red blood cell": "RBC", "rbc count": "RBC",
    "hemoglobin": "Hemoglobin", "hgb": "Hemoglobin",
    "hematocrit": "Hematocrit", "hct": "Hematocrit",
    "platelet count": "Platelets", "platelets": "Platelets", "plt": "Platelets",
    "mcv": "MCV", "mch": "MCH", "mchc": "MCHC", "rdw": "RDW", "mpv": "MPV",
    # endocrine / vitamins
    "tsh": "TSH", "t4 free": "Free T4", "free t4": "Free T4", "t3 free": "Free T3",
    "vitamin d 25-hydroxy total": "Vitamin D", "vitamin d": "Vitamin D",
    "25-hydroxyvitamin d": "Vitamin D", "vitamin b12": "Vitamin B12", "b12": "Vitamin B12",
}

DEFAULT_ANALYTES = [
    "ALT", "AST", "Alkaline Phosphatase", "Total Bilirubin",
    "LDL", "HDL", "Total Cholesterol", "Triglycerides",
    "Glucose", "HbA1c", "Creatinine", "eGFR", "BUN",
    "Hemoglobin", "WBC", "Platelets", "TSH", "Vitamin D",
]

TODAY = date(2026, 7, 10)


def canonical(name):
    if not isinstance(name, str):
        return None
    key = re.sub(r"\s+", " ", name.strip().lower())
    if key in SYNONYMS:
        return SYNONYMS[key]
    # Progressive cleanup for OCR/qualifier noise, retrying the table after each step:
    #   "A LDL Chol Calc (NIH)" -> a stray leading flag letter + trailing calc qualifier
    #   "Protein, Total�"   -> OCR replacement char
    variants = []
    k = key.replace("�", "").strip()
    k = re.sub(r"[.,;:]", "", k)
    variants.append(k)
    k = re.sub(r"^a\s+(?=[a-z])", "", k)                      # drop leading "a " artifact
    variants.append(k)
    k = re.sub(r"\s*\(nih\)$", "", k)                          # drop "(nih)"
    k = re.sub(r"\s+(calc|calculated|cal|total|automated)$", "", k).strip()
    variants.append(k)
    for v in variants:
        if v in SYNONYMS:
            return SYNONYMS[v]
    return None


NUM_RE = re.compile(r"[-+]?\d*\.?\d+")
FLAG_RE = re.compile(r"\b(HIGH|LOW|CRITICAL|ABNORMAL|H|L|POSITIVE|NEGATIVE)\b", re.I)


def parse_value(raw):
    """Return (float value, flag) from a messy value cell, or (None, flag)."""
    if isinstance(raw, (int, float)):
        return float(raw), None
    if not isinstance(raw, str):
        return None, None
    s = raw.strip()
    if not s:
        return None, None
    flag = None
    fm = FLAG_RE.search(s)
    if fm:
        flag = fm.group(1).upper()
    # kill reference-range-looking substrings so we never grab a range bound:
    # e.g. "119 (70-100)" -> keep 119, drop the parenthetical range
    s_wo_range = re.sub(r"\(?\s*\d+\.?\d*\s*[-–]\s*\d+\.?\d*\s*\)?", " ", s)
    m = NUM_RE.search(s_wo_range) or NUM_RE.search(s)
    if not m:
        return None, flag
    try:
        return float(m.group(0)), flag
    except ValueError:
        return None, flag


def parse_ref(ref):
    """Parse a reference range into (low, high); either bound may be None.

    Handles '65-139 mg/dL', '0.5-1.2', '140 - 440', '>=60', '<0.2', '4.30 - 6.00'.
    Returns None when nothing sensible can be read.
    """
    if ref is None:
        return None
    s = str(ref).replace("–", "-").replace("—", "-").replace("≥", ">=").replace("≤", "<=")
    # normalize spelled-out / OCR'd one-sided forms: "> OR = 60", "greater than or equal to 60"
    s = re.sub(r">\s*or\s*=", ">=", s, flags=re.I)
    s = re.sub(r"<\s*or\s*=", "<=", s, flags=re.I)
    s = re.sub(r"greater than or equal to", ">=", s, flags=re.I)
    s = re.sub(r"less than or equal to", "<=", s, flags=re.I)
    s = re.sub(r"greater than", ">", s, flags=re.I)
    s = re.sub(r"less than", "<", s, flags=re.I)
    two = re.search(r"(\d+\.?\d*)\s*-\s*(\d+\.?\d*)", s)
    if two:
        lo, hi = float(two.group(1)), float(two.group(2))
        if lo <= hi:
            return (lo, hi)
    m = re.search(r">=?\s*(\d+\.?\d*)", s)
    if m:
        return (float(m.group(1)), None)
    m = re.search(r"<=?\s*(\d+\.?\d*)", s)
    if m:
        return (None, float(m.group(1)))
    return None


def fmt_band(band, unit):
    lo, hi = band
    u = f" {unit}" if unit else ""
    if lo is not None and hi is not None:
        return f"Normal: {lo:g}–{hi:g}{u}"
    if lo is not None:
        return f"Normal: ≥{lo:g}{u}"
    return f"Normal: ≤{hi:g}{u}"


def out_of_range(value, band):
    if not band:
        return False
    lo, hi = band
    return (lo is not None and value < lo) or (hi is not None and value > hi)


def parse_unit(record, raw_value):
    for k in UNIT_KEYS:
        if isinstance(record.get(k), str) and record[k].strip():
            return record[k].strip()
    if isinstance(raw_value, str):
        m = re.search(r"(mg/dL|g/dL|IU/L|U/L|mmol/L|mEq/L|ng/mL|pg/mL|mcg/dL|%|"
                      r"Thousand/uL|Million/uL|fL|pg|mL/min[^ ]*)", raw_value, re.I)
        if m:
            return m.group(1)
    return None


MONTHS = "January February March April May June July August September October November December".split()


def parse_date(s):
    """Normalize an observed date string to a datetime.date, or None."""
    if not isinstance(s, str):
        return None
    t = s.strip()
    if not t:
        return None
    t = re.sub(r"^(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s*", "", t, flags=re.I)
    t = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?\s*([AP]\.?M\.?)?$", "", t, flags=re.I).strip()
    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2})\s*[-–]\s*\d{1,2},?\s*(\d{4})$", t)
    if m:
        t = f"{m.group(1)} {m.group(2)}, {m.group(3)}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        try:
            return datetime.strptime(t, "%Y-%m-%d").date()
        except ValueError:
            return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y",
                "%d-%b-%Y", "%d-%B-%Y", "%m-%d-%Y", "%b %d %Y", "%B %d %Y"):
        try:
            dt = datetime.strptime(t, fmt).date()
            if dt.year >= 1900:
                return dt
        except ValueError:
            continue
    return None


def emit_records(node, sink, panel=None):
    """Recursively walk significant_test_results, appending analyte records to sink.

    Each appended item: {"name":..., "raw":..., "unit":..., "ref":..., "item_date":...}
    """
    if isinstance(node, list):
        for item in node:
            emit_records(item, sink, panel)
        return
    if not isinstance(node, dict):
        return

    name = next((node[k] for k in NAME_KEYS if isinstance(node.get(k), str)), None)
    raw_value = next((node[k] for k in VALUE_KEYS if k in node), None)
    unit = next((node[k] for k in UNIT_KEYS if isinstance(node.get(k), str)), None)
    ref = next((node[k] for k in REF_KEYS if node.get(k) is not None), None)
    item_date = next((node[k] for k in ITEM_DATE_KEYS if isinstance(node.get(k), str)), None)
    interp = " ".join(str(node[k]) for k in ("interpretation", "notes", "note", "status")
                      if node.get(k))

    if name is not None and raw_value is not None and not isinstance(raw_value, (dict, list)):
        sink.append({"name": name, "raw": raw_value, "unit": unit, "ref": ref,
                     "item_date": item_date, "interp": interp})

    if name is None:
        # treat this dict as a panel: analyte-name -> value-ish
        for k, v in node.items():
            if k.lower() in META_KEYS:
                continue
            if isinstance(v, (str, int, float)):
                sink.append({"name": k, "raw": v, "unit": None, "ref": None,
                             "item_date": None, "interp": ""})
            elif isinstance(v, (dict, list)):
                emit_records(v, sink, panel=k)
    else:
        # named record: still recurse into nested containers (e.g. results: [...])
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                emit_records(v, sink, panel=name)


def extract_all():
    meta = json.load(open(METADATA_FILE, encoding="utf-8"))["files"]
    rows = []  # long-format extracted points
    unmatched = Counter()

    for fname, entry in meta.items():
        if entry["primary_category"] not in TEST_CATEGORIES:
            continue
        file_date = parse_date((entry.get("clinical_date") or {}).get("value"))
        doc = json.load(open(os.path.join(JSON_DIR, fname), encoding="utf-8"))
        st = doc.get("properties", {}).get("significant_test_results")
        if st is None:
            continue

        sink = []
        emit_records(st, sink)
        for rec in sink:
            value, flag = parse_value(rec["raw"])
            if value is None:
                continue
            canon = canonical(rec["name"])
            if canon is None:
                unmatched[re.sub(r"\s+", " ", str(rec['name']).strip().lower())] += 1
                continue
            when = parse_date(rec["item_date"]) or file_date
            if when is None or when > TODAY:
                continue
            unit = rec["unit"] or parse_unit(rec, rec["raw"])
            # The source (MedGemma) sometimes flags a value it could not read reliably;
            # honor that rather than plot a hallucinated number as if it were clean.
            suspect = bool(re.search(r"erroneous|misread|potentially|unable to|illegible",
                                     rec.get("interp", ""), re.I))
            ref_raw = rec["ref"] if isinstance(rec["ref"], str) else (
                None if rec["ref"] is None else str(rec["ref"]))
            rows.append({
                "date": when, "analyte": canon, "value": value,
                "unit": unit or "", "flag": flag or "",
                "reference_range": ref_raw or "", "ref_band": parse_ref(rec["ref"]),
                "suspect": suspect, "source_file": fname, "raw_name": rec["name"],
            })
    return rows, unmatched


def canonical_band(rows, analyte):
    """Pick the most common parsed reference band for an analyte, plus a unit."""
    bands = Counter(r["ref_band"] for r in rows
                    if r["analyte"] == analyte and r["ref_band"])
    if not bands:
        return None, ""
    band = bands.most_common(1)[0][0]
    unit = next((r["unit"] for r in rows
                 if r["analyte"] == analyte and r["ref_band"] == band and r["unit"]), "")
    return band, unit


def write_csv(rows):
    rows_sorted = sorted(rows, key=lambda r: (r["analyte"], r["date"]))
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "analyte", "value", "unit", "flag", "reference_range",
                    "out_of_range", "suspect", "source_file", "raw_name"])
        for r in rows_sorted:
            oor = out_of_range(r["value"], r.get("ref_band")) and not r.get("suspect")
            w.writerow([r["date"].isoformat(), r["analyte"], r["value"], r["unit"],
                        r["flag"], r.get("reference_range", ""),
                        "yes" if oor else "", "yes" if r.get("suspect") else "",
                        r["source_file"], r["raw_name"]])
    n_suspect = sum(1 for r in rows if r.get("suspect"))
    print(f"Wrote {CSV_OUT} ({len(rows)} data points"
          + (f", {n_suspect} flagged suspect by source" if n_suspect else "") + ")")


def build_series(rows, analytes, min_points):
    """Return a list of per-analyte series dicts that meet the point threshold."""
    clean = defaultdict(list)
    suspect = defaultdict(list)
    for r in rows:
        (suspect if r.get("suspect") else clean)[r["analyte"]].append(
            (r["date"], r["value"], r["unit"]))

    out = []
    for a in analytes:
        pts = sorted(set(clean.get(a, [])))
        by_day = defaultdict(list)  # collapse same-day duplicates by mean
        for d, v, u in pts:
            by_day[d].append(v)
        merged = sorted((d, sum(vs) / len(vs)) for d, vs in by_day.items())
        if len(merged) < min_points:
            continue
        band, band_unit = canonical_band(rows, a)
        unit = next((u for _, _, u in pts if u), "") or band_unit
        out.append({
            "analyte": a,
            "points": merged,
            "suspect": sorted(set(suspect.get(a, []))),
            "unit": unit,
            "band": band,
        })
    return out


def draw_panel(ax, s, detailed=False):
    """Draw one analyte's trend, its normal-range band, and any suspect points."""
    fs = 9 if detailed else 7          # tick font
    ms = 6 if detailed else 4          # marker size
    xs = [d for d, _ in s["points"]]
    ys = [v for _, v in s["points"]]
    band = s["band"]

    # y-limits from the clean data AND the finite band bounds, so the normal range
    # is always visible even when every reading sits above or below it.
    span = list(ys)
    if band:
        span += [b for b in band if b is not None]
    lo, hi = min(span), max(span)
    pad = (hi - lo) * 0.12 or (abs(hi) * 0.1 or 1.0)
    y0, y1 = lo - pad, hi + pad
    ax.set_ylim(y0, y1)

    # shaded normal range
    if band:
        blo = band[0] if band[0] is not None else y0
        bhi = band[1] if band[1] is not None else y1
        ax.axhspan(blo, bhi, color=BAND_COLOR, alpha=0.12, zorder=0)
        for b in band:
            if b is not None:
                ax.axhline(b, color=BAND_COLOR, alpha=0.5, lw=0.8, ls="--", zorder=1)
        ax.text(0.015, 0.04, fmt_band(band, s["unit"]), transform=ax.transAxes,
                fontsize=fs, color=BAND_COLOR, va="bottom")

    # trend line, then in-range vs out-of-range markers on top
    ax.plot(xs, ys, lw=1.4, color=LINE_COLOR, zorder=2)
    in_x = [d for d, v in s["points"] if not out_of_range(v, band)]
    in_y = [v for d, v in s["points"] if not out_of_range(v, band)]
    oor_x = [d for d, v in s["points"] if out_of_range(v, band)]
    oor_y = [v for d, v in s["points"] if out_of_range(v, band)]
    ax.scatter(in_x, in_y, s=ms * ms, color=LINE_COLOR, zorder=3)
    ax.scatter(oor_x, oor_y, s=ms * ms + 6, color=OOR_COLOR, zorder=4,
               label="out of range")

    # suspect points: source flagged as possibly misread; kept off the trend line,
    # clamped to the top margin if their (unreliable) value is off-scale.
    for sd, sv, _ in s["suspect"]:
        inside = y0 <= sv <= y1
        y_at = sv if inside else y1
        ax.scatter([sd], [y_at], marker="x", s=44, color=SUSPECT_COLOR, zorder=5,
                   clip_on=False)
        label = "suspect" if inside else f"suspect ({sv:g})"
        ax.annotate(label, (sd, y_at), textcoords="offset points", xytext=(4, -6),
                    fontsize=fs - 1, color=SUSPECT_COLOR)

    title = s["analyte"] + (f"  ({s['unit']})" if s["unit"] else "")
    if s["suspect"]:
        title += "  ⚠"
    ax.set_title(title, fontsize=fs + 3, loc="left")
    ax.grid(True, alpha=0.25, lw=0.5)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(30)
        lbl.set_fontsize(fs)
        lbl.set_ha("right")
    ax.tick_params(axis="y", labelsize=fs)
    latest = ys[-1]
    ax.annotate(f"{latest:g}", (xs[-1], latest), textcoords="offset points",
                xytext=(4, 4), fontsize=fs, color=LINE_COLOR)


def grid_figure(series_list, title):
    n = len(series_list)
    ncol = 2 if n > 1 else 1
    nrow = (n + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(6.6 * ncol, 2.9 * nrow), squeeze=False)
    for idx, s in enumerate(series_list):
        draw_panel(axes[idx // ncol][idx % ncol], s, detailed=False)
    for j in range(n, nrow * ncol):
        axes[j // ncol][j % ncol].axis("off")
    fig.suptitle(title, fontsize=13, x=0.02, ha="left")
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    return fig


def detail_figure(s, rows):
    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    draw_panel(ax, s, detailed=True)
    pts = s["points"]
    span = f"{pts[0][0]} to {pts[-1][0]}"
    sources = len({r["source_file"] for r in rows if r["analyte"] == s["analyte"]})
    band_txt = fmt_band(s["band"], s["unit"]) if s["band"] else "no reference range in source"
    oor = sum(1 for _, v in pts if out_of_range(v, s["band"]))
    caption = (f"{len(pts)} result date(s), {span}  ·  {sources} source document(s)  ·  "
               f"{band_txt}"
               + (f"  ·  {oor} reading(s) out of range" if oor else "")
               + ("  ·  ⚠ includes value(s) the source flagged as possibly misread"
                  if s["suspect"] else ""))
    fig.text(0.02, 0.02, caption, fontsize=8, color="#374151")
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    return fig


def render(series_list, rows, want_png):
    if not series_list:
        print("No analytes met the minimum-points threshold; nothing to plot.")
        return
    title = "Lab test value trends — Yuzhe Ni"

    with PdfPages(PDF_OUT) as pdf:
        fig = grid_figure(series_list, title + "  (overview)")
        pdf.savefig(fig)
        if want_png:
            fig.savefig(PNG_OUT, dpi=140)
        plt.close(fig)
        for s in series_list:
            fig = detail_figure(s, rows)
            pdf.savefig(fig)
            plt.close(fig)
        meta = pdf.infodict()
        meta["Title"] = title
        meta["Subject"] = "Lab test value trends with reference ranges"
    print(f"Wrote {PDF_OUT} ({1 + len(series_list)} pages: overview + "
          f"{len(series_list)} analyte detail pages)")
    if want_png:
        print(f"Wrote {PNG_OUT} (overview grid, {len(series_list)} panels)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analytes", nargs="+", metavar="NAME",
                    help="canonical analyte names to plot (default: a common panel)")
    ap.add_argument("--all", action="store_true", help="plot every analyte found")
    ap.add_argument("--list", action="store_true",
                    help="list analytes found (with point counts) and exit")
    ap.add_argument("--min-points", type=int, default=2,
                    help="minimum dated points required to plot an analyte (default 2)")
    ap.add_argument("--show-unmatched", action="store_true",
                    help="also print analyte names that could not be canonicalized")
    ap.add_argument("--no-png", action="store_true",
                    help="write only CSV + PDF, skip the PNG overview image")
    args = ap.parse_args()

    rows, unmatched = extract_all()
    write_csv(rows)

    found = Counter(r["analyte"] for r in rows)
    if args.list:
        print(f"\n{len(found)} canonical analytes extracted:")
        for a, c in found.most_common():
            dates = sorted({r["date"] for r in rows if r["analyte"] == a})
            print(f"  {a:20} {c:3} points  {dates[0]} .. {dates[-1]}")
        if args.show_unmatched:
            print(f"\n{len(unmatched)} un-canonicalized names (add to SYNONYMS to include):")
            for nm, c in unmatched.most_common(40):
                print(f"  {c:3}  {nm}")
        return

    if args.all:
        analytes = [a for a, _ in found.most_common()]
    elif args.analytes:
        analytes = []
        for a in args.analytes:
            hit = canonical(a) or (a if a in found else None)
            analytes.append(hit or a)
    else:
        analytes = [a for a in DEFAULT_ANALYTES if a in found]

    print(f"\nPlotting {len(analytes)} analyte(s): {', '.join(analytes)}")
    series_list = build_series(rows, analytes, args.min_points)
    render(series_list, rows, want_png=not args.no_png)

    if args.show_unmatched and unmatched:
        print(f"\n{sum(unmatched.values())} values under {len(unmatched)} un-canonicalized "
              f"names were skipped (use --list --show-unmatched to see them).")


if __name__ == "__main__":
    main()
