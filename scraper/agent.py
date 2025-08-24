#!/usr/bin/env python3
"""
Agent runner that orchestrates:
- Load sources
- Fetch call links
- Parse HTML/PDF pages
- Deduplicate + sort
- Save data.json
"""

from __future__ import annotations
import os
import sys
import time
from typing import List, Dict

# ---- make imports work both as "python -m scraper.agent" and "python scraper/agent.py"
if __package__ is None or __package__ == "":
    sys.path.append(os.path.dirname(__file__))
    from fetchers import load_sources, collect_links, SLEEP_BETWEEN
    from extractors import parse_call
    from normalizers import dedupe, save_data
else:
    from .fetchers import load_sources, collect_links, SLEEP_BETWEEN
    from .extractors import parse_call
    from .normalizers import dedupe, save_data


def main() -> None:
    sources = load_sources()
    all_calls: List[Dict[str, str]] = []

    for src in sources:
        agency = src["agency"]
        url = src["url"]
        base = src.get("base", url)

        print(f"[list] {agency} -> {url}")
        time.sleep(SLEEP_BETWEEN)
        pairs = collect_links(url, base)
        print(f"  found {len(pairs)} candidate links")

        for (title_guess, href) in pairs[:80]:   # safety cap per source
            try:
                time.sleep(SLEEP_BETWEEN)
                call = parse_call(agency, title_guess, href)
                if len(call["title"]) < 4:
                    continue
                all_calls.append(call)
            except Exception as e:
                print(f"[warn] parse_call failed {href} -> {e}")

    clean_calls = dedupe(all_calls)
    print(f"[done] {len(all_calls)} raw -> {len(clean_calls)} after dedupe")
    save_data(clean_calls)


if __name__ == "__main__":
    main()
