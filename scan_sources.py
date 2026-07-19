#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neuroscience at Work — Source Radar (with auto-scoring)
=======================================================
Weekly scanner that finds NEW scholarly sources at the intersection of
neuroscience + psychology + workforce management, deduplicates them against the
existing registry, AUTO-SCORES each find (a rough A?/B?/C? guess), flags possible
neuromyths, and writes everything into a "for review" inbox + a short digest.md.

It NEVER edits your master registry and NEVER decides the final tier.
The score is a hint to speed up your review — you (or Claude) confirm the real
reliability tier and the neuromyth check before anything enters the master base.

Free data sources, no API key required: OpenAlex, Crossref, Europe PMC, arXiv.

Run on GitHub Actions weekly (.github/workflows/weekly-scan.yml), or locally:
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
DIGEST_PATH = CAND_DIR / "digest.md"

# Important signals up front so the CSV is easy to skim.
FIELDS = ["found_date", "auto_tier_guess", "auto_score", "myth_flag",
          "source_api", "title", "authors", "year", "pub_date", "type", "venue",
          "open_access", "preprint", "doi", "url", "query", "status"]

UA = ("NeuroscienceAtWorkRadar/1.1 (+https://github.com/) "
      "Python-urllib; research-source-monitor")

# ---- defaults (can be overridden in config.yaml) ---------------------------
DEFAULT_TRUSTED_VENUES = [
    "lancet", "nature", "science", "new england journal", "nejm", "jama", "bmj",
    "plos", "pnas", "proceedings of the national academy", "psychological science",
    "journal of applied psychology", "journal of occupational health psychology",
    "occupational and environmental medicine", "sleep", "current biology", "neuron",
    "nature reviews", "nature human behaviour", "management science",
    "quarterly journal of economics", "organization science", "frontiers",
    "journal of managerial psychology", "journal of happiness studies",
    "information systems research", "psychological bulletin", "american psychologist",
    "health psychology", "work & stress", "work and stress",
    "scandinavian journal of work", "journal of organizational behavior",
    "academy of management", "psychoneuroendocrinology", "world psychiatry",
]
DEFAULT_INTERGOV = ["who", "world health", "oecd", " ilo", "eurofound",
                    "surgeon general", "cdc", "niosh", "eu-osha"]
DEFAULT_MYTH_TRIGGERS = [
    "dopamine detox", "dopamine fasting", "8-second attention", "eight-second attention",
    "goldfish attention", "10% of the brain", "10 percent of the brain",
    "ten percent of the brain", "reptilian brain", "amygdala hijack",
    "left-brain", "right-brain", "left brain", "right brain", "learning styles",
    "10,000 hour", "10000 hour", "ten thousand hour", "neuro-linguistic programming",
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def norm_doi(doi: str) -> str:
    if not doi:
        return ""
    doi = doi.strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)


def norm_title(title: str) -> str:
    if not title:
        return ""
    t = re.sub(r"[^a-z0-9]+", " ", title.lower())
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
        "digest_top_n": 12,
        "must_match_any": ["work", "job", "employee", "workplace", "occupational",
                           "burnout", "stress", "remote", "hybrid", "attention",
                           "focus", "leadership", "engagement", "manager", "cognitive",
                           "wellbeing", "technostress"],
        "queries": [
            "workplace burnout neuroscience", "occupational stress cortisol",
            "remote work wellbeing productivity", "hybrid work employee outcomes",
            "attention interruptions knowledge workers", "psychological safety teams",
            "work engagement job demands resources", "human-AI collaboration workplace",
            "technostress employees", "loneliness work social connection",
        ],
        "sources": {"openalex": True, "crossref": True, "europepmc": True, "arxiv": True},
        "trusted_venues": DEFAULT_TRUSTED_VENUES,
        "intergov_venues": DEFAULT_INTERGOV,
        "myth_triggers": DEFAULT_MYTH_TRIGGERS,
    }
    if CONFIG_PATH.exists() and yaml is not None:
        try:
            user = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
            defaults.update({k: v for k, v in user.items() if v is not None})
        except Exception as e:
            print(f"  ! could not read config.yaml ({e}); using defaults")
    return defaults


def load_known():
    dois, titles = set(), set()
    for path in (SEED_PATH, INBOX_PATH):
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                d, t = norm_doi(row.get("doi", "")), norm_title(row.get("title", ""))
                if d:
                    dois.add(d)
                if t:
                    titles.add(t)
    return dois, titles


