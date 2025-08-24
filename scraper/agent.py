#!/usr/bin/env python3
from __future__ import annotations
import os, json, time, hashlib
from typing import Dict, List
import yaml

from fetchers import fetch_listing_links, fetch_call_page
from extractors import extract_structured_fields
from normalizers import dedupe_and_sort, validate_call, is_india_related

DATA_JSON = "data.json"

def load_sources(path: str = "scraper/sources.yaml") -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]

def load_previous() -> Dict:
    if not os.path.exists(DATA_JSON):
        return {"updated_utc": "", "calls": []}
    with open(DATA_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def main():
    sources = load_sources()
    prev = load_previous()

    all_calls: List[Dict] = []
    for s in sources:
        agency = s["agency"]
        print(f"[agent] Listing: {agency}")
        links = fetch_listing_links(s)
        print(f"[agent]  -> {len(links)} candidate links")
        for title_guess, url in links:
            raw_page = fetch_call_page(url, s)
            call = extract_structured_fields(
                title_guess=title_guess,
                page_text=raw_page["text"],
                url=url,
                agency=agency,
                country=s.get("country",""),
            )
            if not validate_call(call):
                continue
            call["india_related"] = "yes" if is_india_related(call) else "no"
            all_calls.append(call)
            time.sleep(0.5)

    clean = dedupe_and_sort(all_calls)

    out = {
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "calls": clean,
    }

    if json.dumps(prev, sort_keys=True) != json.dumps(out, sort_keys=True):
        with open(DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[agent] Wrote {len(clean)} calls to {DATA_JSON}")
    else:
        print("[agent] No changes; data.json up to date")

if __name__ == "__main__":
    main()
