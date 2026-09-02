#!/usr/bin/env python3
"""Crosswalk the 1902 Schedule reserves to present-day reserves (KG_BUILD_PLAN §1.1).

    python3 build/link_reserves_modern.py [--sim 0.86]

Modern side: NRCan "Aboriginal Lands of Canada" (GeoBase AL) — every current
Indian Reserve with its official name, number and CLSS code
(`registries/external/nrcan_aboriginal_lands/indian_reserves_ca_2026-08-30.csv`).

Matching is deliberately conservative and tiered; a link is a *succeeded-by*
claim, never sameAs, and anything below the top tier goes to a review CSV:

  T1  province + reserve number + name similarity ≥ sim      (strongest)
  T2  province + reserve number, unique on both sides
  T3  province + exact normalised name, unique on both sides
  T4  province + name similarity ≥ sim, unique best match
  --  ambiguous / unmatched → review

Outputs: registries/crosswalks/reserve_modern.csv     accepted links (with tier)
         registries/crosswalks/reserve_modern_review.csv  everything to judge
"""
import argparse
import collections
import csv
import re
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

# 1902 Schedule province wording -> NRCan JUR1 codes it can correspond to
PROV_TO_JUR = {
    "NOVA SCOTIA": {"NS"}, "NEW BRUNSWICK": {"NB"}, "PRINCE EDWARD ISLAND": {"PE"},
    "QUEBEC": {"QC"}, "ONTARIO": {"ON"}, "BRITISH COLUMBIA": {"BC"},
    "YUKON DISTRICT": {"YT"},
    # in 1902 the prairies were still "Manitoba and the North-West Territories"
    "MANITOBA AND THE NORTHWEST TERRITORIES": {"MB", "SK", "AB", "NT", "NU"},
}
# "STAR BLANKET I.R. 83D", "CHIPEWYAN 201A", "SIX NATIONS INDIAN RESERVE NO. 40"
NUMBERED = re.compile(
    r"^(?P<name>.*?)\s+(?:(?:I\.?\s?R\.?|INDIAN\s+RESERVE|RESERVE|IR)\s*)?"
    r"(?:NO\.?\s*)?(?P<no>\d+[A-Z]*)\s*$", re.I)
TRAILING_TYPE = re.compile(r"\s+(?:I\.?\s?R\.?|INDIAN\s+RESERVE|RESERVE|SETTLEMENT)\.?\s*$", re.I)


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c))


def norm_name(s):
    s = strip_accents(s).lower()
    s = re.sub(r"\bno\.?\s*\d+[a-z]*\b", " ", s)
    s = re.sub(r"\b(indian reserve|reserve|i\.?r\.?|band|first nation|nation)\b", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_name_no(raw):
    """(name, reserve_number) from a modern official name."""
    s = strip_accents(raw).strip()
    m = NUMBERED.match(s)
    if m and m.group("name").strip():
        return TRAILING_TYPE.sub("", m.group("name")).strip(), m.group("no").upper()
    return TRAILING_TYPE.sub("", s).strip(), ""


def sim(a, b):
    ta, tb = set(a.split()), set(b.split())
    j = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    c = len(ga & gb) / max(1, min(len(ga), len(gb))) if ga and gb else 0.0
    return max(j, c)


def norm_no(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    m = re.match(r"\s*(\d+[A-Za-z]*)", str(v))
    return m.group(1).upper() if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", type=float, default=0.86)
    args = ap.parse_args()

    hist = pd.read_parquet(ROOT / "registries/entities/reserves_1902.parquet")
    hist = hist[hist.name.notna()].copy()
    hist["key"] = hist.name.map(norm_name)
    hist["rno"] = hist.reserve_no.map(norm_no)

    mod = pd.read_csv(ROOT / "registries/external/nrcan_aboriginal_lands/indian_reserves_ca_2026-08-30.csv")
    mod[["m_name", "m_no"]] = mod.NAME1.apply(lambda n: pd.Series(split_name_no(n)))
    mod["key"] = mod.m_name.map(norm_name)
    print(f"historical 1902 reserves: {len(hist):,} | modern NRCan reserves: {len(mod):,} "
          f"({(mod.m_no != '').sum():,} with a parsed number)")

    by_jur = collections.defaultdict(list)
    for row in mod.itertuples():
        by_jur[row.JUR1].append(row)

    links, review = [], []
    for h in hist.itertuples():
        jurs = PROV_TO_JUR.get(str(h.province).strip().upper(), set())
        cands = [m for j in jurs for m in by_jur.get(j, [])]
        if not cands:
            review.append(dict(queue="no_province_match", reserve_name=h.name,
                               province=h.province, reserve_no=h.rno, detail="", tier="", score=""))
            continue
        num = [m for m in cands if h.rno and m.m_no == h.rno]
        namesim = sorted(((sim(h.key, m.key), m) for m in cands if h.key and m.key),
                         key=lambda t: -t[0])
        best = namesim[0] if namesim else (0.0, None)

        tier = None
        chosen = None
        if num:
            good = [m for m in num if sim(h.key, m.key) >= args.sim]
            if len(good) == 1:
                tier, chosen, score = "T1", good[0], sim(h.key, good[0].key)
            elif len(num) == 1:
                tier, chosen, score = "T2", num[0], sim(h.key, num[0].key)
        if tier is None:
            exact = [m for m in cands if h.key and m.key == h.key]
            if len(exact) == 1:
                tier, chosen, score = "T3", exact[0], 1.0
            elif best[0] >= args.sim and sum(1 for s, _ in namesim if s >= args.sim) == 1:
                tier, chosen, score = "T4", best[1], best[0]

        if tier:
            links.append(dict(tier=tier, score=round(score, 3),
                              hist_name=h.name, hist_province=h.province, hist_reserve_no=h.rno,
                              hist_band=h.tribe_band, hist_acres=h.acres,
                              modern_name=chosen.NAME1, modern_no=chosen.m_no,
                              modern_jur=chosen.JUR1, clss_code=chosen.ALCODE,
                              clss_url=f"https://clss.nrcan-rncan.gc.ca/mb-nc/en/index.html?can={chosen.ALCODE}"))
        else:
            near = "; ".join(f"{m.NAME1} ({s:.2f})" for s, m in namesim[:3])
            review.append(dict(queue="ambiguous" if best[0] >= 0.6 else "no_match",
                               reserve_name=h.name, province=h.province, reserve_no=h.rno,
                               detail=near, tier="", score=round(best[0], 3)))

    ldf = pd.DataFrame(links)
    out = ROOT / "registries/crosswalks"
    ldf.to_csv(out / "reserve_modern.csv", index=False)
    with open(out / "reserve_modern_review.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["queue", "reserve_name", "province", "reserve_no",
                                          "detail", "tier", "score"])
        w.writeheader()
        w.writerows(review)
    print(f"\nlinked {len(ldf):,} of {len(hist):,} ({100*len(ldf)/len(hist):.0f}%)")
    print("by tier:", dict(ldf.tier.value_counts()) if len(ldf) else {})
    print("by province:", dict(ldf.hist_province.value_counts()) if len(ldf) else {})
    print("review:", dict(collections.Counter(r["queue"] for r in review)))
    if len(ldf):
        print("\nsample links:")
        print(ldf.head(8)[["tier", "score", "hist_name", "hist_reserve_no",
                           "modern_name", "modern_jur"]].to_string(index=False))


if __name__ == "__main__":
    main()
