"""
Categorize the JSON summaries in json/ and emit metadata.json.

Deterministic and evidence-bearing: every category records the literal string
that produced it. Ambiguous files are flagged needs_review rather than guessed.
No LLM is used.

Signals are read from three places:
  json/<stem>.json   parsed  -> populated-block fit, MRN, dates, provider
  chunks/<stem>.txt  raw     -> institution letterhead, anchored content regexes
  manifest.json              -> provenance, and packet inheritance for multi-chunk sources

Usage:
    python categorize_json.py            # generate metadata.json
    python categorize_json.py --verify   # re-read metadata.json and assert invariants
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
JSON_DIR = os.path.join(BASE, "json")
CHUNKS_DIR = os.path.join(BASE, "chunks")
MANIFEST_FILE = os.path.join(BASE, "manifest.json")
METADATA_FILE = os.path.join(BASE, "metadata.json")

TODAY = date(2026, 7, 10)

# ---------------------------------------------------------------------------
# Config -- all tuning knobs live here, never scattered as literals below
# ---------------------------------------------------------------------------

W_PRIOR = 3.0
W_INST = 1.0
W_BLOCK = 1.5
TITLE_REGION_CHARS = 600   # letterhead / title band
TITLE_BOOST = 2.0
ADMIT_FLOOR = 3.0          # "a family prior alone, and nothing else"
ADMIT_FRACTION = 0.40      # a category joins categories[] at >=40% of the top score

CATEGORIES = [
    "lab_result", "genetic_screen", "clinic_note", "after_visit_summary",
    "pathology_report", "operative_report", "discharge_summary", "ed_note",
    "diagnostic_study", "immunization_record", "patient_message",
    "records_release_admin", "portal_chrome", "unknown",
]

# Filename family -> (category, prior strength). First match wins.
# The prior is a floor, not a verdict: content can outscore it.
FAMILY_PRIORS = [
    (r"^NYU Langone Health MyChart - Test Details", "lab_result", 1.0),
    (r"^NYU Langone Health MyChart - Past Visit Details", "clinic_note", 1.0),
    (r"^NYU Langone Health MyChart - Note from Care Team", "patient_message", 0.9),
    (r"^MyChart - Test Details", "lab_result", 1.0),
    (r"^MyChart - Past Visit Details", "clinic_note", 1.0),
    (r"^AVS - ", "after_visit_summary", 1.0),
    (r"^Scan - .*PATHOLOGY", "pathology_report", 0.9),
    (r"^Scan - RADIOLOGY", None, 0.0),          # filename lies: it is a sleep study
    (r"^Scan - CBC", "lab_result", 0.6),
    (r"^GetPDFaspx", "records_release_admin", 0.35),  # weak: packets are mixed
    (r"^labreport", "lab_result", 1.0),
    (r"^YN Labs", "lab_result", 0.6),           # genetic_screen must be earned by content
    (r"^GraphLabTestResults", "lab_result", 1.0),
    (r"^Immunizations? ?", "immunization_record", 0.8),
    (r"^Message Detail", "patient_message", 0.8),
    (r"^Document", "after_visit_summary", 0.7),
    (r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", "lab_result", 0.7),  # UUID -> Labcorp labs
    (r"^[0-9A-F]{32}$", "lab_result", 0.7),                   # hex -> Rutgers CMP
]

# MRN is the strongest institution proof and overrides hostname regexes.
MRN_MAP = {
    "16489597": "NYU Langone",
    "10173445": "RWJBarnabas",
    "39114407": "Memorial Sloan Kettering",
    "140262180": "Columbia",
    "10288752": "Premise Health",
    "5822082": "RWJBarnabas",
    "006296456": "RWJ University Hospital",
    "5322566": "St. Peter's University Hospital",
}

INSTITUTION_PATTERNS = [
    (r"nyulangone|NYU Langone", "NYU Langone"),
    (r"mychart\.rwjbh\.org|rwjbh", "RWJBarnabas"),
    (r"ROBERT WOOD JOHNSON UNIV", "RWJ University Hospital"),
    (r"ST PETERS UNIVERSITY HOSPITAL", "St. Peter's University Hospital"),
    (r"mskcc|Sloan Kettering", "Memorial Sloan Kettering"),
    (r"myconnectnyc", "Columbia"),
    (r"MyQuest|questdiagnostics", "Quest Diagnostics"),
    (r"[Ll]abcorp", "Labcorp"),
    (r"Watermark Medical", "Watermark Medical"),
    (r"Premise Health", "Premise Health"),
    (r"Clinical Reference Lab", "Clinical Reference Laboratory"),
    (r"chconline\.ucr\.edu", "UCR Student Health"),
    (r"Hurtado", "Rutgers Student Health"),
]

# Small bonus when an institution's typical output includes this category.
INSTITUTION_FIT = {
    "Watermark Medical": {"diagnostic_study"},
    "Quest Diagnostics": {"lab_result"},
    "Labcorp": {"lab_result", "genetic_screen"},
    "Clinical Reference Laboratory": {"lab_result"},
    "Premise Health": {"lab_result"},
    "Memorial Sloan Kettering": {"after_visit_summary"},
    "Columbia": {"after_visit_summary"},
    "RWJ University Hospital": {"records_release_admin"},
    "St. Peter's University Hospital": {"records_release_admin"},
}

# Anchored, weighted content rules. Never a bare substring.
# Matches inside the first TITLE_REGION_CHARS get TITLE_BOOST.
CONTENT_RULES = [
    ("lab_result", r"(?mi)^\s*Reference Range", 1.5),
    ("lab_result", r"(?i)\bNormal range\s*:", 1.5),
    ("lab_result", r"(?i)\bCollected on\b", 1.5),
    ("lab_result", r"(?i)\bSpecimen Type\s*:", 1.2),
    ("lab_result", r"(?i)\bResulting lab\s*:", 1.5),
    ("lab_result", r"(?i)\b(CBC|Comprehensive Metabolic Panel|Basic Metabolic Panel|"
                   r"HEPATIC FUNCTION PANEL|Lipid Panel|Hemoglobin A1c)\b", 1.5),

    ("genetic_screen", r"(?i)\bcarrier (panel|screen)", 2.5),
    ("genetic_screen", r"(?i)\bpathogenic variant", 2.0),
    ("genetic_screen", r"(?i)\bInvitae\b", 2.0),
    ("genetic_screen", r"\bGCDH\b", 1.5),
    ("genetic_screen", r"\bNM_\d{6}", 2.0),

    ("clinic_note", r"(?mi)^\s*(History of Present Illness|Assessment and Plan|"
                    r"Review of Systems|Physical Exam(ination)?)\b", 2.0),
    ("clinic_note", r"(?i)\bProgress Note\b", 2.0),
    ("clinic_note", r"(?i)\bTelephone Encounter\b", 2.0),
    ("clinic_note", r"(?i)\bEncounter Type\b", 1.5),

    ("after_visit_summary", r"(?i)After Visit Summary", 2.5),
    ("after_visit_summary", r"(?i)\bAVS\b", 1.5),
    ("after_visit_summary", r"(?i)Today'?s Visit", 1.5),

    ("pathology_report", r"(?mi)^\s*DIAGNOSIS\s*:", 2.5),
    ("pathology_report", r"(?i)SURGICAL PATHOLOGY|DERMATOPATHOLOGY", 2.5),
    ("pathology_report", r"(?mi)^\s*(GROSS DESCRIPTION|MICROSCOPIC)", 2.0),
    ("pathology_report", r"(?i)Specimen (Submitted|Received)", 1.5),
    ("pathology_report", r"(?i)\bVerruca\b", 1.0),

    # The lookbehind keeps "Post-op Note" (a follow-up clinic note) from firing here:
    # a hyphen creates a word boundary, so a bare \bOp Note\b would match it.
    ("operative_report", r"(?i)OPERATIVE (REPORT|NOTE)|(?<![-\w])Op Note\b", 2.5),
    ("operative_report", r"(?mi)^\s*(PRE|POST)OPERATIVE DIAGNOSIS", 2.0),
    ("operative_report", r"(?i)Estimated Blood Loss", 2.0),
    ("operative_report", r"(?mi)^\s*Surgeon\s*:", 1.5),

    ("discharge_summary", r"(?i)DISCHARGE SUMMARY", 2.5),
    ("discharge_summary", r"(?i)Discharge Disposition", 2.0),
    ("discharge_summary", r"(?i)Discharge Instructions", 1.5),

    ("ed_note", r"(?i)ED Triage Note", 2.5),
    ("ed_note", r"(?i)Emergency Department", 2.0),
    ("ed_note", r"(?i)ED Abdominal Pain Order", 1.5),
    ("ed_note", r"\bNBED\b", 1.5),
    ("ed_note", r"(?i)Arrival Date", 1.5),

    # Title-anchored so stray body collisions ("paresthesia", proper nouns) cannot fire.
    ("diagnostic_study", r"(?i)Home Sleep Test|ARES Sleep Study|Sleep Study Report", 2.5),
    ("diagnostic_study", r"(?i)polysomnogra", 2.0),
    ("diagnostic_study", r"(?i)obstructive sleep apnea", 1.5),
    ("diagnostic_study", r"\bAHI\b", 1.0),
    ("diagnostic_study", r"\bRDI\b", 1.0),

    ("immunization_record", r"(?i)\bImmunization", 2.0),
    ("immunization_record", r"(?i)\b(Tdap|MMR|Varicella|Meningococcal)\b", 1.5),

    ("patient_message", r"(?mi)^\s*(From|To|Sent)\s*:", 1.5),
    ("patient_message", r"(?i)Message Detail", 2.0),
    ("patient_message", r"(?i)\bcare team\b", 1.0),

    ("records_release_admin", r"(?i)\bDatavant\b", 2.5),
    ("records_release_admin", r"(?i)smartrequest", 2.5),
    ("records_release_admin", r"(?i)Authorization (to|for) (Release|Disclose)", 2.0),
    ("records_release_admin", r"(?i)Release of Information", 2.0),
    ("records_release_admin", r"(?mi)^\s*INVOICE", 1.5),
    ("records_release_admin", r"(?i)Records [Ff]rom\s*:", 2.0),
    ("records_release_admin", r"\bHIPAA\b", 1.5),
]

NAV_TOKEN_RE = re.compile(
    r"(?mi)^\s*(Log ?out|Menu|Home|Appointments|Messages|Test Results|Billing|Visits)\s*$"
)

TAG_RULES = [
    ("genetic", r"(?i)Lynch syndrome|\bMSH2\b|genetic counsel|carrier (panel|screen)|"
                r"pathogenic variant|\bGCDH\b|\bInvitae\b"),
    ("radiology", r"(?i)\b(X-?ray|CT scan|MRI|ultrasound|radiograph)\b"),
    ("hematology", r"(?i)\bhematolog"),
]

# Which JSON block a category expects to see populated.
BLOCK_FIT = {
    "lab_result": "significant_test_results",
    "genetic_screen": "significant_test_results",
    "immunization_record": "significant_test_results",
    "clinic_note": "visit_notes",
    "ed_note": "visit_notes",
    "discharge_summary": "care_plans_and_next_steps",
    "after_visit_summary": "care_plans_and_next_steps",
    "patient_message": "care_plans_and_next_steps",
    "pathology_report": "significant_test_results",
    "operative_report": "visit_notes",
    "diagnostic_study": "significant_test_results",
}

PROP_BLOCKS = ["patient_demographics", "clinical_background",
               "significant_test_results", "visit_notes", "care_plans_and_next_steps"]

COMPILED_CONTENT = [(c, re.compile(p), w, p) for c, p, w in CONTENT_RULES]
COMPILED_TAGS = [(t, re.compile(p)) for t, p in TAG_RULES]
COMPILED_INST = [(re.compile(p), name) for p, name in INSTITUTION_PATTERNS]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_populated(v):
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() not in ("", "N/A", "None", "null", "-")
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def walk_scalars(obj, path=""):
    """Yield (key_path, key, value) for every scalar leaf."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}" if path else k
            if isinstance(v, (dict, list)):
                yield from walk_scalars(v, sub)
            else:
                yield sub, k, v
    elif isinstance(obj, list):
        for item in obj:
            yield from walk_scalars(item, path)


