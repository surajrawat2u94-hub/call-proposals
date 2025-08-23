#!/usr/bin/env python3
"""
Scrapes research funding calls from a curated set of 66 agencies only.
Heuristics extract: deadline, eligibility, budget, area.
Deduplicates and writes data.json for the dashboard.

- No broad web crawling: only agency call/announcement pages below.
- Robust to failures: each source is best-effort, failures don’t stop the run.
- HTML-first; PDF text extraction optional (uses PyPDF2 if available).

Requires:
  requests, beautifulsoup4, lxml, python-dateutil, (optional) PyPDF2
"""

from __future__ import annotations
import json
import os
import re
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# === Optional PDF text extraction (safe fallback if library absent) ===
try:
    from PyPDF2 import PdfReader  # type: ignore
    PDF_OK = True
except Exception:
    PdfReader = None
    PDF_OK = False

# ----------------------------- HTTP defaults ---------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
SLEEP_BETWEEN = 0.8  # politeness

KEYWORDS = (
    "call", "calls", "open call", "grant", "grants", "fund", "funding",
    "proposal", "proposals", "fellowship", "opportunit", "programme", "program"
)
EXCLUDE = ("faq", "faqs", "form", "forms", "guideline", "guidelines", "procurement", "tender", "advertisement")

# --- Agencies you asked for: list of 66 curated sources (name, listing url, base) ---
SOURCES: List[Dict[str, str]] = [
    # ----------- INDIA (National) -----------
    {"agency": "DBT (Department of Biotechnology)", "url": "https://dbtindia.gov.in/latest-announcements", "base": "https://dbtindia.gov.in"},
    {"agency": "DST (Department of Science & Technology)", "url": "https://dst.gov.in/call-for-proposals", "base": "https://dst.gov.in"},
    {"agency": "ICMR (Indian Council of Medical Research)", "url": "https://main.icmr.nic.in/calls", "base": "https://main.icmr.nic.in"},
    {"agency": "CSIR (Council of Scientific & Industrial Research)", "url": "https://www.csir.res.in/grants-schemes", "base": "https://www.csir.res.in"},
    {"agency": "ANRF (formerly SERB)", "url": "https://www.serb.gov.in/home/notifications", "base": "https://www.serb.gov.in"},
    {"agency": "IGSTC (Indo-German Science & Technology Centre)", "url": "https://www.igstc.org/", "base": "https://www.igstc.org"},
    {"agency": "BIRAC", "url": "https://birac.nic.in/cfp.php", "base": "https://birac.nic.in"},
    {"agency": "IUSSTF", "url": "https://iusstf.org/program", "base": "https://iusstf.org"},
    {"agency": "GITA (Global Innovation & Tech Alliance)", "url": "https://gita.org.in/calls/", "base": "https://gita.org.in"},
    {"agency": "TDB (Technology Development Board)", "url": "https://tdb.gov.in/call-for-proposals/", "base": "https://tdb.gov.in"},
    {"agency": "MoES (Ministry of Earth Sciences)", "url": "https://moes.gov.in/announcements", "base": "https://moes.gov.in"},
    {"agency": "MoEFCC (Environment, Forest & Climate Change)", "url": "https://moef.gov.in/en/important-links/advertisements/", "base": "https://moef.gov.in"},
    {"agency": "AYUSH (Ministry of Ayush)", "url": "https://ayush.gov.in/ayush-research", "base": "https://ayush.gov.in"},
    {"agency": "MeitY (IT & Electronics)", "url": "https://www.meity.gov.in/", "base": "https://www.meity.gov.in"},
    {"agency": "DHR (Dept. of Health Research)", "url": "https://dhr.gov.in/schemes", "base": "https://dhr.gov.in"},
    {"agency": "UGC", "url": "https://www.ugc.ac.in/noticeboard/", "base": "https://www.ugc.ac.in"},
    {"agency": "ICSSR", "url": "https://www.icssr.org/announcements", "base": "https://www.icssr.org"},
    {"agency": "ICAR", "url": "https://icar.org.in/", "base": "https://icar.org.in"},
    {"agency": "DRDO", "url": "https://www.drdo.gov.in/tenders", "base": "https://www.drdo.gov.in"},
    {"agency": "INSA (Indian National Science Academy)", "url": "https://insaindia.res.in/awards.php", "base": "https://insaindia.res.in"},
    {"agency": "CSIR-HRDG", "url": "https://csirhrdg.res.in/", "base": "https://csirhrdg.res.in"},
    {"agency": "TIFAC", "url": "https://tifac.org.in/index.php/highlights", "base": "https://tifac.org.in"},
    {"agency": "NMHS (National Mission on Himalayan Studies)", "url": "http://nmhs.org.in/", "base": "http://nmhs.org.in"},
    {"agency": "UCOST (Uttarakhand Council for S&T)", "url": "https://ucost.in/", "base": "https://ucost.in"},
    {"agency": "GUJCOST (Gujarat Council on S&T)", "url": "https://dst.gujarat.gov.in/gujcost", "base": "https://dst.gujarat.gov.in"},
    {"agency": "KSCSTE (Kerala S&T)", "url": "https://kscste.kerala.gov.in/category/notifications/", "base": "https://kscste.kerala.gov.in"},
    {"agency": "TNSCST (Tamil Nadu S&T)", "url": "https://www.tnscst.nic.in/", "base": "https://www.tnscst.nic.in"},
    {"agency": "RGSTC (Maharashtra)", "url": "https://rgstc.maharashtra.gov.in/", "base": "https://rgstc.maharashtra.gov.in"},
    {"agency": "MPCOST (Madhya Pradesh)", "url": "https://mpcost.gov.in/", "base": "https://mpcost.gov.in"},
    {"agency": "UPCST (Uttar Pradesh)", "url": "http://upcst.gov.in/", "base": "http://upcst.gov.in"},
    {"agency": "DST West Bengal / WB-DST", "url": "https://www.dstwb-cst.org/", "base": "https://www.dstwb-cst.org"},
    {"agency": "Punjab State Council for S&T", "url": "https://pscst.gov.in/", "base": "https://pscst.gov.in"},
    {"agency": "Haryana State Council for S&T", "url": "https://dstharyana.gov.in/", "base": "https://dstharyana.gov.in"},
    {"agency": "HIMCOSTE (Himachal Pradesh)", "url": "http://www.himcoste.hp.gov.in/", "base": "http://www.himcoste.hp.gov.in"},
    {"agency": "JKDST/J&K S&T", "url": "https://jkdst.gov.in/", "base": "https://jkdst.gov.in"},
    {"agency": "KSCST (Karnataka State Council for S&T)", "url": "https://kscst.org.in/", "base": "https://kscst.org.in"},
    {"agency": "ASTEC (Assam S&T)", "url": "http://www.astec.assam.gov.in/", "base": "http://www.astec.assam.gov.in"},
    {"agency": "APSCST (Arunachal Pradesh S&T)", "url": "http://www.apscst.gov.in/", "base": "http://www.apscst.gov.in"},
    {"agency": "Tripura S&T", "url": "https://dst.tripura.gov.in/", "base": "https://dst.tripura.gov.in"},
    # ----------- INTERNATIONAL -----------
    {"agency": "European Research Council (ERC)", "url": "https://erc.europa.eu/news-events/news", "base": "https://erc.europa.eu"},
    {"agency": "EU Funding & Tenders Portal", "url": "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities", "base": "https://ec.europa.eu"},
    {"agency": "UKRI Funding Finder", "url": "https://www.ukri.org/opportunity/", "base": "https://www.ukri.org"},
    {"agency": "Royal Society (UK)", "url": "https://royalsociety.org/grants-schemes-awards/grants/", "base": "https://royalsociety.org"},
    {"agency": "Wellcome Trust", "url": "https://wellcome.org/grant-funding/schemes", "base": "https://wellcome.org"},
    {"agency": "Leverhulme Trust", "url": "https://www.leverhulme.ac.uk/funding", "base": "https://www.leverhulme.ac.uk"},
    {"agency": "British Academy", "url": "https://www.thebritishacademy.ac.uk/funding/", "base": "https://www.thebritishacademy.ac.uk"},
    {"agency": "MRC (UKRI)", "url": "https://www.ukri.org/opportunity/?filter_council%5B%5D=MRC", "base": "https://www.ukri.org"},
    {"agency": "BBSRC (UKRI)", "url": "https://www.ukri.org/opportunity/?filter_council%5B%5D=BBSRC", "base": "https://www.ukri.org"},
    {"agency": "EPSRC (UKRI)", "url": "https://www.ukri.org/opportunity/?filter_council%5B%5D=EPSRC", "base": "https://www.ukri.org"},
    {"agency": "NERC (UKRI)", "url": "https://www.ukri.org/opportunity/?filter_council%5B%5D=NERC", "base": "https://www.ukri.org"},
    {"agency": "Innovate UK (UKRI)", "url": "https://www.ukri.org/opportunity/?filter_council%5B%5D=Innovate%20UK", "base": "https://www.ukri.org"},
    {"agency": "NIH (USA)", "url": "https://grants.nih.gov/grants/guide/search_guide.htm", "base": "https://grants.nih.gov"},
    {"agency": "NSF (USA)", "url": "https://beta.nsf.gov/funding/opportunities", "base": "https://beta.nsf.gov"},
    {"agency": "US DOE Office of Science", "url": "https://science.osti.gov/grants", "base": "https://science.osti.gov"},
    {"agency": "NASA (NSPIRES)", "url": "https://nspires.nasaprs.com/external/solicitations/solicitations.do", "base": "https://nspires.nasaprs.com"},
    {"agency": "DARPA (USA)", "url": "https://www.darpa.mil/work-with-us/opportunities", "base": "https://www.darpa.mil"},
    {"agency": "US Office of Naval Research (ONR)", "url": "https://www.onr.navy.mil/work-with-us/funding-opportunities/announcements", "base": "https://www.onr.navy.mil"},
    {"agency": "USDA NIFA", "url": "https://nifa.usda.gov/grants", "base": "https://nifa.usda.gov"},
    {"agency": "DFG (Germany)", "url": "https://www.dfg.de/en/research_funding/announcements_proposals", "base": "https://www.dfg.de"},
    {"agency": "ANR (France)", "url": "https://anr.fr/en/call-for-proposals/", "base": "https://anr.fr"},
    {"agency": "SNSF (Switzerland)", "url": "https://www.snf.ch/en/funding/programmes", "base": "https://www.snf.ch"},
    {"agency": "NWO (Netherlands)", "url": "https://www.nwo.nl/en/funding/funding", "base": "https://www.nwo.nl"},
    {"agency": "FWO (Belgium)", "url": "https://www.fwo.be/en/funding/", "base": "https://www.fwo.be"},
    {"agency": "FWF (Austria)", "url": "https://www.fwf.ac.at/en/research-funding/fwf-programmes", "base": "https://www.fwf.ac.at"},
    {"agency": "JSPS (Japan)", "url": "https://www.jsps.go.jp/english/e-grants/grants.html", "base": "https://www.jsps.go.jp"},
    {"agency": "AMED (Japan)", "url": "https://www.amed.go.jp/koubo/index.html", "base": "https://www.amed.go.jp"},
    {"agency": "NRF (South Africa)", "url": "https://www.nrf.ac.za/funding/calls/", "base": "https://www.nrf.ac.za"},
    {"agency": "NSERC (Canada)", "url": "https://www.nserc-crsng.gc.ca/Professors-Professeurs/Grants-Subventions/index_eng.asp", "base": "https://www.nserc-crsng.gc.ca"},
    {"agency": "CIHR (Canada)", "url": "https://cihr-irsc.gc.ca/e/37788.html", "base": "https://cihr-irsc.gc.ca"},
    {"agency": "NHMRC (Australia)", "url": "https://www.nhmrc.gov.au/funding/find-funding", "base": "https://www.nhmrc.gov.au"},
    {"agency": "MBIE (New Zealand)", "url": "https://www.mbie.govt.nz/science-and-technology/science-and-innovation/funding-information-and-opportunities/", "base": "https://www.mbie.govt.nz"},
    {"agency": "SFI (Ireland)", "url": "https://www.sfi.ie/funding/", "base": "https://www.sfi.ie"},
    {"agency": "ISF (Israel)", "url": "https://www.isf.org.il/#/callus", "base": "https://www.isf.org.il"},
    {"agency": "A*STAR (Singapore)", "url": "https://www.a-star.edu.sg/Research/Funding-Opportunities", "base": "https://www.a-star.edu.sg"},
    {"agency": "Hong Kong RGC", "url": "https://www.ugc.edu.hk/eng/rgc/funding_opport/index.html", "base": "https://www.ugc.edu.hk"},
    {"agency": "TWAS", "url": "https://twas.org/opportunities", "base": "https://twas.org"},
    {"agency": "ICGEB", "url": "https://www.icgeb.org/activities/grants/", "base": "https://www.icgeb.org"},
    {"agency": "EMBO", "url": "https://www.embo.org/funding/fellowships-grants/", "base": "https://www.embo.org"},
    {"agency": "HFSP", "url": "https://www.hfsp.org/funding", "base": "https://www.hfsp.org"},
    {"agency": "EUREKA Network", "url": "https://eurekanetwork.org/open-calls/", "base": "https://eurekanetwork.org"},
    {"agency": "COST (Cooperation in Science & Technology)", "url": "https://www.cost.eu/opportunities/call-for-proposals/", "base": "https://www.cost.eu"},
    {"agency": "Cancer Research UK", "url": "https://www.cancerresearchuk.org/funding-for-researchers", "base": "https://www.cancerresearchuk.org"},
    {"agency": "Versus Arthritis (UK)", "url": "https://www.versusarthritis.org/research/for-researchers/apply-for-funding/", "base": "https://www.versusarthritis.org"},
    {"agency": "Chan Zuckerberg Initiative", "url": "https://chanzuckerberg.com/science/grants/", "base": "https://chanzuckerberg.com"},
    {"agency": "Alzheimer’s Association", "url": "https://www.alz.org/research/for_researchers/grants", "base": "https://www.alz.org"},
    {"agency": "Simons Foundation", "url": "https://www.simonsfoundation.org/grant/", "base": "https://www.simonsfoundation.org"},
]
# Count check (should be 66)
assert len(SOURCES) == 66, f"SOURCES currently {len(SOURCES)}, expected 66."

