#!/usr/bin/env python3
"""
Scrapes research funding calls from a set of known sources,
extracts key fields from HTML or PDF (Deadline, Eligibility, Area, Budget),
deduplicates results, and writes data.json for the dashboard.

- Heuristic extraction first (regex + structural parsing)
- Optional OpenAI fallback if OPENAI_API_KEY is set (for messy pages/PDFs)
- Designed to run in GitHub Actions on a schedule

Requires:
  requests, beautifulsoup4, lxml, python-dateutil, pdfplumber or PyPDF2
"""

from __future__ import annotations
import os
import re
import json
import time
import logging
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# ----------------------------- PDF extraction backends -----------------------

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

# ----------------------------- Optional AI fallback --------------------------

OPENAI_ENABLED = False
try:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if OPENAI_API_KEY:
        import openai  # type: ignore
        openai.api_key = OPENAI_API_KEY
        OPENAI_ENABLED = True
except Exception:
    OPENAI_ENABLED = False

# ----------------------------- config & logging ------------------------------

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
SLEEP_BETWEEN = 1.0  # be polite

# Link heuristics
KEYWORDS = (
    "call", "grant", "fund", "funding", "proposal", "fellowship",
    "scheme", "schemes", "research", "programme", "program", "apply",
)
EXCLUDE_WORDS = ("faq", "faqs", "form", "forms", "guideline", "guidelines", "result", "results", "awardees")

# Indian agencies (for india_related flag)
INDIAN_AGENCIES = (
    "dst", "dbt", "serb", "icmr", "csir", "igstc", "anrf", "ugc",
    "meity", "icar", "drdo", "insa", "aicte", "nmhs", "ucost",
    "iiser", "iit", "iisc"
)

NON_CALL_HINTS = ("result", "shortlisted", "selected candidates", "awardees", "faq", "faqs")

# ----------------------------- sources --------------------------------------
# Starter list (good, stable listing pages). Add more easily.
SOURCES: List[Dict[str, str]] = [
    # ---- India ----
    {"agency": "DBT (Department of Biotechnology)",
     "url": "https://dbtindia.gov.in/latest-announcements",
     "base": "https://dbtindia.gov.in"},
    {"agency": "DST (Department of Science & Technology)",
     "url": "https://dst.gov.in/call-for-proposals",
     "base": "https://dst.gov.in"},
    {"agency": "SERB (Science & Engineering Research Board)",
     "url": "https://www.serb.gov.in/home/whatsnew",
     "base": "https://www.serb.gov.in"},
    {"agency": "ICMR (Indian Council of Medical Research)",
     "url": "https://main.icmr.nic.in/calls",
     "base": "https://main.icmr.nic.in"},
    {"agency": "CSIR (Council of Scientific & Industrial Research)",
     "url": "https://www.csir.res.in/grants-schemes",
     "base": "https://www.csir.res.in"},
    {"agency": "IGSTC (Indo-German Science & Technology Centre)",
     "url": "https://www.igstc.org/programmes",
     "base": "https://www.igstc.org"},
    {"agency": "MeitY (Ministry of Electronics & IT) – R&D",
     "url": "https://www.meity.gov.in/broadcast",
     "base": "https://www.meity.gov.in"},
    {"agency": "NMHS (National Mission on Himalayan Studies)",
     "url": "https://nmhs.org.in/advertisement.php",
     "base": "https://nmhs.org.in"},
    {"agency": "UCOST (Uttarakhand Council for Science & Technology)",
     "url": "https://ucost.uk.gov.in/advertisements/",
     "base": "https://ucost.uk.gov.in"},

    # ---- International (stable) ----
    {"agency": "European Research Council (ERC)",
     "url": "https://erc.europa.eu/news-events/news",
     "base": "https://erc.europa.eu"},
    {"agency": "UKRI (UK Research and Innovation)",
     "url": "https://www.ukri.org/opportunity/",
     "base": "https://www.ukri.org"},
    {"agency": "Royal Society (UK)",
     "url": "https://royalsociety.org/grants-schemes-awards/grants/",
     "base": "https://royalsociety.org"},
    {"agency": "NIH Grants (USA)",
     "url": "https://grants.nih.gov/funding/searchguide/announce.htm",
     "base": "https://grants.nih.gov"},
    {"agency": "NSF Funding (USA)",
     "url": "https://beta.nsf.gov/funding/opportunities",
     "base": "https://beta.nsf.gov"},
    {"agency": "Wellcome Trust (UK)",
     "url": "https://wellcome.org/grant-funding/schemes",
     "base": "https://wellcome.org"},
    {"agency": "HFSP (Human Frontier Science Program)",
     "url": "https://www.hfsp.org/funding",
     "base": "https://www.hfsp.org"},
    {"agency": "DAAD (Germany) Scholarship/Funding",
     "url": "https://www.daad.de/en/study-and-research-in-germany/scholarships/",
     "base": "https://www.daad.de"},
    {"agency": "DFG (Germany) – Funding at DFG",
     "url": "https://www.dfg.de/en/research-funding/funding-opportunities",
     "base": "https://www.dfg.de"},
]

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

DATE_PATTERNS = [
    r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b",                     # 31-08-2025, 31/08/25, 31.08.2025
    r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})\b",                   # 31 Aug 2025, 31 August 2025
    r"\b(\d{1,2}(st|nd|rd|th)?\s+[A-Za-z]{3,9},?\s+\d{4})\b",     # 31st August, 2025
]
DEADLINE_KEYS = r"(deadline|last\s*date|apply\s*by|closing\s*date|last\s*day|submission\s*(?:till|by)|deadline\s*extended\s*to)"

