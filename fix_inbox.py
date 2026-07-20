#!/usr/bin/env python3
"""
fix_inbox.py — one-time migration for neuroscience-at-work-radar

PROBLEM
  candidates/inbox.csv carries a stale 14-column header from an older version
  of scan_sources.py. The current script writes 17-column rows, but only writes
  a header when the file does not exist — so every new row since the schema
  change sits underneath a header that does not describe it. Columns are
  silently misaligned when the file is opened in Excel or read by any tool.

WHAT THIS DOES
  1. Backs up the current file to candidates/inbox.csv.backup
  2. Reads every row, detecting 14-column (legacy) and 17-column (current) rows
  3. Maps legacy rows into the current schema, leaving the three new fields blank
  4. Rewrites inbox.csv with the correct 17-column header

Run once. Safe to re-run — after the first run every row is already 17 columns.
"""
import csv
import shutil
import sys
from pathlib import Path

INBOX = Path("candidates/inbox.csv")

NEW = ["found_date", "auto_tier_guess", "auto_score", "myth_flag",
       "source_api", "title", "authors", "year", "pub_date", "type", "venue",
       "open_access", "preprint", "doi", "url", "query", "status"]

OLD = ["found_date", "source_api", "query", "title", "authors", "year",
       "pub_date", "type", "venue", "open_access", "preprint", "doi",
       "url", "status"]


def main():
    if not INBOX.exists():
        sys.exit(f"Not found: {INBOX}  — run this from the repo root.")

    backup = INBOX.with_suffix(".csv.backup")
    shutil.copy2(INBOX, backup)
    print(f"Backup written: {backup}")

    rows, counts, skipped = [], {}, 0
    with INBOX.open(encoding="utf-8", newline="") as f:
        for raw in csv.reader(f):
            if not raw or raw[0] == "found_date":
                continue
            n = len(raw)
            counts[n] = counts.get(n, 0) + 1
            if n == len(NEW):
                rows.append(dict(zip(NEW, raw)))
            elif n == len(OLD):
                d = dict(zip(OLD, raw))
                d.update(auto_tier_guess="", auto_score="", myth_flag="")
                rows.append(d)
            else:
                skipped += 1

    with INBOX.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=NEW)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in NEW})

    print(f"Rows found by width: {counts}")
    print(f"Rows written: {len(rows)}   Skipped (malformed): {skipped}")
    print(f"Done. {INBOX} now has a correct {len(NEW)}-column header.")


if __name__ == "__main__":
    main()