# ----------------------------- helpers --------------------------------------
def http_get(url: str) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception:
        return None

def absolute_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def is_pdf(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")

def looks_like_call_text(s: str) -> bool:
    s = s.lower()
    if any(bad in s for bad in EXCLUDE):
        return False
    return any(k in s for k in KEYWORDS)

# ----------------------------- extraction -----------------------------------
def extract_fields_from_text(text: str) -> Dict[str, str]:
    details = {
        "deadline": "N/A",
        "eligibility": "N/A",
        "budget": "N/A",
        "area": "N/A",
        "recurring": "no",
    }
    # Deadline-like
    m = re.search(r"(deadline|last date|apply by|closing date)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if m:
        val = clean(m.group(2))
        dt = try_parse_date(val)
        details["deadline"] = dt if dt else val

    # Eligibility-like
    m = re.search(r"(eligibility|who can apply)\s*[:\-–]\s*(.+?)(\n|\.|$)", text, re.I)
    if m:
        details["eligibility"] = clean(m.group(2))

    # Budget-like
    m = re.search(r"(budget|funding|amount|grant)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if m:
        details["budget"] = clean(m.group(2))
    else:
        m2 = re.search(r"(₹|INR|EUR|€|\$|GBP|£)\s?[\d,]+(\.\d+)?", text, re.I)
        if m2:
            details["budget"] = clean(m2.group(0))

    # Area guess
    l = text.lower()
    if any(x in l for x in ["medical", "biomedical", "health", "medicine"]):
        details["area"] = "Medical Research"
    elif any(x in l for x in ["biotech", "biotechnology"]):
        details["area"] = "Biotechnology"
    elif any(x in l for x in ["physics", "physical science"]):
        details["area"] = "Physical Sciences"
    elif any(x in l for x in ["chemistry", "chemical"]):
        details["area"] = "Chemical Sciences"
    elif any(x in l for x in ["engineering", "technology"]):
        details["area"] = "Science & Technology"

    if re.search(r"\b(annual|rolling|ongoing|every year)\b", l):
        details["recurring"] = "yes"

    return details

def try_parse_date(s: str) -> Optional[str]:
    try:
        s = re.sub(r"(\d)(st|nd|rd|th)", r"\1", s)
        dt = dateparser.parse(s, dayfirst=True, fuzzy=True)
        if dt:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return None

def extract_from_html(page_html: str) -> Tuple[str, Dict[str, str]]:
    soup = BeautifulSoup(page_html, "lxml")
    title = ""
    if soup.find("h1"):
        title = soup.find("h1").get_text(" ", strip=True)
    elif soup.title:
        title = soup.title.get_text(" ", strip=True)
    title = clean(title)
    full_text = clean(soup.get_text(" "))
    details = extract_fields_from_text(full_text)
    # Try tables (sometimes cleaner)
    for table in soup.find_all("table"):
        ttext = clean(table.get_text(" "))
        alt = extract_fields_from_text(ttext)
        for k, v in alt.items():
            if v != "N/A" and details.get(k) == "N/A":
                details[k] = v
    return title, details

def extract_from_pdf_bytes(b: bytes) -> Tuple[str, Dict[str, str]]:
    if not (PDF_OK and PdfReader):
        return "", {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A", "recurring": "no"}
    try:
        from io import BytesIO
        reader = PdfReader(BytesIO(b))
        text = ""
        for p in reader.pages[:10]:
            try:
                text += "\n" + (p.extract_text() or "")
            except Exception:
                pass
        text = clean(text)
        lines = [ln for ln in text.splitlines() if clean(ln)]
        title = clean(lines[0]) if lines else ""
        details = extract_fields_from_text(text)
        return title, details
    except Exception:
        return "", {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A", "recurring": "no"}

# ----------------------------- crawl & parse --------------------------------
def collect_links(listing_url: str, base: str) -> List[Tuple[str, str]]:
    r = http_get(listing_url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        txt = clean(a.get_text(" "))
        href = a["href"].strip()
        if not txt:
            continue
        if not looks_like_call_text(txt):
            continue
        url = urllib.parse.urljoin(base, href)
        key = (txt.lower(), url.split("#")[0])
        if key in seen:
            continue
        seen.add(key)
        links.append((txt, url))
    return links

def parse_call(agency: str, title_guess: str, url: str) -> Dict[str, str]:
    title = ""
    details = {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A", "recurring": "no"}

    if is_pdf(url):
        r = http_get(url)
        if r and r.content:
            t, d = extract_from_pdf_bytes(r.content)
            title = t or title_guess
            details.update(d)
    else:
        r = http_get(url)
        if r and r.text:
            t, d = extract_from_html(r.text)
            title = t or title_guess
            details.update(d)

    return {
        "title": clean(title) if title else clean(title_guess) if title_guess else "N/A",
        "deadline": details.get("deadline", "N/A"),
        "funding_agency": agency,
        "area": details.get("area", "N/A"),
        "eligibility": details.get("eligibility", "N/A"),
        "budget": details.get("budget", "N/A"),
        "website": url,
        "recurring": details.get("recurring", "no"),
        "india_related": "yes" if "india" in (agency + " " + url).lower() else "no",
    }

def dedupe(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = {}
    def score(x: Dict[str, str]) -> int:
        return sum(1 for k in ("deadline", "eligibility", "budget", "area") if x.get(k) and x.get(k) != "N/A")
    for r in rows:
        key = (r.get("title", "").lower(), r.get("funding_agency", "").lower())
        if key not in seen:
            seen[key] = r
        else:
            if score(r) > score(seen[key]):
                seen[key] = r
    return list(seen.values())

# ----------------------------- main -----------------------------------------
def main():
    all_calls: List[Dict[str, str]] = []
    for src in SOURCES:
        agency, url, base = src["agency"], src["url"], src["base"]
        time.sleep(SLEEP_BETWEEN)
        try:
            pairs = collect_links(url, base)
            for title_guess, href in pairs[:100]:
                time.sleep(SLEEP_BETWEEN)
                try:
                    row = parse_call(agency, title_guess, href)
                    if len(row["title"]) < 3:
                        continue
                    all_calls.append(row)
                except Exception:
                    continue
        except Exception:
            continue

    out = dedupe(all_calls)

    # Sort: upcoming deadlines first (unknown at end)
    def sk(r: Dict[str, str]):
        d = r.get("deadline", "N/A")
        return (1, "9999-12-31") if d == "N/A" else (0, d)

    out.sort(key=sk)
    payload = {
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "calls": out,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(out)} calls to data.json")

if __name__ == "__main__":
    main()
