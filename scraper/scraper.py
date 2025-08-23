#!/usr/bin/env python3
"""
Scrapes funding calls ONLY from your curated agencies list.

Source list priority:
  1) sources.json  (generated from your PDF by the workflow step)
  2) data/agencies.pdf (parsed on the fly if sources.json missing)
  3) tiny built-in fallback (DST/DBT) so local runs don't crash

Extraction:
  - HTML: robust title + heuristic fields (Deadline, Eligibility, Area, Budget)
  - PDF: pdfplumber / PyPDF2 text + same heuristics
  - Optional AI fallback if OPENAI_API_KEY is set (fills missing fields)
De-duplication by (title + agency).
Writes data.json as a FLAT ARRAY (what your dashboard expects).
"""

from __future__ import annotations
import os, re, json, time, logging
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# ---------- logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger("scraper")

# ---------- HTTP ----------
HEADERS = {"User-Agent": "FundingCallsBot/1.6 (+https://github.com/)"}
TIMEOUT = 30
SLEEP_BETWEEN = 1.0

# ---------- optional PDF backends ----------
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

# ---------- optional OpenAI ----------
OPENAI_ENABLED = False
try:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if OPENAI_API_KEY:
        import openai  # type: ignore
        openai.api_key = OPENAI_API_KEY
        OPENAI_ENABLED = True
except Exception:
    OPENAI_ENABLED = False

# ---------- heuristics ----------
KEYWORDS = ("call", "grant", "fund", "funding", "proposal", "fellowship",
            "scheme", "schemes", "research", "programme", "program", "opportunit")
EXCLUDE_WORDS = ("faq", "faqs", "form", "forms", "guideline", "guidelines", "tender", "corrigendum")
INDIAN_AGENCIES_HINT = ("dst","dbt","serb","icmr","csir","igstc","anrf","ugc","meity","icar","drdo","icssr","nmhs","ucost","nabard")

# ---------- helpers ----------
def http_get(url:str) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning("GET failed %s -> %s", url, e)
        return None

def absolute_url(base:str, href:str) -> str:
    return urljoin(base, href or "")

def clean(s:str) -> str:
    return re.sub(r"\s+"," ", (s or "")).strip()

def norm_key(*parts:str) -> str:
    return "|".join(clean(p).lower() for p in parts if p)

def is_pdf(url:str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")

def india_related(agency:str, text:str) -> bool:
    t = (agency + " " + text).lower()
    return " india" in t or "indian" in t or any(k in t for k in INDIAN_AGENCIES_HINT)

def looks_like_call(text:str="", href:str="") -> bool:
    t = (text or "").lower()
    h = (href or "").lower()
    if any(x in t for x in EXCLUDE_WORDS) or any(x in h for x in EXCLUDE_WORDS):
        return False
    if any(k in t for k in KEYWORDS): return True
    if any(re.search(rf"/{k}\b", h) for k in KEYWORDS): return True
    if any(x in h for x in ("/call","/calls","/fund","/funding","/grants","/grant","/scheme","/schemes")): return True
    return False

def parse_date_any(s:str) -> Optional[str]:
    try:
        s = re.sub(r"(\d)(st|nd|rd|th)", r"\1", s)
        d = dateparser.parse(s, dayfirst=True, fuzzy=True)
        return d.strftime("%Y-%m-%d") if d else None
    except Exception:
        return None

# ---------- sources loading ----------
def load_sources_from_pdf(pdf_path:str="data/agencies.pdf") -> List[Dict[str,str]]:
    if not os.path.exists(pdf_path): return []
    text = ""
    # try pdfplumber then PyPDF2
    try:
        if "pdfplumber" in PDF_BACKENDS:
            import pdfplumber  # type: ignore
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text += "\n" + (page.extract_text() or "")
        elif "pypdf2" in PDF_BACKENDS:
            from io import BytesIO
            from PyPDF2 import PdfReader  # type: ignore
            with open(pdf_path,"rb") as f:
                data = f.read()
            reader = PdfReader(BytesIO(data))
            for p in reader.pages:
                try:
                    text += "\n" + (p.extract_text() or "")
                except Exception:
                    pass
    except Exception:
        pass
    if not text.strip(): return []
    lines = [clean(x) for x in text.splitlines()]
    url_re = re.compile(r"(https?://[^\s)]+)", re.I)
    out, seen = [], set()
    for i, ln in enumerate(lines):
        m = url_re.search(ln)
        if not m: continue
        url = m.group(1).rstrip(").,;]")
        # nearest non-empty previous line as agency
        agency = ""
        j = i-1
        while j >= 0 and not agency:
            if lines[j] and not url_re.search(lines[j]):
                agency = lines[j]
            j -= 1
        if not agency:
            try: agency = urlparse(url).netloc
            except: agency = url
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}" if urlparse(url).netloc else url
        key = (agency.lower(), base.lower())
        if key in seen: continue
        seen.add(key)
        out.append({"agency": agency, "url": url, "base": base})
    return out

