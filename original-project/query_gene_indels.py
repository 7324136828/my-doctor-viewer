#!/usr/bin/env python3
"""
Query WGS VCF files for all variants (SNVs and indels) in a given gene
and annotate them with ClinVar clinical significance data via NCBI APIs.

Usage:
    python query_gene_indels.py --gene MSH2
    python query_gene_indels.py --gene MSH2 --input-dir ./input --pass-only
    python query_gene_indels.py --gene MSH2 --include-no-clinvar --output results.tsv
    python query_gene_indels.py --gene MSH2 --json
    python query_gene_indels.py --gene MSH2 --json --output results.json
    python query_gene_indels.py --gene MSH2 --api-key YOUR_NCBI_KEY
"""

import argparse
import sys
import time
import json
import re
import csv
import io
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library is required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# NCBI API helpers
# ---------------------------------------------------------------------------

NCBI_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_NCBI_API_KEY = None  # set via --api-key argument


def _ncbi_request(endpoint: str, params: dict, retries: int = 3) -> "requests.Response":
    """GET request to an NCBI eutils endpoint with rate-limiting and retries."""
    params = dict(params)
    if _NCBI_API_KEY:
        params["api_key"] = _NCBI_API_KEY
    delay = 0.11 if _NCBI_API_KEY else 0.34  # 10 req/s with key, 3 req/s without

    for attempt in range(retries):
        try:
            time.sleep(delay)
            resp = requests.get(
                f"{NCBI_EUTILS}/{endpoint}", params=params, timeout=30
            )
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"NCBI request failed after {retries} attempts: {exc}") from exc
            time.sleep(2 ** attempt)


# ---------------------------------------------------------------------------
# Gene coordinate lookup
# ---------------------------------------------------------------------------

_ACCESSION_TO_CHROM = {
    "NC_000001": "1",  "NC_000002": "2",  "NC_000003": "3",
    "NC_000004": "4",  "NC_000005": "5",  "NC_000006": "6",
    "NC_000007": "7",  "NC_000008": "8",  "NC_000009": "9",
    "NC_000010": "10", "NC_000011": "11", "NC_000012": "12",
    "NC_000013": "13", "NC_000014": "14", "NC_000015": "15",
    "NC_000016": "16", "NC_000017": "17", "NC_000018": "18",
    "NC_000019": "19", "NC_000020": "20", "NC_000021": "21",
    "NC_000022": "22", "NC_000023": "X",  "NC_000024": "Y",
    "NC_012920": "MT",
}


def _accession_to_chrom(accession: str) -> str | None:
    prefix = accession.split(".")[0]
    return _ACCESSION_TO_CHROM.get(prefix)


def get_gene_coordinates(gene_name: str) -> tuple[str, int, int]:
    """
    Return (chrom, start, end) for a human gene using NCBI Gene esummary.
    Coordinates are 0-based (as returned by NCBI); comparison against
    1-based VCF POS is handled in parse_vcf_indels.
    """
    resp = _ncbi_request("esearch.fcgi", {
        "db": "gene",
        "term": f"{gene_name}[gene] AND Homo sapiens[organism] AND alive[prop]",
        "retmode": "json",
        "retmax": 10,
    })
    # output response data in json

    data = resp.json()
    gene_ids = data.get("esearchresult", {}).get("idlist", [])
    if not gene_ids:
        raise ValueError(f"Gene '{gene_name}' not found in NCBI Gene database.")

    for gene_id in gene_ids:
        resp = _ncbi_request("esummary.fcgi", {
            "db": "gene",
            "id": gene_id,
            "retmode": "json",
        })
        # output response data in json
        doc = resp.json().get("result", {}).get(gene_id, {})

        # Verify the gene symbol matches (case-insensitive)
        if doc.get("name", "").upper() != gene_name.upper():
            continue

        for ginfo in doc.get("genomicinfo", []):
            accver = ginfo.get("chraccver", "")
            chrom = _accession_to_chrom(accver)
            if chrom:
                raw_start = int(ginfo.get("chrstart", 0))
                raw_stop  = int(ginfo.get("chrstop",  0))
                return chrom, min(raw_start, raw_stop), max(raw_start, raw_stop)

    raise ValueError(
        f"Could not find primary chromosome coordinates for '{gene_name}'. "
        "The gene may use a non-standard symbol."
    )


# ---------------------------------------------------------------------------
# VCF parsing
# ---------------------------------------------------------------------------

