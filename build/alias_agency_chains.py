#!/usr/bin/env python3
"""Alias agency chains that are OCR or printer's variants of one another.

`build_agency_chains.py` collapses heading variants by exact canonical name,
so a one-off misprint founds a chain of its own: WILLIAMS LAKE AGENCY has
five chains ("William's Lake.", "William Lake", "Williams Lake,"),
COWICHAN three ("Cowichay", "Cowichain"), COUTCHEECHING four. Mentions and
letters attributed to the fragment never reach the real chain's page.

As with bands and persons, chain ids are never changed; this script writes
registries/crosswalks/agency_chain_canonical.csv (chain_id → canonical
chain_id) and the index redirects through it.

Rule (guarded — a wrong merge here conflates two agencies): same unit type;
names equal after squashing punctuation/apostrophes/spaces at similarity
≥ 0.90 (difflib ratio) or with equal consonant skeletons (≥ 0.85 when one
side is a single-year chain of at least 7 letters); no digit, ordinal or
compass token differs (so "4TH DIVISION" ≠ "1ST DIVISION", SOUTHEASTERN ≠
SOUTHWESTERN); and the fragment is attested in at most 3 report years or its
span is disjoint from the canonical's. The canonical is the chain with the
most attested years. Pairs failing only the span test go to
registries/crosswalks/agency_chain_canonical_review.csv.

    python3 build/alias_agency_chains.py
"""
import difflib
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REG = ROOT / "registries/entities/agency_chains.parquet"
OUT = ROOT / "registries/crosswalks/agency_chain_canonical.csv"
REVIEW = ROOT / "registries/crosswalks/agency_chain_canonical_review.csv"

UNIT = re.compile(r"\b(AGENCY|SUPERINTENDENCY|INSPECTORATE)\b")
GUARD = re.compile(r"\b(\d+(?:ST|ND|RD|TH)?|NORTH|SOUTH|EAST|WEST|NORTHERN|SOUTHERN|EASTERN|"
                   r"WESTERN|NORTHEASTERN|NORTHWESTERN|SOUTHEASTERN|SOUTHWESTERN|UPPER|LOWER|"
                   r"NEW|OLD|LITTLE|BIG|GRAND)\b")


def squash(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()
    s = UNIT.sub(" ", s)
    s = re.sub(r"\b(THE|OF|OR)\b", " ", s)
    return re.sub(r"[^A-Z0-9]+", "", s)


def skeleton(s):
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[AEIOUY]", "", squash(s)))


def guard_tokens(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()
    return sorted(GUARD.findall(re.sub(r"[^A-Z0-9 ]+", " ", s)))


def main():
    ch = pd.read_parquet(REG)
    info = {r.chain_id: r for r in ch.itertuples()}
    parent = {c: c for c in ch.chain_id}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    cands, review = [], []
    rows = list(ch.itertuples())
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a.unit_type != b.unit_type:
                continue
            sa, sb = squash(a.canonical), squash(b.canonical)
            if not sa or not sb or sa == sb:
                continue
            ratio = difflib.SequenceMatcher(None, sa, sb).ratio()
            one_off = min(a.n_years, b.n_years) <= 1 and ratio >= 0.85 and \
                min(len(sa), len(sb)) >= 7   # "Cowichay" 1903 inside Cowichan 1881–1915
            if ratio < 0.90 and skeleton(a.canonical) != skeleton(b.canonical) and not one_off:
                continue
            if guard_tokens(a.canonical) != guard_tokens(b.canonical):
                continue
            small, big = (a, b) if a.n_years <= b.n_years else (b, a)
            disjoint = small.last_year < big.first_year or big.last_year < small.first_year
            rec = dict(chain_id=small.chain_id, canonical=small.canonical, n_years=small.n_years,
                       span=f"{small.first_year}–{small.last_year}",
                       target=big.chain_id, target_canonical=big.canonical,
                       target_n_years=big.n_years, target_span=f"{big.first_year}–{big.last_year}",
                       ratio=round(ratio, 3))
            if small.n_years <= 3 or disjoint:
                cands.append(rec)
                parent[find(small.chain_id)] = find(big.chain_id)
            else:
                review.append(dict(rec, reason="both chains well attested with overlapping spans"))

    groups = defaultdict(list)
    for c in ch.chain_id:
        groups[find(c)].append(c)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        canon = max(members, key=lambda c: (info[c].n_years, -info[c].first_year))
        for m in members:
            if m == canon:
                continue
            r = info[m]
            out.append(dict(chain_id=m, canonical=r.canonical, unit_type=r.unit_type,
                            n_years=r.n_years, span=f"{r.first_year}–{r.last_year}",
                            canonical_chain_id=canon, canonical_name=info[canon].canonical,
                            rule="squash_ratio>=0.90|skeleton, guards, span"))
    out = pd.DataFrame(out).sort_values(["canonical_chain_id", "chain_id"])
    out.to_csv(OUT, index=False)
    pd.DataFrame(review).to_csv(REVIEW, index=False)
    print(f"{len(out)} chains aliased into {out.canonical_chain_id.nunique()} canonical chains → {OUT}")
    print(f"{len(review)} pairs for review → {REVIEW}")
    for cid, g in list(out.groupby("canonical_chain_id"))[:12]:
        print(f"  {info[cid].canonical}: " + " | ".join(f"{r.canonical} ({r.span})" for _, r in g.iterrows()))


if __name__ == "__main__":
    main()
