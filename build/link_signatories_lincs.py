#!/usr/bin/env python3
"""Re-anchor the minted letter-signatory persons to LINCS agents (step 6).

    python3 build/link_signatories_lincs.py [--accept 0.92] [--margin 0.02]

`link_mentions.py` minted 412 persons from agency-report signatures before
LINCS became the person authority for DIA staff (KG_BUILD_PLAN §1.3).  The
signatories are the agents themselves -- exactly the population LINCS
resolved -- so most of them should carry a LINCS agent URI, not a minted
one.  This keeps every person_id (the wiki's mention anchors) and swaps the
URI where LINCS owns the identity, the same way officers rows carry LINCS
URIs.

Matching: build/person_names.py (do not rewrite), year-overlap gated the
same way link_officers_lincs.py gates its direct pass.

Outputs:
  registries/entities/persons_minted.parquet   updated in place (+lincs_agent)
  registries/crosswalks/signatories_lincs.csv  every decision
  review rows appended to the CSV with status below_gate/ambiguous
"""
import argparse
import collections
import csv
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))

from person_names import blocking_keys, parse, score  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accept", type=float, default=0.92)
    ap.add_argument("--margin", type=float, default=0.02)
    args = ap.parse_args()

    pm = pd.read_parquet(ROOT / "registries/entities/persons_minted.parquet")
    lincs = pd.read_parquet(ROOT / "registries/external/lincs_ia_activities_dedup.parquet")
    lincs["year"] = pd.to_datetime(lincs.begin, errors="coerce").dt.year
    agents = lincs.groupby("agent").agg(
        label=("agent_label", "first"),
        years=("year", lambda y: set(y.dropna().astype(int))),
    )
    lincs_parsed = {a: parse(r.label) for a, r in agents.iterrows()}
    block = collections.defaultdict(list)
    for a, p in lincs_parsed.items():
        for k in blocking_keys(p):
            block[k].append(a)

    rows = []
    for r in pm.itertuples():
        p = parse(r.name)
        span = set(range(int(r.first_year) - 2, int(r.last_year) + 3))
        cands = {}
        for k in blocking_keys(p):
            for a in block.get(k, ()):
                if a not in cands:
                    cands[a] = score(p, lincs_parsed[a])
        ranked = sorted(((s, a) for a, s in cands.items() if s > 0),
                        reverse=True)
        status, agent = "no_candidate", ""
        detail = "; ".join(f"{agents.loc[a].label} {s:.2f}" for s, a in ranked[:3])
        if ranked:
            s, a = ranked[0]
            overlap = bool(agents.loc[a].years & span)
            margin = s - ranked[1][0] if len(ranked) > 1 else 1.0
            if s >= args.accept and margin >= args.margin and overlap:
                status, agent = "linked", a
            elif s >= args.accept and margin >= args.margin:
                status = "no_year_overlap"
            elif s >= args.accept:
                status = "ambiguous"
            elif s >= 0.85:
                status = "below_gate"
        rows.append(dict(person_id=r.person_id, name=r.name,
                         first_year=r.first_year, last_year=r.last_year,
                         n_signatures=r.n_signatures, status=status,
                         lincs_agent=agent, candidates=detail))

    xw = pd.DataFrame(rows)
    xw.to_csv(ROOT / "registries/crosswalks/signatories_lincs.csv", index=False,
              quoting=csv.QUOTE_MINIMAL)

    linked = xw[xw.status == "linked"].set_index("person_id").lincs_agent
    pm["lincs_agent"] = pm.person_id.map(linked).fillna("")
    pm.loc[pm.lincs_agent != "", "uri"] = pm.lincs_agent
    pm.loc[pm.lincs_agent != "", "uri_source"] = "lincs"
    pm.to_parquet(ROOT / "registries/entities/persons_minted.parquet", index=False)

    print(xw.status.value_counts().to_string())
    print(f"\n{len(linked)}/{len(pm)} signatory persons now carry a LINCS URI; "
          f"crosswalk signatories_lincs.csv written")


if __name__ == "__main__":
    main()
