#!/usr/bin/env python3
"""
Scrape funding calls from multiple agencies and merge into data.json

How you extend:
- Add a new `fetch_<agency>()` function that returns a list[dict] of calls.
- Each call should map to the `STANDARD_FIELDS` keys (any missing values
  can be '', None, or omitted; they will be normalized to N/A by the UI).
- Register the adapter in ADAPTERS at the bottom of the file.

Run locally:
  python scraper.py

This file is used by GitHub Actions (update-data.yml) to refresh data.json daily.
"""

import json
import os
import re
import sys
from typing import Dict, List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# ------------- Settings -------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUTPUT_FILE = os.path.join(REPO_ROOT, "data.json")
TIMEOUT = 40
HEADERS = {
    "User-Agent": "FundingCallsBot/1.0 (+https://github.com/)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

STANDARD_FIELDS = [
    "title", "deadline", "agency", "area", "eligibility", "budgetINR",
    "url", "category", "researchCategory", "extendedDeadline",
    "country", "isRecurring"
]

# ------------- Utilities -------------

def http_get(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")

def text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(strip=True)) if el else ""

def t(s: str) -> str:
    """normalize whitespace in strings"""
    return re.sub(r"\s+", " ", (s or "").strip())

def parse_date_any(s: str) -> str:
    """Return YYYY-MM-DD or '' if we cannot parse"""
    s = (s or "").strip()
    if not s:
        return ""
    try:
        dt = dateparser.parse(s, dayfirst=False, fuzzy=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""

def clean_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def record_key(r: Dict) -> tuple:
    """Dedupe by normalized (title + agency); ignore url."""
    return (clean_key(r.get("title")), clean_key(r.get("agency")))

def standardize(raw: Dict) -> Dict:
    """Ensure all expected fields exist (even if empty)."""
    out = {k: "" for k in STANDARD_FIELDS}
    out.update(raw or {})
    # normalize booleans / strings
    out["title"] = t(out.get("title"))
    out["agency"] = t(out.get("agency"))
    out["area"] = t(out.get("area"))
    out["eligibility"] = t(out.get("eligibility"))
    out["budgetINR"] = t(out.get("budgetINR"))
    out["url"] = t(out.get("url"))
    out["category"] = t(out.get("category"))
    out["researchCategory"] = t(out.get("researchCategory"))
    out["extendedDeadline"] = parse_date_any(out.get("extendedDeadline") or "")
    out["deadline"] = parse_date_any(out.get("deadline") or "")
    out["country"] = t(out.get("country") or "Global")
    out["isRecurring"] = bool(out.get("isRecurring", False))
    return out

def load_existing() -> List[Dict]:
    if not os.path.exists(OUTPUT_FILE):
        return []
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def merge(existing: List[Dict], new: List[Dict]) -> List[Dict]:
    """
    Dedupe by (title+agency). New records override old ones with same key.
    """
    bykey = {record_key(standardize(r)): standardize(r) for r in existing}
    for r in new:
        rr = standardize(r)
        bykey[record_key(rr)] = rr
    merged = list(bykey.values())

    def sort_key(r):
        # Put no-deadline items at the end; then by title
        d = r.get("deadline") or "9999-12-31"
        return (d, r.get("title", ""))
    merged.sort(key=sort_key)
    return merged

# ------------- Adapters -------------

def fetch_serb_recurring() -> List[Dict]:
    """ANRF/SERB – recurring Core Research Grant (CRG)."""
    return [standardize({
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
        "isRecurring": True
    })]

def fetch_horizon_europe_demo() -> List[Dict]:
    return [standardize({
        "title":"Horizon Europe Collaborative Projects",
        "deadline":"2025-11-15",
        "agency":"European Commission",
        "area":"Multidisciplinary",
        "eligibility":"Universities, research orgs, SMEs",
        "budgetINR":"€ variable",
        "url":"https://research-and-innovation.ec.europa.eu/",
        "category":"International",
        "researchCategory":"Research Proposal",
        "extendedDeadline":"",
        "country":"Global",
        "isRecurring": False
    })]

def fetch_nih_r01_demo() -> List[Dict]:
    return [standardize({
        "title":"NIH R01 Research Project Grant",
        "deadline":"2025-10-05",
        "agency":"NIH",
        "area":"Biomedical",
        "eligibility":"International collaborators allowed via rules",
        "budgetINR":"$ variable",
        "url":"https://grants.nih.gov/grants/funding/r01.htm",
        "category":"International",
        "researchCategory":"Research Proposal",
        "extendedDeadline":"",
        "country":"Global",
        "isRecurring": False
    })]

# ---------- TEMPLATES YOU FILL (replace URL + CSS selectors) ----------

def fetch_icmr_current() -> List[Dict]:
    """
    ICMR – Calls/Grants page scraper (TEMPLATE).
    1) Open the official ICMR 'Calls/Grants' page.
    2) Right click → Inspect → find the container for each call card.
    3) Replace the selectors below.
    """
    URL = "https://www.icmr.gov.in/"  # TODO: put the calls listing URL here
    out: List[Dict] = []
    try:
        soup = http_get(URL)

        # TODO: Replace '.call-card' with the real container for each call.
        for card in soup.select(".call-card"):
            # TODO: Replace these with real selectors:
            a = card.select_one("a")  # title link
            title = text(a) or text(card.select_one(".title"))
            href = urljoin(URL, a.get("href") if a and a.has_attr("href") else "")

            deadline = text(card.select_one(".deadline"))  # e.g., "31 Oct 2025"
            area = text(card.select_one(".area"))
            eligibility = text(card.select_one(".eligibility"))
            budget = text(card.select_one(".budget"))

            # Skip obvious junk
            if not title or not href:
                continue

            out.append(standardize({
                "title": title,
                "deadline": deadline,        # will be parsed
                "agency": "ICMR (Indian Council of Medical Research)",
                "area": area,
                "eligibility": eligibility,
                "budgetINR": budget,
                "url": href,
                "category": "National",
                "researchCategory": "Research Proposal",
                "extendedDeadline": "",
                "country": "India",
                "isRecurring": False
            }))
        return out
    except Exception as e:
        print("ICMR adapter failed:", e, file=sys.stderr)
        return []

def fetch_dbt_current() -> List[Dict]:
    """
    DBT – Calls page scraper (TEMPLATE). Fill selectors like above.
    """
    URL = "https://dbtindia.gov.in/"  # TODO: calls listing URL
    out: List[Dict] = []
    try:
        soup = http_get(URL)

        for card in soup.select(".views-row"):  # example
            a = card.select_one("a")
            title = text(a)
            href = urljoin(URL, a.get("href") if a and a.has_attr("href") else "")

            deadline = text(card.select_one(".field-deadline"))
            area = text(card.select_one(".field-area"))
            eligibility = text(card.select_one(".field-eligibility"))
            budget = text(card.select_one(".field-budget"))

            if not title or not href:
                continue
            if any(x in title.lower() for x in ["faq", "form", "project", "projects"]):
                continue

            out.append(standardize({
                "title": title,
                "deadline": deadline,
                "agency": "DBT (Department of Biotechnology)",
                "area": area,
                "eligibility": eligibility,
                "budgetINR": budget,
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
    DST – Calls page scraper (TEMPLATE). Fill selectors like above.
    """
    URL = "https://dst.gov.in/"  # TODO: calls listing URL
    out: List[Dict] = []
    try:
        soup = http_get(URL)

        for card in soup.select(".call-card"):  # TODO
            a = card.select_one("a")
            title = text(a) or text(card.select_one(".title"))
            href = urljoin(URL, a.get("href") if a and a.has_attr("href") else "")

            deadline = text(card.select_one(".deadline"))
            area = text(card.select_one(".area"))
            eligibility = text(card.select_one(".eligibility"))
            budget = text(card.select_one(".budget"))

            if not title or not href:
                continue
            if any(x in title.lower() for x in ["faq", "form", "project", "projects"]):
                continue

            out.append(standardize({
                "title": title,
                "deadline": deadline,
                "agency": "DST (Department of Science & Technology)",
                "area": area,
                "eligibility": eligibility,
                "budgetINR": budget,
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
    IGSTC – Stricter parser to avoid FAQs/forms/projects (TEMPLATE).
    Replace URL + selectors to point to the actual 'Calls' page.
    """
    URL = "https://www.igstc.org/"  # TODO: calls listing URL
    out: List[Dict] = []
    ALLOW = ["call", "call for", "proposal"]         # keep
    BLOCK = ["faq", "form", "scheme form", "forms", "project", "projects"]  # skip

    try:
        soup = http_get(URL)

        # TODO: Replace with the container that lists calls:
        for a in soup.select("a"):
            title = text(a)
            href = urljoin(URL, a.get("href") if a and a.has_attr("href") else "")
            if not title or not href:
                continue
            tl = title.lower()
            if any(b in tl for b in BLOCK):
                continue
            if not any(w in tl for w in ALLOW):
                continue

            out.append(standardize({
                "title": title,
                "deadline": "",
                "agency": "IGSTC (Indo-German Science Technology Centre)",
                "area": "Joint Collaboration",
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

# ---------------- Register the adapters you want to run ----------------
ADAPTERS = [
    ("SERB recurring", fetch_serb_recurring),
    # Fill these once selectors are ready:
    ("ICMR current", fetch_icmr_current),
    ("DBT current", fetch_dbt_current),
    ("DST current", fetch_dst_current),
    ("IGSTC current", fetch_igstc_current),
    # demos (safe)
    ("Horizon Europe demo", fetch_horizon_europe_demo),
    ("NIH R01 demo", fetch_nih_r01_demo),
]

# ---------------- Main ----------------

def main():
    existing = load_existing()
    collected: List[Dict] = []

    for name, fn in ADAPTERS:
        try:
            print(f"[+] Fetching: {name}")
            batch = fn() or []
            print(f"    -> {len(batch)} items")
            collected.extend(batch)
        except Exception as e:
            print(f"[!] Adapter failed: {name}: {e}", file=sys.stderr)

    merged = merge(existing, collected)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"[✓] Wrote {len(merged)} calls → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
