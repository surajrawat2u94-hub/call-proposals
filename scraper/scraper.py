#!/usr/bin/env python3
"""
AI-assisted funding calls scraper.

Order of extraction for each call URL:
1) Structured HTML: tables (th/td), definition lists (dt/dd), <time>, <meta>, JSON-LD
2) Heuristics over full page text
3) PDF (if linked) with the same steps
4) Optional AI extraction (if OPENAI_API_KEY is set) to fill deadline/eligibility/area/budget

Finally, dedupe by (title + agency), prefer non-empty fields.

Run locally:
    export OPENAI_API_KEY="sk-..."   # optional
    python scraper/scraper.py
"""

import json
import os
import re
import sys
from io import BytesIO
from typing import Dict, List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from pdfminer.high_level import extract_text as pdf_extract_text

# ===== Optional AI client =====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
_AI_CLIENT = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        _AI_CLIENT = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as _e:
        _AI_CLIENT = None

# ===== Paths/Config =====
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUTPUT_FILE = os.path.join(REPO_ROOT, "data.json")
TIMEOUT = 50
PDF_MAX_BYTES = 16 * 1024 * 1024
MAX_AI_CHARS = 15000  # safety cap

HEADERS = {
    "User-Agent": "FundingCallsBot/1.4 (+https://github.com/)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Detection keywords / blocks
KEYWORDS = ["call", "proposal", "funding", "fellowship", "grant", "opportunity"]
BLOCK = ["faq", "faqs", "form", "application form", "format", "tender", "corrigendum"]

# Date regex candidates
DATE_RE = [
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
    r"\d{4}-\d{2}-\d{2}",
    r"\d{1,2}\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}",
]

# A few agencies as seed sources; add more as desired
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
            "https://www.igstc.org/funding-opportunities",
            "https://www.igstc.org/",
        ],
    },
    # Global examples
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
        "start_urls": [
            "https://grants.nih.gov/funding/searchguide/nih-guide-to-grants-and-contracts.cfm"
        ],
    },
]

# Known recurring calls
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