MONTHS = ("January February March April May June July August September "
          "October November December").split()
ABBR = [m[:3] for m in MONTHS]


def parse_date(s):
    """Normalize an observed date string to ISO YYYY-MM-DD, or None.

    Explicit ordered patterns -- not a fuzzy auto-parser, which misreads
    ranges like 'March 25-26, 2019'.
    """
    if not isinstance(s, str):
        return None
    t = s.strip()
    if not t:
        return None

    # strip a leading weekday
    t = re.sub(r"^(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s*", "", t, flags=re.I)
    # strip a trailing clock time
    t = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?\s*([AP]\.?M\.?)?$", "", t, flags=re.I).strip()

    # 'March 25-26, 2019' -> take the start day
    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2})\s*[-–]\s*\d{1,2},?\s*(\d{4})$", t)
    if m:
        t = f"{m.group(1)} {m.group(2)}, {m.group(3)}"

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return t

    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y",
                "%d-%b-%Y", "%d-%B-%Y", "%m-%d-%Y", "%B %d %Y", "%b %d %Y"):
        try:
            dt = datetime.strptime(t, fmt).date()
        except ValueError:
            continue
        if dt.year < 1900:
            return None
        return dt.isoformat()
    return None


PRINT_HEADER_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2}),\s*\d{1,2}:\d{2}\s*[AP]M")

