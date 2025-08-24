from __future__ import annotations
import re, urllib.parse
from typing import Dict, List, Tuple
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CallBot/1.0)"}
TIMEOUT = 30

def urljoin(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)

def is_pdf(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")

def http_get(url: str) -> requests.Response | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception:
        return None

def fetch_listing_links(source: Dict) -> List[Tuple[str, str]]:
    """Collect links that look like calls from a listing page."""
    r = http_get(source["listing"])
    if not r: return []
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    seen = set()
    keywords = ("call","grant","proposal","fund","scheme","fellowship","research")
    exclude  = ("faq","faqs","form","forms","result")

    for a in soup.find_all("a", href=True):
        txt = re.sub(r"\s+", " ", a.get_text(" ")).strip()
        if not txt: continue
        low = txt.lower()
        if any(k in low for k in exclude): 
            continue
        if not any(k in low for k in keywords):
            continue
        href = urljoin(source["base"], a["href"])
        key = (txt, href.split("#")[0])
        if key in seen: 
            continue
        seen.add(key)
        out.append((txt, href))
    return out

def fetch_call_page(url: str, source: Dict) -> Dict:
    """Return {'text': text_content} for HTML or PDF."""
    if is_pdf(url):
        text = fetch_pdf_text(url)
        return {"text": text}
    r = http_get(url)
    if not r:
        return {"text": ""}
    # If page is tiny, optionally render JS with Playwright (per-source opt-in)
    if len(r.text) < 400 and source.get("render", "").lower() == "playwright":
        text = render_js(url)
    else:
        text = r.text
    return {"text": text}

def fetch_pdf_text(url: str) -> str:
    import pdfplumber
    from io import BytesIO
    r = http_get(url)
    if not r or not r.content:
        return ""
    text = ""
    try:
        with pdfplumber.open(BytesIO(r.content)) as pdf:
            for page in pdf.pages[:10]:
                text += "\n" + (page.extract_text() or "")
    except Exception:
        pass
    return re.sub(r"\s+", " ", text).strip()

def render_js(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page()
            page.goto(url, timeout=45000, wait_until="load")
            html = page.content()
            b.close()
        return html
    except Exception:
        return ""
