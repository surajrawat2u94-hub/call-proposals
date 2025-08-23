#!/usr/bin/env python3
"""
Scrapes research funding calls from a set of agency listing pages,
extracts key fields (deadline, eligibility, budget, area), deduplicates,
and writes data.json to the repo root for the dashboard.

Features
- Permissive link discovery (2-pass) + optional per-site CSS selectors
- Parses HTML and PDFs
- Heuristic field extraction + optional OpenAI enrichment (if OPENAI_API_KEY set)
- De-duplicates by (Title + Agency) with “more-complete” preference

Dependencies: requests, beautifulsoup4, lxml, python-dateutil, pdfplumber (or PyPDF2), openai (optional)
"""

from __future__ import annotations
import os
import re
import json
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# ----------------------- optional PDF backends -----------------------
PDF_BACKENDS: List[str] = []
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

# ----------------------- optional OpenAI -----------------------------
OPENAI_ENABLED = False
try:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if OPENAI_API_KEY:
        import openai  # type: ignore

        openai.api_key = OPENAI_API_KEY
        OPENAI_ENABLED = True
except Exception:
    OPENAI_ENABLED = False

# ----------------------- Config / constants --------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 40
SLEEP_BETWEEN = 1.0

KEYWORDS = (
    "call", "proposal", "funding", "grant", "fellowship", "scheme",
    "program", "programme", "apply", "application"
)
EXCLUDE = ("faq", "faqs", "form", "forms", "guideline", "guidelines")

INDIAN_HINTS = (
    "india", "indian", "dst", "dbt", "serb", "icmr", "csir", "igstc",
    "anrf", "nmhs", "ucost", "ugc", "icar", "meity", "drdo"
)

# Add agencies here. (You can grow this list from your PDF later.)
SOURCES: List[Dict[str, str]] = [
    # India
    {"agency": "DBT (Department of Biotechnology)", "url": "https://dbtindia.gov.in/latest-announcements", "base": "https://dbtindia.gov.in"},
    {"agency": "DST (Department of Science & Technology)", "url": "https://dst.gov.in/call-for-proposals", "base": "https://dst.gov.in"},
    {"agency": "ICMR (Indian Council of Medical Research)", "url": "https://main.icmr.nic.in/calls", "base": "https://main.icmr.nic.in"},
    {"agency": "CSIR (Council of Scientific & Industrial Research)", "url": "https://www.csir.res.in/grants-schemes", "base": "https://www.csir.res.in"},
    {"agency": "IGSTC (Indo-German Science & Technology Centre)", "url": "https://www.igstc.org/", "base": "https://www.igstc.org"},
    {"agency": "ANRF (formerly SERB)", "url": "https://www.anrf.gov.in/", "base": "https://www.anrf.gov.in"},
    {"agency": "NMHS (National Mission on Himalayan Studies)", "url": "https://nmhs.org.in/", "base": "https://nmhs.org.in"},
    {"agency": "UCoST (Uttarakhand Council for Science & Technology)", "url": "https://ucost.uk.gov.in/", "base": "https://ucost.uk.gov.in"},

    # International examples (extend as needed)
    {"agency": "European Research Council (ERC)", "url": "https://erc.europa.eu/news-events/news", "base": "https://erc.europa.eu"},
    {"agency": "Royal Society (UK)", "url": "https://royalsociety.org/grants-schemes-awards/grants/", "base": "https://royalsociety.org"},
]

# Optional, per-host CSS selectors for “where links usually live”
AGENCY_SELECTORS = {
    "dbtindia.gov.in":         "main a[href], .view-content a[href], .content a[href]",
    "dst.gov.in":              "main a[href], .view-content a[href]",
    "main.icmr.nic.in":        "main a[href], .page-content a[href], .content a[href]",
    "www.csir.res.in":         "main a[href], .content a[href], .node-content a[href]",
    "www.igstc.org":           "main a[href], .content a[href]",
    "www.anrf.gov.in":         "main a[href], .content a[href], .view-content a[href]",
    "nmhs.org.in":             "main a[href], .content a[href]",
    "ucost.uk.gov.in":         "main a[href], .content a[href]",
    "erc.europa.eu":           "main a[href], .content a[href]",
    "royalsociety.org":        "main a[href], .content a[href], .listing a[href]",
}

# ----------------------- utils --------------------------------------
def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def absolute(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)

def http_get(url: str) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception:
        return None

