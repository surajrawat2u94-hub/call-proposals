#!/usr/bin/env python3
"""
Smarter scraper for funding calls.

- Per-source adapters (scope CSS, must_path, block_path, max_links)
- HTML or PDF extraction
- Heuristics + optional OpenAI fallback
- Writes data.json used by the dashboard
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

# --------- PDF backends ----------
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

# --------- Optional AI -----------
OPENAI_ENABLED = False
try:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if OPENAI_API_KEY:
        import openai  # type: ignore
        openai.api_key = OPENAI_API_KEY
        OPENAI_ENABLED = True
except Exception:
    OPENAI_ENABLED = False

# --------- logging ---------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger("scraper")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
TIMEOUT = 30
SLEEP_BETWEEN = 1.0

# --------- heuristics ------------
KEYWORDS = (
    "call", "grant", "fund", "funding", "proposal", "fellowship",
    "scheme", "schemes", "research", "programme", "program", "apply",
)
NON_CALL_HINTS_IN_BODY = (" only results ", " shortlisted ", " awardees ")
GLOBAL_BLOCK_PATH = ("result", "results", "faq", "faqs", "awardee", "awardees", "form", "forms")

INDIAN_AGENCIES = (
    "dst","dbt","serb","icmr","csir","igstc","anrf","ugc","meity","icar","drdo",
    "insa","aicte","nmhs","ucost","iiser","iit","iisc","isro","barc","csdst","csrt"
)

DATE_PATTERNS = [
    r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b",
    r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})\b",
    r"\b(\d{1,2}(st|nd|rd|th)?\s+[A-Za-z]{3,9},?\s+\d{4})\b",
]
DEADLINE_KEYS = r"(deadline|last\s*date|apply\s*by|closing\s*date|last\s*day|submission\s*(?:till|by)|deadline\s*extended\s*to)"

# --------- sources ---------------
# You can also put these in scraper/sources.json (same structure), and they
# will be merged/overridden.
SOURCES: List[Dict[str, object]] = [
    # ===================== INDIA =====================
    {
        "agency": "DBT (Department of Biotechnology)",
        "url": "https://dbtindia.gov.in/latest-announcements",
        "base": "https://dbtindia.gov.in",
        "scope": ".view-content, .views-row, body",
        "must_path": ["call","advert","adv","fund","grant","scheme","fellow"],
        "block_path": ["result","awardee","faq","form"],
        "max_links": 80,
    },
    {
        "agency": "DST (Department of Science & Technology)",
        "url": "https://dst.gov.in/call-for-proposals",
        "base": "https://dst.gov.in",
        "scope": ".view-content, .node__content, body",
        "must_path": ["call","proposal","fund","grant","scheme","fellow","advert"],
        "block_path": ["result","faq","form"],
        "max_links": 80,
    },
    {
        "agency": "SERB (Science & Engineering Research Board)",
        "url": "https://www.serb.gov.in/home/whatsnew",
        "base": "https://www.serb.gov.in",
        "scope": "body",
        "must_path": ["call","fund","grant","scheme","fellow","advert","rfe","rfp"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "ICMR (Indian Council of Medical Research)",
        "url": "https://main.icmr.nic.in/calls",
        "base": "https://main.icmr.nic.in",
        "scope": "body",
        "must_path": ["call","fund","grant","scheme","fellow","advert","project","pdf"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "CSIR (Council of Scientific & Industrial Research)",
        "url": "https://www.csir.res.in/grants-schemes",
        "base": "https://www.csir.res.in",
        "scope": "body",
        "must_path": ["call","fund","grant","scheme","fellow","advert"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "IGSTC (Indo-German Science & Technology Centre)",
        "url": "https://www.igstc.org/programmes",
        "base": "https://www.igstc.org",
        "scope": "body",
        "must_path": ["call","fund","grant","scheme","fellow","programme"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "MeitY (Ministry of Electronics & IT) – R&D",
        "url": "https://www.meity.gov.in/broadcast",
        "base": "https://www.meity.gov.in",
        "scope": "body",
        "must_path": ["call","fund","grant","scheme","fellow","r&d","advert"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "NMHS (National Mission on Himalayan Studies)",
        "url": "https://nmhs.org.in/advertisement.php",
        "base": "https://nmhs.org.in",
        "scope": "body",
        "must_path": ["advert","call","fund","grant","scheme","fellow"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "UCOST (Uttarakhand Council for Science & Technology)",
        "url": "https://ucost.uk.gov.in/advertisements/",
        "base": "https://ucost.uk.gov.in",
        "scope": "body",
        "must_path": ["advert","call","fund","grant","scheme","fellow"],
        "block_path": ["result","faq","form"],
    },

    # ===================== INTERNATIONAL =====================
    {
        "agency": "European Research Council (ERC)",
        "url": "https://erc.europa.eu/news-events/news",
        "base": "https://erc.europa.eu",
        "scope": "body",
        "must_path": ["call","fund","grant","scheme","fellow","programme"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "UKRI (UK Research and Innovation)",
        "url": "https://www.ukri.org/opportunity/",
        "base": "https://www.ukri.org",
        "scope": "body",
        "must_path": ["opportunity","fund","grant","scheme","fellow","call"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "Royal Society (UK)",
        "url": "https://royalsociety.org/grants-schemes-awards/grants/",
        "base": "https://royalsociety.org",
        "scope": "body",
        "must_path": ["grant","fund","scheme","fellow","call"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "NIH Grants (USA)",
        "url": "https://grants.nih.gov/funding/searchguide/announce.htm",
        "base": "https://grants.nih.gov",
        "scope": "body",
        "must_path": ["rfa","rfa-","rfa_","pa-","par-","rfa.htm","rfa.html","fund","grant","call"],
        "block_path": ["result","faq","form"],
        "max_links": 60,
    },
    {
        "agency": "NSF Funding (USA)",
        "url": "https://beta.nsf.gov/funding/opportunities",
        "base": "https://beta.nsf.gov",
        "scope": "body",
        "must_path": ["opportunity","fund","grant","program","call"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "Wellcome Trust (UK)",
        "url": "https://wellcome.org/grant-funding/schemes",
        "base": "https://wellcome.org",
        "scope": "body",
        "must_path": ["scheme","fund","grant","fellow","call"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "HFSP (Human Frontier Science Program)",
        "url": "https://www.hfsp.org/funding",
        "base": "https://www.hfsp.org",
        "scope": "body",
        "must_path": ["fund","grant","fellow","call"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "DAAD (Germany) Scholarships/Funding",
        "url": "https://www.daad.de/en/study-and-research-in-germany/scholarships/",
        "base": "https://www.daad.de",
        "scope": "body",
        "must_path": ["scholarship","fund","grant","fellow","call"],
        "block_path": ["result","faq","form"],
    },
    {
        "agency": "DFG (Germany) – Funding",
        "url": "https://www.dfg.de/en/research-funding/funding-opportunities",
        "base": "https://www.dfg.de",
        "scope": "body",
        "must_path": ["fund","grant","program","call"],
        "block_path": ["result","faq","form"],
    },
]

# --------- helpers ---------------
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

def link_text_matches(txt: str, url: str) -> bool:
    s = (txt or "").lower()
    u = (url or "").lower()
    if any(k in s for k in KEYWORDS): return True
    if any(k in u for k in KEYWORDS): return True
    return False

# --------- extraction -----------
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

def pick_reasonable_date(cands: List[str]) -> Optional[str]:
    today = datetime.utcnow().date()
    parsed = []
    for c in cands:
        iso = try_parse_date(c)
        if not iso: continue
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        if today - timedelta(days=90) <= d <= today + timedelta(days=720):
            parsed.append(d)
    if not parsed: return None
    return min(parsed).strftime("%Y-%m-%d")

def detect_area(text: str) -> Optional[str]:
    m = text.lower()
    if any(x in m for x in ["medical", "health", "biomedical", "medicine"]): return "Medical Research"
    if any(x in m for x in ["biotech", "biotechnology"]): return "Biotechnology"
    if any(x in m for x in ["physics", "physical science"]): return "Physical Sciences"
    if any(x in m for x in ["chemistry", "chemical"]): return "Chemical Sciences"
    if any(x in m for x in ["engineering", "technology"]): return "Science & Technology"
    if any(x in m for x in ["advanced materials", "materials"]): return "Advanced Materials"
    if any(x in m for x in ["innovation"]): return "Science & Innovation"
    return None

def extract_fields_from_text(text: str) -> Dict[str, str]:
    t = " ".join(text.split())
    details = {"deadline":"N/A","eligibility":"N/A","budget":"N/A","area":"N/A","recurring":"no"}

    # deadline near keys
    m = re.search(DEADLINE_KEYS + r".{0,3}[:\-–—]?\s*(.{0,120})", t, re.I)
    if m:
        region = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)
        cands = []
        for p in DATE_PATTERNS:
            cands += [x[0] if isinstance(x, tuple) else x for x in re.findall(p, region)]
        iso = pick_reasonable_date(cands)
        if iso: details["deadline"] = iso

    if details["deadline"] == "N/A":
        cands = []
        for p in DATE_PATTERNS:
            cands += [x[0] if isinstance(x, tuple) else x for x in re.findall(p, t)]
        iso = pick_reasonable_date(cands)
        if iso: details["deadline"] = iso

    m = re.search(r"(eligibility|who\s+can\s+apply)\s*[:\-–—]\s*(.+?)(\. |; |\n|$)", t, re.I)
    if m: details["eligibility"] = clean_text(m.group(2))

    m = re.search(r"(budget|funding|amount|grant)\s*[:\-–—]\s*([^\.;\n]+)", t, re.I)
    if m:
        details["budget"] = clean_text(m.group(2))
    else:
        m2 = re.search(r"(₹|INR|EUR|€|\$|GBP|£)\s?[\d,]+(?:\.\d+)?", t, re.I)
        if m2: details["budget"] = clean_text(m2.group(0))

    ar = detect_area(t)
    if ar: details["area"] = ar

    if re.search(r"\b(annual|every\s*year|rolling|ongoing|always\s*open)\b", t, re.I):
        details["recurring"] = "yes"

    return details

def extract_from_html(url: str, html: str) -> Tuple[str, Dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.find("h1").get_text(strip=True) if soup.find("h1") else soup.title.get_text(strip=True) if soup.title else ""
    text = clean_text(soup.get_text(" "))
    details = extract_fields_from_text(text)
    for table in soup.find_all("table"):
        ttext = clean_text(table.get_text(" "))
        maybe = extract_fields_from_text(ttext)
        for k,v in maybe.items():
            if v != "N/A" and details.get(k,"N/A") == "N/A":
                details[k] = v
    return clean_text(title), details

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
        return "", {"deadline":"N/A","eligibility":"N/A","budget":"N/A","area":"N/A","recurring":"no"}
    text = clean_text(text)
    lines = [ln for ln in text.splitlines() if clean_text(ln)]
    title = clean_text(lines[0]) if lines else ""
    return title, extract_fields_from_text(text)

def ai_enrich(text: str) -> Dict[str, str]:
    if not OPENAI_ENABLED: return {}
    prompt = f"""Extract: deadline (ISO yyyy-mm-dd), eligibility, budget, area, recurring ("yes"/"no").
