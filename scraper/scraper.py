#!/usr/bin/env python3
"""
Scrapes research funding calls from curated sources, extracts key fields
(Deadline, Eligibility, Area, Budget) from HTML or PDF pages, deduplicates,
and writes data.json for the dashboard.

- Heuristic extraction (regex + structure)
- Optional OpenAI fallback if OPENAI_API_KEY is set
- Designed to run in GitHub Actions on a schedule

Requires:
  requests, beautifulsoup4, lxml, python-dateutil, pdfplumber (or PyPDF2), (openai optional)
"""

from __future__ import annotations
import os
import re
import json
import time
import hashlib
import logging
import urllib.parse
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# -------- PDF backends (prefer pdfplumber; fallback to PyPDF2) --------------
PDF_BACKENDS = []
try:
    import pdfplumber  # type: ignore
    PDF_BACKENDS.append("pdfplumber")
except Exception:
    pass
try:
    from PyPDF2 import PdfReader  # type: ignore
    PDF_BACKENDS.append("pypdf2")
except Exception:
    pass

# ---------------------- Optional OpenAI fallback -----------------------------
OPENAI_ENABLED = False
try:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if OPENAI_API_KEY:
        import openai  # type: ignore
        openai.api_key = OPENAI_API_KEY
        OPENAI_ENABLED = True
except Exception:
    OPENAI_ENABLED = False

# ----------------------------- logging & http --------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
log = logging.getLogger("scraper")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
SLEEP_BETWEEN = 1.0  # be polite to servers

# ---------------------------- heuristics config ------------------------------
KEYWORDS = (
    "call", "grant", "fund", "funding", "proposal",
    "fellowship", "scheme", "schemes", "research", "programme", "program"
)
EXCLUDE_WORDS = ("faq", "faqs", "form", "forms", "guideline", "guidelines")

INDIAN_AGENCIES = (
    "dst", "dbt", "serb", "icmr", "csir", "igstc",
    "anrf", "ucost", "nmhs", "ug c", "ugc", "meity", "icar", "drdo",
)

# ----------------------------- curated SOURCES -------------------------------
# Keep your large curated list here (66+ entries are fine). A few examples:
SOURCES: List[Dict[str, str]] = [
    # --- India examples ---
    {"agency": "DBT (Department of Biotechnology)",
     "url": "https://dbtindia.gov.in/latest-announcements",
     "base": "https://dbtindia.gov.in"},
    {"agency": "DST (Department of Science & Technology)",
     "url": "https://dst.gov.in/call-for-proposals",
     "base": "https://dst.gov.in"},
    {"agency": "ICMR (Indian Council of Medical Research)",
     "url": "https://main.icmr.nic.in/calls",
     "base": "https://main.icmr.nic.in"},
    {"agency": "CSIR (Council of Scientific & Industrial Research)",
     "url": "https://www.csir.res.in/grants-schemes",
     "base": "https://www.csir.res.in"},
    {"agency": "IGSTC (Indo-German Science & Technology Centre)",
     "url": "https://www.igstc.org/",
     "base": "https://www.igstc.org"},

    # --- add the rest of your curated ~66 agencies here ---
    # e.g. ANRF, UCOST, NMHS, SERB, UGC, ICAR, DRDO, MEITY, etc.
    # International examples:
    {"agency": "European Research Council",
     "url": "https://erc.europa.eu/news-events/news",
     "base": "https://erc.europa.eu"},
    {"agency": "Royal Society",
     "url": "https://royalsociety.org/grants-schemes-awards/grants/",
     "base": "https://royalsociety.org"},
]

# ---------------------- normalize / dedupe SOURCES ---------------------------
def _normalize_sources(sources: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    out = []
    for s in sources:
        agency = (s.get("agency") or "").strip()
        url = (s.get("url") or "").strip()
        base = (s.get("base") or "").strip()
        if not agency or not url or not base:
            continue
        key = (agency.lower(), url.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"agency": agency, "url": url, "base": base})
    return out

SOURCES = _normalize_sources(SOURCES)

EXPECTED_COUNT = 66  # For reference only
if len(SOURCES) != EXPECTED_COUNT:
    print(f"WARNING: SOURCES currently {len(SOURCES)} (expected {EXPECTED_COUNT}). Continuing…")

# ----------------------------- helpers --------------------------------------
def http_get(url: str) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning("GET failed %s -> %s", url, e)
        return None

def absolute_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)

def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def norm_key(*parts: str) -> str:
    return "|".join(clean_text(p).lower() for p in parts if p)

