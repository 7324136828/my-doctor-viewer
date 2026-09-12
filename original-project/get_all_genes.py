#!/usr/bin/env python3
"""
Download all genes for an organism from the NCBI Gene database via eutils.

Uses server-side history (usehistory=y) so large result sets are fetched
efficiently in batches without re-running the search.

Usage:
    python get_all_genes.py
    python get_all_genes.py --organism "Homo sapiens" --output genes.tsv
    python get_all_genes.py --organism "Mus musculus" --output mouse_genes.json
    python get_all_genes.py --api-key YOUR_NCBI_KEY --batch-size 1000
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

NCBI_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_BATCH = 500

_API_KEY: str | None = None


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(endpoint: str, params: dict, retries: int = 3) -> dict:
    """GET an eutils endpoint and return parsed JSON."""
    full_params: dict = {"retmode": "json"}
    if _API_KEY:
        full_params["api_key"] = _API_KEY
    full_params.update(params)

    delay = 0.11 if _API_KEY else 0.34  # respect NCBI rate limits

    for attempt in range(retries):
        try:
            time.sleep(delay)
            resp = requests.get(
                f"{NCBI_EUTILS}/{endpoint}",
                params=full_params,
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"NCBI request failed after {retries} attempts: {exc}") from exc
            time.sleep(2 ** attempt)


# ---------------------------------------------------------------------------
# NCBI Gene query helpers
# ---------------------------------------------------------------------------

def search_genes(organism: str) -> tuple[int, str, str]:
    """
    Run esearch with usehistory=y.
    Returns (total_count, WebEnv, query_key).
    """
    data = _get("esearch.fcgi", {
        "db":         "gene",
        "term":       f'"{organism}"[Organism] AND alive[prop]',
        "usehistory": "y",
        "retmax":     0,  # we only need the count + history tokens here
    })
    result   = data.get("esearchresult", {})
    total    = int(result.get("count", 0))
    web_env  = result.get("webenv", "")
    query_key = result.get("querykey", "")
    return total, web_env, query_key


def fetch_summaries(
    web_env: str,
    query_key: str,
    retstart: int,
    retmax: int,
) -> list[dict]:
    """Fetch one batch of gene summaries from the server-side history."""
    data = _get("esummary.fcgi", {
        "db":        "gene",
        "WebEnv":    web_env,
        "query_key": query_key,
        "retstart":  retstart,
        "retmax":    retmax,
    })
    result = data.get("result", {})
    uids   = result.get("uids", [])
    return [result[uid] for uid in uids if uid in result]


# ---------------------------------------------------------------------------
# Record extraction
# ---------------------------------------------------------------------------

TSV_HEADERS = [
    "Gene ID", "Symbol", "Name", "Chromosome",
    "Gene Type", "Organism", "Description",
]


def _extract(record: dict) -> dict:
    """Map a raw esummary record to a flat output row."""
    return {
        "Gene ID":     record.get("uid", ""),
        "Symbol":      record.get("name", ""),
        "Name":        record.get("description", ""),
        "Chromosome":  record.get("chromosome", ""),
        "Gene Type":   record.get("genetype", ""),
        "Organism":    record.get("organism", {}).get("scientificname", ""),
        "Description": record.get("summary", ""),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download all genes for an organism from NCBI Gene via eutils.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--organism", default="Homo sapiens", metavar="NAME",
        help='Organism scientific name (default: "Homo sapiens")',
    )
    parser.add_argument(
        "--output", default="genes.tsv", metavar="FILE",
        help="Output file (.tsv or .json, default: genes.tsv)",
    )
    parser.add_argument(
        "--format", choices=["tsv", "json"], default=None, metavar="FMT",
        help="Output format; inferred from --output extension when omitted",
    )
    parser.add_argument(
        "--api-key", metavar="KEY",
        help="NCBI API key (allows 10 req/s instead of 3 req/s)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH, metavar="N",
        help=f"Records per API call (default: {DEFAULT_BATCH})",
    )
    args = parser.parse_args()

    global _API_KEY
    _API_KEY = args.api_key

    out_path = Path(args.output)
    fmt = args.format or ("json" if out_path.suffix.lower() == ".json" else "tsv")

    # --- 1. Search (get total count + history tokens) ---
    print(f"Searching NCBI Gene for: {args.organism!r} ...", file=sys.stderr)
    try:
        total, web_env, query_key = search_genes(args.organism)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if total == 0:
        print("No genes found. Check the organism name.", file=sys.stderr)
        sys.exit(1)

    print(
        f"Found {total:,} gene record(s). "
        f"Fetching in batches of {args.batch_size} ...",
        file=sys.stderr,
    )

    # --- 2. Fetch all batches ---
    all_rows: list[dict] = []
    tsv_fh = None
    writer = None

    if fmt == "tsv":
        tsv_fh = open(out_path, "w", newline="", encoding="utf-8")
        writer = csv.DictWriter(tsv_fh, fieldnames=TSV_HEADERS, delimiter="\t")
        writer.writeheader()

    try:
        retstart = 0
        while retstart < total:
            try:
                batch = fetch_summaries(web_env, query_key, retstart, args.batch_size)
            except RuntimeError as exc:
                print(f"\nERROR fetching batch at offset {retstart}: {exc}", file=sys.stderr)
                sys.exit(1)

            if not batch:
                break

            rows = [_extract(r) for r in batch]
            retstart += len(batch)

            if fmt == "tsv":
                writer.writerows(rows)
            else:
                all_rows.extend(rows)

            pct = min(retstart, total) / total * 100
            print(f"  {retstart:,} / {total:,}  ({pct:.1f}%)", file=sys.stderr, end="\r")

    finally:
        if tsv_fh:
            tsv_fh.close()

    print(file=sys.stderr)  # newline after progress line

    # --- 3. Write JSON (TSV was streamed above) ---
    if fmt == "json":
        payload = {
            "organism": args.organism,
            "total":    len(all_rows),
            "genes":    all_rows,
        }
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    print(f"Done. {retstart:,} gene(s) written to: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