DATE_KEY_PRIORITY = [
    ("collection_date", 0), ("collected_date", 0), ("specimen_date", 0),
    ("date_of_surgery", 1), ("surgery_date", 1), ("date_time", 1),
    ("result_date", 2), ("report_date", 2),
    ("date_of_visit", 3),
    ("order_date", 4), ("date", 5),
]


def resolve_date(doc, raw, primary_category):
    """Return (value, source, candidates). Never returns a print-header timestamp."""
    header_dates = set(PRINT_HEADER_RE.findall(raw[:300]))
    # Compare in ISO space too: MedGemma often rewrites the '7/10/26' portal print
    # header into date_of_visit as '7/10/2026', which no literal match would catch.
    header_iso = {iso for iso in (parse_date(d) for d in header_dates) if iso}

    # An immunization history spans years -- forcing one date invents precision.
    admins = [v for _, k, v in walk_scalars(doc) if k == "administration_date"]
    admin_iso = sorted({d for d in (parse_date(a) for a in admins) if d})
    if len(admin_iso) > 1:
        return None, f"no single encounter date (record spans {admin_iso[0]}..{admin_iso[-1]})", admin_iso

    buckets = defaultdict(list)
    for path, key, val in walk_scalars(doc):
        if not isinstance(val, str):
            continue
        if key.lower() in ("date_of_birth", "dob"):
            continue
        raw_val = val.strip()
        if raw_val in header_dates:
            continue
        for kname, prio in DATE_KEY_PRIORITY:
            if key.lower() == kname:
                iso = parse_date(raw_val)
                if iso and iso not in header_iso:
                    buckets[prio].append((iso, f"{path} '{raw_val}'"))
                break

    candidates = sorted({iso for lst in buckets.values() for iso, _ in lst})
    if not buckets:
        return None, None, candidates

    # future dates are implausible except for an AVS, which can be dated forward
    def plausible(iso):
        if primary_category == "after_visit_summary":
            return True
        return datetime.strptime(iso, "%Y-%m-%d").date() <= TODAY

    for prio in sorted(buckets):
        for iso, src in buckets[prio]:
            if plausible(iso):
                return iso, src, candidates
    return None, None, candidates