def is_pdf_link(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")

def is_probably_indian(agency: str, text: str) -> bool:
    t = f"{agency} {text}".lower()
    return any(k in t for k in INDIAN_AGENCIES) or (" india" in t or "indian" in t)

def looks_like_call_text(s: str) -> bool:
    s = (s or "").lower()
    if any(x in s for x in EXCLUDE_WORDS):
        return False
    return any(k in s for k in KEYWORDS)

# ----------------------------- extraction -----------------------------------
def extract_from_html(url: str, html: str) -> Tuple[str, Dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")

    title = (
        soup.find("h1").get_text(strip=True)
        if soup.find("h1") else soup.title.get_text(strip=True) if soup.title else ""
    )
    title = clean_text(title)

    text = clean_text(soup.get_text(" "))
    details = extract_fields_from_text(text)

    for table in soup.find_all("table"):
        ttext = clean_text(table.get_text(" "))
        maybe = extract_fields_from_text(ttext)
        for k, v in maybe.items():
            if v != "N/A" and details.get(k, "N/A") == "N/A":
                details[k] = v

    return title, details

def extract_fields_from_text(text: str) -> Dict[str, str]:
    details = {
        "deadline": "N/A",
        "eligibility": "N/A",
        "budget": "N/A",
        "area": "N/A",
        "recurring": "no",
    }

    # Deadline/last date
    m = re.search(r"(deadline|last date)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if not m:
        m = re.search(r"(apply by|closing date)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if m:
        candidate = clean_text(m.group(2))
        dt = try_parse_date(candidate)
        details["deadline"] = dt if dt else candidate

    # Eligibility
    m = re.search(r"(eligibility|who can apply)\s*[:\-–]\s*(.+?)(\n|\.|\r|$)", text, re.I)
    if m:
        details["eligibility"] = clean_text(m.group(2))
    else:
        m2 = re.search(r"eligibility(.{0,12})[:\-–]\s*(.+)", text, re.I)
        if m2:
            details["eligibility"] = clean_text(m2.group(2).split(". ")[0])

    # Budget/funding
    m = re.search(r"(budget|funding|amount|grant)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if m:
        details["budget"] = clean_text(m.group(2))
    else:
        m2 = re.search(r"(₹|INR|EUR|€|\$|GBP|£)\s?[\d,]+(\.\d+)?", text, re.I)
        if m2:
            details["budget"] = clean_text(m2.group(0))

    area = detect_area(text)
    if area:
        details["area"] = area

    if re.search(r"\b(annual|every year|rolling|ongoing)\b", text, re.I):
        details["recurring"] = "yes"

    return details

def try_parse_date(s: str) -> Optional[str]:
    try:
        s = re.sub(r"(\d)(st|nd|rd|th)", r"\1", s)
        dt = dateparser.parse(s, dayfirst=True, fuzzy=True)
        if dt:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        return None
    return None

def detect_area(text: str) -> Optional[str]:
    m = text.lower()
    if any(x in m for x in ["medical", "health", "biomedical", "medicine"]):
        return "Medical Research"
    if any(x in m for x in ["biotech", "biotechnology"]):
        return "Biotechnology"
    if any(x in m for x in ["physics", "physical science"]):
        return "Physical Sciences"
    if any(x in m for x in ["chemistry", "chemical"]):
        return "Chemical Sciences"
    if any(x in m for x in ["engineering", "technology"]):
        return "Science & Technology"
    if any(x in m for x in ["advanced materials", "materials"]):
        return "Advanced Materials"
    if any(x in m for x in ["innovation"]):
        return "Science & Innovation"
    return None

def extract_from_pdf_bytes(b: bytes) -> Tuple[str, Dict[str, str]]:
    text = ""
    if "pdfplumber" in PDF_BACKENDS:
        with pdfplumber.open(io_bytes(b)) as pdf:
            for page in pdf.pages[:8]:
                text += "\n" + (page.extract_text() or "")
    elif "pypdf2" in PDF_BACKENDS:
        from io import BytesIO
        reader = PdfReader(BytesIO(b))
        for page in reader.pages[:8]:
            try:
                text += "\n" + (page.extract_text() or "")
            except Exception:
                pass
    else:
        return "", {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A", "recurring": "no"}

    text = clean_text(text)
    lines = [ln for ln in text.splitlines() if clean_text(ln)]
    title = clean_text(lines[0]) if lines else ""
    details = extract_fields_from_text(text)
    return title, details

def io_bytes(b: bytes):
    from io import BytesIO
    return BytesIO(b)

def ai_enrich(text: str) -> Dict[str, str]:
    if not OPENAI_ENABLED:
        return {}
    prompt = f"""
Extract the fields: deadline, eligibility, budget, area, recurring.
Return STRICT JSON. Deadline in YYYY-MM-DD if possible; 'N/A' otherwise.
Text:
{text[:12000]}
    """.strip()
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=300,
        )
        content = resp["choices"][0]["message"]["content"].strip()
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return {}
        data = json.loads(m.group(0))
        out = {}
        for k in ("deadline", "eligibility", "budget", "area", "recurring"):
            v = clean_text(str(data.get(k, "N/A")))
            out[k] = v if v else "N/A"
        return out
    except Exception as e:
        log.warning("AI enrich failed: %s", e)
        return {}

# ----------------------------- crawl & parse --------------------------------
def collect_links(listing_url: str, base: str) -> List[Tuple[str, str]]:
    r = http_get(listing_url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        txt = clean_text(a.get_text(" "))
        href = a["href"].strip()
        href_abs = absolute_url(base, href)
        key = (txt.lower(), href_abs.split("#")[0])
        if key in seen:
            continue
        if not txt:
            continue
        if looks_like_call_text(txt):
            seen.add(key)
            links.append((txt, href_abs))
    return links

def parse_call(agency: str, title_guess: str, url: str) -> Dict[str, str]:
    # Default details
    title, details = "", {
        "deadline": "N/A",
        "eligibility": "N/A",
        "budget": "N/A",
        "area": "N/A",
        "recurring": "no",
    }
    text_for_ai = ""

    if is_pdf_link(url):
        r = http_get(url)
        if r and r.content:
            title, details = extract_from_pdf_bytes(r.content)
            text_for_ai = f"{title}\n\n{clean_text(str(details))}"
    else:
        r = http_get(url)
        if r and r.text:
            title, details = extract_from_html(url, r.text)
            soup = BeautifulSoup(r.text, "lxml")
            text_for_ai = clean_text(soup.get_text(" "))

    final_title = clean_text(title) or clean_text(title_guess)

    if OPENAI_ENABLED:
        needs = any(details.get(k, "N/A") == "N/A" for k in ("deadline", "eligibility", "budget", "area"))
        if needs and text_for_ai:
            enriched = ai_enrich(text_for_ai)
            for k, v in enriched.items():
                if details.get(k, "N/A") == "N/A" and v and v != "N/A":
                    details[k] = v

    if details.get("area") in [None, "", "N/A"]:
        maybe = detect_area(text_for_ai)
        if maybe:
            details["area"] = maybe

    out = {
        "title": final_title if final_title else "N/A",
        "deadline": details.get("deadline", "N/A"),
        "funding_agency": agency,
        "area": details.get("area", "N/A"),
        "eligibility": details.get("eligibility", "N/A"),
        "budget": details.get("budget", "N/A"),
        "website": url,
        "recurring": details.get("recurring", "no"),
        "india_related": "yes" if is_probably_indian(agency, text_for_ai) else "no",
    }
    return out

def dedupe_calls(calls: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = {}
    for c in calls:
        key = norm_key(c.get("title", ""), c.get("funding_agency", ""))
        if key not in seen:
            seen[key] = c
        else:
            def score(x: Dict[str, str]) -> int:
                return sum(1 for k in ("deadline", "eligibility", "budget", "area") if x.get(k) and x.get(k) != "N/A")
            if score(c) > score(seen[key]):
                seen[key] = c
    return list(seen.values())

# -------------------------------- main --------------------------------------
def main():
    all_calls: List[Dict[str, str]] = []
    for src in SOURCES:
        agency = src["agency"]
        url = src["url"]
        base = src["base"]
        log.info("Listing: %s (%s)", agency, url)
        time.sleep(SLEEP_BETWEEN)
        pairs = collect_links(url, base)
        log.info("  found %d candidate links", len(pairs))

        for title_guess, href in pairs[:80]:  # safety cap per source
            try:
                time.sleep(SLEEP_BETWEEN)
                call = parse_call(agency, title_guess, href)
                if len(call["title"]) < 4:
                    continue
                all_calls.append(call)
            except Exception as e:
                log.warning("parse_call failed %s -> %s", href, e)

    clean_calls = dedupe_calls(all_calls)

    def sortkey(c: Dict[str, str]) -> Tuple[int, str]:
        d = c.get("deadline", "N/A")
        if d == "N/A":
            return (1, "9999-12-31")
        return (0, d)

    clean_calls.sort(key=sortkey)

    out = {
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "calls": clean_calls,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("Wrote %d calls to data.json", len(clean_calls))

if __name__ == "__main__":
    main()
