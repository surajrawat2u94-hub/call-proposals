# scraper/extractors.py
from __future__ import annotations
import io
import re
from typing import Dict, Tuple, Optional

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from .fetchers import http_get, is_pdf_link

# Try to import both PDF backends
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

def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

# ---------------- regex + heuristics ---------------- #

def try_parse_date(s: str) -> Optional[str]:
    try:
        s = re.sub(r"(\d)(st|nd|rd|th)", r"\1", s)
        dt = dateparser.parse(s, fuzzy=True, dayfirst=True)
        if dt:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        return None
    return None

def detect_area(text: str) -> Optional[str]:
    t = text.lower()
    if any(x in t for x in ("medical", "health", "biomedical", "medicine")):
        return "Medical Research"
    if any(x in t for x in ("biotech", "biotechnology")):
        return "Biotechnology"
    if any(x in t for x in ("physics", "physical science")):
        return "Physical Sciences"
    if any(x in t for x in ("chemistry", "chemical")):
        return "Chemical Sciences"
    if any(x in t for x in ("engineering", "technology")):
        return "Science & Technology"
    if "advanced materials" in t or "materials " in t:
        return "Advanced Materials"
    if "innovation" in t:
        return "Science & Innovation"
    return None

def extract_fields_from_text(text: str) -> Dict[str, str]:
    details = {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A"}

    # DEADLINE
    m = re.search(r"(Deadline|Last Date|Apply by)[:\-\s]+([^\n\r;,.]+)", text, re.I)
    if not m:
        m = re.search(r"(closing date|submission deadline)[:\-\s]+([^\n\r;,.]+)", text, re.I)
    if m:
        candidate = clean_text(m.group(2))
        details["deadline"] = try_parse_date(candidate) or candidate

    # ELIGIBILITY
    m = re.search(r"(Eligibility|Who can apply)[:\-\s]+(.+?)(?:[\.\n\r]|$)", text, re.I)
    if m:
        details["eligibility"] = clean_text(m.group(2))

    # BUDGET
    m = re.search(r"(Funding|Budget|Grant)[:\-\s]+([^\n\r;,.]+)", text, re.I)
    if m:
        details["budget"] = clean_text(m.group(2))
    else:
        m2 = re.search(r"(₹|INR|USD|EUR|GBP)[\s]?[0-9][\d,\.]*", text)
        if m2:
            details["budget"] = clean_text(m2.group(0))

    # AREA
    area = detect_area(text)
    if area:
        details["area"] = area

    return details

# ---------------- HTML / PDF extraction ---------------- #

def extract_from_html(url: str, html: str) -> Tuple[str, Dict[str, str], str]:
    soup = BeautifulSoup(html, "lxml")
    title = ""
    if soup.find("h1"):
        title = clean_text(soup.find("h1").get_text(" "))
    elif soup.title:
        title = clean_text(soup.title.get_text(" "))

    full_text = clean_text(soup.get_text(" "))
    details = extract_fields_from_text(full_text)

    # also look inside tables (sometimes labeled)
    for table in soup.find_all("table"):
        ttext = clean_text(table.get_text(" "))
        maybe = extract_fields_from_text(ttext)
        for k, v in maybe.items():
            if details.get(k, "N/A") == "N/A" and v != "N/A":
                details[k] = v

    return title, details, full_text

def extract_from_pdf(url: str) -> Tuple[str, Dict[str, str], str]:
    r = http_get(url)
    if not r or not r.content:
        return "", {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A"}, ""

    text = ""
    if "pdfplumber" in PDF_BACKENDS:
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            for page in pdf.pages[:8]:
                text += "\n" + (page.extract_text() or "")
    elif "pypdf2" in PDF_BACKENDS:
        reader = PdfReader(io.BytesIO(r.content))
        for page in reader.pages[:8]:
            try:
                text += "\n" + (page.extract_text() or "")
            except Exception:
                pass

    text = clean_text(text)
    lines = [ln for ln in text.splitlines() if clean_text(ln)]
    title = clean_text(lines[0]) if lines else ""
    details = extract_fields_from_text(text)
    return title, details, text