def find_mrn(doc, raw):
    for path, key, val in walk_scalars(doc):
        if key.lower() in ("mrn", "medical_record_number") and val:
            v = str(val).strip()
            if re.fullmatch(r"0?\d{6,9}", v):
                return v, f"json:{path}"
    m = re.search(r"MRN[:\s#]*([0-9]{6,9})", raw)
    if m:
        return m.group(1), f"chunk:MRN {m.group(1)}"
    return None, None


def find_institution(mrn, raw):
    if mrn and mrn in MRN_MAP:
        return MRN_MAP[mrn], f"MRN {mrn}"
    for rx, name in COMPILED_INST:
        m = rx.search(raw)
        if m:
            return name, f"chunk:{m.group(0)!r}"
    return None, None


def family_prior(stem):
    for pat, cat, strength in FAMILY_PRIORS:
        if re.search(pat, stem):
            return (cat, strength, pat) if cat else (None, 0.0, pat)
    return None, 0.0, None


def score_file(stem, doc, raw):
    """Pass 1: score one file on its own evidence."""
    scores = Counter()
    evidence = defaultdict(list)

    cat, strength, pat = family_prior(stem)
    if cat:
        scores[cat] += W_PRIOR * strength
        evidence[cat].append(f"family:{pat}")

    mrn, mrn_ev = find_mrn(doc, raw)
    inst, inst_ev = find_institution(mrn, raw)
    if inst:
        for c in INSTITUTION_FIT.get(inst, ()):
            scores[c] += W_INST
            evidence[c].append(f"institution:{inst}")

    title = raw[:TITLE_REGION_CHARS]
    for c, rx, w, pat_src in COMPILED_CONTENT:
        m = rx.search(raw)
        if not m:
            continue
        boost = TITLE_BOOST if rx.search(title) else 1.0
        scores[c] += w * boost
        snippet = " ".join(m.group(0).split())[:60]
        evidence[c].append(f"{'title' if boost > 1 else 'body'}:{snippet!r}")

    props = doc.get("properties", {})
    props = props if isinstance(props, dict) else {}
    populated = {b for b in PROP_BLOCKS if is_populated(props.get(b))}
    for c, block in BLOCK_FIT.items():
        if block in populated and scores.get(c, 0) > 0:
            scores[c] += W_BLOCK
            evidence[c].append(f"block:{block}")

    # portal_chrome rises as the file empties out
    nav_hits = len(NAV_TOKEN_RE.findall(raw))
    clinical = sum(v for c, v in scores.items() if c not in ("portal_chrome", "records_release_admin"))
    if len(populated) <= 1 and nav_hits >= 2 and clinical < 4.0:
        scores["portal_chrome"] += 2.0 + (5 - len(populated)) * 0.5
        evidence["portal_chrome"].append(f"{len(populated)} populated block(s), {nav_hits} nav tokens")

    # genetic_screen must be earned by content, never by institution alone
    if scores.get("genetic_screen", 0) > 0:
        content_ev = [e for e in evidence["genetic_screen"] if e.startswith(("title:", "body:"))]
        if not content_ev:
            del scores["genetic_screen"]
            evidence.pop("genetic_screen", None)

    tags = sorted({t for t, rx in COMPILED_TAGS if rx.search(raw)})

    return {
        "scores": scores, "evidence": evidence, "tags": tags,
        "mrn": mrn, "mrn_ev": mrn_ev, "inst": inst, "inst_ev": inst_ev,
        "populated": sorted(populated),
    }


