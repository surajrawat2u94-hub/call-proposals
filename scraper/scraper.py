#!/usr/bin/env python3
"""
call-proposals scraper
- Scheduled by GitHub Actions (daily)
- Fetches/merges calls from multiple agencies into data.json
- Defensive: one adapter failure will not stop others
"""

import json
import os
import re
import sys
import time
import hashlib
from typing import List, Dict
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# ---------- SETTINGS ----------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUTPUT_FILE = os.path.join(REPO_ROOT, "data.json")
TIMEOUT = 30
SLEEP_BETWEEN = 1.0  # politeness (seconds between network calls)
HEADERS = {
    "User-Agent": "FundingCallsBot/1.0 (+https://github.com/)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Final schema keys used by the website
BASE_FIELDS = [
    "id", "title", "deadline", "agency", "area", "eligibility", "budgetINR", "url",
    "category", "researchCategory", "extendedDeadline", "country", "isRecurring"
]

# ---------- UTILS ----------
def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def to_iso(dstr: str) -> str:
    if not dstr:
        return ""
    try:
        dt = dateparser.parse(dstr, dayfirst=False, fuzzy=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""

def make_id(title: str, agency: str, url: str) -> int:
    raw = f"{title}|{agency}|{url}"
    return int(hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8], 16)

def standardize(item: Dict) -> Dict:
    """Return a dict that matches our schema; provide safe defaults."""
    out = {k: "" for k in BASE_FIELDS}
    out.update({
        "id": item.get("id"),
        "title": norm_space(item.get("title")),
        "deadline": item.get("deadline", ""),
        "agency": norm_space(item.get("agency")),
        "area": norm_space(item.get("area")),
        "eligibility": norm_space(item.get("eligibility")),
        "budgetINR": norm_space(item.get("budgetINR")),
        "url": item.get("url", ""),
        "category": norm_space(item.get("category")),
        "researchCategory": norm_space(item.get("researchCategory")),
        "extendedDeadline": item.get("extendedDeadline", ""),
        "country": norm_space(item.get("country") or "Global"),
        "isRecurring": bool(item.get("isRecurring", False)),
    })
    if not out["id"]:
        out["id"] = make_id(out["title"], out["agency"], out["url"])
    return out

def get_soup(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")

def safe_get_text(el) -> str:
    return norm_space(el.get_text()) if el else ""

# ---------- MERGE ----------
def load_existing(path: str) -> List[Dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def key_fn(r: Dict):
    return (norm_space(r.get("title")), norm_space(r.get("agency")), r.get("url", ""))

def merge_records(existing: List[Dict], new: List[Dict]) -> List[Dict]:
    by_key = {key_fn(standardize(r)): standardize(r) for r in existing}
    for r in new:
        rr = standardize(r)
        by_key[key_fn(rr)] = rr
    merged = list(by_key.values())
    def sort_tuple(r):  # sort by deadline (empty last), then title
        d = r.get("deadline") or "9999-12-31"
        return (d, r.get("title", ""))
    merged.sort(key=sort_tuple)
    return merged

# ---------- ADAPTERS ----------
def fetch_serb_recurring() -> List[Dict]:
    """ANRF (formerly SERB) – CRG recurring (always open annually)."""
    item = {
        "title": "SERB Core Research Grant (CRG) – Annual",
        "deadline": "",
        "agency": "ANRF (formerly SERB)",
        "area": "Science & Engineering",
        "eligibility": "Faculty researchers in Indian institutions",
        "budgetINR": "",
        "url": "https://www.serbonline.in/",
        "category": "National",
        "researchCategory": "Research Proposal",
        "extendedDeadline": "",
        "country": "India",
        "isRecurring": True,
    }
    return [standardize(item)]

def fetch_icmr_current() -> List[Dict]:
    """
    ICMR – TEMPLATE parser
    NOTE: ICMR reorganizes content often. Replace SELECTORS with the
    dedicated 'Call for Proposals / Grants' listing page when available.
    The function is defensive and will return [] if nothing matches.
    """
    URL = "https://www.icmr.gov.in/"
    results = []
    try:
        soup = get_soup(URL)
        # TODO: Replace this with the correct container for 'grants / calls'
        for a in soup.select("a"):
            title = safe_get_text(a)
            href = urljoin(URL, a.get("href") or "")
            if not href or not title:
                continue
            t = title.lower()
            if any(k in t for k in ["call", "grant", "fellow", "scheme"]):
                results.append(standardize({
                    "title": title,
                    "deadline": "",
                    "agency": "ICMR (Indian Council of Medical Research)",
                    "area": "",
                    "eligibility": "",
                    "budgetINR": "",
                    "url": href,
                    "category": "National",
                    "researchCategory": "Research Proposal",
                    "extendedDeadline": "",
                    "country": "India",
                    "isRecurring": False
                }))
        return results
    except Exception as e:
        print("ICMR adapter failed:", e, file=sys.stderr)
        return []

def fetch_dbt_current() -> List[Dict]:
    """
    DBT – TEMPLATE parser
    Replace URL + selectors with DBT's official 'Call for Proposals' page
    when you decide the canonical listing URL.
    """
    URL = "https://dbtindia.gov.in/"  # Placeholder
    out = []
    try:
        soup = get_soup(URL)
        for a in soup.select("a"):
            title = safe_get_text(a)
            href = urljoin(URL, a.get("href") or "")
            if not title or not href:
                continue
            if any(k in title.lower() for k in ["call", "grant", "fellow", "r&d"]):
                out.append(standardize({
                    "title": title,
                    "deadline": "",
                    "agency": "DBT (Department of Biotechnology)",
                    "area": "",
                    "eligibility": "",
                    "budgetINR": "",
                    "url": href,
                    "category": "National",
                    "researchCategory": "Research Proposal",
                    "extendedDeadline": "",
                    "country": "India",
                    "isRecurring": False
                }))
        return out
    except Exception as e:
        print("DBT adapter failed:", e, file=sys.stderr)
        return []

def fetch_dst_current() -> List[Dict]:
    """
    DST – TEMPLATE parser
    Similar note as above: replace with the best 'call for proposals' page.
    """
    URL = "https://dst.gov.in/"  # Placeholder
    out = []
    try:
        soup = get_soup(URL)
        for a in soup.select("a"):
            t = safe_get_text(a)
            href = urljoin(URL, a.get("href") or "")
            if not href or not t:
                continue
            if any(k in t.lower() for k in ["call", "fund", "proposal", "fellow", "grant"]):
                out.append(standardize({
                    "title": t,
                    "deadline": "",
                    "agency": "DST (Department of Science & Technology)",
                    "area": "",
                    "eligibility": "",
                    "budgetINR": "",
                    "url": href,
                    "category": "National",
                    "researchCategory": "Research Proposal",
                    "extendedDeadline": "",
                    "country": "India",
                    "isRecurring": False
                }))
        return out
    except Exception as e:
        print("DST adapter failed:", e, file=sys.stderr)
        return []

def fetch_igstc_current() -> List[Dict]:
    """
    IGSTC – TEMPLATE parser for Indo-German calls
    """
    URL = "https://www.igstc.org/"  # Placeholder
    out = []
    try:
        soup = get_soup(URL)
        for a in soup.select("a"):
            t = safe_get_text(a)
            href = urljoin(URL, a.get("href") or "")
            if not href or not t:
                continue
            if any(k in t.lower() for k in ["call", "2+2", "fund", "grant"]):
                out.append(standardize({
                    "title": t,
                    "deadline": "",
                    "agency": "IGSTC (Indo-German Science Technology Centre)",
                    "area": "",
                    "eligibility": "",
                    "budgetINR": "",
                    "url": href,
                    "category": "Joint Collaboration",
                    "researchCategory": "Research Proposal",
                    "extendedDeadline": "",
                    "country": "India",
                    "isRecurring": False
                }))
        return out
    except Exception as e:
        print("IGSTC adapter failed:", e, file=sys.stderr)
        return []

def fetch_horizon_europe() -> List[Dict]:
    """International – Horizon Europe (STATIC demo item)."""
    item = {
        "title": "Horizon Europe Collaborative Projects",
        "deadline": "2025-11-15",
        "agency": "European Commission",
        "area": "Multidisciplinary",
        "eligibility": "Universities, research orgs, SMEs",
        "budgetINR": "€ variable",
        "url": "https://research-and-innovation.ec.europa.eu/",
        "category": "International",
        "researchCategory": "Research Proposal",
        "extendedDeadline": "",
        "country": "Global",
        "isRecurring": False,
    }
    return [standardize(item)]

def fetch_nih_r01() -> List[Dict]:
    """International – NIH R01 (STATIC demo item)."""
    item = {
        "title": "NIH R01 Research Project Grant",
        "deadline": "2025-10-05",
        "agency": "NIH",
        "area": "Biomedical",
        "eligibility": "International collaborators allowed via rules",
        "budgetINR": "$ variable",
        "url": "https://grants.nih.gov/grants/funding/r01.htm",
        "category": "International",
        "researchCategory": "Research Proposal",
        "extendedDeadline": "",
        "country": "Global",
        "isRecurring": False
    }
    return [standardize(item)]

# Register active adapters here (order matters only for logging)
ADAPTERS = [
    ("SERB recurring", fetch_serb_recurring),
    ("ICMR current (template)", fetch_icmr_current),
    ("DBT current (template)", fetch_dbt_current),
    ("DST current (template)", fetch_dst_current),
    ("IGSTC current (template)", fetch_igstc_current),
    ("Horizon Europe (static)", fetch_horizon_europe),
    ("NIH R01 (static)", fetch_nih_r01),
]

# ---------- MAIN ----------
def main():
    # 1) Load the existing dataset
    existing = load_existing(OUTPUT_FILE)

    # 2) Collect from all adapters
    collected: List[Dict] = []
    for name, fn in ADAPTERS:
        try:
            print(f"[+] Fetching: {name}")
            batch = fn() or []
            print(f"    -> {len(batch)} items")
            collected.extend(batch)
        except Exception as e:
            print(f"[!] Adapter failed: {name} :: {e}", file=sys.stderr)
        time.sleep(SLEEP_BETWEEN)

    # 3) Merge, dedupe, sort
    merged = merge_records(existing, collected)

    # 4) Write
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"[✓] Wrote {len(merged)} calls → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
