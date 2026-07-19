#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neuroscience at Work — Source Radar
===================================
Weekly scanner that looks for NEW scholarly sources at the intersection of
neuroscience + psychology + workforce management, deduplicates them against the
existing registry, and writes the new finds into a "for review" inbox.

It NEVER edits your master registry. It only proposes candidates that YOU approve.

Data sources (all free, no API key required):
  * OpenAlex          https://openalex.org
  * Crossref          https://www.crossref.org
  * Europe PMC        https://europepmc.org
  * arXiv             https://arxiv.org

Run on GitHub Actions weekly (see .github/workflows/weekly-scan.yml), or locally:
    python scan_sources.py               # normal run
    python scan_sources.py --selftest    # offline logic check (no internet)
"""

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import yaml  # PyYAML
except Exception:
    yaml = None

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
SEED_PATH = ROOT / "registry_seed.csv"
CAND_DIR = ROOT / "candidates"
INBOX_PATH = CAND_DIR / "inbox.csv"

FIELDS = ["found_date", "source_api", "query", "title", "authors", "year",
          "pub_date", "type", "venue", "open_access", "preprint", "doi", "url", "status"]

UA = ("NeuroscienceAtWorkRadar/1.0 (+https://github.com/) "
      "Python-urllib; research-source-monitor")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def norm_doi(doi: str) -> str:
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi


def norm_title(title: str) -> str:
    if not title:
        return ""
    t = title.lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def http_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def http_text(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def load_config():
    defaults = {
        "contact_email": "you@example.com",
        "since_days": 8,
        "max_per_query": 15,
        "max_new_total": 120,
        "must_match_any": ["work", "job", "employee", "workplace", "occupational",
                           "burnout", "stress", "remote", "hybrid", "attention",
                           "leadership", "engagement", "manager", "cognitive"],
        "queries": [
            "workplace burnout neuroscience",
            "occupational stress cortisol",
            "remote work wellbeing productivity",
            "hybrid work employee outcomes",
            "attention interruptions knowledge workers",
            "psychological safety teams",
            "work engagement job demands resources",
            "human-AI collaboration workplace",
            "technostress employees",
            "loneliness work social connection",
        ],
        "sources": {"openalex": True, "crossref": True, "europepmc": True, "arxiv": True},
    }
    if CONFIG_PATH.exists() and yaml is not None:
        try:
            user = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
            defaults.update({k: v for k, v in user.items() if v is not None})
        except Exception as e:
            print(f"  ! could not read config.yaml ({e}); using defaults")
    return defaults


def load_known():
    """DOIs + normalized titles already in the registry seed and the inbox."""
    dois, titles = set(), set()
    for path in (SEED_PATH, INBOX_PATH):
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                d = norm_doi(row.get("doi", ""))
                t = norm_title(row.get("title", ""))
                if d:
                    dois.add(d)
                if t:
                    titles.add(t)
    return dois, titles


# --------------------------------------------------------------------------- #
# source connectors  -> list of normalized records
# --------------------------------------------------------------------------- #
def rec(**kw):
    base = {k: "" for k in FIELDS}
    base.update(kw)
    return base


def from_openalex(query, cutoff, cfg):
    q = urllib.parse.quote(query)
    url = (f"https://api.openalex.org/works?search={q}"
           f"&filter=from_publication_date:{cutoff}"
           f"&sort=publication_date:desc&per-page={cfg['max_per_query']}"
           f"&mailto={urllib.parse.quote(cfg['contact_email'])}")
    out = []
    for w in http_json(url).get("results", []):
        authors = ", ".join(a.get("author", {}).get("display_name", "")
                            for a in (w.get("authorships") or [])[:6])
        venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name", "") or ""
        wtype = w.get("type", "") or ""
        out.append(rec(
            source_api="OpenAlex", query=query, title=(w.get("title") or "").strip(),
            authors=authors, year=str(w.get("publication_year") or ""),
            pub_date=w.get("publication_date", "") or "", type=wtype, venue=venue,
            open_access="OA" if (w.get("open_access") or {}).get("is_oa") else "?",
            preprint="yes" if wtype == "preprint" else "",
            doi=norm_doi(w.get("doi") or ""),
            url=w.get("doi") or w.get("id") or "",
        ))
    return out


def from_crossref(query, cutoff, cfg):
    q = urllib.parse.quote(query)
    url = (f"https://api.crossref.org/works?query={q}"
           f"&filter=from-pub-date:{cutoff}&rows={cfg['max_per_query']}"
           f"&sort=published&order=desc&mailto={urllib.parse.quote(cfg['contact_email'])}")
    out = []
    for it in http_json(url).get("message", {}).get("items", []):
        title = (it.get("title") or [""])[0].strip()
        authors = ", ".join(f"{a.get('given','')} {a.get('family','')}".strip()
                            for a in (it.get("author") or [])[:6])
        dp = (((it.get("issued") or {}).get("date-parts") or [[None]])[0])
        year = str(dp[0]) if dp and dp[0] else ""
        pub_date = "-".join(str(x) for x in dp) if dp and dp[0] else ""
        wtype = it.get("type", "") or ""
        out.append(rec(
            source_api="Crossref", query=query, title=title, authors=authors,
            year=year, pub_date=pub_date, type=wtype,
            venue=(it.get("container-title") or [""])[0],
            open_access="?", preprint="yes" if wtype == "posted-content" else "",
            doi=norm_doi(it.get("DOI") or ""),
            url=it.get("URL") or (("https://doi.org/" + it["DOI"]) if it.get("DOI") else ""),
        ))
    return out


def from_europepmc(query, cutoff, cfg):
    q = urllib.parse.quote(query)
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search"
           f"?query={q}&format=json&pageSize={cfg['max_per_query']}"
           "&resultType=lite&sort=P_PDATE_D%20desc")
    out = []
    for r in http_json(url).get("resultList", {}).get("result", []):
        out.append(rec(
            source_api="EuropePMC", query=query, title=(r.get("title") or "").strip(),
            authors=r.get("authorString", "") or "", year=str(r.get("pubYear") or ""),
            pub_date=r.get("firstPublicationDate", "") or "",
            type=r.get("pubType", "") or "", venue=r.get("journalTitle", "") or "",
            open_access="OA" if r.get("isOpenAccess") == "Y" else "?",
            preprint="yes" if "preprint" in (r.get("pubType", "") or "").lower() else "",
            doi=norm_doi(r.get("doi") or ""),
            url=("https://doi.org/" + r["doi"]) if r.get("doi")
                else (f"https://europepmc.org/abstract/{r.get('source','MED')}/{r.get('id','')}"),
        ))
    return out


def from_arxiv(query, cutoff, cfg):
    q = urllib.parse.quote(f"all:{query}")
    url = (f"http://export.arxiv.org/api/query?search_query={q}"
           f"&start=0&max_results={cfg['max_per_query']}"
           "&sortBy=submittedDate&sortOrder=descending")
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out = []
    root = ET.fromstring(http_text(url))
    for e in root.findall("a:entry", ns):
        published = (e.findtext("a:published", default="", namespaces=ns) or "")[:10]
        if published and published < cutoff:
            continue
        authors = ", ".join(a.findtext("a:name", default="", namespaces=ns)
                            for a in e.findall("a:author", ns)[:6])
        doi = e.findtext("arxiv:doi", default="", namespaces=ns) or ""
        out.append(rec(
            source_api="arXiv", query=query,
            title=" ".join((e.findtext("a:title", default="", namespaces=ns) or "").split()),
            authors=authors, year=published[:4], pub_date=published,
            type="preprint", venue="arXiv", open_access="OA", preprint="yes",
            doi=norm_doi(doi), url=e.findtext("a:id", default="", namespaces=ns) or "",
        ))
    return out


CONNECTORS = {
    "openalex": from_openalex,
    "crossref": from_crossref,
    "europepmc": from_europepmc,
    "arxiv": from_arxiv,
}


# --------------------------------------------------------------------------- #
# core
# --------------------------------------------------------------------------- #
def matches_topic(r, must_match_any):
    hay = (r["title"] + " " + r["venue"]).lower()
    return any(k.lower() in hay for k in must_match_any) if must_match_any else True


def scan(records_by_source):
    """Given pre-fetched records (real run or selftest), filter+dedupe+write."""
    cfg = load_config()
    known_dois, known_titles = load_known()
    seen_now_doi, seen_now_title = set(), set()
    new_rows = []
    today = dt.date.today().isoformat()

    for src, records in records_by_source.items():
        for r in records:
            if not r["title"]:
                continue
            if not matches_topic(r, cfg["must_match_any"]):
                continue
            d, t = norm_doi(r["doi"]), norm_title(r["title"])
            if d and (d in known_dois or d in seen_now_doi):
                continue
            if t and (t in known_titles or t in seen_now_title):
                continue
            if d:
                seen_now_doi.add(d)
            if t:
                seen_now_title.add(t)
            r["found_date"] = today
            r["status"] = "NEW — to review"
            new_rows.append(r)
            if len(new_rows) >= cfg["max_new_total"]:
                break
        if len(new_rows) >= cfg["max_new_total"]:
            break

    # newest first
    new_rows.sort(key=lambda x: (x.get("pub_date", ""), x.get("year", "")), reverse=True)
    write_candidates(new_rows)
    return new_rows


def write_candidates(new_rows):
    CAND_DIR.mkdir(parents=True, exist_ok=True)
    # append to cumulative inbox
    inbox_exists = INBOX_PATH.exists()
    with INBOX_PATH.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not inbox_exists:
            w.writeheader()
        for r in new_rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    # also a dated weekly file for easy scanning
    if new_rows:
        iso = dt.date.today().isocalendar()
        weekly = CAND_DIR / f"candidates_{iso[0]}-W{iso[1]:02d}.csv"
        with weekly.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in new_rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})


def run_online():
    cfg = load_config()
    cutoff = (dt.date.today() - dt.timedelta(days=int(cfg["since_days"]))).isoformat()
    print(f"  Radar run — looking back to {cutoff}")
    print(f"  Queries: {len(cfg['queries'])} | sources: "
          f"{[s for s,on in cfg['sources'].items() if on]}\n")
    by_source = {}
    for name, on in cfg["sources"].items():
        if not on or name not in CONNECTORS:
            continue
        got = []
        for query in cfg["queries"]:
            try:
                found = CONNECTORS[name](query, cutoff, cfg)
                got.extend(found)
                print(f"    {name:>10}  '{query}'  -> {len(found)}")
            except Exception as e:
                print(f"    {name:>10}  '{query}'  ! {str(e).splitlines()[0][:80]}")
            time.sleep(1.0)  # be polite to the APIs
        by_source[name] = got
    new_rows = scan(by_source)
    print(f"\n  NEW candidates written: {len(new_rows)}  ->  {INBOX_PATH}")
    if new_rows:
        print("  Review them, then move the good ones into your master registry")
        print("  and add their DOI/title to registry_seed.csv so they aren't suggested again.")
    return new_rows


def selftest():
    """Offline check of filter + dedupe + writer with fake API output."""
    print("  Running offline self-test (no internet)...")
    sample = {
        "openalex": [
            rec(source_api="OpenAlex", title="Burnout and cortisol in remote employees",
                authors="A. Author", year="2026", pub_date="2026-07-01", type="article",
                venue="Journal of Occupational Health Psychology", open_access="OA",
                doi="10.1000/new-oa-1", url="https://doi.org/10.1000/new-oa-1"),
            rec(source_api="OpenAlex", title="A study about butterflies in spring",  # off-topic -> filtered
                authors="B. Author", year="2026", pub_date="2026-07-02", type="article",
                venue="Lepidoptera Today", doi="10.1000/offtopic", url="x"),
        ],
        "crossref": [
            rec(source_api="Crossref", title="Burnout and Cortisol in Remote Employees",  # dup title
                authors="A. Author", year="2026", pub_date="2026-7-1", type="journal-article",
                doi="10.1000/new-oa-1", url="x"),
            rec(source_api="Crossref", title="Hybrid work and manager engagement outcomes",
                authors="C. Writer", year="2026", pub_date="2026-06-28", type="journal-article",
                doi="10.1000/new-oa-2", url="https://doi.org/10.1000/new-oa-2"),
        ],
    }
    # ensure a clean inbox for the test
    if INBOX_PATH.exists():
        INBOX_PATH.unlink()
    rows = scan(sample)
    titles = [r["title"] for r in rows]
    assert any("Hybrid work" in t for t in titles), "expected the hybrid-work item"
    assert not any("butterflies" in t.lower() for t in titles), "off-topic should be filtered"
    assert sum("burnout and cortisol" in t.lower() for t in titles) == 1, "title dedupe failed"
    print(f"  OK — {len(rows)} unique on-topic candidates kept (off-topic + duplicate removed).")
    print(f"  Wrote: {INBOX_PATH}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Weekly radar for new workplace-neuroscience sources.")
    ap.add_argument("--selftest", action="store_true", help="offline logic check, no internet")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    run_online()
    return 0


if __name__ == "__main__":
    sys.exit(main())