def resolve(scores):
    if not scores:
        return "unknown", 0.0, 0.0
    ranked = scores.most_common()
    top = ranked[0][1]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    conf = top / (top + runner + 1e-9)
    return ranked[0][0], top, conf


def bucket(conf):
    return "high" if conf >= 0.66 else ("medium" if conf >= 0.45 else "low")


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------

def generate():
    manifest = json.load(open(MANIFEST_FILE, encoding="utf-8"))
    by_chunk = {e["chunk_file"]: e for e in manifest}
    groups = defaultdict(list)
    for e in manifest:
        groups[e["source_file"]].append(e["chunk_file"])

    stems = sorted(os.path.splitext(f)[0] for f in os.listdir(JSON_DIR) if f.endswith(".json"))

    raw_by_stem, doc_by_stem, scored = {}, {}, {}
    for stem in stems:
        doc = json.load(open(os.path.join(JSON_DIR, stem + ".json"), encoding="utf-8"))
        raw = open(os.path.join(CHUNKS_DIR, stem + ".txt"), encoding="utf-8", errors="replace").read()
        doc_by_stem[stem], raw_by_stem[stem] = doc, raw
        scored[stem] = score_file(stem, doc, raw)

    # Pass 2: packet inheritance for the multi-chunk sources only
    inherited = {}
    for source_file, chunk_files in groups.items():
        if len(chunk_files) <= 1:
            continue
        sibling_stems = [os.path.splitext(c)[0] for c in chunk_files]
        packet_id = os.path.splitext(source_file)[0]

        donor = next((s for s in sibling_stems if scored[s]["inst"]), None)
        donor_mrn = next((s for s in sibling_stems if scored[s]["mrn"]), None)
        is_release_packet = any(
            scored[s]["scores"].get("records_release_admin", 0) > 0 for s in sibling_stems
        )
        for s in sibling_stems:
            info = {"packet_id": packet_id, "siblings": sorted(sibling_stems)}
            if not scored[s]["inst"] and donor:
                scored[s]["inst"] = scored[donor]["inst"]
                scored[s]["inst_ev"] = "inherited via manifest source_file"
                info["inst_from"] = donor
            if not scored[s]["mrn"] and donor_mrn:
                scored[s]["mrn"] = scored[donor_mrn]["mrn"]
                scored[s]["mrn_ev"] = "inherited via manifest source_file"
                info["mrn_from"] = donor_mrn
            if is_release_packet and scored[s]["scores"].get("records_release_admin", 0) == 0:
                scored[s]["scores"]["records_release_admin"] = ADMIT_FLOOR
                scored[s]["evidence"]["records_release_admin"].append(
                    f"inherited:packet {packet_id}")
                info["release_packet"] = True
            inherited[s] = info

    files = {}
    for stem in stems:
        sc = scored[stem]
        scores, evidence = sc["scores"], sc["evidence"]
        primary, top, conf = resolve(scores)

        # If nothing self-evidenced and we're in a packet, fall back to the packet
        if primary == "unknown" and stem in inherited:
            primary, top, conf = "records_release_admin", ADMIT_FLOOR, 0.30

        cutoff = max(ADMIT_FLOOR, ADMIT_FRACTION * top) if top else 0
        admitted = [
            {"category": c, "score": round(v, 2), "evidence": evidence[c][:6]}
            for c, v in scores.most_common() if v >= cutoff
        ]
        if not admitted:
            admitted = [{"category": primary, "score": round(top, 2),
                         "evidence": evidence.get(primary, [])[:6]}]

        clinical_date, date_src, candidates = resolve_date(
            doc_by_stem[stem], raw_by_stem[stem], primary)

        entry = by_chunk[stem + ".txt"]
        pk = inherited.get(stem)

        doc = doc_by_stem[stem]
        provider = doc.get("doctor_name") or None
        if isinstance(provider, str) and not provider.strip():
            provider = None

        # A file carried solely by its filename prior has no content proof, however
        # lopsided its score margin looks. Flag it rather than assert it.
        primary_ev = evidence.get(primary, [])
        prior_only = not any(e.startswith(("title:", "body:")) for e in primary_ev)

        review_reasons = []
        if bucket(conf) == "low":
            review_reasons.append("low confidence")
        if primary == "unknown":
            review_reasons.append("no category scored")
        if top < ADMIT_FLOOR:
            review_reasons.append("below admit floor")
        if prior_only:
            review_reasons.append("classified on filename prior alone, no content anchor")
        if not sc["inst"]:
            review_reasons.append("institution unresolved")
        if pk and (pk.get("inst_from") or pk.get("mrn_from")):
            review_reasons.append("institution/MRN inherited from packet sibling")
        needs_review = bool(review_reasons)

        files[stem + ".json"] = {
            "primary_category": primary,
            "categories": admitted,
            "tags": sc["tags"],
            "provenance": {
                "source_file": entry["source_file"],
                "chunk_index": entry["chunk_index"],
                "total_chunks": entry["total_chunks"],
                "source_text_chars": len(raw_by_stem[stem]),
                "json_chars": os.path.getsize(os.path.join(JSON_DIR, stem + ".json")),
                "populated_blocks": sc["populated"],
                "packet": ({"id": pk["packet_id"], "siblings": pk["siblings"]} if pk else None),
            },
            "institution": {
                "value": sc["inst"],
                "evidence": sc["inst_ev"],
                "inherited_from": (pk or {}).get("inst_from"),
            },
            "mrn": {
                "value": sc["mrn"],
                "evidence": sc["mrn_ev"],
                "inherited": bool((pk or {}).get("mrn_from")),
            },
            "clinical_date": {
                "value": clinical_date,
                "source": date_src,
                "candidates": candidates,
            },
            "provider": {"value": provider, "source": "doctor_name" if provider else None},
            "confidence": bucket(conf),
            "confidence_score": round(conf, 3),
            "needs_review": needs_review,
            "review_reasons": review_reasons,
        }

    summary = {
        "total_files": len(files),
        "by_primary_category": dict(Counter(f["primary_category"] for f in files.values()).most_common()),
        "by_institution": dict(Counter(
            (f["institution"]["value"] or "unresolved") for f in files.values()).most_common()),
        "by_confidence": dict(Counter(f["confidence"] for f in files.values()).most_common()),
        "needs_review_count": sum(1 for f in files.values() if f["needs_review"]),
    }

    out = {"generated": datetime.now().isoformat(timespec="seconds"),
           "summary": summary, "files": files}
    with open(METADATA_FILE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)

    print(f"Wrote {METADATA_FILE} ({len(files)} files)\n")
    print("by_primary_category:")
    for k, v in summary["by_primary_category"].items():
        print(f"  {k:24} {v}")
    print("\nby_institution:")
    for k, v in summary["by_institution"].items():
        print(f"  {k:34} {v}")
    print(f"\nneeds_review: {summary['needs_review_count']}")
    return out


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