# -------- Helpers --------
def http_get(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")

def full_url(base: str, href: str) -> str:
    return urljoin(base, href or "")

def is_pdf_url(u: str) -> bool:
    return (u or "").lower().split("?")[0].endswith(".pdf")

def page_text(soup: BeautifulSoup) -> str:
    for s in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        s.extract()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

def pick_title(soup: BeautifulSoup, fallback: str) -> str:
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return (fallback or "").strip()

def parse_date_any(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    try:
        d = dateparser.parse(s, dayfirst=False, fuzzy=True)
        return d.strftime("%Y-%m-%d")
    except Exception:
        return ""

def extract_pdf_text(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        r.raise_for_status()
        b = r.content
        if len(b) > PDF_MAX_BYTES:
            return ""
        return pdf_extract_text(BytesIO(b)) or ""
    except Exception:
        return ""

def clean_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def record_key(r: Dict) -> str:
    return clean_key(f"{r.get('title','')}|{r.get('agency','')}")

def standardize(r: Dict) -> Dict:
    def t(x): return (x or "").strip()
    return {
        "title": t(r.get("title")),
        "deadline": parse_date_any(r.get("deadline") or ""),
        "agency": t(r.get("agency")),
        "area": t(r.get("area")),
        "eligibility": t(r.get("eligibility")),
        "budgetINR": t(r.get("budgetINR")),
        "url": t(r.get("url")),
        "category": t(r.get("category")),
        "researchCategory": t(r.get("researchCategory")),
        "extendedDeadline": parse_date_any(r.get("extendedDeadline") or ""),
        "country": t(r.get("country") or "Global"),
        "isRecurring": bool(r.get("isRecurring", False)),
    }

def merge(a: Dict, b: Dict) -> Dict:
    out = a.copy()
    for k, v in b.items():
        if k == "isRecurring":
            out[k] = out.get(k) or bool(v)
            continue
        if (not out.get(k)) and v:
            out[k] = v
        if k == "deadline" and (not out.get("deadline")) and v:
            out["deadline"] = v
    if not out.get("url") and b.get("url"):
        out["url"] = b["url"]
    return out

def dedupe_merge(rows: List[Dict]) -> List[Dict]:
    m = {}
    for r in rows:
        r = standardize(r)
        k = record_key(r)
        if k in m:
            m[k] = merge(m[k], r)
        else:
            m[k] = r
    return list(m.values())

# -------- Structured HTML extraction --------
def grab_from_meta_jsonld(soup: BeautifulSoup) -> Dict:
    out = {}
    # <time> tags
    t = soup.find("time")
    if t and (t.get("datetime") or t.get_text(strip=True)):
        out["deadline"] = parse_date_any(t.get("datetime") or t.get_text(strip=True))

    # <meta>
    md = soup.find("meta", attrs={"name": "deadline"}) or soup.find(
        "meta", attrs={"property": "deadline"}
    )
    if md and md.get("content"):
        out["deadline"] = parse_date_any(md.get("content"))

    # JSON-LD
    for ld in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(ld.string)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict):
                continue
            for k in ["deadline", "endDate", "validThrough", "dateDue", "applicationDeadline"]:
                if it.get(k):
                    out["deadline"] = parse_date_any(it[k])
            for k in ["name", "headline"]:
                if not out.get("title") and it.get(k):
                    out["title"] = it[k]
    return out

def kv_from_table_like(soup: BeautifulSoup) -> Dict:
    out = {}
    # tables
    for tbl in soup.find_all("table"):
        for tr in tbl.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            key = cells[0].lower()
            val = " ".join(cells[1:]).strip()
            if not val:
                continue
            if "deadline" in key or "last date" in key or "closing" in key:
                out.setdefault("deadline", parse_date_any(val))
            elif "eligibil" in key:
                out.setdefault("eligibility", val[:600])
            elif "budget" in key or "funding" in key or "grant amount" in key:
                out.setdefault("budgetINR", val[:250])
            elif "area" in key or "research area" in key or "thematic" in key:
                out.setdefault("area", val[:200])
    # definition lists
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            k = dt.get_text(" ", strip=True).lower()
            v = dd.get_text(" ", strip=True)
            if not v:
                continue
            if "deadline" in k or "last date" in k or "closing" in k:
                out.setdefault("deadline", parse_date_any(v))
            elif "eligibil" in k:
                out.setdefault("eligibility", v[:600])
            elif "budget" in k or "funding" in k:
                out.setdefault("budgetINR", v[:250])
            elif "area" in k or "research area" in k or "thematic" in k:
                out.setdefault("area", v[:200])
    return out

# -------- Heuristic extraction --------
def window_heuristics(txt: str) -> Dict:
    out = {}
    # deadline
    for pat in DATE_RE:
        m = re.search(
            r"(deadline|last date|closing date|submission(?:\s+deadline)?)"
            r".{0,80}?(" + pat + ")",
            txt,
            flags=re.I | re.S,
        )
        if m:
            out["deadline"] = parse_date_any(m.group(2))
            break
    # eligibility
    m = re.search(
        r"(Eligibility)(?:\s*[:\-]|\s*\n)\s*(.+?)(?:\n[A-Z][^\n]{2,}|Budget|Funding|Area|Scope|Duration|How to apply)",
        txt,
        flags=re.I | re.S,
    )
    if m:
        out["eligibility"] = re.sub(r"\s+", " ", m.group(2)).strip()[:600]
    # budget
    m = re.search(
        r"(Budget|Funding(?:\s+limit)?|Grant(?:\s+amount)?)"
        r"(?:\s*[:\-]|\s*\n)\s*(.+?)(?:\n[A-Z][^\n]{2,}|Eligibility|Area|Scope|Duration|How to apply)",
        txt,
        flags=re.I | re.S,
    )
    if m:
        out["budgetINR"] = re.sub(r"\s+", " ", m.group(2)).strip()[:250]
    # area
    m = re.search(
        r"(Area|Research Area|Thematic Area)"
        r"(?:\s*[:\-]|\s*\n)\s*(.+?)(?:\n[A-Z][^\n]{2,}|Eligibility|Budget|Funding|Scope|Duration)",
        txt,
        flags=re.I | re.S,
    )
    if m:
        out["area"] = re.sub(r"\s+", " ", m.group(2)).strip()[:200]
    return out

# -------- AI extraction (optional) --------
def ai_extract(text_blob: str) -> Dict:
    """Optional AI extractor. Only used if OPENAI_API_KEY is present."""
    if not _AI_CLIENT or not text_blob.strip():
        return {}
    prompt = (
        "You are given raw text from a funding call webpage or PDF. "
        "Extract the following fields in JSON with keys: "
        "title, deadline (YYYY-MM-DD), eligibility, budgetINR, area. "
        "If unknown, return empty string for a field. Keep it concise.\n\n"
        f"TEXT:\n{text_blob[:MAX_AI_CHARS]}\n"
    )
    try:
        resp = _AI_CLIENT.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a precise information extractor."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        content = resp.choices[0].message.content.strip()
        j = re.search(r"\{.*\}", content, flags=re.S)
        data = json.loads(j.group(0)) if j else json.loads(content)
        out = {}
        for k in ["title", "deadline", "eligibility", "budgetINR", "area"]:
            v = data.get(k, "") if isinstance(data, dict) else ""
            out[k] = v if isinstance(v, str) else ""
        out["deadline"] = parse_date_any(out.get("deadline", ""))
        return out
    except Exception:
        return {}

# -------- Per-page/pd extraction --------
def extract_from_page(url: str) -> Dict:
    soup = http_get(url)
    txt = page_text(soup)

    base = {"url": url}

    # 1) Structured
    s1 = grab_from_meta_jsonld(soup)
    s2 = kv_from_table_like(soup)
    merged = merge(base, s1)
    merged = merge(merged, s2)

    # Title
    if not merged.get("title"):
        merged["title"] = pick_title(soup, "")

    # 2) Heuristic on page text
    h = window_heuristics(txt)
    merged = merge(merged, h)

    # 3) PDF (first candidate)
    pdf = ""
    for a in soup.select("a[href]"):
        u = urljoin(url, a.get("href"))
        if is_pdf_url(u):
            pdf = u
            break
    if pdf:
        ptxt = extract_pdf_text(pdf)
        ph = window_heuristics(ptxt)
        # Try AI on PDF text first
        if OPENAI_API_KEY:
            pai = ai_extract(ptxt)
            ph = merge(ph, pai)
        merged = merge(merged, ph)

    # 4) Optional AI on page text if still missing key fields
    need_ai = (not merged.get("deadline")) or (not merged.get("eligibility") and not merged.get("budgetINR"))
    if OPENAI_API_KEY and need_ai:
        ai = ai_extract(txt)
        merged = merge(merged, ai)

    return merged

# -------- Discovery --------
def discover_calls_for_agency(agency: Dict) -> List[Dict]:
    out = []
    for start in agency["start_urls"]:
        try:
            soup = http_get(start)
        except Exception as e:
            print(f"[!] {start}: {e}", file=sys.stderr)
            continue
        for a in soup.select("a[href]"):
            title = a.get_text(" ", strip=True)
            href = a.get("href")
            if not title or not href:
                continue
            u = urljoin(start, href)
            t = title.lower()
            if any(b in t for b in BLOCK):  # skip FAQs/forms/etc.
                continue
            if not (any(k in t for k in KEYWORDS) or is_pdf_url(u)):
                continue
            # keep domain
            if urlparse(u).netloc != urlparse(agency["base"]).netloc:
                continue
            out.append({"title": title, "url": u})
    # URL-level dedupe
    seen = set()
    final = []
    for r in out:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        final.append(r)
    return final[:80]

def records_for_agency(ag: Dict) -> List[Dict]:
    results = []
    calls = discover_calls_for_agency(ag)
    for itm in calls:
        try:
            rec = extract_from_page(itm["url"])
            # context
            rec["agency"] = ag["name"]
            rec["category"] = ag["category"]
            rec["researchCategory"] = ag["research"]
            rec["country"] = ag["country"]
            rec["isRecurring"] = False
            if not rec.get("title"):
                rec["title"] = itm["title"]
            # secondary filter against FAQ-ish titles
            tl = (rec["title"] or "").lower()
            if any(b in tl for b in BLOCK):
                continue
            print(f"  [+] {ag['name']}: {rec['title'][:90]}")
            results.append(standardize(rec))
        except Exception as e:
            print(f"  [!] {itm['url']}: {e}", file=sys.stderr)
    return results

def load_existing() -> List[Dict]:
    if not os.path.exists(OUTPUT_FILE):
        return []
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []

def main():
    rows = []
    rows.extend([standardize(r) for r in RECURRING])

    for ag in AGENCIES:
        print(f"[Agency] {ag['name']}")
        rows.extend(records_for_agency(ag))

    existing = load_existing()
    merged = dedupe_merge(existing + rows)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"[✓] saved {len(merged)} calls → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