def _is_concrete_variant(ref: str, alt: str) -> bool:
    """True when there is at least one concrete non-reference ALT allele."""
    for a in alt.split(","):
        if a not in (".", "*", "<NON_REF>") and not a.startswith("<"):
            return True
    return False


def _parse_genotype(sample_field: str, fmt_field: str) -> list[int | None]:
    """Return a list of allele indices from the GT sub-field, e.g. [0, 1]."""
    fmt_keys = fmt_field.split(":")
    sample_vals = sample_field.split(":")
    gt_raw = "."
    if "GT" in fmt_keys:
        gt_raw = sample_vals[fmt_keys.index("GT")]
    tokens = re.split(r"[/|]", gt_raw)
    result = []
    for t in tokens:
        result.append(None if t == "." else int(t))
    return result


def _classify_variant(ref: str, alt: str, sample: str, fmt: str) -> tuple[str, str, str]:
    """
    Classify any called variant (SNV, MNV, or indel).
    Returns (variant_type, your_data, risk_allele) where:
      variant_type: "SNV" | "Indel"
      your_data   : for SNV/MNV – actual alleles e.g. "A/T" or "G/G";
                    for Indel   – I/D notation e.g. "ID", "DD", "II"
      risk_allele : for SNV/MNV – the ALT nucleotide(s);
                    for Indel   – "I" (insertion) or "D" (deletion)
    """
    alts = [a for a in alt.split(",") if a not in (".", "*", "<NON_REF>") and not a.startswith("<")]
    if not alts:
        return "Unknown", ".", "."

    main_alt = alts[0]
    all_alleles = [ref] + alts  # allele index mirrors GT number

    gt = _parse_genotype(sample, fmt)

    if len(main_alt) == len(ref):  # SNV or MNV
        variant_type = "SNV"
        risk_allele  = main_alt
        if None in gt or not gt:
            your_data = "?/?"
        else:
            called = [all_alleles[g] if g < len(all_alleles) else "?" for g in gt]
            your_data = "/".join(called)
    else:  # Indel
        variant_type = "Indel"
        if len(main_alt) > len(ref):
            allele_map  = {0: "D", 1: "I"}  # REF = shorter = D; ALT = longer = I
            risk_allele = "I"
        else:
            allele_map  = {0: "I", 1: "D"}  # REF = longer = I; ALT = shorter = D
            risk_allele = "D"
        if None in gt or not gt:
            your_data = "?"
        else:
            calls = [allele_map.get(g, "?") for g in gt]
            if calls.count("D") == 2:
                your_data = "DD"
            elif calls.count("I") == 2:
                your_data = "II"
            else:
                your_data = "ID"

    return variant_type, your_data, risk_allele