# Verified against the source chunks, not assumed from filenames. Two families are
# genuinely mixed: the RWJ "Past Visit Details" pages are mostly After Visit Summaries
# (they literally contain "AFTER VISIT SUMMARY" / "Today's Visit"), and several RWJ
# "Test Details" pages deliver Dermatopathology / SURGICAL PATHOLOGY reports.
FAMILY_ORACLE = [
    (r"^NYU Langone Health MyChart - Test Details", {"lab_result"}, 68),
    (r"^NYU Langone Health MyChart - Past Visit Details",
     {"clinic_note", "after_visit_summary", "operative_report"}, 18),
    (r"^MyChart - Test Details", {"lab_result", "pathology_report"}, 14),
    (r"^MyChart - Past Visit Details", {"clinic_note", "after_visit_summary"}, 11),
]

REQUIRED_KEYS = ["primary_category", "categories", "tags", "provenance",
                 "institution", "mrn", "clinical_date", "provider",
                 "confidence", "confidence_score", "needs_review", "review_reasons"]


def verify():
    meta = json.load(open(METADATA_FILE, encoding="utf-8"))
    files, summary = meta["files"], meta["summary"]
    manifest = json.load(open(MANIFEST_FILE, encoding="utf-8"))
    by_chunk = {e["chunk_file"]: e for e in manifest}
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))

    # 1 completeness
    on_disk = {f for f in os.listdir(JSON_DIR) if f.endswith(".json")}
    check("1a len(files)==157", len(files) == 157, f"got {len(files)}")
    check("1b files == json/ on disk", set(files) == on_disk,
          f"missing={sorted(on_disk - set(files))[:3]} extra={sorted(set(files) - on_disk)[:3]}")
    check("1c primary counts sum to 157", sum(summary["by_primary_category"].values()) == 157)
    missing_keys = {f: [k for k in REQUIRED_KEYS if k not in e] for f, e in files.items()}
    bad = {f: k for f, k in missing_keys.items() if k}
    check("1d all entries have required keys", not bad, str(list(bad.items())[:2]))
    check("1e all categories in vocabulary",
          all(e["primary_category"] in CATEGORIES for e in files.values()))

    # 2 family oracle
    for pat, expected, count in FAMILY_ORACLE:
        fam = [f for f in files if re.search(pat, f)]
        hits = [f for f in fam if files[f]["primary_category"] in expected]
        rate = len(hits) / len(fam) if fam else 0
        violators = [(f, files[f]["primary_category"]) for f in fam if f not in hits][:3]
        label = "|".join(sorted(expected))
        check(f"2 {pat[:34]} -> {label[:34]} >=95%",
              len(fam) == count and rate >= 0.95,
              f"n={len(fam)} (want {count}) rate={rate:.0%} violators={violators}")

    # 3 the five known filename/content mismatches
    f = "Scan - RADIOLOGY HERITAGE DOCUMENT - Jul 26, 2019.json"
    check("3a RADIOLOGY file is diagnostic_study",
          files[f]["primary_category"] == "diagnostic_study", files[f]["primary_category"])
    check("3b RADIOLOGY file has no radiology tag", "radiology" not in files[f]["tags"])
    check("3c Test Details 384939 -> MSK",
          files["MyChart - Test Details 384939.json"]["institution"]["value"] == "Memorial Sloan Kettering",
          files["MyChart - Test Details 384939.json"]["institution"]["value"])
    for n in ("38472192", "384838"):
        k = f"MyChart - Past Visit Details {n}.json"
        check(f"3d Past Visit {n} -> MSK",
              files[k]["institution"]["value"] == "Memorial Sloan Kettering",
              files[k]["institution"]["value"])
    k = "Scan - CBC WITH DIFFERENTIAL - Jul 8, 2025.json"
    check("3e Scan-CBC institution == Premise Health",
          files[k]["institution"]["value"] == "Premise Health", files[k]["institution"]["value"])
    k = "YN Labs 5-28-26_3.json"
    check("3f YN Labs _3 -> genetic_screen", files[k]["primary_category"] == "genetic_screen",
          files[k]["primary_category"])

    # 4 over-fire guards
    n_diag = summary["by_primary_category"].get("diagnostic_study", 0)
    check("4a exactly 1 diagnostic_study primary", n_diag == 1, f"got {n_diag}")
    offenders = []
    for name, e in files.items():
        if e["primary_category"] != "pathology_report":
            continue
        ev = " ".join(next(c["evidence"] for c in e["categories"]
                           if c["category"] == "pathology_report"))
        if not re.search(r"DIAGNOSIS|PATHOLOGY|Specimen|GROSS|MICROSCOPIC", ev, re.I):
            offenders.append(name)
    check("4b pathology_report primary always has a specimen anchor", not offenders, str(offenders[:3]))
    zero_hit = ["NYU Langone Health MyChart - Note from Care Team 844939394.json",
                "NYU Langone Health MyChart - Past Visit Details 447839.json",
                "NYU Langone Health MyChart - Test Details 57385673731.json",
                "NYU Langone Health MyChart - Test Details 574838184.json"]
    unk = [f for f in zero_hit if files[f]["primary_category"] == "unknown"]
    check("4c the 4 zero-content files are not 'unknown'", not unk, str(unk))

    # 5 inheritance invariant
    kids = [f for f in files if f.startswith("GetPDFaspx 1_")]
    check("5a GetPDFaspx 1_* count == 10", len(kids) == 10, f"got {len(kids)}")
    check("5b every packet child has an institution",
          all(files[f]["institution"]["value"] for f in kids),
          str([f for f in kids if not files[f]["institution"]["value"]]))
    check("5c every packet child links packet 'GetPDFaspx 1'",
          all((files[f]["provenance"]["packet"] or {}).get("id") == "GetPDFaspx 1" for f in kids))
    # Inheritance is a FALLBACK. Every GetPDFaspx chunk happens to carry MRN 006296456
    # in its own text, so it never fires here. The invariant that matters is that the
    # packet resolves to exactly one MRN and one institution across all 10 chunks.
    mrns = {files[f]["mrn"]["value"] for f in kids}
    insts = {files[f]["institution"]["value"] for f in kids}
    check("5d packet resolves to exactly one MRN", mrns == {"006296456"}, str(mrns))
    check("5e packet resolves to exactly one institution",
          insts == {"RWJ University Hospital"}, str(insts))
    bare = [f for f in files if re.match(r"^GetPDFaspx_\d", f)]
    check("5f second packet is St. Peter's",
          {files[f]["institution"]["value"] for f in bare} == {"St. Peter's University Hospital"},
          str({files[f]["institution"]["value"] for f in bare}))

    # 6 provenance integrity
    mism = [f for f, e in files.items()
            if e["provenance"]["chunk_index"] != by_chunk[f[:-5] + ".txt"]["chunk_index"]
            or e["provenance"]["total_chunks"] != by_chunk[f[:-5] + ".txt"]["total_chunks"]]
    check("6 provenance matches manifest.json", not mism, str(mism[:3]))

    # 7 date sanity
    hdr = {"7/10/26", "7/9/26", "2026-07-10", "2026-07-09"}
    bad_dates = [f for f, e in files.items() if e["clinical_date"]["value"] in hdr]
    check("7a no clinical_date is a print-header timestamp", not bad_dates, str(bad_dates[:3]))
    future = [f for f, e in files.items()
              if e["clinical_date"]["value"]
              and datetime.strptime(e["clinical_date"]["value"], "%Y-%m-%d").date() > TODAY
              and e["primary_category"] != "after_visit_summary"]
    check("7b no future clinical_date outside AVS", not future, str(future[:3]))

    npass = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok and detail else ""))
    print(f"\n{npass}/{len(results)} checks passed")

    review = sorted(f for f, e in files.items() if e["needs_review"])
    print(f"\nneeds_review ({len(review)}) -- the human spot-check budget:")
    for f in review:
        e = files[f]
        print(f"  {e['primary_category']:22} conf={e['confidence_score']:.2f}  {f}")
    return npass == len(results)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true",
                    help="re-read metadata.json and assert invariants")
    args = ap.parse_args()
    if args.verify:
        sys.exit(0 if verify() else 1)
    else:
        generate()
