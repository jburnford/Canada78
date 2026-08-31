#!/usr/bin/env python3
"""Acceptance check for the officers (Return A) family against Ben Hoy's rows.

    python3 eval/compare_officers_hoy.py [--years 1881 1891 ...]

Ben Hoy's `dia_employees_person_level_1875-1916.csv` is a human-checked
transcription of the same Return A staff lists we extract with Qwen, so his
per-year headcount is the closest thing to ground truth we have for coverage.
It is a *coverage* check, not an accuracy one: a ratio near 1.0 says we found
about as many distinct employees as he did that year; well under 1.0 means the
year's page span is still cut short (the failure mode that forced the
2026-08-30 re-run, where an END regex fired on an in-table "Indian Lands"
heading and truncated spans to 4-6 pages).

Prints one line per year: our distinct names, Hoy's, the ratio, and our page span.

A ratio above 1.00 is not better extraction. Six of the officers chunk
directories contain a stray chunk cut from far outside Return A — the Auditor
General's expenditure ledgers and Returns C-G, which list retired allowances
and teachers' salaries. Those rows are real departmental staff with real page
provenance, but they are not Return A officers, and they are what push 1884 to
1.03 and 1888 to 1.04. `--span-only` drops them so the ratio measures what it
claims to; the foreign chunks are listed either way.
"""
import argparse
import collections
import glob
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HOY = ROOT / "registries/external/hoy_building_borders/dia_employees_person_level_1875-1916.csv"
INITIALS = re.compile(r"\b([a-z])[a-z]*\.?")
SUFFIX = re.compile(r"\b(m\.?d|b\.?a|jr|sr|esq|q\.?c|ll\.?d)\b")


def norm_person(raw):
    """'Mostyn-Hoops, S.E., M.D.' and 'S. E. Mostyn Hoops' -> the same key.

    Surname is the longest alphabetic token, which survives both the
    "Surname, Initials" of Hoy's sheet and the "Initials Surname" the printer
    used; the given names contribute their initials only.
    """
    s = "".join(c for c in unicodedata.normalize("NFKD", str(raw))
                if not unicodedata.combining(c)).lower()
    s = SUFFIX.sub(" ", s)
    s = re.sub(r"[^a-z\s,]", " ", s).strip()
    if not s:
        return ""
    if "," in s:
        last, _, rest = s.partition(",")
        parts = [last.strip()] + rest.split()
    else:
        parts = s.split()
        parts = [parts[-1]] + parts[:-1]
    surname = parts[0].replace(" ", "")
    inits = "".join(p[0] for p in parts[1:] if p)
    return f"{surname}|{inits}" if surname else ""


def foreign_chunks(year, gap=40):
    """Chunk ids cut far outside the Return A block for this year.

    The officers chunker occasionally picked up a page from the Auditor
    General's ledgers or Returns C-G; those pages sit hundreds of pages away
    from the staff list, so distance from the first chunk identifies them.
    """
    rj = ROOT / f"eval/results/officers{year}_qwen38_medium/run.json"
    if not rj.exists():
        return {}
    cfg = json.load(open(rj))
    starts = {k: int(re.match(r"(\d+)", v["pages"]).group(1))
              for k, v in cfg.items() if v.get("pages")}
    if not starts:
        return {}
    base = min(starts.values())
    return {k: cfg[k]["pages"] for k, s in starts.items() if s - base > gap}


def our_year(year, span_only=False):
    names, pages = set(), []
    skip = set(foreign_chunks(year)) if span_only else set()
    for path in sorted(glob.glob(str(ROOT / f"eval/results/officers{year}_qwen38_medium/out_*.jsonl"))):
        if re.search(r"out_(\d+)\.jsonl$", path).group(1) in skip:
            continue
        for line in open(path):
            row = json.loads(line)
            if row.get("row_type") != "data" and not row.get("name"):
                continue
            key = norm_person(row.get("name", ""))
            if key and "|" in key and len(key.split("|")[0]) > 2:
                names.add(key)
            if row.get("page"):
                pages.append(int(row["page"]))
    return names, (min(pages), max(pages)) if pages else (0, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="*", type=int)
    ap.add_argument("--span-only", action="store_true",
                    help="drop chunks cut outside the Return A page block")
    args = ap.parse_args()

    hoy = pd.read_csv(HOY)
    hoy["key"] = hoy.Name.map(norm_person)
    by_year = collections.defaultdict(set)
    for r in hoy.itertuples():
        if r.key:
            by_year[int(r.Date)].add(r.key)

    years = args.years or sorted(
        int(re.search(r"officers(\d{4})", p).group(1))
        for p in glob.glob(str(ROOT / "eval/results/officers*_qwen38_medium")))
    print(f"{'year':>5} {'ours':>6} {'hoy':>6} {'ratio':>6} {'shared':>7} {'pages':>12}")
    for y in years:
        ours, (p0, p1) = our_year(y, args.span_only)
        theirs = by_year.get(y, set())
        if not ours and not theirs:
            continue
        ratio = len(ours) / len(theirs) if theirs else float("nan")
        flag = "" if ratio >= 0.95 or not theirs else ("  <-- short" if ratio < 0.9 else "  <-- low")
        fc = foreign_chunks(y)
        note = f"  (foreign chunks {', '.join(sorted(fc))}: {'; '.join(fc.values())})" if fc and not args.span_only else ""
        print(f"{y:>5} {len(ours):>6} {len(theirs):>6} {ratio:>6.2f} "
              f"{len(ours & theirs):>7} {f'{p0}-{p1}':>12}{flag}{note}")


if __name__ == "__main__":
    main()
