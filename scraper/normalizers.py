# scraper/normalizers.py
from __future__ import annotations
import os
import json
from typing import Dict, List, Tuple

def norm_key(*parts: str) -> str:
    return "|".join(p.strip().lower() for p in parts if p)

def truncate_title(title: str, max_len: int = 80) -> str:
    t = (title or "").strip()
    return (t[: max_len - 1] + "…") if len(t) > max_len else t

def dedupe(calls: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Deduplicate by (title + agency). Prefer the record with more filled fields.
    """
    seen: Dict[str, Dict[str, str]] = {}
    for c in calls:
        key = norm_key(c.get("title", ""), c.get("funding_agency", ""))
        if key not in seen:
            seen[key] = c
        else:
            def score(x: Dict[str, str]) -> int:
                return sum(1 for k in ("deadline", "eligibility", "budget", "area") if x.get(k) and x.get(k) != "N/A")
            if score(c) > score(seen[key]):
                seen[key] = c
    return list(seen.values())

# -------------- optional AI fallback -------------- #
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AI_ENABLED = False
try:
    if OPENAI_API_KEY:
        import openai  # type: ignore
        openai.api_key = OPENAI_API_KEY
        AI_ENABLED = True
except Exception:
    AI_ENABLED = False

def ai_enrich(text: str) -> Dict[str, str]:
    if not AI_ENABLED or not text:
        return {}
    prompt = f"""
Extract the following fields from the funding-call text.
Return STRICT JSON with keys: deadline, eligibility, budget, area.
- deadline must be YYYY-MM-DD if possible, else "N/A"
- eligibility concise one-liner
- budget a short amount or "N/A"
- area: Medical Research / Biotechnology / Science & Technology / Physical Sciences / Chemical Sciences / Advanced Materials / Science & Innovation

Text:
{text[:12000]}
"""
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=300,
        )
        content = resp["choices"][0]["message"]["content"]
        j = json.loads(content[content.find("{"): content.rfind("}")+1])
        out = {}
        for k in ("deadline", "eligibility", "budget", "area"):
            v = str(j.get(k, "N/A")).strip()
            out[k] = v if v else "N/A"
        return out
    except Exception:
        return {}
