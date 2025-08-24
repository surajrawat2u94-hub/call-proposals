from __future__ import annotations
import os
import re
import time
import urllib.parse
from typing import List, Tuple, Dict, Optional

import requests
from bs4 import BeautifulSoup
import yaml

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}
TIMEOUT = 30
SLEEP_BETWEEN = 1.0  # polite crawling

KEYWORDS = (
    "call", "proposal", "fund", "funding", "grant",
    "fellowship", "scheme", "schemes", "programme", "program"
)
EXCLUDE = ("faq", "faqs", "form", "forms", "guideline", "guidelines")


def http_get(url: str) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"[get] fail {url} -> {e}")
        return None


def absolute_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)


def looks_like_call(text: str) -> bool:
    t = (text or "").lower()
    if any(x in t for x in EXCLUDE):
        return False
    return any(k in t for k in KEYWORDS)


def collect_links(listing_url: str, base: str) -> List[Tuple[str, str]]:
    """
    From a listing page, return (title, href_abs) candidates.
    """
    r = http_get(listing_url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out: List[Tuple[str, str]] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        txt = " ".join((a.get_text(" ") or "").split())
        href = a["href"].strip()
        if not txt:
            continue
        if not looks_like_call(txt):
            continue
        href_abs = absolute_url(base, href)
        key = (txt.lower(), href_abs.split("#")[0])
        if key in seen:
            continue
        seen.add(key)
        out.append((txt, href_abs))
    return out


def _fallback_sources() -> List[Dict[str, str]]:
    # Small seed list – extend via sources.yaml
    return [
        # India (examples)
        {"agency": "DST (Department of Science & Technology)",
         "url": "https://dst.gov.in/call-for-proposals",
         "base": "https://dst.gov.in"},
        {"agency": "DBT (Department of Biotechnology)",
         "url": "https://dbtindia.gov.in/latest-announcements",
         "base": "https://dbtindia.gov.in"},
        {"agency": "ICMR (Indian Council of Medical Research)",
         "url": "https://main.icmr.nic.in/calls",
         "base": "https://main.icmr.nic.in"},
        # International (examples)
        {"agency": "European Research Council",
         "url": "https://erc.europa.eu/news-events/news",
         "base": "https://erc.europa.eu"},
        {"agency": "Wellcome Trust (UK)",
         "url": "https://wellcome.org/grant-funding/schemes",
         "base": "https://wellcome.org"},
    ]


def load_sources() -> List[Dict[str, str]]:
    """
    Load sources from scraper/sources.yaml if present,
    otherwise return a built-in minimal list.
    """
    cand = os.path.join(os.path.dirname(__file__), "sources.yaml")
    if os.path.exists(cand):
        with open(cand, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
            if isinstance(data, list) and data:
                return data
    return _fallback_sources()
