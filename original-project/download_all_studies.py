#!/usr/bin/env python3
"""Download the complete study history represented by ClinicalTrials.gov RSS URLs.

RSS searches only show studies first posted or updated in the last 14 days. This
script converts the same search criteria to ClinicalTrials.gov API v2 requests
and follows pagination to retrieve every matching study.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


API_URL = "https://clinicaltrials.gov/api/v2/studies"
USER_AGENT = "clinicaltrials-history-downloader/1.0"
QUERY_PARAMETERS = ("cond", "term", "locn", "titles", "intr", "outc", "spons", "lead", "id", "patient")
VALID_SORT_FIELDS = {
    "LastUpdatePostDate",
    "StudyFirstPostDate",
    "ResultsFirstPostDate",
}


def read_non_comment_lines(path: Path) -> list[str]:
    lines = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith(("http://", "https://")):
            raise ValueError(f"{path}:{line_number}: expected an HTTP(S) URL")
        lines.append(line)
    return lines


def fields_from_reference(path: Path) -> list[str]:
    """Extract the comma-separated fields value from a URL in a reference file."""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        url = raw_line.strip()
        if not url.startswith(("http://", "https://")):
            continue
        values = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("fields")
        if values:
            fields = [field.strip() for field in values[0].split(",") if field.strip()]
            if fields:
                return fields
    raise ValueError(f"No URL containing a fields parameter was found in {path}")


def rss_to_api_query(rss_url: str) -> tuple[dict[str, str], str | None]:
    """Convert persistent RSS search criteria to API v2 parameters.

    dateField is intentionally used only for sorting. RSS applies an implicit
    14-day range to it; omitting that range is what retrieves all history.
    """
    parsed = urllib.parse.urlsplit(rss_url)
    if parsed.netloc.lower() != "clinicaltrials.gov" or parsed.path != "/api/rss":
        raise ValueError(f"Not a ClinicalTrials.gov RSS URL: {rss_url}")

    rss_params = urllib.parse.parse_qs(parsed.query)
    api_params: dict[str, str] = {}
    for name in QUERY_PARAMETERS:
        if name in rss_params and rss_params[name][0].strip():
            api_params[f"query.{name}"] = rss_params[name][0].strip()

    if "aggFilters" in rss_params and rss_params["aggFilters"][0].strip():
        api_params["aggFilters"] = rss_params["aggFilters"][0].strip()

    date_field = rss_params.get("dateField", [None])[0]
    if date_field is not None and date_field not in VALID_SORT_FIELDS:
        raise ValueError(f"Unsupported RSS dateField {date_field!r} in {rss_url}")

    if not api_params:
        raise ValueError(f"RSS URL has no reusable search criteria: {rss_url}")
    return api_params, date_field


def get_json(url: str, timeout: float, retries: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            if attempt == retries:
                raise RuntimeError(f"Request failed after {retries + 1} attempts: {error}") from error
            delay = 2**attempt
            print(f"  Request failed; retrying in {delay}s: {error}", file=sys.stderr)
            time.sleep(delay)
    raise AssertionError("retry loop exited unexpectedly")


def download_search(
    rss_url: str,
    fields: list[str],
    page_size: int,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    search_params, date_field = rss_to_api_query(rss_url)
    base_params = {
        **search_params,
        "format": "json",
        "markupFormat": "markdown",
        "fields": ",".join(fields),
        "pageSize": str(page_size),
    }
    if date_field:
        base_params["sort"] = f"{date_field}:desc"

    studies: list[dict[str, Any]] = []
    next_page_token: str | None = None
    total_count: int | None = None
    page_number = 0

    while True:
        page_number += 1
        params = dict(base_params)
        if next_page_token:
            params["pageToken"] = next_page_token
        else:
            params["countTotal"] = "true"

        api_url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        page = get_json(api_url, timeout, retries)
        if total_count is None:
            total_count = page.get("totalCount")

        page_studies = page.get("studies")
        if not isinstance(page_studies, list):
            raise RuntimeError("ClinicalTrials.gov response has no studies array")
        studies.extend(page_studies)
        print(
            f"  Page {page_number}: {len(page_studies)} studies "
            f"({len(studies)}/{total_count if total_count is not None else '?'})",
            file=sys.stderr,
        )

        next_page_token = page.get("nextPageToken")
        if not next_page_token:
            break

    if total_count is not None and len(studies) != total_count:
        raise RuntimeError(
            f"Expected {total_count} studies but downloaded {len(studies)}"
        )

    return {
        "source_rss_url": rss_url,
        "api_search": search_params,
        "sort": f"{date_field}:desc" if date_field else None,
        "study_count": len(studies),
        "studies": studies,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download all historical studies matching RSS search URLs."
    )
    parser.add_argument(
        "feeds",
        nargs="?",
        type=Path,
        default=Path("studies.txt"),
        help="Text file containing ClinicalTrials.gov RSS URLs (default: studies.txt)",
    )
    parser.add_argument(
        "-f",
        "--fields-from",
        type=Path,
        default=Path("clinic-trials.txt"),
        help="File containing a URL with the desired fields parameter",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("studies.json")
    )
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if not 1 <= args.page_size <= 1000:
            raise ValueError("--page-size must be between 1 and 1000")
        if args.timeout <= 0:
            raise ValueError("--timeout must be greater than zero")
        if args.retries < 0:
            raise ValueError("--retries cannot be negative")

        rss_urls = read_non_comment_lines(args.feeds)
        if not rss_urls:
            raise ValueError(f"No RSS URLs found in {args.feeds}")
        fields = fields_from_reference(args.fields_from)

        feeds = []
        for index, rss_url in enumerate(rss_urls, 1):
            print(f"Feed {index}/{len(rss_urls)}: {rss_url}", file=sys.stderr)
            feeds.append(
                download_search(
                    rss_url, fields, args.page_size, args.timeout, args.retries
                )
            )

        unique_ids = {
            study.get("protocolSection", {})
            .get("identificationModule", {})
            .get("nctId")
            for feed in feeds
            for study in feed["studies"]
        }
        unique_ids.discard(None)
        result = {
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "field_count": len(fields),
            "fields": fields,
            "feed_count": len(feeds),
            "study_count": sum(feed["study_count"] for feed in feeds),
            "unique_study_count": len(unique_ids),
            "feeds": feeds,
        }

        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary_output.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_output.replace(args.output)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(
        f"Saved {result['study_count']} studies "
        f"({result['unique_study_count']} unique) to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
