#!/usr/bin/env python3
"""Download RSS/Atom feeds listed in a text file and save them as JSON."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


USER_AGENT = "rss-to-json/1.0"


def local_name(tag: str) -> str:
    """Return an XML tag without its namespace."""
    return tag.rsplit("}", 1)[-1]


def direct_children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if local_name(child.tag) == name]


def child_text(element: ET.Element, *names: str) -> str | None:
    for name in names:
        matches = direct_children(element, name)
        if matches:
            value = "".join(matches[0].itertext()).strip()
            if value:
                return value
    return None


def element_link(element: ET.Element) -> str | None:
    """Read either an RSS text link or an Atom href link."""
    links = direct_children(element, "link")
    for link in links:
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href")
    for link in links:
        if link.text and link.text.strip():
            return link.text.strip()
        if link.get("href"):
            return link.get("href")
    return None


def parse_entry(element: ET.Element) -> dict[str, Any]:
    categories: list[str] = []
    for category in direct_children(element, "category"):
        value = category.get("term") or "".join(category.itertext()).strip()
        if value:
            categories.append(value)

    entry = {
        "title": child_text(element, "title"),
        "link": element_link(element),
        "id": child_text(element, "guid", "id"),
        "description": child_text(element, "description", "summary", "content"),
        "published": child_text(element, "pubDate", "published", "updated"),
        "author": child_text(element, "author", "creator"),
        "categories": categories,
    }
    return {key: value for key, value in entry.items() if value not in (None, [], "")}


def parse_feed(xml_data: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = ET.fromstring(xml_data)
    root_type = local_name(root.tag).lower()

    if root_type == "rss":
        channels = direct_children(root, "channel")
        if not channels:
            raise ValueError("RSS document does not contain a channel")
        container = channels[0]
        entries = direct_children(container, "item")
    elif root_type == "feed":
        container = root
        entries = direct_children(container, "entry")
    else:
        raise ValueError(f"Unsupported feed type: {root_type}")

    metadata = {
        "title": child_text(container, "title"),
        "link": element_link(container),
        "description": child_text(container, "description", "subtitle"),
        "language": child_text(container, "language"),
        "last_updated": child_text(container, "lastBuildDate", "updated"),
    }
    metadata = {
        key: value for key, value in metadata.items() if value not in (None, "")
    }
    return metadata, [parse_entry(entry) for entry in entries]


def read_urls(path: Path) -> list[str]:
    urls: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith(("http://", "https://")):
            raise ValueError(f"{path}:{line_number}: expected an HTTP(S) URL")
        urls.append(line)
    return urls


def download_feed(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def build_output(urls: list[str], timeout: float) -> tuple[dict[str, Any], int]:
    feeds: list[dict[str, Any]] = []
    failures = 0

    for url in urls:
        print(f"Downloading {url}", file=sys.stderr)
        try:
            metadata, entries = parse_feed(download_feed(url, timeout))
            feeds.append({"url": url, "feed": metadata, "entries": entries})
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as error:
            failures += 1
            feeds.append({"url": url, "error": str(error), "entries": []})
            print(f"  Failed: {error}", file=sys.stderr)

    result = {
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "feed_count": len(feeds),
        "entry_count": sum(len(feed["entries"]) for feed in feeds),
        "feeds": feeds,
    }
    return result, failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download RSS/Atom URLs from a text file into one JSON file."
    )
    parser.add_argument("input", nargs="?", type=Path, default=Path("rss-feed.txt"))
    parser.add_argument("-o", "--output", type=Path, default=Path("rss-feed.json"))
    parser.add_argument("--timeout", type=float, default=30.0, help="Seconds per request (default: 30)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        urls = read_urls(args.input)
        if not urls:
            raise ValueError(f"No feed URLs found in {args.input}")
        if args.timeout <= 0:
            raise ValueError("--timeout must be greater than zero")

        result, failures = build_output(urls, args.timeout)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    print(
        f"Saved {result['entry_count']} entries from {len(urls) - failures}/{len(urls)} feeds to {args.output}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
