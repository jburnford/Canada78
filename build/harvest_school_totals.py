#!/usr/bin/env python3
"""The Department's own per-class school totals, harvested from the deputy
superintendent's narrative, and the comparison of the extracted register
against them. This is the acceptance gate the school family lacked: the
2026-09-02 review found the extractor's default "day" type had put ~40% of
residential enrolment under day schools for a week without anything noticing.

Writes registries/annotations/school_totals_printed.csv
  year, klass (day|boarding|industrial|residential), n_schools, enrolment,
  avg_attendance, fiscal_note, page, sentence
and prints extracted-vs-printed per class per year. Exit 1 if any class in a
year with a printed figure is outside --tolerance (default 0.15).

Source text: the generated wiki's report pages (site/reports/YYYY/*/index.html),
because the narrative is already segmented and tag-light there. The sentences
vary by year, so each pattern is small and the harvest is reviewed by eye
(the sentence is kept beside every figure).
"""
import argparse
import csv
import glob
import html
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "registries/annotations/school_totals_printed.csv"
NUM = r"([\d,]{2,6})"
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
           "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
           "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def n(s):
    s = str(s).lower().replace(",", "").strip()
    if s.isdigit():
        return int(s)
    tot, parts = 0, re.split(r"[-\s]+", s)
    for p in parts:
        if p in WORDNUM:
            tot += WORDNUM[p]
    return tot or None


PATTERNS = [
    # 1910: "Of day schools in operation there were 241, and the proportion of enrolment connected with them was 6,784"
    ("day", re.compile(r"day schools in operation there were (\d+)[^.]{0,80}?enrolment[^.]{0,40}?" + NUM, re.I)),
    ("boarding", re.compile(r"boarding schools there were ([a-z\-]+|\d+)[^.]{0,60}?enrolment[^.]{0,40}?" + NUM, re.I)),
    ("industrial", re.compile(r"industrial schools[^.]{0,40}?number of ([a-z\-]+|\d+)[^.]{0,60}?enrolment[^.]{0,40}?" + NUM, re.I)),
    # 1925: "at the 247 day schools then in operation an enrolment of 7,477 and an average attendance of 3,516"
    ("day", re.compile(r"(\d+) day schools[^.]{0,80}?enrol(?:l)?ment of " + NUM + r"(?: and an average attendance of " + NUM + ")?", re.I)),
    ("residential", re.compile(r"(\d+) residential schools[^.]{0,80}?enrol(?:l)?ment of " + NUM + r"(?: and an average attendance of " + NUM + ")?", re.I)),
    # 1926/1928 denominational table: "Total 74 residential schools: enrolment, 6,327"
    ("residential", re.compile(r"Total (\d+) residential schools:? enrolment,? " + NUM, re.I)),
    # 1899: "Industrial schools 983 Boarding schools 847" after "total enrolment in industrial and boarding schools"
    ("industrial", re.compile(r"total enrolment in industrial and boarding schools[^:]{0,60}:\s*-?\s*Industrial schools " + NUM, re.I)),
    ("boarding", re.compile(r"total enrolment in industrial and boarding schools[^:]{0,60}:[^B]{0,40}Boarding schools " + NUM, re.I)),
]


def harvest():
    rows = []
    for f in sorted(glob.glob(str(ROOT / "site/reports/*/*/index.html"))):
        year = int(f.split("/")[-3])
        t = html.unescape(open(f, encoding="utf-8").read())
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"\s+", " ", t)
        for klass, pat in PATTERNS:
            for m in pat.finditer(t):
                g = m.groups()
                if len(g) >= 2:
                    n_sch, enrol = n(g[0]), n(g[1])
                    att = n(g[2]) if len(g) > 2 and g[2] else None
                else:
                    n_sch, enrol, att = None, n(g[0]), None
                if not enrol or enrol < 100:
                    continue
                # "In the fiscal year 1919-20, there was at the 247 day schools…" (1925 report)
                before = t[max(0, m.start() - 200):m.start()]
                fiscal = "retrospective" if re.search(r"five years ago|fiscal year 19\d\d\s*-|ten years ago", before, re.I) else ""
                # "enrolment … on June 30" is pupils present at year end, not the
                # statement's cumulative roll (1899: 1,830 against a roll of ~3,000)
                if re.search(r"on (June|March) 3[01]", before + t[m.start():m.end()], re.I):
                    fiscal = "point_in_time"
                rows.append(dict(year=year, klass=klass, n_schools=n_sch, enrolment=enrol, avg_attendance=att,
                                 fiscal_note=fiscal, page=Path(f).parent.name,
                                 sentence=t[max(0, m.start() - 60):m.end() + 40].strip()))
    df = pd.DataFrame(rows).drop_duplicates(["year", "klass", "enrolment"])
    return df


def compare(printed, tol):
    s = pd.read_parquet(ROOT / "registries/entities/schools.parquet")
    o = pd.read_parquet(ROOT / "registries/annotations/observations.parquet")
    r = o[(o.source_family == "school") & (o.series_id == "roll_total")].merge(
        s[["school_id", "type_class"]], left_on="entity_id", right_on="school_id")
    ext = r.groupby(["entity_id", "year", "type_class"]).value.max().reset_index()
    ext = ext.groupby(["year", "type_class"]).agg(n=("entity_id", "nunique"), roll=("value", "sum")).reset_index()
    bad = 0
    done = set()
    print(f"{'year':>5} {'class':<12} {'printed':>8} {'extracted':>10} {'ratio':>6}  {'schools p/e':>12}")
    for p in printed[printed.fiscal_note == ""].itertuples():
        kl = "residential" if p.klass in ("boarding", "industrial", "residential") else "day"
        e = ext[(ext.year == p.year) & (ext.type_class == kl)]
        if p.klass in ("boarding", "industrial"):
            # the register's class merges the two; compare only when the year's
            # other half is printed too
            other = printed[(printed.year == p.year) & (printed.klass.isin({"boarding", "industrial"} - {p.klass}))]
            if not len(other):
                continue
            pe = int(p.enrolment) + int(other.enrolment.iloc[0])
            a, b = p.n_schools, other.n_schools.iloc[0]
            ps = (int(a) if pd.notna(a) else 0) + (int(b) if pd.notna(b) else 0)
            label = "residential"
        else:
            pe, ps, label = int(p.enrolment), (int(p.n_schools) if pd.notna(p.n_schools) else None), p.klass
        if (p.year, label) in done:
            continue
        done.add((p.year, label))
        if not len(e):
            print(f"{p.year:>5} {label:<12} {pe:>8,} {'—':>10}")
            continue
        ratio = float(e.roll.iloc[0]) / pe
        flag = "" if abs(ratio - 1) <= tol else "  <-- outside tolerance"
        bad += bool(flag)
        print(f"{p.year:>5} {label:<12} {pe:>8,} {int(e.roll.iloc[0]):>10,} {ratio:>6.2f}  {ps or '—':>5}/{int(e.n.iloc[0]):<5}{flag}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tolerance", type=float, default=0.15)
    args = ap.parse_args()
    df = harvest()
    df.to_csv(OUT, index=False)
    print(f"printed totals harvested: {len(df)} → {OUT}")
    bad = compare(df, args.tolerance)
    if bad:
        print(f"\n{bad} class-years outside ±{args.tolerance:.0%}: do not promote the school family")
        sys.exit(1)
    print("\nall printed class-years within tolerance")


if __name__ == "__main__":
    main()
