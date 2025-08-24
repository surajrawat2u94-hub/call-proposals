from __future__ import annotations
import re
from typing import Dict, List, Tuple

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def is_india_related(call: Dict) -> bool:
    t = (call.get("funding_agency","") + " " + call.get("area","")).lower()
    return call.get("country") == "IN" or ("india" in t or "indian" in t)

def validate_call(c: Dict) -> bool:
    return len(_norm(c.get("title",""))) >= 4 and c.get("website","").startswith("http")

def _score(c: Dict) -> int:
    return sum(1 for k in ("deadline","eligibility","budget","area") if c.get(k) and c[k] != "N/A")

def dedupe_and_sort(calls: List[Dict]) -> List[Dict]:
    seen = {}
    for c in calls:
        key = _norm(c.get("title","")) + "|" + _norm(c.get("funding_agency",""))
        if key not in seen or _score(c) > _score(seen[key]):
            seen[key] = c

    out = list(seen.values())

    def sortkey(x: Dict) -> Tuple[int, str, str]:
        d = x.get("deadline","N/A")
        return (0 if d != "N/A" else 1, d, _norm(x.get("title","")))
    out.sort(key=sortkey)
    return out
