#!/usr/bin/env python3
import json, os, sys, re, hashlib
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "data.json")
TIMEOUT = 30
HEADERS = {"User-Agent": "FundingCallsBot/1.0 (+https://github.com/)"}

BASE_FIELDS = ["id","title","deadline","agency","area","eligibility","budgetINR","url",
               "category","researchCategory","extendedDeadline","country","isRecurring"]

def norm_space(s): return re.sub(r"\s+", " ", (s or "").strip())
def to_iso(dstr):
    if not dstr: return ""
    try: return dateparser.parse(dstr, fuzzy=True).strftime("%Y-%m-%d")
    except: return ""

def make_id(title, agency, url):
    raw = f"{title}|{agency}|{url}"
    return int(hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8], 16)

def standardize(item):
    out = {k: "" for k in BASE_FIELDS}
    out.update({
        "id":            item.get("id"),
        "title":         norm_space(item.get("title")),
        "deadline":      item.get("deadline",""),
        "agency":        norm_space(item.get("agency")),
        "area":          norm_space(item.get("area")),
        "eligibility":   norm_space(item.get("eligibility")),
        "budgetINR":     norm_space(item.get("budgetINR")),
        "url":           item.get("url",""),
        "category":      norm_space(item.get("category")),
        "researchCategory": norm_space(item.get("researchCategory")),
        "extendedDeadline": item.get("extendedDeadline",""),
        "country":       norm_space(item.get("country") or "Global"),
        "isRecurring":   bool(item.get("isRecurring", False)),
    })
    if not out["id"]:
        out["id"] = make_id(out["title"], out["agency"], out["url"])
    return out

# ---------- Adapters (add more as you like) ----------
def fetch_serb_recurring():
    return [standardize({
        "title":"SERB Core Research Grant (CRG) – Annual","deadline":"",
        "agency":"ANRF (formerly SERB)","area":"Science & Engineering",
        "eligibility":"Faculty researchers in Indian institutions","budgetINR":"",
        "url":"https://www.serbonline.in/","category":"National",
        "researchCategory":"Research Proposal","extendedDeadline":"",
        "country":"India","isRecurring":True
    })]

def fetch_horizon_europe():
    return [standardize({
        "title":"Horizon Europe Collaborative Projects","deadline":"2025-11-15",
        "agency":"European Commission","area":"Multidisciplinary",
        "eligibility":"Universities, research orgs, SMEs","budgetINR":"€ variable",
        "url":"https://research-and-innovation.ec.europa.eu/","category":"International",
        "researchCategory":"Research Proposal","extendedDeadline":"",
        "country":"Global","isRecurring":False
    })]

def fetch_nih_r01():
    return [standardize({
        "title":"NIH R01 Research Project Grant","deadline":"2025-10-05",
        "agency":"NIH","area":"Biomedical","eligibility":"International collaborators allowed via rules",
        "budgetINR":"$ variable","url":"https://grants.nih.gov/grants/funding/r01.htm",
        "category":"International","researchCategory":"Research Proposal",
        "extendedDeadline":"","country":"Global","isRecurring":False
    })]

# Example template to extend (fill selectors & parsing):
def fetch_icmr_current():
    URL = "https://www.icmr.gov.in/"        # TODO: replace with the actual calls page
    try:
        r = requests.get(URL, headers=HEADERS, timeout=TIMEOUT); r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        results = []
        for a in soup.select("a"):           # TODO: Narrow this to the right section
            title = norm_space(a.get_text())
            href  = urljoin(URL, a.get("href") or "")
            if not title or not href: continue
            if not any(k in title.lower() for k in ["grant","fellow","call","proposal"]): continue
            results.append(standardize({
                "title":title,"deadline":"","agency":"ICMR (Indian Council of Medical Research)",
                "area":"","eligibility":"","budgetINR":"","url":href,"category":"National",
                "researchCategory":"Research Proposal","extendedDeadline":"","country":"India","isRecurring":False
            }))
        return results
    except Exception as e:
        print("ICMR fetch failed:", e, file=sys.stderr)
        return []

ADAPTERS = [fetch_serb_recurring, fetch_horizon_europe, fetch_nih_r01, fetch_icmr_current]

# ---------- Merge ----------
def load_existing(path):
    if not os.path.exists(path): return []
    try:
        with open(path,"r",encoding="utf-8") as f: data=json.load(f)
        return data if isinstance(data,list) else []
    except: return []

def key_fn(r):
    return (norm_space(r.get("title")), norm_space(r.get("agency")), r.get("url",""))

def merge_records(existing, new):
    by = { key_fn(standardize(r)) : standardize(r) for r in existing }
    for r in new:
        rr = standardize(r)
        by[key_fn(rr)] = rr
    merged = list(by.values())
    def sort_key(r): return (r.get("deadline") or "9999-12-31", r.get("title",""))
    merged.sort(key=sort_key)
    return merged

def main():
    existing = load_existing(OUTPUT_FILE)
    collected=[]
    for fn in ADAPTERS:
        try: collected.extend(fn() or [])
        except Exception as e: print(f"Adapter {fn.__name__} failed:", e, file=sys.stderr)
    merged = merge_records(existing, collected)
    with open(OUTPUT_FILE,"w",encoding="utf-8") as f:
        json.dump(merged,f,ensure_ascii=False,indent=2)
    print(f"Wrote {len(merged)} calls to {OUTPUT_FILE}")

if __name__=="__main__": main()
