# scraper/fetchers.py
from __future__ import annotations
import re
import time
import urllib.parse
from typing import List, Tuple, Optional

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
SLEEP_BETWEEN = 0.8

KEYWORDS = (
    "call", "grant", "fund", "funding", "proposal",
    "fellowship", "scheme", "schemes", "research", "programme", "program"
)
EXCLUDE = ("faq", "faqs", "form", "forms", "guideline", "guidelines")

def http_get(url: str) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception:
        return None

def absolute_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)

def looks_like_call_text(s: str) -> bool:
    s = (s or "").lower()
    if any(x in s for x in EXCLUDE):
        return False
    return any(k in s for k in KEYWORDS)

def collect_links(listing_url: str, base: str) -> List[Tuple[str, str]]:
    """
    From a listing page, collect (title, url) pairs that look like calls.
    """
    r = http_get(listing_url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    links, seen = [], set()

    for a in soup.find_all("a", href=True):
        txt = " ".join((a.get_text(" ") or "").split())
        href_abs = absolute_url(base, a["href"])
        key = (txt.lower(), href_abs.split("#")[0])
        if key in seen or not txt:
            continue
        if looks_like_call_text(txt):
            seen.add(key)
            links.append((txt, href_abs))
    time.sleep(SLEEP_BETWEEN)
    return links

def is_pdf_link(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")