def is_pdf(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")

def india_flag(agency: str, text: str) -> str:
    low = (agency + " " + text).lower()
    return "yes" if (any(k in low for k in INDIAN_HINTS)) else "no"

def looks_like_call(title: str, href: str) -> bool:
    low = (title + " " + href).lower()
    if any(x in low for x in EXCLUDE):
        return False
    return any(k in low for k in KEYWORDS) or is_pdf(href)

# ----------------------- extraction ---------------------------------
def try_parse_date(s: str) -> Optional[str]:
    try:
        s = re.sub(r"(\d)(st|nd|rd|th)", r"\1", s)
        dt = dateparser.parse(s, dayfirst=True, fuzzy=True)
        return dt.strftime("%Y-%m-%d") if dt else None
    except Exception:
        return None

def detect_area(text: str) -> Optional[str]:
    m = text.lower()
    if any(x in m for x in ["medical", "biomedical", "health", "medicine"]):  return "Medical Research"
    if any(x in m for x in ["biotech", "biotechnology"]):                     return "Biotechnology"
    if any(x in m for x in ["physics", "physical science"]):                  return "Physical Sciences"
    if any(x in m for x in ["chemistry", "chemical"]):                        return "Chemical Sciences"
    if any(x in m for x in ["materials", "advanced materials"]):              return "Advanced Materials"
    if any(x in m for x in ["engineering", "technology", "innovation"]):      return "Science & Technology"
    return None

def extract_fields_from_text(text: str) -> Dict[str, str]:
    out = {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A", "recurring": "no"}

    # Deadline
    m = re.search(r"(deadline|last date|apply by|closing date)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if m:
        candidate = clean(m.group(2))
        out["deadline"] = try_parse_date(candidate) or candidate

    # Eligibility
    m = re.search(r"(eligibility|who can apply)\s*[:\-–]\s*(.+?)(\n|\.|\r|$)", text, re.I)
    if m: out["eligibility"] = clean(m.group(2))

    # Budget
    m = re.search(r"(budget|funding|amount|grant)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if m:
        out["budget"] = clean(m.group(2))
    else:
        m2 = re.search(r"(₹|INR|EUR|€|\$|GBP|£)\s?[\d,]+(\.\d+)?", text, re.I)
        if m2: out["budget"] = clean(m2.group(0))

    # Area
    area = detect_area(text)
    if area: out["area"] = area

    # Recurring
    if re.search(r"\b(annual|every year|rolling|ongoing|recurring)\b", text, re.I):
        out["recurring"] = "yes"

    return out

def extract_from_html(url: str, html: str) -> Tuple[str, Dict[str, str], str]:
    soup = BeautifulSoup(html, "lxml")
    title = clean(soup.find("h1").get_text(strip=True)) if soup.find("h1") else (clean(soup.title.get_text(strip=True)) if soup.title else "")
    text = clean(soup.get_text(" "))
    details = extract_fields_from_text(text)

    # Try tables for labeled data
    for table in soup.find_all("table"):
        t = clean(table.get_text(" "))
        maybe = extract_fields_from_text(t)
        for k, v in maybe.items():
            if v != "N/A" and details.get(k, "N/A") == "N/A":
                details[k] = v
    return title, details, text

def extract_from_pdf_bytes(b: bytes) -> Tuple[str, Dict[str, str], str]:
    text = ""
    if "pdfplumber" in PDF_BACKENDS:
        from io import BytesIO
        import pdfplumber  # type: ignore
        with pdfplumber.open(BytesIO(b)) as pdf:
            for page in pdf.pages[:8]:
                text += "\n" + (page.extract_text() or "")
    elif "pypdf2" in PDF_BACKENDS:
        from io import BytesIO
        from PyPDF2 import PdfReader  # type: ignore
        reader = PdfReader(BytesIO(b))
        for p in reader.pages[:8]:
            try:
                text += "\n" + (p.extract_text() or "")
            except Exception:
                pass
    text = clean(text)
    title = clean(text.splitlines()[0]) if text else ""
    details = extract_fields_from_text(text)
    return title, details, text

def ai_enrich(text: str) -> Dict[str, str]:
    if not OPENAI_ENABLED or not text:
        return {}
    prompt = f"""
Return STRICT JSON with keys: deadline, eligibility, budget, area, recurring.
Use ISO YYYY-MM-DD for deadline when possible; else "N/A".
Text:
{text[:12000]}
"""
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=300,
        )
        content = resp["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.S)
        if not m: return {}
        data = json.loads(m.group(0))
        out = {}
        for k in ("deadline", "eligibility", "budget", "area", "recurring"):
            v = clean(str(data.get(k, "N/A")))
            out[k] = v if v else "N/A"
        return out
    except Exception:
        return {}

# ----------------------- link discovery ------------------------------
from urllib.parse import urlparse

def collect_links(listing_url: str, base: str) -> List[Tuple[str, str]]:
    r = http_get(listing_url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    links: List[Tuple[str, str]] = []
    seen = set()

    def add(txt: str, href_abs: str):
        key = (clean(txt).lower(), href_abs.split("#")[0])
        if key not in seen:
            seen.add(key)
            links.append((clean(txt) or href_abs, href_abs))

    # If we know a good container selector for this host, try it first
    host = urlparse(listing_url).netloc or urlparse(base).netloc
    sel = AGENCY_SELECTORS.get(host)
    if sel:
        for a in soup.select(sel):
            txt = clean(a.get_text(" "))
            href = a.get("href", "").strip()
            if not href:
                continue
            href_abs = absolute(base, href)
            if looks_like_call(txt, href_abs):
                add(txt, href_abs)
        if len(links) >= 5:
            return links[:80]

    # Pass 1: strict calls (keywords / PDFs)
    for a in soup.select("a[href]"):
        txt = clean(a.get_text(" "))
        href = a.get("href", "").strip()
        if not href:
            continue
        href_abs = absolute(base, href)
        if looks_like_call(txt, href_abs):
            add(txt, href_abs)

    # Pass 2: Not enough? Broaden within content areas
    if len(links) < 5:
        for a in soup.select("main a[href], article a[href], .content a[href], .view-content a[href]"):
            if len(links) >= 80:
                break
            txt = clean(a.get_text(" "))
            href = a.get("href", "").strip()
            if not href:
                continue
            href_abs = absolute(base, href)
            low = (txt + " " + href_abs).lower()
            if any(x in low for x in EXCLUDE):
                continue
            add(txt, href_abs)

    return links[:80]

# ----------------------- parse a single call -------------------------
def parse_call(agency: str, link_title: str, url: str) -> Dict[str, str]:
    title, details, full_text = "", {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A", "recurring": "no"}, ""

    if is_pdf(url):
        r = http_get(url)
        if r and r.content:
            title, details, full_text = extract_from_pdf_bytes(r.content)
    else:
        r = http_get(url)
        if r and r.text:
            title, details, full_text = extract_from_html(url, r.text)

    final_title = clean(title) or clean(link_title)

    # AI enrichment if missing and key available
    if OPENAI_ENABLED and any(details.get(k, "N/A") == "N/A" for k in ("deadline", "eligibility", "budget", "area")):
        enriched = ai_enrich(full_text)
        for k, v in enriched.items():
            if details.get(k, "N/A") == "N/A" and v and v != "N/A":
                details[k] = v

    if details.get("area") in (None, "", "N/A"):
        maybe_area = detect_area(full_text)
        if maybe_area:
            details["area"] = maybe_area

    return {
        "title": final_title if final_title else "N/A",
        "deadline": details.get("deadline", "N/A"),
        "funding_agency": agency,
        "area": details.get("area", "N/A"),
        "eligibility": details.get("eligibility", "N/A"),
        "budget": details.get("budget", "N/A"),
        "website": url,
        "recurring": details.get("recurring", "no"),
        "india_related": india_flag(agency, full_text),
    }

# ----------------------- dedupe & sort --------------------------------
def dedupe(calls: List[Dict[str, str]]) -> List[Dict[str, str]]:
    best = {}
    for c in calls:
        key = (c.get("title", "").strip().lower(), c.get("funding_agency", "").strip().lower())
        if key not in best:
            best[key] = c
        else:
            def score(x: Dict[str, str]) -> int:
                return sum(1 for k in ("deadline", "eligibility", "budget", "area") if x.get(k) and x.get(k) != "N/A")
            if score(c) > score(best[key]):
                best[key] = c
    return list(best.values())

def sortkey(c: Dict[str, str]) -> tuple:
    d = c.get("deadline", "N/A")
    if d == "N/A": return (1, "9999-12-31")
    return (0, d)

# ----------------------- main ----------------------------------------
def main():
    all_calls: List[Dict[str, str]] = []
    for src in SOURCES:
        agency, url, base = src["agency"], src["url"], src["base"]
        time.sleep(SLEEP_BETWEEN)
        pairs = collect_links(url, base)
        for t, href in pairs:
            try:
                time.sleep(SLEEP_BETWEEN)
                call = parse_call(agency, t, href)
                if len(call["title"]) < 4:
                    continue
                all_calls.append(call)
            except Exception:
                pass

    clean_calls = dedupe(all_calls)
    clean_calls.sort(key=sortkey)

    out = {
        "updated_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "calls": clean_calls,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(clean_calls)} calls to data.json")

if __name__ == "__main__":
    main()
