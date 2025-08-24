from __future__ import annotations
import json
import time
from typing import Dict, List, Tuple


def _key(c: Dict[str, str]) -> str:
    t = (c.get("title") or "").strip().lower()
    a = (c.get("funding_agency") or "").strip().lower()
    return f"{t}|{a}"


def _score(c: Dict[str, str]) -> int:
    return sum(1 for k in ("deadline", "eligibility", "budget", "area") if c.get(k) and c.get(k) != "N/A")


def dedupe(calls: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: Dict[str, Dict[str, str]] = {}
    for c in calls:
        k = _key(c)
        if k not in seen or _score(c) > _score(seen[k]):
            seen[k] = c

    items = list(seen.values())

    def sortkey(x: Dict[str, str]) -> Tuple[int, str]:
        d = x.get("deadline", "N/A")
        if d == "N/A":
            return (1, "9999-12-31")
        return (0, d)

    items.sort(key=sortkey)
    return items


def save_data(calls: List[Dict[str, str]]) -> None:
    out = {
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "calls": calls,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[write] data.json -> {len(calls)} calls")
