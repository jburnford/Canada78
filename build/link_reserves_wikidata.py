#!/usr/bin/env python3
"""Attach Wikidata QIDs to the modern side of the reserve crosswalk (KG_BUILD_PLAN §3.3).

    python3 build/link_reserves_wikidata.py [--sim 0.86]

`build/link_reserves_modern.py` already carried each 1902-Schedule reserve to a
present-day NRCan "Aboriginal Lands" polygon (ALCODE / CLSS code).  This script
does the second hop, NRCan -> Wikidata, and then folds the result back onto the
historical rows, so a reserve page can carry a *succeeded-by* link to a modern
item with coordinates, GeoNames and a Statistics Canada census-subdivision code.

Wikidata side: `registries/external/wikidata/reserves_wd.csv`, a bulk SPARQL dump
of `wdt:P31/wdt:P279* wd:Q155239` (Indian reservation of Canada) carrying
P2887 reserve number (which on Wikidata holds the 5-digit CLSS code), P3012
StatCan geographic code, P1566 GeoNames, P131 and P625.

NRCan -> Wikidata tiers (a link is *succeeded-by*, never sameAs):

  W1  P2887 == ALCODE                                       (identifier match)
  W2  province + reserve number + name similarity >= sim, unique
  W3  province + exact normalised name, unique on both sides
  W4  province + name similarity >= sim, unique best match

Outputs: registries/crosswalks/reserve_wikidata.csv         accepted links
         registries/crosswalks/reserve_wikidata_review.csv  ambiguous / unmatched
"""
import argparse
import collections
import csv
import re
from pathlib import Path

import pandas as pd

from link_reserves_modern import norm_name, norm_no, sim, split_name_no  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

PROVINCE_JUR = {
    "British Columbia": "BC", "Alberta": "AB", "Saskatchewan": "SK",
    "Manitoba": "MB", "Ontario": "ON", "Quebec": "QC", "Québec": "QC",
    "New Brunswick": "NB", "Nova Scotia": "NS", "Prince Edward Island": "PE",
    "Newfoundland and Labrador": "NL", "Yukon": "YT",
    "Northwest Territories": "NT", "Nunavut": "NU",
}
# generous lat/lon boxes, used only to *infer* a province when P131 gives a
# county or a band government rather than a province — never to reject a match
PROV_BBOX = {  # jur: (lon_min, lon_max, lat_min, lat_max)
    "BC": (-139.1, -114.0, 48.2, 60.1), "AB": (-120.1, -109.9, 48.9, 60.1),
    "SK": (-110.1, -101.3, 48.9, 60.1), "MB": (-102.1, -88.9, 48.9, 60.1),
    "ON": (-95.2, -74.2, 41.6, 57.0), "QC": (-79.9, -56.9, 44.9, 62.7),
    "NB": (-69.1, -63.7, 44.5, 48.2), "NS": (-66.5, -59.6, 43.3, 47.1),
    "PE": (-64.5, -61.9, 45.9, 47.1), "NL": (-67.9, -52.5, 46.6, 60.5),
    "YT": (-141.1, -123.7, 59.9, 69.7), "NT": (-136.6, -101.9, 59.9, 78.9),
    "NU": (-121.0, -61.0, 59.9, 83.2),
}
POINT = re.compile(r"Point\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)")
QID = re.compile(r"^Q\d+$")


def pad_clss(v):
    """CLSS codes appear as 6065, '06065' and occasionally '06065 '."""
    m = re.match(r"\s*0*(\d+)", str(v))
    return m.group(1).zfill(5) if m else ""


def prov_from_coord(coord):
    m = POINT.match(str(coord))
    if not m:
        return ""
    lon, lat = float(m.group(1)), float(m.group(2))
    hits = [j for j, (x0, x1, y0, y1) in PROV_BBOX.items()
            if x0 <= lon <= x1 and y0 <= lat <= y1]
    return hits[0] if len(hits) == 1 else ""


def digits(v):
    """SPARQL CSV round-trips numeric ids as floats ('1215005.0')."""
    return re.sub(r"\.0*$", "", str(v).strip())


def load_wikidata(path):
    """One row per Wikidata item, with the multi-valued columns collapsed."""
    raw = pd.read_csv(path, dtype=str)
    raw["qid"] = raw.item.str.rsplit("/", n=1).str[-1]
    out = []
    for qid, g in raw.groupby("qid", sort=False):
        label = g.itemLabel.dropna()
        label = next((s for s in label if not QID.match(str(s))), "")
        admins = [a for a in g.adminLabel.dropna().unique()]
        jur = next((PROVINCE_JUR[a] for a in admins if a in PROVINCE_JUR), "")
        coord = next((c for c in g.coord.dropna()), "")
        if not jur:
            jur = prov_from_coord(coord)
        clss = {pad_clss(c) for c in g.clss.dropna()}
        name, no = split_name_no(label) if label else ("", "")
        out.append(dict(
            qid=qid, label=label, key=norm_name(name), no=norm_no(no), jur=jur,
            clss=sorted(c for c in clss if c),
            statcan=";".join(sorted({digits(s) for s in g.statcan.dropna()})),
            geonames=";".join(sorted({digits(s) for s in g.geonames.dropna()})),
            coord=coord,
            # P131 values that are band governments, not provinces/counties, are
            # a free reserve -> modern band signal for build/link_bands_modern.py
            admin=";".join(admins),
        ))
    return pd.DataFrame(out)