def parse_vcf_variants(
    vcf_path: Path,
    chrom: str,
    start: int,
    end: int,
    pass_only: bool = False,
) -> list[dict]:
    """
    Stream a VCF file and return all SNVs and indels within [start, end] on chrom.
    start/end are 0-based from NCBI; VCF POS is 1-based, so we compare pos-1.
    """
    variants = []
    with open(vcf_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue

            vcf_chrom = parts[0].lstrip("chr")
            if vcf_chrom != chrom:
                continue

            try:
                pos = int(parts[1])
            except ValueError:
                continue

            # Convert VCF 1-based POS to 0-based for comparison with NCBI coords
            if not (start <= pos - 1 <= end):
                continue

            filt = parts[6]
            if pass_only and filt != "PASS":
                continue

            ref = parts[3]
            alt = parts[4]
            if not _is_concrete_variant(ref, alt):
                continue

            rs_id  = parts[2] if parts[2].startswith("rs") else None
            fmt    = parts[8] if len(parts) > 8 else "GT"
            sample = parts[9] if len(parts) > 9 else "."

            variant_type, your_data, risk_allele = _classify_variant(ref, alt, sample, fmt)

            variants.append({
                "chrom":        vcf_chrom,
                "pos":          pos,
                "rs_id":        rs_id,
                "ref":          ref,
                "alt":          alt,
                "filter":       filt,
                "variant_type": variant_type,
                "your_data":    your_data,
                "risk_allele":  risk_allele,
            })

    return variants


# ---------------------------------------------------------------------------
# ClinVar annotation
# ---------------------------------------------------------------------------

def parse_spdi(spdi: str) -> dict | None:
    """
    Parse a canonical SPDI string into its components.

    SPDI format:
        sequence_accession:zero_based_position:deleted_sequence:inserted_sequence

    Example:
        NC_000022.11:43928846:C:G

    Returns a dict with sequence, zero_based_position, reference_allele, and
    asserted_allele, or None if the string is absent or malformed.
    """
    if not spdi:
        return None
    parts = spdi.split(":", 3)
    if len(parts) != 4:
        return None
    sequence, position, deleted, inserted = parts
    return {
        "sequence":           sequence,
        "zero_based_position": position,
        "reference_allele":   deleted,
        "asserted_allele":    inserted,
    }


def search_clinvar(rsid: str, condition: str) -> list[str]:
    """
    Return ClinVar Entrez UIDs matching *rsid* filtered by *condition*
    (searched against the ClinVar disease/phenotype [dis] field).
    """
    query = f'{rsid}[All Fields] AND "{condition}"[dis]'
    resp = _ncbi_request("esearch.fcgi", {
        "db":     "clinvar",
        "term":   query,
        "retmode": "json",
        "retmax": 100,
    })
    return resp.json().get("esearchresult", {}).get("idlist", [])


def get_clinvar_data(rs_ids: list[str]) -> dict[str, dict | None]:
    """
    Query ClinVar via NCBI eutils for each rs ID.

    Returns {rs_id: data | None} where data contains:
      significance, condition, review_status,
      vcv_accession, variant_name,
      canonical_spdi, reference_allele, candidate_risk_allele,
      rcv_accessions, scv_accessions,
      explicit_risk_allele_classification.
    """
    results: dict[str, dict | None] = {}

    for rs_id in rs_ids:
        try:
            # Step 1: find ClinVar variation IDs for this rs number
            resp = _ncbi_request("esearch.fcgi", {
                "db":      "clinvar",
                "term":    f"{rs_id}[rs]",
                "retmode": "json",
                "retmax":  5,
            })
            cv_ids = resp.json().get("esearchresult", {}).get("idlist", [])

            if not cv_ids:
                results[rs_id] = None
                continue

            # Step 2: fetch summaries
            resp = _ncbi_request("esummary.fcgi", {
                "db":      "clinvar",
                "id":      ",".join(cv_ids),
                "retmode": "json",
            })
            result_map = resp.json().get("result", {})

            record = None
            for cv_id in cv_ids:
                candidate = result_map.get(cv_id, {})
                if candidate:
                    record = candidate
                    break

            if not record:
                results[rs_id] = None
                continue

            # Extract germline classification
            germline = record.get("germline_classification", {})
            significance  = germline.get("description", "")
            review_status = germline.get("review_status", "")

            # Extract primary condition name (trait_set lives inside germline_classification)
            trait_set = germline.get("trait_set", [])
            condition = trait_set[0].get("trait_name", "") if trait_set else ""

            # SPDI-derived allele info from the first variation record
            variation_set = record.get("variation_set", [])
            spdi_str = variation_set[0].get("canonical_spdi", "") if variation_set else ""
            allele = parse_spdi(spdi_str)

            results[rs_id] = {
                "significance":   significance,
                "condition":      condition,
                "review_status":  review_status,
                # enriched fields from the new ClinVar lookup module
                "vcv_accession":  record.get("accession_version", ""),
                "variant_name":   record.get("title", ""),
                "canonical_spdi": spdi_str,
                "reference_allele":      allele["reference_allele"] if allele else None,
                "candidate_risk_allele": allele["asserted_allele"]  if allele else None,
                "rcv_accessions": record.get("supporting_submissions", {}).get("rcv", []),
                "scv_accessions": record.get("supporting_submissions", {}).get("scv", []),
                "explicit_risk_allele_classification":
                    "risk allele" in significance.casefold(),
            }

        except Exception as exc:  # broad catch: network errors, JSON decode errors
            print(f"  Warning: ClinVar query failed for {rs_id}: {exc}", file=sys.stderr)
            results[rs_id] = None

    return results


def _classify_risk(
    significance: str,
    review_status: str,
    risk_allele: str,
) -> tuple[str, str, str]:
    """
    Map ClinVar fields to table columns.
    Returns (risk_version, your_status, evidence).

    Risk Version:
      - Pathogenic          → the risk allele itself (e.g. "D" for deletion, "T" for SNV)
      - Likely Pathogenic   → "Likely Pathogenic"
      - Uncertain           → "Uncertain Significance"
      - Likely Benign       → "Likely Benign"
      - Benign              → "Benign"

    Your Status:
      - Pathogenic          → "At Risk"
      - Likely Pathogenic   → "Likely At Risk"
      - Uncertain           → "Uncertain"
      - Likely/Benign       → "Benign"
    """
    sig_lc = (significance or "").lower()
    rev_lc = (review_status or "").lower()

    if "pathogenic" in sig_lc and "likely" not in sig_lc:
        risk_version = risk_allele        # e.g. "D" or "T" — the specific risk allele
        your_status  = "At Risk"
    elif "likely pathogenic" in sig_lc:
        risk_version = "Likely Pathogenic"
        your_status  = "Likely At Risk"
    elif "uncertain" in sig_lc:
        risk_version = "Uncertain Significance"
        your_status  = "Uncertain"
    elif "likely benign" in sig_lc:
        risk_version = "Likely Benign"
        your_status  = "Benign"
    elif "benign" in sig_lc:
        risk_version = "Benign"
        your_status  = "Benign"
    else:
        risk_version = significance or "Unknown"
        your_status  = "Unknown"

    # Evidence level from review status
    if "practice guideline" in rev_lc:
        evidence = "High (P)"
    elif "expert panel" in rev_lc:
        evidence = "High (R)"
    elif "multiple submitters" in rev_lc or ("criteria provided" in rev_lc and "multiple" in rev_lc):
        evidence = "Medium (R)"
    elif "criteria provided" in rev_lc:
        evidence = "Low (R)"
    else:
        evidence = "Low"

    return risk_version, your_status, evidence


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

HEADERS = [
    "Variant ID", "Gene", "Type", "Your Data",
    "Risk Version", "Your Status", "Condition", "Evidence",
    "VCV Accession", "Canonical SPDI",
    "Variant Name", "Reference Allele", "Candidate Risk Allele",
    "Explicit Risk Allele", "RCV Accessions", "SCV Accessions",
]


def _print_table(rows: list[dict], gene: str) -> None:
    widths = {h: len(h) for h in HEADERS}
    for row in rows:
        for h in HEADERS:
            widths[h] = max(widths[h], len(str(row.get(h, ""))))

    sep = "+-" + "-+-".join("-" * widths[h] for h in HEADERS) + "-+"
    hdr = "| " + " | ".join(h.ljust(widths[h]) for h in HEADERS) + " |"

    print(f"\nVariants found in gene: {gene}")
    print(sep)
    print(hdr)
    print(sep)
    for row in rows:
        print("| " + " | ".join(str(row.get(h, "")).ljust(widths[h]) for h in HEADERS) + " |")
    print(sep)
    print(f"Total: {len(rows)} variant(s)\n")


def _write_tsv(rows: list[dict], output_path: str) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADERS, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Results written to: {output_path}")


def _output_json(rows: list[dict], gene: str, output_path: str | None = None) -> None:
    """Serialise rows to JSON and write to a file or stdout."""
    payload = {
        "gene": gene,
        "total": len(rows),
        "variants": rows,
    }
    text = json.dumps(payload, indent=2)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Results written to: {output_path}", file=sys.stderr)
    else:
        print(text)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find insertion/deletion variants in a gene from WGS VCF files "
            "and annotate with ClinVar clinical significance."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--gene", required=True, metavar="SYMBOL",
                        help="HGNC gene symbol, e.g. MSH2")
    parser.add_argument("--input-dir", default="input", metavar="DIR",
                        help="Directory containing VCF files (default: input/)")
    parser.add_argument("--pass-only", action="store_true",
                        help="Only report variants with FILTER=PASS")
    parser.add_argument("--include-no-clinvar", action="store_true",
                        help="Also show variants not found in ClinVar")
    parser.add_argument("--output", metavar="FILE",
                        help="Write results to a file (.json or .tsv) in addition to printing")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON (progress messages go to stderr)")
    parser.add_argument("--api-key", metavar="KEY",
                        help="NCBI API key for higher rate limits (optional)")
    args = parser.parse_args()

    global _NCBI_API_KEY
    _NCBI_API_KEY = args.api_key

    gene = args.gene.upper()
    input_dir = Path(args.input_dir)

    # When emitting JSON, route all progress to stderr so stdout stays clean
    log = sys.stderr if args.json else sys.stdout

    if not input_dir.is_dir():
        print(f"ERROR: Input directory not found: {input_dir}", file=sys.stderr)
        sys.exit(1)

    # --- 1. Look up gene coordinates ---
    print(f"[1/3] Looking up coordinates for gene: {gene} ...", file=log)
    try:
        chrom, start, end = get_gene_coordinates(gene)
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"      Chromosome {chrom}: {start:,} \u2013 {end:,} (GRCh38, 0-based)", file=log)

    # --- 2. Parse VCF files ---
    # Prioritise snp-indel VCF; ignore CNV and SV files (they use different indel semantics)
    snp_indel_vcfs = sorted(input_dir.glob("*.snp-indel*.vcf"))
    if not snp_indel_vcfs:
        # Fall back to any non-sv, non-cnv VCF
        snp_indel_vcfs = [
            p for p in sorted(input_dir.glob("*.vcf"))
            if "sv" not in p.name.lower() and "cnv" not in p.name.lower()
        ]

    if not snp_indel_vcfs:
        print(f"ERROR: No suitable VCF files found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"\n[2/3] Scanning VCF file(s) for variants in {gene} ...", file=log)
    all_variants: list[dict] = []
    for vcf_path in snp_indel_vcfs:
        print(f"      {vcf_path.name} ...", end="", flush=True, file=log)
        variants = parse_vcf_variants(vcf_path, chrom, start, end, pass_only=args.pass_only)
        all_variants.extend(variants)
        n_snv   = sum(1 for v in variants if v["variant_type"] == "SNV")
        n_indel = sum(1 for v in variants if v["variant_type"] == "Indel")
        print(f" {n_snv} SNV(s), {n_indel} indel(s) found", file=log)

    if not all_variants:
        print(f"\nNo variants found in {gene}.", file=log)
        sys.exit(0)

    # --- 3. ClinVar annotation ---
    rs_ids_with_data = [v["rs_id"] for v in all_variants if v["rs_id"]]
    unique_rs_ids = list(dict.fromkeys(rs_ids_with_data))  # deduplicate, preserve order

    print(f"\n[3/3] Querying ClinVar for {len(unique_rs_ids)} rs ID(s) ...", file=log)
    clinvar_map = get_clinvar_data(unique_rs_ids) if unique_rs_ids else {}

    # --- Build output rows ---
    rows: list[dict] = []
    for v in all_variants:
        cv = clinvar_map.get(v["rs_id"]) if v["rs_id"] else None

        if cv is None and not args.include_no_clinvar:
            continue  # skip unannotated variants unless requested

        if cv:
            risk_version, your_status, evidence = _classify_risk(
                cv["significance"], cv["review_status"], v["risk_allele"]
            )
            condition = cv["condition"] or "N/A"
        else:
            risk_version = "-"
            your_status  = "-"
            condition    = "-"
            evidence     = "-"

        rows.append({
            "Variant ID":    v["rs_id"] or ".",
            "Gene":          gene,
            "Type":          v["variant_type"],
            "Your Data":     v["your_data"],
            "Risk Version":  risk_version,
            "Your Status":   your_status,
            "Condition":     condition,
            "Evidence":      evidence,
            # enriched ClinVar fields (shown in table/TSV; all present in JSON)
            "VCV Accession":  cv.get("vcv_accession", "")        if cv else "",
            "Canonical SPDI": cv.get("canonical_spdi", "")       if cv else "",
            # additional fields included in JSON output only
            "Variant Name":  cv.get("variant_name", "")          if cv else "",
            "Reference Allele":      cv.get("reference_allele")  if cv else None,
            "Candidate Risk Allele": cv.get("candidate_risk_allele") if cv else None,
            "Explicit Risk Allele":  cv.get("explicit_risk_allele_classification", False) if cv else False,
            "RCV Accessions": cv.get("rcv_accessions", [])       if cv else [],
            "SCV Accessions": cv.get("scv_accessions", [])       if cv else [],
        })

    if not rows:
        msg = f"No ClinVar-annotated variants found in {gene}."
        if not args.include_no_clinvar:
            msg += " Try --include-no-clinvar to see all variants."
        print(f"\n{msg}", file=log)
        sys.exit(0)

    # --- Output ---
    if args.json:
        _output_json(rows, gene, output_path=args.output)
    else:
        _print_table(rows, gene)
        if args.output:
            if args.output.endswith(".json"):
                _output_json(rows, gene, output_path=args.output)
            else:
                _write_tsv(rows, args.output)


if __name__ == "__main__":
    main()