def load_sources() -> List[Dict[str,str]]:
    # 1) sources.json
    if os.path.exists("sources.json"):
        try:
            with open("sources.json","r",encoding="utf-8") as f:
                data = json.load(f)
            out, seen = [], set()
            for r in data:
                a, u, b = clean(r.get("agency","")), clean(r.get("url","")), clean(r.get("base",""))
                if not (a and u and b): continue
                key = (a.lower(), b.lower())
                if key in seen: continue
                seen.add(key); out.append({"agency":a, "url":u, "base":b})
            if out: return out
        except Exception as e:
            log.warning("Failed reading sources.json: %s", e)
    # 2) parse agencies.pdf if present
    pdf_sources = load_sources_from_pdf()
    if pdf_sources:
        log.info("Loaded %d sources from data/agencies.pdf", len(pdf_sources))
        return pdf_sources
    # 3) minimal fallback
    log.warning("Using fallback sources (sources.json / PDF not found)")
    return [
        {"agency":"DST (Department of Science & Technology)","url":"https://dst.gov.in/call-for-proposals","base":"https://dst.gov.in"},
        {"agency":"DBT (Department of Biotechnology)","url":"https://dbtindia.gov.in/latest-announcements","base":"https://dbtindia.gov.in"},
    ]

SOURCES = load_sources()