# --------------------------------------------------------------------------- #
# auto-scoring
# --------------------------------------------------------------------------- #
def score_record(r, cfg):
    """Rough triage score. Returns (score:int, tier_guess:str, myth_flag:str).
    Tiers end with '?' on purpose — they are GUESSES for you to confirm."""
    t = (r.get("title") or "").lower()
    v = (r.get("venue") or "").lower()
    typ = (r.get("type") or "").lower()
    score = 0

    # peer-reviewed vs preprint (the biggest trust signal we can read automatically)
    if r.get("preprint") == "yes" or "preprint" in typ or "posted-content" in typ:
        score -= 3
    else:
        score += 3

    # evidence type
    if any(k in t for k in ("meta-analysis", "meta analysis", "systematic review")):
        score += 3
    if "review" in typ or "review" in t:
        score += 1
    if any(k in typ for k in ("journal-article", "research-article", "article", "journal article")):
        score += 1

    # venue trust / intergovernmental source
    if any(k in v for k in cfg["trusted_venues"]):
        score += 3
    if any(k in v for k in cfg["intergov_venues"]):
        score += 3

    # open access (minor convenience only — not a trust signal)
    if r.get("open_access") == "OA":
        score += 1

    # topic relevance strength
    hay = t + " " + v
    matches = sum(1 for k in cfg["must_match_any"] if k.lower() in hay)
    score += min(matches, 4)

    # neuromyth flag (flag, do NOT penalize — the paper might be debunking the myth)
    myth = "MYTH-CHECK" if any(m in t for m in cfg["myth_triggers"]) else ""

    tier = "A?" if score >= 9 else "B?" if score >= 5 else "C?"
    return score, tier, myth


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
        out.append(rec(source_api="OpenAlex", query=query, title=(w.get("title") or "").strip(),
                       authors=authors, year=str(w.get("publication_year") or ""),
                       pub_date=w.get("publication_date", "") or "", type=wtype, venue=venue,
                       open_access="OA" if (w.get("open_access") or {}).get("is_oa") else "?",
                       preprint="yes" if wtype == "preprint" else "",
                       doi=norm_doi(w.get("doi") or ""), url=w.get("doi") or w.get("id") or ""))
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
        out.append(rec(source_api="Crossref", query=query, title=title, authors=authors,
                       year=year, pub_date=pub_date, type=wtype,
                       venue=(it.get("container-title") or [""])[0], open_access="?",
                       preprint="yes" if wtype == "posted-content" else "",
                       doi=norm_doi(it.get("DOI") or ""),
                       url=it.get("URL") or (("https://doi.org/" + it["DOI"]) if it.get("DOI") else "")))
    return out


def from_europepmc(query, cutoff, cfg):
    q = urllib.parse.quote(query)
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search"
           f"?query={q}&format=json&pageSize={cfg['max_per_query']}"
           "&resultType=lite&sort=P_PDATE_D%20desc")
    out = []
    for r in http_json(url).get("resultList", {}).get("result", []):
        out.append(rec(source_api="EuropePMC", query=query, title=(r.get("title") or "").strip(),
                       authors=r.get("authorString", "") or "", year=str(r.get("pubYear") or ""),
                       pub_date=r.get("firstPublicationDate", "") or "",
                       type=r.get("pubType", "") or "", venue=r.get("journalTitle", "") or "",
                       open_access="OA" if r.get("isOpenAccess") == "Y" else "?",
                       preprint="yes" if "preprint" in (r.get("pubType", "") or "").lower() else "",
                       doi=norm_doi(r.get("doi") or ""),
                       url=("https://doi.org/" + r["doi"]) if r.get("doi")
                           else f"https://europepmc.org/abstract/{r.get('source','MED')}/{r.get('id','')}"))
    return out