def try_parse_date(s: str) -> Optional[str]:
    try:
        s_norm = re.sub(r"(\d)(st|nd|rd|th)", r"\1", s, flags=re.I)
        s_norm = s_norm.replace("–", "-").replace("—", "-")
        dt = dateparser.parse(s_norm, dayfirst=True, fuzzy=True)
        if dt:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return None

def pick_reasonable_date(candidates: List[str]) -> Optional[str]:
    today = datetime.utcnow().date()
    out = []
    for c in candidates:
        iso = try_parse_date(c)
        if not iso:
            continue
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        if today - timedelta(days=60) <= d <= today + timedelta(days=550):
            out.append(d)
    if not out:
        return None
    return min(out).strftime("%Y-%m-%d")

def extract_fields_from_text(text: str) -> Dict[str, str]:
    details = {
        "deadline": "N/A",
        "eligibility": "N/A",
        "budget": "N/A",
        "area": "N/A",
        "recurring": "no",
    }
    t = " ".join(text.split())

    # 1) deadline near explicit keys
    m = re.search(DEADLINE_KEYS + r".{0,3}[:\-–—]?\s*(.{0,120})", t, re.I)
    if m:
        region = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)
        if region:
            cands = []
            for pat in DATE_PATTERNS:
                cands += [x[0] if isinstance(x, tuple) else x for x in re.findall(pat, region)]
            iso = pick_reasonable_date(cands)
            if iso:
                details["deadline"] = iso
            else:
                for pat in DATE_PATTERNS:
                    mm = re.search(pat, region)
                    if mm:
                        iso = try_parse_date(mm.group(1))
                        if iso:
                            details["deadline"] = iso
                            break

    # 2) fallback: any plausible date in full text
    if details["deadline"] == "N/A":
        cands = []
        for pat in DATE_PATTERNS:
            cands += [x[0] if isinstance(x, tuple) else x for x in re.findall(pat, t)]
        iso = pick_reasonable_date(cands)
        if iso:
            details["deadline"] = iso

    # eligibility
    m = re.search(r"(eligibility|who\s+can\s+apply)\s*[:\-–—]\s*(.+?)(\. |; |\n|$)", t, re.I)
    if m:
        details["eligibility"] = clean_text(m.group(2))

    # budget
    m = re.search(r"(budget|funding|amount|grant)\s*[:\-–—]\s*([^\.;\n]+)", t, re.I)
    if m:
        details["budget"] = clean_text(m.group(2))
    else:
        m2 = re.search(r"(₹|INR|EUR|€|\$|GBP|£)\s?[\d,]+(?:\.\d+)?", t, re.I)
        if m2:
            details["budget"] = clean_text(m2.group(0))

    # area
    area = detect_area(t)
    if area:
        details["area"] = area

    # recurring
    if re.search(r"\b(annual|every\s*year|rolling|ongoing|always\s*open)\b", t, re.I):
        details["recurring"] = "yes"

    return details

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

def extract_from_html(url: str, html: str) -> Tuple[str, Dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    title = (
        soup.find("h1").get_text(strip=True)
        if soup.find("h1") else soup.title.get_text(strip=True) if soup.title else ""
    )
    title = clean_text(title)
    text = clean_text(soup.get_text(" "))
    details = extract_fields_from_text(text)
    # overlay from table content if helpful
    for table in soup.find_all("table"):
        ttext = clean_text(table.get_text(" "))
        maybe = extract_fields_from_text(ttext)
        for k, v in maybe.items():
            if v != "N/A" and details.get(k, "N/A") == "N/A":
                details[k] = v
    return title, details

def io_bytes(b: bytes):
    from io import BytesIO
    return BytesIO(b)

def extract_from_pdf_bytes(b: bytes) -> Tuple[str, Dict[str, str]]:
    text = ""
    if "pdfplumber" in PDF_BACKENDS:
        import pdfplumber
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

def ai_enrich(text: str) -> Dict[str, str]:
    if not OPENAI_ENABLED:
        return {}
    prompt = f"""
Extract: deadline (ISO yyyy-mm-dd if possible), eligibility, budget, area, recurring ("yes"/"no").
Return STRICT JSON with keys: deadline, eligibility, budget, area, recurring.

Text:
{text[:12000]}
""".strip()
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=300,
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
        if looks_like_call_text(txt) or looks_like_call_text(href_abs):
            if not any(bad in txt.lower() for bad in EXCLUDE_WORDS):
                seen.add(key)
                links.append((txt, href_abs))
    return links

def parse_call(agency: str, title_guess: str, url: str) -> Optional[Dict[str, str]]:
    title = ""
    details = {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A", "recurring": "no"}
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
            text_for_ai = clean_text(BeautifulSoup(r.text, "lxml").get_text(" "))

    # Skip obvious non-call pages
    if any(h in text_for_ai.lower() for h in NON_CALL_HINTS):
        return None

    final_title = clean_text(title) or clean_text(title_guess)
    if len(final_title) < 4:
        return None

    # Optional AI enrichment
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
        "title": final_title,
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

def dedupe(calls: List[Dict[str, str]]) -> List[Dict[str, str]]:
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

# ----------------------------- main -----------------------------------------

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
        for title_guess, href in pairs[:80]:
            try:
                time.sleep(SLEEP_BETWEEN)
                call = parse_call(agency, title_guess, href)
                if call:
                    all_calls.append(call)
            except Exception as e:
                log.warning("parse_call failed %s -> %s", href, e)

    clean_calls = dedupe(all_calls)

    # Sort: upcoming deadlines first; N/A at end
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
