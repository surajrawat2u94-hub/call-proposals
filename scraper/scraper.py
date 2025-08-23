#!/usr/bin/env python3
"""
Scrape funding calls from multiple agencies:
- discover call links from agency landing pages (keywords-based)
- extract fields from web page text
- if a PDF is linked, fetch it and extract the same fields; merge results
- dedupe by (title + agency) and prefer non-empty values
- write/merge data.json

Run:
  python scraper.py
Used by GitHub Actions to refresh data.json daily.
"""

import json
import os
import re
import sys
from io import BytesIO
from typing import Dict, List
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from pdfminer.high_level import extract_text as pdf_extract_text

# ------------------ Settings ------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUTPUT_FILE = os.path.join(REPO_ROOT, "data.json")
TIMEOUT = 40
PDF_MAX_BYTES = 12 * 1024 * 1024  # 12 MB cap per PDF (safety)

HEADERS = {
    "User-Agent": "FundingCallsBot/1.2 (+https://github.com/)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Where we start crawling for each agency
AGENCIES = [
    {
        "name": "ICMR (Indian Council of Medical Research)",
        "country": "India",
        "category": "National",
        "research": "Research Proposal",
        "base": "https://www.icmr.gov.in/",
        "start_urls": [
            "https://www.icmr.gov.in/Pages/Opportunities/Opportunities_Grants.html",
            "https://www.icmr.gov.in/Pages/ICMR_Announcement.html",
        ],
    },
    {
        "name": "DBT (Department of Biotechnology)",
        "country": "India",
        "category": "National",
        "research": "Research Proposal",
        "base": "https://dbtindia.gov.in/",
        "start_urls": [
            "https://dbtindia.gov.in/whats-new",
            "https://dbtindia.gov.in/call-for-proposals",
        ],
    },
    {
        "name": "DST (Department of Science & Technology)",
        "country": "India",
        "category": "National",
        "research": "Research Proposal",
        "base": "https://dst.gov.in/",
        "start_urls": [
            "https://dst.gov.in/call-for-proposals",
            "https://dst.gov.in/funding",
        ],
    },
    {
        "name": "IGSTC (Indo-German Science Technology Centre)",
        "country": "India",
        "category": "Joint Collaboration",
        "research": "Research Proposal",
        "base": "https://www.igstc.org/",
        "start_urls": [
            "https://www.igstc.org/",
            "https://www.igstc.org/funding-opportunities",
        ],
    },
    # Demo international sources to ensure you always see content:
    {
        "name": "European Research Council",
        "country": "Global",
        "category": "International",
        "research": "Research Proposal",
        "base": "https://erc.europa.eu/",
        "start_urls": ["https://erc.europa.eu/news-events/news"],
    },
    {
        "name": "NIH",
        "country": "Global",
        "category": "International",
        "research": "Research Proposal",
        "base": "https://grants.nih.gov/",
        "start_urls": ["https://grants.nih.gov/funding/searchguide/nih-guide-to-grants-and-contracts.cfm"],
    },
]

# Recurring call (example)
RECURRING = [
    {
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
]

# ------------------ Utilities ------------------

KEYWORDS = ["call", "proposal", "funding opportunity", "fellowship", "grant"]
BLOCK = ["faq", "form", "application form", "project report", "past project", "tender"]

DATE_PATTERNS = [
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
    r"\d{4}-\d{2}-\d{2}",
    r"\d{1,2}\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}",
]

def http_get(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")

def is_internal(url: str, base: str) -> bool:
    if not url: return False
    pu = urlparse(urljoin(base, url))
    pb = urlparse(base)
    return pu.netloc == pb.netloc

def full_url(base: str, href: str) -> str:
    return urljoin(base, href or "")

def text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""

def clean_all_text(soup: BeautifulSoup) -> str:
    # page-level text
    for s in soup(["script","style","noscript","header","footer","nav"]):
        s.extract()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

def parse_date_any(s: str) -> str:
    s = (s or "").strip()
    if not s: return ""
    try:
        dt = dateparser.parse(s, dayfirst=False, fuzzy=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""

def is_pdf_url(url: str) -> bool:
    return (url or "").lower().split("?")[0].endswith(".pdf")

def fetch_pdf_text(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()
        content = resp.content
        if len(content) > PDF_MAX_BYTES:
            return ""
        return pdf_extract_text(BytesIO(content)) or ""
    except Exception:
        return ""

def extract_fields_from_text(txt: str) -> Dict[str, str]:
    """Heuristic extraction of title/deadline/eligibility/budget/area from big text blobs (web page or PDF)."""
    out = {"title": "", "deadline": "", "eligibility": "", "budgetINR": "", "area": ""}

    if not txt: return out

    # Title guess: first "call-like" line
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    for l in lines[:60]:
        ll = l.lower()
        if any(k in ll for k in ["call for", "call:", "funding opportunity", "invitation", "scheme", "grant", "fellowship"]):
            out["title"] = l[:180]
            break
    if not out["title"] and lines:
        out["title"] = lines[0][:180]

    # Deadline (find near keywords)
    for pat in DATE_PATTERNS:
        m = re.search(r"(deadline|last date|closing date|submission(?:\s+deadline)?)"
                      r".{0,60}?(" + pat + ")", txt, flags=re.I | re.S)
        if m:
            out["deadline"] = parse_date_any(m.group(2))
            if out["deadline"]: break

    # Eligibility block
    m = re.search(r"(Eligibility)(?:\s*[:\-]|\s*\n)\s*(.+?)\n(?:[A-Z][^\n]{2,}|Budget|Funding|Area|Scope|Duration|How to apply)",
                  txt, flags=re.I | re.S)
    if m:
        out["eligibility"] = re.sub(r"\s+", " ", m.group(2)).strip()[:600]

    # Budget block
    m = re.search(r"(Budget|Funding(?:\s+limit)?|Grant(?:\s+amount)?)"
                  r"(?:\s*[:\-]|\s*\n)\s*(.+?)\n(?:[A-Z][^\n]{2,}|Eligibility|Area|Scope|Duration|How to apply)",
                  txt, flags=re.I | re.S)
    if m:
        out["budgetINR"] = re.sub(r"\s+", " ", m.group(2)).strip()[:250]

    # Area (weak heuristic)
    m = re.search(r"(Area|Research Area|Thematic Area)(?:\s*[:\-]|\s*\n)\s*(.+?)\n(?:[A-Z][^\n]{2,}|Eligibility|Budget|Funding|Scope|Duration)",
                  txt, flags=re.I | re.S)
    if m:
        out["area"] = re.sub(r"\s+", " ", m.group(2)).strip()[:200]

    return out

def standardize(raw: Dict) -> Dict:
    def t(v): return (v or "").strip()
    return {
        "title": t(raw.get("title")),
        "deadline": parse_date_any(raw.get("deadline") or ""),
        "agency": t(raw.get("agency")),
        "area": t(raw.get("area")),
        "eligibility": t(raw.get("eligibility")),
        "budgetINR": t(raw.get("budgetINR")),
        "url": t(raw.get("url")),
        "category": t(raw.get("category")),
        "researchCategory": t(raw.get("researchCategory")),
        "extendedDeadline": parse_date_any(raw.get("extendedDeadline") or ""),
        "country": t(raw.get("country") or "Global"),
        "isRecurring": bool(raw.get("isRecurring", False)),
    }

def clean_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def record_key(r: Dict) -> str:
    return clean_key(f"{r.get('title','')}|{r.get('agency','')}")

def merge_record(base: Dict, new: Dict) -> Dict:
    """Prefer non-empty fields; prefer having deadline; keep URL."""
    out = base.copy()
    for k,v in new.items():
        if k in ("isRecurring",):  # bool cannot be "better"
            out[k] = out.get(k) or v
            continue
        if not out.get(k) and v:
            out[k] = v
        # for deadline: prefer one that exists over empty
        if k == "deadline":
            if (not out["deadline"]) and v:
                out["deadline"] = v
    # always keep a URL
    if not out.get("url") and new.get("url"):
        out["url"] = new["url"]
    return out

def dedupe_merge(rows: List[Dict]) -> List[Dict]:
    bykey: Dict[str, Dict] = {}
    for r in rows:
        r = standardize(r)
        k = record_key(r)
        if k in bykey:
            bykey[k] = merge_record(bykey[k], r)
        else:
            bykey[k] = r
    return list(bykey.values())

def load_existing() -> List[Dict]:
    if not os.path.exists(OUTPUT_FILE): return []
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

# ------------------ Core extraction ------------------

def extract_from_call_page(url: str) -> Dict[str, str]:
    """Heuristically extract fields from a call page."""
    soup = http_get(url)
    page_txt = clean_all_text(soup)
    web_fields = extract_fields_from_text(page_txt)

    # Title improvement: prefer <h1> or document title
    h1 = soup.find("h1")
    if h1 and text(h1):
        web_fields["title"] = text(h1)
    elif soup.title and soup.title.string:
        web_fields["title"] = soup.title.string.strip()

    # Find a linked PDF (first candidate)
    pdf_url = ""
    for a in soup.select("a[href]"):
        href = a.get("href")
        full = urljoin(url, href)
        if is_pdf_url(full):
            pdf_url = full
            break

    pdf_fields = {}
    if pdf_url:
        txt = fetch_pdf_text(pdf_url)
        if txt:
            pdf_fields = extract_fields_from_text(txt)

    # Merge and return
    merged = merge_record(standardize({"url": url}), standardize(web_fields))
    if pdf_fields:
        merged = merge_record(merged, standardize(pdf_fields))
    return merged

def discover_calls(agency: Dict) -> List[Dict]:
    """Return list of {title, url} discovered from the agency start URLs."""
    found = []
    for start in agency["start_urls"]:
        try:
            soup = http_get(start)
        except Exception as e:
            print(f"[!] Could not open {start}: {e}", file=sys.stderr)
            continue

        for a in soup.select("a[href]"):
            title = text(a)
            href = a.get("href")
            if not title or not href: continue
            url = full_url(start, href)
            t = title.lower()
            if any(b in t for b in BLOCK):  # skip FAQ/forms/tenders
                continue
            if not (any(k in t for k in KEYWORDS) or is_pdf_url(url)):
                continue
            # keep same-domain only
            if not is_internal(url, agency["base"]):
                continue
            found.append({"title": title, "url": url})
    # Rough dedupe by url
    seen = set(); out=[]
    for r in found:
        if r["url"] in seen: continue
        seen.add(r["url"]); out.append(r)
    return out[:60]  # safety cap

def build_records_for_agency(agency: Dict) -> List[Dict]:
    calls = discover_calls(agency)
    out = []
    for item in calls:
        try:
            rec = extract_from_call_page(item["url"])
            # attach agency context
            rec["agency"] = agency["name"]
            rec["category"] = agency["category"]
            rec["researchCategory"] = agency["research"]
            rec["country"] = agency["country"]
            rec["isRecurring"] = False
            # fallback to list title if title empty
            if not rec.get("title"):
                rec["title"] = item["title"]
            out.append(standardize(rec))
            print(f"  [+] {agency['name']}: {rec['title'][:80]}")
        except Exception as e:
            print(f"  [!] Failed {item['url']}: {e}", file=sys.stderr)
    return out

# ------------------ Main ------------------

def main():
    all_rows: List[Dict] = []
    # recurring first
    for r in RECURRING:
        all_rows.append(standardize(r))

    # agencies
    for ag in AGENCIES:
        print(f"[Agency] {ag['name']}")
        rows = build_records_for_agency(ag)
        print(f"   -> {len(rows)} calls")
        all_rows.extend(rows)

    # merge with existing (if you want to keep old calls around)
    existing = load_existing()
    merged = dedupe_merge(existing + all_rows)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"[✓] Wrote {len(merged)} calls → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