def from_arxiv(query, cutoff, cfg):
    q = urllib.parse.quote(f"all:{query}")
    url = (f"http://export.arxiv.org/api/query?search_query={q}"
           f"&start=0&max_results={cfg['max_per_query']}"
           "&sortBy=submittedDate&sortOrder=descending")
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out = []
    for e in ET.fromstring(http_text(url)).findall("a:entry", ns):
        published = (e.findtext("a:published", default="", namespaces=ns) or "")[:10]
        if published and published < cutoff:
            continue
        authors = ", ".join(a.findtext("a:name", default="", namespaces=ns)
                            for a in e.findall("a:author", ns)[:6])
        doi = e.findtext("arxiv:doi", default="", namespaces=ns) or ""
        out.append(rec(source_api="arXiv", query=query,
                       title=" ".join((e.findtext("a:title", default="", namespaces=ns) or "").split()),
                       authors=authors, year=published[:4], pub_date=published,
                       type="preprint", venue="arXiv", open_access="OA", preprint="yes",
                       doi=norm_doi(doi), url=e.findtext("a:id", default="", namespaces=ns) or ""))
    return out


CONNECTORS = {"openalex": from_openalex, "crossref": from_crossref,
              "europepmc": from_europepmc, "arxiv": from_arxiv}


# --------------------------------------------------------------------------- #
# core
# --------------------------------------------------------------------------- #
def matches_topic(r, must_match_any):
    hay = (r["title"] + " " + r["venue"]).lower()
    return any(k.lower() in hay for k in must_match_any) if must_match_any else True


def scan(records_by_source, cfg=None):
    cfg = cfg or load_config()
    known_dois, known_titles = load_known()
    seen_doi, seen_title = set(), set()
    new_rows = []
    today = dt.date.today().isoformat()

    for records in records_by_source.values():
        for r in records:
            if not r["title"] or not matches_topic(r, cfg["must_match_any"]):
                continue
            d, t = norm_doi(r["doi"]), norm_title(r["title"])
            if (d and (d in known_dois or d in seen_doi)) or (t and (t in known_titles or t in seen_title)):
                continue
            if d:
                seen_doi.add(d)
            if t:
                seen_title.add(t)
            s, tier, myth = score_record(r, cfg)
            r["auto_score"] = str(s)
            r["auto_tier_guess"] = tier
            r["myth_flag"] = myth
            r["found_date"] = today
            r["status"] = "NEW — to review"
            new_rows.append(r)
            if len(new_rows) >= cfg["max_new_total"]:
                break
        if len(new_rows) >= cfg["max_new_total"]:
            break

    # best (highest score) first, newest as tiebreak
    new_rows.sort(key=lambda x: (int(x.get("auto_score") or 0), x.get("pub_date", "")), reverse=True)
    write_candidates(new_rows)
    write_digest(new_rows, cfg)
    return new_rows


def write_candidates(new_rows):
    CAND_DIR.mkdir(parents=True, exist_ok=True)
    inbox_exists = INBOX_PATH.exists()
    with INBOX_PATH.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not inbox_exists:
            w.writeheader()
        for r in new_rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    if new_rows:
        iso = dt.date.today().isocalendar()
        weekly = CAND_DIR / f"candidates_{iso[0]}-W{iso[1]:02d}.csv"
        with weekly.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in new_rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})


def write_digest(new_rows, cfg):
    CAND_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    lines = [f"# Weekly source radar — digest ({today})", ""]
    if not new_rows:
        lines += ["No new sources this week. The radar ran and found nothing new "
                  "beyond your registry. (This is normal some weeks.)", ""]
        DIGEST_PATH.write_text("\n".join(lines), encoding="utf-8")
        return

    myth_rows = [r for r in new_rows if r.get("myth_flag")]
    lines.append(f"Found **{len(new_rows)}** new candidate(s). "
                 f"Best-scored first. Tiers marked `A?/B?/C?` are **guesses to confirm** — "
                 f"you or Claude set the real A/B/C tier and run the neuromyth check before "
                 f"anything enters the master base.")
    if myth_rows:
        lines.append(f"\n> ⚠ {len(myth_rows)} item(s) contain a possible-neuromyth phrase "
                     f"and are marked `MYTH-CHECK` — verify the claim before trusting.")
    lines += ["", "## Top candidates", ""]

    for i, r in enumerate(new_rows[:cfg.get("digest_top_n", 12)], 1):
        title = r.get("title", "").strip()
        venue = r.get("venue", "") or r.get("source_api", "")
        year = r.get("year", "")
        tier = r.get("auto_tier_guess", "")
        score = r.get("auto_score", "")
        oa = "OA" if r.get("open_access") == "OA" else ""
        pre = "preprint" if r.get("preprint") == "yes" else (r.get("type", "") or "")
        url = r.get("url", "")
        myth = "  ⚠ MYTH-CHECK" if r.get("myth_flag") else ""
        badge = " · ".join(x for x in [f"guess {tier}", f"score {score}", oa, pre] if x)
        link = f"[link]({url})" if url else ""
        lines.append(f"{i}. **{title}** — {venue} ({year})")
        lines.append(f"   {badge} {link}{myth}")
        lines.append("")

    lines += ["---",
              "Full list with all columns: `candidates/inbox.csv`  ·  "
              "one file per week: `candidates/candidates_YYYY-Www.csv`",
              "",
              "**How the score works (rough triage only):** +peer-reviewed / "
              "+meta-analysis or systematic review / +trusted journal or WHO-OECD-ILO-type "
              "source / +on-topic keywords / −preprint. It speeds up your review; it does "
              "**not** replace your judgement on trust or neuromyths."]
    DIGEST_PATH.write_text("\n".join(lines), encoding="utf-8")


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
            time.sleep(1.0)
        by_source[name] = got
    new_rows = scan(by_source, cfg)
    print(f"\n  NEW candidates: {len(new_rows)}  ->  {INBOX_PATH}")
    print(f"  Digest written ->  {DIGEST_PATH}")
    if new_rows:
        top = new_rows[0]
        print(f"  Top pick: [{top['auto_tier_guess']}] {top['title'][:70]}")
    return new_rows


