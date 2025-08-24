from __future__ import annotations
import re
from typing import Dict, Tuple, Optional

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from .fetchers import http_get

# optional PDF backends
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


def is_pdf_link(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def try_parse_date(s: str) -> Optional[str]:
    try:
        s = re.sub(r"(\d)(st|nd|rd|th)", r"\1", s)
        dt = dateparser.parse(s, dayfirst=True, fuzzy=True)
        return dt.strftime("%Y-%m-%d") if dt else None
    except Exception:
        return None


def detect_area(text: str) -> Optional[str]:
    t = text.lower()
    if any(x in t for x in ["medical", "biomedical", "health"]):
        return "Medical Research"
    if "biotech" in t or "biotechnology" in t:
        return "Biotechnology"
    if "materials" in t:
        return "Advanced Materials"
    if "engineering" in t or "technology" in t:
        return "Science & Technology"
    if "innovation" in t:
        return "Science & Innovation"
    if "physics" in t:
        return "Physical Sciences"
    return None


def extract_fields_from_text(text: str) -> Dict[str, str]:
    out = {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A"}

    # deadline
    m = re.search(r"(deadline|last date|apply by|closing date)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if m:
        cand = clean(m.group(2))
        d = try_parse_date(cand)
        out["deadline"] = d or cand

    # eligibility
    m = re.search(r"(eligibility|who can apply)\s*[:\-–]\s*(.+?)(?:\.|$|\n|\r)", text, re.I)
    if m:
        out["eligibility"] = clean(m.group(2))

    # budget (simple)
    m = re.search(r"(₹|INR|EUR|€|\$|GBP|£)\s?[\d,]+(\.\d+)?", text, re.I)
    if m:
        out["budget"] = clean(m.group(0))

    area = detect_area(text)
    if area:
        out["area"] = area

    return out


def parse_html(url: str, html: str) -> Tuple[str, Dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    title = ""
    if soup.find("h1"):
        title = soup.find("h1").get_text(strip=True)
    elif soup.title:
        title = soup.title.get_text(strip=True)
    title = clean(title)

    text = clean(soup.get_text(" "))
    details = extract_fields_from_text(text)

    # Try table tweaks
    for table in soup.find_all("table"):
        more = extract_fields_from_text(clean(table.get_text(" ")))
        for k, v in more.items():
            if details.get(k, "N/A") == "N/A" and v != "N/A":
                details[k] = v

    return title, details


def parse_pdf_bytes(b: bytes) -> Tuple[str, Dict[str, str]]:
    text = ""
    if "pdfplumber" in PDF_BACKENDS:
        import io
        with pdfplumber.open(io.BytesIO(b)) as pdf:  # type: ignore
            for page in pdf.pages[:8]:
                text += "\n" + (page.extract_text() or "")
    elif "pypdf2" in PDF_BACKENDS:
        from io import BytesIO
        reader = PdfReader(BytesIO(b))  # type: ignore
        for page in reader.pages[:8]:
            try:
                text += "\n" + (page.extract_text() or "")
            except Exception:
                pass
    else:
        return "", {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A"}

    text = clean(text)
    title = text.split("\n", 1)[0].strip() if text else ""
    details = extract_fields_from_text(text)
    return title, details


def parse_call(agency: str, title_guess: str, url: str) -> Dict[str, str]:
    title, details = "", {"deadline": "N/A", "eligibility": "N/A", "budget": "N/A", "area": "N/A"}

    if is_pdf_link(url):
        r = http_get(url)
        if r and r.content:
            title, details = parse_pdf_bytes(r.content)
    else:
        r = http_get(url)
        if r and r.text:
            title, details = parse_html(url, r.text)

    final_title = clean(title) or clean(title_guess)
    # truncate overly long titles for UI readability
    if len(final_title) > 100:
        final_title = final_title[:97] + "..."

    return {
        "title": final_title or "N/A",
        "deadline": details.get("deadline", "N/A"),
        "funding_agency": agency,
        "area": details.get("area", "N/A"),
        "eligibility": details.get("eligibility", "N/A"),
        "budget": details.get("budget", "N/A"),
        "website": url,
    }
