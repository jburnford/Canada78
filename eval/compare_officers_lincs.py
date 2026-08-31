#!/usr/bin/env python3
"""Acceptance check for the officers -> LINCS person links (KG_BUILD_PLAN §1.3, step 6).

    python3 eval/compare_officers_lincs.py [--years 1889 1890 ...] [--sample N]

`compare_officers_hoy.py` asks whether we found as many *names* as Ben Hoy did.
This asks the harder question: do we land on the same *people*.

Both sides reach the LINCS agent URIs, but by independent routes — Hoy's
human transcription on one side, our Qwen extraction of the printed return on
the other — so agreement is evidence about the extraction and the linker
together, not just about the name matcher they share.

Per year it prints, over LINCS agent URIs:

    ours    agents our Return A rows link to
    hoy     agents Hoy's rows for that year link to
    both    the intersection
    prec    both / ours   — an agent we claim that Hoy does not is a suspect link
    rec     both / hoy    — an agent Hoy has that we miss is a coverage gap

A low `prec` is the number to chase: it means we attached a person to a row
they do not belong to. `rec` tracks the same page-span coverage that
`compare_officers_hoy.py` measures, so the two should move together.
"""
import argparse
import collections
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))

from person_names import blocking_keys, parse, score  # noqa: E402

ATTEST = ROOT / "registries/annotations/officer_attestations.parquet"
HOY = ROOT / "registries/external/hoy_building_borders/dia_employees_person_level_1875-1916.csv"
LINCS = ROOT / "registries/external/lincs_ia_activities_dedup.parquet"


def hoy_agents_by_year(accept=0.90, margin=0.015):
    """Hoy's rows -> LINCS agents, with the same matcher the linker uses."""
    li = pd.read_parquet(LINCS)
    li["year"] = pd.to_datetime(li.begin, errors="coerce").dt.year
    parsed, buckets = {}, collections.defaultdict(set)
    for lb, y, ag in zip(li.agent_label, li.year, li.agent):
        if pd.isna(y):
            continue
        if ag not in parsed:
            parsed[ag] = parse(lb)
        for k in blocking_keys(parsed[ag]):
            buckets[(k, int(y))].add(ag)

    h = pd.read_csv(HOY)
    h["year"] = h.Date.astype("Int64")
    out = collections.defaultdict(set)
    for nm, year in zip(h.Name, h.year):
        if pd.isna(year):
            continue
        pn = parse(nm)
        if not pn[0]:
            continue
        pool = set()
        for k in blocking_keys(pn):
            pool |= buckets.get((k, int(year)), set())
        cands = sorted(((score(pn, parsed[a]), a) for a in pool), key=lambda t: -t[0])
        cands = [c for c in cands if c[0] > 0]
        if not cands:
            continue
        lead = cands[0][0] - (cands[1][0] if len(cands) > 1 else 0.0)
        if cands[0][0] >= accept and lead >= margin:
            out[int(year)].add(cands[0][1])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="*", type=int)
    ap.add_argument("--sample", type=int, default=0,
                    help="print N example agents we claim and Hoy does not")
    args = ap.parse_args()

    a = pd.read_parquet(ATTEST)
    a = a[a.lincs_agent.notna()]
    ours = a.groupby("year").lincs_agent.agg(set).to_dict()
    theirs = hoy_agents_by_year()

    years = args.years or sorted(ours)
    print(f"{'year':>5} {'ours':>6} {'hoy':>6} {'both':>6} {'prec':>6} {'rec':>6}")
    tot_o = tot_h = tot_b = 0
    for y in years:
        o, t = ours.get(y, set()), theirs.get(y, set())
        if not o and not t:
            continue
        b = o & t
        tot_o += len(o); tot_h += len(t); tot_b += len(b)
        prec = len(b) / len(o) if o else float("nan")
        rec = len(b) / len(t) if t else float("nan")
        flag = "  <-- low prec" if prec < 0.9 else ("  <-- low rec" if rec < 0.85 else "")
        print(f"{y:>5} {len(o):>6} {len(t):>6} {len(b):>6} {prec:>6.2f} {rec:>6.2f}{flag}")
    print(f"{'all':>5} {tot_o:>6} {tot_h:>6} {tot_b:>6} "
          f"{tot_b/tot_o:>6.2f} {tot_b/tot_h:>6.2f}")

    if args.sample:
        lab = pd.read_parquet(LINCS).drop_duplicates("agent").set_index("agent").agent_label
        print(f"\nagents we claim that Hoy's rows do not reach (first {args.sample}):")
        shown = 0
        for y in years:
            for ag in sorted(ours.get(y, set()) - theirs.get(y, set())):
                nm = sorted(set(a[(a.year == y) & (a.lincs_agent == ag)].name_as_printed))
                print(f"  {y} {str(lab.get(ag))[:26]:26} <- {nm}")
                shown += 1
                if shown >= args.sample:
                    return


if __name__ == "__main__":
    main()