# ---------- extraction ----------
def extract_fields_from_text(text:str) -> Dict[str,str]:
    details = {"deadline":"", "eligibility":"", "budgetINR":"", "area":"", "recurring":"no"}
    # deadline
    m = re.search(r"(deadline|last date|apply by|closing date)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if m:
        candidate = clean(m.group(2))
        details["deadline"] = parse_date_any(candidate) or candidate
    # eligibility
    m = re.search(r"(eligibility|who can apply)\s*[:\-–]\s*(.+?)(\n|\.|\r|$)", text, re.I)
    if m:
        details["eligibility"] = clean(m.group(2))
    else:
        m2 = re.search(r"eligibility(.{0,12})[:\-–]\s*(.+)", text, re.I)
        if m2:
            details["eligibility"] = clean(m2.group(2).split(". ")[0])
    # budget
    m = re.search(r"(budget|funding|amount|grant)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if m:
        details["budgetINR"] = clean(m.group(2))
    else:
        m2 = re.search(r"(₹|INR|EUR|€|\$|GBP|£)\s?[\d,]+(\.\d+)?", text, re.I)
        if m2:
            details["budgetINR"] = clean(m2.group(0))
    # area (coarse)
    low = text.lower()
    if any(x in low for x in ["medical","biomedical","health","medicine"]): details["area"]="Medical Research"
    elif any(x in low for x in ["biotech","biotechnology"]): details["area"]="Biotechnology"
    elif "physics" in low: details["area"]="Physical Sciences"
    elif "chemistry" in low or "chemical" in low: details["area"]="Chemical Sciences"
    elif "engineering" in low or "technology" in low: details["area"]="Science & Technology"
    elif "materials" in low: details["area"]="Advanced Materials"
    elif "innovation" in low: details["area"]="Science & Innovation"
    # recurring
    if re.search(r"\b(annual|every year|rolling|ongoing)\b", low, re.I): details["recurring"]="yes"
    return details

def extract_from_html(url:str, html:str) -> Tuple[str, Dict[str,str], str]:
    soup = BeautifulSoup(html, "lxml")
    # title
    title = ""
    if soup.find("h1"): title = soup.find("h1").get_text(strip=True)
    elif soup.find("meta", attrs={"property":"og:title"}): title = soup.find("meta", attrs={"property":"og:title"})["content"]
    elif soup.title: title = soup.title.get_text(strip=True)
    title = clean(title)
    # body text
    for s in soup(["script","style","noscript","header","footer","nav"]): s.extract()
    text = clean(soup.get_text(" "))
    details = extract_fields_from_text(text)
    # overlay from tables/definition lists
    for tbl in soup.find_all("table"):
        t = clean(tbl.get_text(" "))
        may = extract_fields_from_text(t)
        for k,v in may.items():
            if v and not details.get(k): details[k]=v
    for dl in soup.find_all("dl"):
        t = clean(dl.get_text(" "))
        may = extract_fields_from_text(t)
        for k,v in may.items():
            if v and not details.get(k): details[k]=v
    return title, details, text

def extract_from_pdf_bytes(b:bytes) -> Tuple[str, Dict[str,str], str]:
    text = ""
    if "pdfplumber" in PDF_BACKENDS:
        from io import BytesIO
        with pdfplumber.open(BytesIO(b)) as pdf:
            for page in pdf.pages[:8]:
                try: text += "\n" + (page.extract_text() or "")
                except Exception: pass
    elif "pypdf2" in PDF_BACKENDS:
        from io import BytesIO
        reader = PdfReader(BytesIO(b))  # type: ignore
        for p in reader.pages[:8]:
            try: text += "\n" + (p.extract_text() or "")
            except Exception: pass
    text = clean(text)
    lines = [ln for ln in text.splitlines() if clean(ln)]
    title = clean(lines[0]) if lines else ""
    details = extract_fields_from_text(text)
    return title, details, text

def ai_fill(text:str, details:Dict[str,str]) -> Dict[str,str]:
    if not OPENAI_ENABLED or not text.strip(): return details
    prompt = f"""
Extract compact JSON with keys: deadline (YYYY-MM-DD or empty), eligibility, budgetINR, area, recurring("yes"/"no").
If unknown, use empty string.
TEXT:
{text[:12000]}
""".strip()
    try:
        resp = openai.ChatCompletion.create(  # type: ignore
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0.0, max_tokens=300,
        )
        content = resp["choices"][0]["message"]["content"].strip()
        m = re.search(r"\{.*\}", content, re.S)
        data = json.loads(m.group(0)) if m else {}
        for k in ("deadline","eligibility","budgetINR","area","recurring"):
            v = clean(str(data.get(k,"")))
            if v and not details.get(k): details[k]=v
        # normalize date
        if details.get("deadline"):
            details["deadline"] = parse_date_any(details["deadline"]) or details["deadline"]
    except Exception as e:
        log.warning("AI fallback failed: %s", e)
    return details

# ---------- link discovery ----------
def collect_links(listing_url:str, base:str) -> List[Tuple[str,str]]:
    r = http_get(listing_url)
    if not r: return []
    soup = BeautifulSoup(r.text, "lxml")
    containers = soup.select("main a[href], article a[href], .content a[href], .view-content a[href]")
    if not containers: containers = soup.select("a[href]")

    base_host = urlparse(base).netloc or urlparse(listing_url).netloc
    results, seen = [], set()

    def add(txt, href_abs):
        key = (clean(txt).lower(), href_abs.split("#")[0])
        if key in seen: return
        seen.add(key); results.append((clean(txt) or href_abs, href_abs))

    # primary pass
    for a in containers:
        txt = clean(a.get_text(" "))
        href = a.get("href","").strip()
        href_abs = absolute_url(base, href)
        if urlparse(href_abs).netloc != base_host: continue
        if not txt and not href: continue
        if looks_like_call(txt, href_abs) or is_pdf(href_abs):
            add(txt, href_abs)

    # fallback if too few
    if len(results) < 6:
        for a in soup.select("a[href]"):
            if len(results) >= 60: break
            txt = clean(a.get_text(" "))
            href = a.get("href","").strip()
            href_abs = absolute_url(base, href)
            if urlparse(href_abs).netloc != base_host: continue
            low = (txt + " " + href_abs).lower()
            if any(x in low for x in EXCLUDE_WORDS): continue
            add(txt, href_abs)

    return results[:80]

# ---------- per-call parse ----------
def parse_call(agency:str, title_guess:str, url:str) -> Dict[str,str]:
    title, details, raw = "", {"deadline":"","eligibility":"","budgetINR":"","area":"","recurring":"no"}, ""
    if is_pdf(url):
        r = http_get(url)
        if r and r.content:
            title, details, raw = extract_from_pdf_bytes(r.content)
    else:
        r = http_get(url)
        if r and r.text:
            title, details, raw = extract_from_html(url, r.text)
    # AI fallback if still empty
    if OPENAI_ENABLED and (not details.get("deadline") or not details.get("eligibility") or not details.get("area")):
        details = ai_fill(raw, details)

    final_title = clean(title) or clean(title_guess) or "N/A"
    country = "India" if india_related(agency, raw) else "Global"

    # Map to your dashboard schema
    return {
        "title": final_title,
        "deadline": details.get("deadline") or "",
        "agency": agency,
        "area": details.get("area") or "",
        "eligibility": details.get("eligibility") or "",
        "budgetINR": details.get("budgetINR") or "",
        "url": url,
        "category": "",               # optional; keep empty or set "National/International"
        "researchCategory": "Research Proposal",
        "extendedDeadline": "",
        "country": country,
        "isRecurring": (details.get("recurring","no").lower()=="yes"),
    }

def dedupe(rows:List[Dict[str,str]]) -> List[Dict[str,str]]:
    best: Dict[str,Dict[str,str]] = {}
    def score(r): return sum(1 for k in ("deadline","eligibility","area","budgetINR") if r.get(k))
    for r in rows:
        k = norm_key(r.get("title",""), r.get("agency",""))
        if k not in best or score(r) > score(best[k]):
            best[k] = r
    return list(best.values())

# ---------- main ----------
def main():
    all_rows: List[Dict[str,str]] = []
    for src in SOURCES:
        agency, url, base = src["agency"], src["url"], src["base"]
        log.info("Listing: %s (%s)", agency, url)
        time.sleep(SLEEP_BETWEEN)
        pairs = collect_links(url, base)
        log.info("  found %d candidate links", len(pairs))
        for title_guess, href in pairs:
            try:
                time.sleep(SLEEP_BETWEEN)
                row = parse_call(agency, title_guess, href)
                if len(row["title"]) < 4: continue
                all_rows.append(row)
            except Exception as e:
                log.warning("parse_call failed %s -> %s", href, e)

    clean_rows = dedupe(all_rows)
    # sort by deadline asc; empties at bottom
    def sortkey(r):
        d = r.get("deadline") or "9999-12-31"
        return (0, d) if r.get("deadline") else (1, d)
    clean_rows.sort(key=sortkey)

    # IMPORTANT: write a FLAT ARRAY (your dashboard expects this)
    with open("data.json","w",encoding="utf-8") as f:
        json.dump(clean_rows, f, ensure_ascii=False, indent=2)
    log.info("Wrote %d calls -> data.json", len(clean_rows))

if __name__ == "__main__":
    main()