def selftest():
    print("  Running offline self-test (no internet)...")
    cfg = load_config()
    sample = {
        "a": [
            rec(source_api="OpenAlex",
                title="A systematic review and meta-analysis of burnout in remote employees",
                authors="A. Author", year="2026", pub_date="2026-07-01", type="article",
                venue="Journal of Occupational Health Psychology", open_access="OA",
                doi="10.1000/trust-meta", url="https://doi.org/10.1000/trust-meta"),
            rec(source_api="arXiv", title="A speculative preprint on employee attention and AI",
                authors="B. Author", year="2026", pub_date="2026-07-02", type="preprint",
                venue="arXiv", open_access="OA", preprint="yes",
                doi="", url="https://arxiv.org/abs/0000.00000"),
            rec(source_api="Crossref", title="Does a dopamine detox improve worker focus",
                authors="C. Writer", year="2026", pub_date="2026-06-30", type="journal-article",
                venue="Some Journal", doi="10.1000/myth", url="x"),
            rec(source_api="OpenAlex", title="Butterflies of the alpine meadow in spring",
                authors="D. Naturalist", year="2026", pub_date="2026-07-03", type="article",
                venue="Lepidoptera Today", doi="10.1000/offtopic", url="x"),
        ]
    }
    if INBOX_PATH.exists():
        INBOX_PATH.unlink()
    rows = scan(sample, cfg)
    titles = [r["title"] for r in rows]
    assert not any("butterfl" in t.lower() for t in titles), "off-topic should be filtered"
    # trusted peer-reviewed meta-analysis must outrank the preprint
    assert rows[0]["title"].startswith("A systematic review"), "scoring order wrong"
    assert rows[0]["auto_tier_guess"] == "A?", f"expected A?, got {rows[0]['auto_tier_guess']}"
    pre = next(r for r in rows if r["preprint"] == "yes")
    assert int(pre["auto_score"]) < int(rows[0]["auto_score"]), "preprint should score lower"
    myth = next(r for r in rows if "dopamine detox" in r["title"].lower())
    assert myth["myth_flag"] == "MYTH-CHECK", "neuromyth flag missing"
    assert DIGEST_PATH.exists(), "digest not written"
    print(f"  OK — {len(rows)} candidates kept, scored & sorted.")
    print(f"       top = [{rows[0]['auto_tier_guess']}] {rows[0]['title'][:60]}")
    print(f"       preprint scored {pre['auto_score']} vs top {rows[0]['auto_score']}; "
          f"myth flagged: {myth['myth_flag']}")
    print(f"  Wrote: {INBOX_PATH}  and  {DIGEST_PATH}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Weekly radar (auto-scored) for workplace-neuroscience sources.")
    ap.add_argument("--selftest", action="store_true", help="offline logic check, no internet")
    args = ap.parse_args()
    return selftest() if args.selftest else (run_online() and 0) or 0


if __name__ == "__main__":
    sys.exit(main())