Return STRICT JSON with keys: deadline, eligibility, budget, area, recurring.

Text:
{text[:12000]}"""
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role":"user", "content":prompt}],
            temperature=0.0, max_tokens=300,
        )
        content = resp["choices"][0]["message"]["content"].strip()
        m = re.search(r"\{.*\}", content, re.S)
        if not m: return {}
        data = json.loads(m.group(0))
        out = {}
        for k in ("deadline","eligibility","budget","area","recurring"):
            v = clean_text(str(data.get(k,"N/A")))
            out[k] = v if v else "N/A"
        return out
    except Exception as e:
        log.warning("AI enrich failed: %s", e)
        return {}

# --------- link collection -------
def collect_links(src: Dict[str, object]) -> List[Tuple[str,str]]:
    url = src["url"]; base = src.get("base", "")
    r = http_get(str(url))
    if not r: return []
    soup = BeautifulSoup(r.text, "lxml")

    scope_sel = str(src.get("scope") or "body")
    scope = soup.select_one(scope_sel) or soup
    anchors = scope.find_all("a", href=True)

    must_paths = set([x.lower() for x in (src.get("must_path") or [])])
    block_paths = set([x.lower() for x in (src.get("block_path") or [])]) | set(GLOBAL_BLOCK_PATH)
    max_links = int(src.get("max_links") or 80)

    kept: List[Tuple[str,str]] = []
    seen = set()

    for a in anchors:
        txt = clean_text(a.get_text(" "))
        href_abs = absolute_url(str(base), a["href"].strip())
        key = (txt.lower(), href_abs.split("#")[0])
        if key in seen: continue
        href_low = href_abs.lower()

        # Exclude blocked path fragments
        if any(bp in href_low for bp in block_paths): continue

        # Keep if anchor/URL matches keywords
        ok = link_text_matches(txt, href_abs)

        # Otherwise keep if URL includes any must_path fragments
        if not ok and must_paths:
            if any(mp in href_low for mp in must_paths):
                ok = True

        if ok:
            kept.append((txt or href_abs.split("/")[-1], href_abs))
            seen.add(key)
            if len(kept) >= max_links: break

    return kept

# --------- parse page ------------
def parse_call(agency: str, title_guess: str, url: str) -> Optional[Dict[str,str]]:
    title = ""
    details = {"deadline":"N/A","eligibility":"N/A","budget":"N/A","area":"N/A","recurring":"no"}
    body_text = ""

    if is_pdf_link(url):
        r = http_get(url)
        if r and r.content:
            title, details = extract_from_pdf_bytes(r.content)
            body_text = f"{title}\n{details}"
    else:
        r = http_get(url)
        if r and r.text:
            title, details = extract_from_html(url, r.text)
            body_text = clean_text(BeautifulSoup(r.text, "lxml").get_text(" "))

    if any(h in f" {body_text.lower()} " for h in NON_CALL_HINTS_IN_BODY):
        return None

    final_title = clean_text(title) or clean_text(title_guess)
    if len(final_title) < 4: return None

    if OPENAI_ENABLED and any(details.get(k,"N/A")=="N/A" for k in ("deadline","eligibility","budget","area")):
        enriched = ai_enrich(body_text)
        for k,v in enriched.items():
            if details.get(k,"N/A")=="N/A" and v and v!="N/A":
                details[k]=v

    if details.get("area") in ("","N/A",None):
        maybe = detect_area(body_text)
        if maybe: details["area"] = maybe

    return {
        "title": final_title,
        "deadline": details.get("deadline","N/A"),
        "funding_agency": agency,
        "area": details.get("area","N/A"),
        "eligibility": details.get("eligibility","N/A"),
        "budget": details.get("budget","N/A"),
        "website": url,
        "recurring": details.get("recurring","no"),
        "india_related": "yes" if is_probably_indian(agency, body_text) else "no",
    }

# --------- dedupe ----------------
def dedupe(calls: List[Dict[str,str]]) -> List[Dict[str,str]]:
    seen = {}
    for c in calls:
        key = norm_key(c.get("title",""), c.get("funding_agency",""))
        if key not in seen:
            seen[key]=c
        else:
            def score(x): return sum(1 for k in ("deadline","eligibility","budget","area") if x.get(k) and x.get(k)!="N/A")
            if score(c) > score(seen[key]): seen[key]=c
    return list(seen.values())

# --------- main ------------------
def load_sources_from_json() -> List[Dict[str,object]]:
    path = os.path.join(os.path.dirname(__file__), "sources.json")
    if not os.path.exists(path): return []
    try:
        with open(path,"r",encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data,list): return data
    except Exception as e:
        log.warning("sources.json ignored: %s", e)
    return []

def main():
    sources = SOURCES[:]  # built-ins
    sources_from_file = load_sources_from_json()
    if sources_from_file:
        log.info("Merging %d sources from sources.json", len(sources_from_file))
        sources.extend(sources_from_file)

    all_calls: List[Dict[str,str]] = []
    for src in sources:
        agency = str(src["agency"])
        log.info("Listing: %s (%s)", agency, src["url"])
        time.sleep(SLEEP_BETWEEN)
        pairs = collect_links(src)
        log.info("  candidates: %d", len(pairs))
        for title_guess, href in pairs:
            try:
                time.sleep(SLEEP_BETWEEN)
                call = parse_call(agency, title_guess, href)
                if call: all_calls.append(call)
            except Exception as e:
                log.warning("parse_call failed %s -> %s", href, e)

    clean_calls = dedupe(all_calls)

    def sortkey(c: Dict[str,str]) -> Tuple[int,str]:
        d = c.get("deadline","N/A")
        if d=="N/A": return (1,"9999-12-31")
        return (0,d)

    clean_calls.sort(key=sortkey)
    out = {
        "updated_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "calls": clean_calls,
    }
    with open("data.json","w",encoding="utf-8") as f:
        json.dump(out,f,ensure_ascii=False,indent=2)
    log.info("Wrote %d calls to data.json", len(clean_calls))

if __name__ == "__main__":
    main()