def match_nrcan_to_wd(mod, wd, threshold):
    """NRCan ALCODE -> Wikidata qid, with a tier and the runners-up."""
    by_clss = collections.defaultdict(list)
    by_jur = collections.defaultdict(list)
    for w in wd.itertuples():
        for c in w.clss:
            by_clss[c].append(w)
        if w.jur:
            by_jur[w.jur].append(w)

    chosen, review = {}, []
    for m in mod.itertuples():
        alcode = pad_clss(m.ALCODE)
        hit = by_clss.get(alcode, [])
        if len(hit) == 1:
            chosen[m.ALCODE] = ("W1", 1.0, hit[0])
            continue

        cands = by_jur.get(m.JUR1, [])
        namesim = sorted(((sim(m.key, w.key), w) for w in cands if m.key and w.key),
                         key=lambda t: -t[0])
        best = namesim[0] if namesim else (0.0, None)
        tier = None
        num = [w for w in cands if m.m_no and w.no == m.m_no]
        if num:
            good = [w for w in num if sim(m.key, w.key) >= threshold]
            if len(good) == 1:
                tier, pick, score = "W2", good[0], sim(m.key, good[0].key)
        if tier is None:
            exact = [w for w in cands if m.key and w.key == m.key]
            if len(exact) == 1:
                tier, pick, score = "W3", exact[0], 1.0
            elif best[0] >= threshold and sum(1 for s, _ in namesim if s >= threshold) == 1:
                tier, pick, score = "W4", best[1], best[0]

        if tier:
            chosen[m.ALCODE] = (tier, score, pick)
        elif len(hit) > 1 or best[0] >= 0.6:
            review.append(dict(queue="ambiguous", alcode=m.ALCODE, modern_name=m.NAME1,
                               jur=m.JUR1,
                               detail="; ".join(f"{w.label} [{w.qid}] ({s:.2f})"
                                                for s, w in namesim[:3]),
                               score=round(best[0], 3)))
    return chosen, review


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", type=float, default=0.86)
    args = ap.parse_args()

    wd = load_wikidata(ROOT / "registries/external/wikidata/reserves_wd.csv")
    mod = pd.read_csv(ROOT / "registries/external/nrcan_aboriginal_lands/"
                             "indian_reserves_ca_2026-08-30.csv")
    mod[["m_name", "m_no"]] = mod.NAME1.apply(lambda n: pd.Series(split_name_no(n)))
    mod["key"] = mod.m_name.map(norm_name)
    mod["m_no"] = mod.m_no.map(norm_no)
    print(f"Wikidata reserve items: {len(wd):,} "
          f"({(wd.clss.map(len) > 0).sum():,} with a CLSS code, "
          f"{(wd.jur != '').sum():,} placed in a province) | "
          f"NRCan reserves: {len(mod):,}")

    chosen, review = match_nrcan_to_wd(mod, wd, args.sim)
    print(f"NRCan -> Wikidata: {len(chosen):,} of {len(mod):,} "
          f"({100*len(chosen)/len(mod):.0f}%) "
          f"{dict(collections.Counter(t for t, _, _ in chosen.values()))}")

    # fold onto the historical rows
    hist = pd.read_csv(ROOT / "registries/crosswalks/reserve_modern.csv")
    links, hist_review = [], []
    for h in hist.itertuples():
        got = chosen.get(h.clss_code) or chosen.get(pad_clss(h.clss_code))
        if not got:
            hist_review.append(dict(queue="no_wikidata_item", alcode=h.clss_code,
                                    modern_name=h.modern_name, jur=h.modern_jur,
                                    detail=f"hist: {h.hist_name} ({h.hist_province})",
                                    score=""))
            continue
        tier, score, w = got
        links.append(dict(
            wd_tier=tier, wd_score=round(score, 3), hist_tier=h.tier,
            hist_name=h.hist_name, hist_province=h.hist_province,
            hist_reserve_no=h.hist_reserve_no, hist_band=h.hist_band,
            modern_name=h.modern_name, modern_jur=h.modern_jur, clss_code=h.clss_code,
            qid=w.qid, wd_label=w.label, statcan_code=w.statcan,
            geonames_id=w.geonames, coord=w.coord, wd_admin=w.admin))

    ldf = pd.DataFrame(links)
    out = ROOT / "registries/crosswalks"
    ldf.to_csv(out / "reserve_wikidata.csv", index=False)
    fields = ["queue", "alcode", "modern_name", "jur", "detail", "score"]
    with open(out / "reserve_wikidata_review.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(review + hist_review)

    print(f"\nhistorical reserves with a Wikidata successor: {len(ldf):,} of {len(hist):,} "
          f"({100*len(ldf)/max(1,len(hist)):.0f}% of the modern-linked ones)")
    if len(ldf):
        print("by tier:", dict(ldf.wd_tier.value_counts()))
        print(f"carrying a StatCan CSD code: {(ldf.statcan_code != '').sum():,} | "
              f"GeoNames: {(ldf.geonames_id != '').sum():,} | "
              f"coordinates: {(ldf.coord != '').sum():,}")
        print("\nsample links:")
        print(ldf.head(8)[["wd_tier", "hist_name", "hist_province", "modern_name",
                           "qid", "wd_label"]].to_string(index=False))
    print("review:", dict(collections.Counter(r["queue"] for r in review + hist_review)))


if __name__ == "__main__":
    main()
