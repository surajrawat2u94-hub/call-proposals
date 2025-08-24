# scraper/agent.py
from __future__ import annotations
import json
import time
import yaml
from typing import Dict, List, Tuple

from .fetchers import collect_links, http_get, is_pdf_link, SLEEP_BETWEEN
from .extractors import extract_from_html, extract_from_pdf
from .normalizers import dedupe, truncate_title, ai_enrich

def get_sources() -> List[Dict[str, str]]:
    with open("scraper/sources.yaml", "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    # expected fields in each source: name/agency, url, base
    return data["sources"]

def parse_call(agency: str, title_guess: str, url: str) -> Dict[str, str]:
    """
    Parse a single call (HTML or PDF). Use AI fallback when needed.
    """
    title, details, text_for_ai = "", {"deadline":"N/A","eligibility":"N/A","budget":"N/A","area":"N/A"}, ""

    if is_pdf_link(url):
        title, details, text_for_ai = extract_from_pdf(url)
    else:
        r = http_get(url)
        if r and r.text:
            title, details, text_for_ai = extract_from_html(url, r.text)

    # AI fallback
    if any(details.get(k, "N/A") == "N/A" for k in ("deadline","eligibility","budget","area")):
        enriched = ai_enrich(text_for_ai)
        for k, v in enriched.items():
            if details.get(k, "N/A") == "N/A" and v != "N/A":
                details[k] = v

    final_title = title.strip() or title_guess.strip()
    final_title = truncate_title(final_title, 80)

    return {
        "title": final_title if final_title else "N/A",
        "deadline": details.get("deadline", "N/A"),
        "funding_agency": agency,
        "area": details.get("area", "N/A"),
        "eligibility": details.get("eligibility", "N/A"),
        "budget": details.get("budget", "N/A"),
        "website": url,
    }

def run() -> None:
    all_calls: List[Dict[str, str]] = []
    for s in get_sources():
        agency = s.get("agency") or s.get("name") or ""
        url = s["url"]
        base = s["base"]
        links = collect_links(url, base)

        for title_guess, href in links[:100]:
            try:
                call = parse_call(agency, title_guess, href)
                if len(call["title"]) >= 4:
                    all_calls.append(call)
                time.sleep(SLEEP_BETWEEN)
            except Exception:
                continue

    final = dedupe(all_calls)
    def s_key(c: Dict[str, str]):
        d = c.get("deadline", "N/A")
        return ("1", "9999-12-31") if d == "N/A" else ("0", d)
    final.sort(key=s_key)

    out = {"updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "calls": final}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    run()
