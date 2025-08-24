from __future__ import annotations
import os, re, json
from typing import Dict, Optional
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
USE_LLM = bool(OPENAI_API_KEY)

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def _try_date(s: str) -> Optional[str]:
    try:
        s = re.sub(r"(\d)(st|nd|rd|th)", r"\1", s)
        dt = dateparser.parse(s, dayfirst=True, fuzzy=True)
        if dt:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        return None
    return None

def _detect_area(text: str) -> Optional[str]:
    t = text.lower()
    if any(x in t for x in ["medical","health","biomedical","medicine"]): return "Medical Research"
    if "biotech" in t or "biotechnology" in t: return "Biotechnology"
    if "materials" in t: return "Advanced Materials"
    if "physics" in t or "physical science" in t: return "Physical Sciences"
    if "chemistry" in t: return "Chemical Sciences"
    if "engineering" in t or "technology" in t: return "Science & Technology"
    if "innovation" in t: return "Science & Innovation"
    return None

def _extract_heuristic(title_guess: str, page_text: str) -> Dict:
    text = page_text
    if "<html" in page_text.lower() or "<body" in page_text.lower():
        soup = BeautifulSoup(page_text, "lxml")
        html_title = soup.find("h1")
        title = _clean(html_title.get_text(" ")) if html_title else _clean(soup.title.get_text(" ")) if soup.title else ""
        text = _clean(soup.get_text(" "))
    else:
        title = ""
    title = title or _clean(title_guess)

    deadline = "N/A"
    m = re.search(r"(deadline|last date|apply by|closing date)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if m:
        candidate = _clean(m.group(2))
        deadline = _try_date(candidate) or candidate

    eligibility = "N/A"
    m = re.search(r"(eligibility|who can apply)\s*[:\-–]\s*(.+?)(\. |$|\n|\r)", text, re.I)
    if m:
        eligibility = _clean(m.group(2))

    budget = "N/A"
    m = re.search(r"(budget|funding|amount|grant)\s*[:\-–]\s*([^\n\r;,.]+)", text, re.I)
    if m:
        budget = _clean(m.group(2))
    else:
        m = re.search(r"(₹|INR|EUR|€|\$|GBP|£)\s?[\d,]+(\.\d+)?", text, re.I)
        if m: budget = _clean(m.group(0))

    area = _detect_area(text) or "N/A"
    return {
        "title": title or "N/A",
        "deadline": deadline,
        "eligibility": eligibility,
        "budget": budget,
        "area": area,
        "recurring": "yes" if re.search(r"\b(annual|rolling|ongoing)\b", text, re.I) else "no",
        "raw_text": text[:12000],
    }

def _llm_fill(missing: Dict) -> Dict:
    if not USE_LLM: 
        return {}
    import openai
    openai.api_key = OPENAI_API_KEY
    schema = (
        "Return JSON with keys: deadline, eligibility, budget, area, recurring. "
        "Use ISO YYYY-MM-DD for deadline if possible else 'N/A'. "
        "recurring must be 'yes' or 'no'."
    )
    prompt = f"{schema}\n\nTEXT:\n{missing['raw_text']}"
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=300,
        )
        content = resp["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.S)
        if not m: return {}
        data = json.loads(m.group(0))
        out = {}
        for k in ("deadline","eligibility","budget","area","recurring"):
            v = _clean(str(data.get(k, "N/A")))
            out[k] = v if v else "N/A"
        return out
    except Exception:
        return {}

def extract_structured_fields(title_guess: str, page_text: str, url: str, agency: str, country: str) -> Dict:
    base = _extract_heuristic(title_guess, page_text)
    if USE_LLM and any(base[k] == "N/A" for k in ("deadline","eligibility","budget","area")):
        fill = _llm_fill(base)
        for k, v in fill.items():
            if base.get(k, "N/A") == "N/A" and v != "N/A":
                base[k] = v
    return {
        "title": base["title"],
        "deadline": base["deadline"],
        "funding_agency": agency,
        "area": base["area"],
        "eligibility": base["eligibility"],
        "budget": base["budget"],
        "website": url,
        "recurring": base.get("recurring","no"),
        "country": country,
    }
