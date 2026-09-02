#!/usr/bin/env python3
"""Historical band -> present-day First Nation band government (KG_BUILD_PLAN §1.1, step 3).

    python3 build/link_bands_modern.py [--sim 0.86]

The historical side is the census-derived band registry
(`registries/entities/bands_census.parquet`, 3,377 identities 1881-1929).
The modern side is assembled from three sources that share the ISC band number:

  * ISC "First Nations Location"  — 638 bands: band number, name, coordinates
  * Wikidata `wdt:P31/P279* Q2882257` (First Nation band) — QID + P2865 band number
  * StatCan Aboriginal Population Profile band <-> census-subdivision table —
    which reserves (CSDs) each modern band holds today

The link is *succeeded-by*, never sameAs: an 1881 "Beardy's band" is not the
same entity as the modern First Nation, it is its documented predecessor.

Tiers, strongest first:

  M1  reserve chain: an 1902-Schedule reserve held by this band links (via
      `reserve_wikidata.csv` -> StatCan CSD code) to a modern band, and the
      band names agree at least loosely.  Geography, not spelling, carries it.
  M2  province + name similarity >= sim, unique best candidate
  M3  province + name similarity >= sim - 0.06, unique, and no rival within 0.05
  M4  no province known (recapitulation / district pools): national name match
      >= 0.92, unique

Outputs: registries/crosswalks/band_modern.csv          accepted links
         registries/crosswalks/band_modern_review.csv   ambiguous / unmatched
"""
import argparse
import collections
import csv
import re
from pathlib import Path

import pandas as pd

from link_reserves_modern import norm_name
from link_reserves_wikidata import PROV_BBOX, PROVINCE_JUR, digits

ROOT = Path(__file__).resolve().parent.parent

# census "province" pools (already normalised by mint_bands_from_census.py) to
# the modern jurisdictions their bands can possibly sit in today
POOL_TO_JUR = {
    "british columbia": {"BC"}, "b.c": {"BC"}, "british columbia inspectorates": {"BC"},
    "north-west coast": {"BC"},
    "ontario": {"ON"}, "treaty no. 9": {"ON"},
    "quebec": {"QC"}, "ungava": {"QC"}, "ungava (quebec)": {"QC"},
    "labrador, canadian interior": {"QC", "NL"},
    "nova scotia": {"NS"}, "new brunswick": {"NB"}, "prince edward island": {"PE"},
    "manitoba": {"MB"}, "saskatchewan inspectorates": {"SK"},
    "isle a la crosse": {"SK"}, "isle à la crosse": {"SK"},
    "yukon": {"YT"}, "yukon territory": {"YT"},
    "treaty no. 8": {"AB", "BC", "SK", "NT"},
    "treaty no. 10": {"SK", "AB"},
    "treaty no. 11": {"NT"},
    "prairies/nwt": {"MB", "SK", "AB", "NT", "NU"},
    "manitoba and the north-west territories": {"MB", "SK", "AB", "NT", "NU"},
    "manitoba and the northwest territories": {"MB", "SK", "AB", "NT", "NU"},
    "rupert's land": {"MB", "SK", "AB", "NT", "NU"},
    "eastern rupert's land": {"MB", "ON", "QC"},
    "northwestern territories": {"NT", "NU", "SK", "AB"},
    "northwest territories and other unorganized distri8": {"NT", "NU"},
}
for _pool in ("athabasca and m'kenzie rivers", "eastern athabasca", "mackenzie river",
              "mackenzie", "mckenzie", "lower mackenzie", "upper mackenzie",
              "great slave lake", "peace river", "rivière aux liards",
              "nelson and churchill rivers", "franklin", "arctic coast, esquimaux",
              "franklin district (formerly arctic coast)"):
    POOL_TO_JUR[_pool] = {"NT", "NU", "AB", "SK"}

# name noise on the modern side: every band is a "First Nation" / "Band" / "Nation"
MODERN_SUFFIX = re.compile(
    r"\b(first nation(s)?|indian band|band council|band|nation|tribe|tribal council|"
    r"government|the)\b", re.I)


PAREN = re.compile(r"\(([^)]*)\)")


def norm_band(s):
    return norm_name(MODERN_SUFFIX.sub(" ", str(s)))


def band_variants(raw):
    """"Spahamin (Douglas Lake)" is two names, not one long one.

    The census printer routinely gave the departmental name with the local or
    English name in brackets; scoring the concatenation lets a modern band match
    on the wrong half, so each half competes separately.
    """
    raw = str(raw)
    parts = [PAREN.sub(" ", raw)] + PAREN.findall(raw)
    out = set()
    for p in parts:
        out.add(norm_band(p))
        # the census printer syllabified names it did not know: "Qual-i-cum",
        # "Mah-ma-lil-le-kullah".  Closing the hyphens recovers the modern
        # spelling, so both readings compete.
        if "-" in p:
            out.add(norm_band(p.replace("-", "")))
    return {v for v in out if v}


def sim_band(a, b):
    """Symmetric name similarity, unlike the containment score used for reserves.

    Reserve names are long and officially formatted, so containment is safe
    there.  Band names are short: "halalt" is fully contained in the bigrams of
    "haltkum adams lake" and scored 1.00 under the reserve metric.  Dice over
    bigrams plus a length-ratio guard removes that whole class of false links.
    """
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb)
    ga = collections.Counter(a[i:i + 2] for i in range(len(a) - 1))
    gb = collections.Counter(b[i:i + 2] for i in range(len(b) - 1))
    inter = sum((ga & gb).values())
    dice = 2 * inter / max(1, sum(ga.values()) + sum(gb.values()))
    ratio = min(len(a), len(b)) / max(len(a), len(b))
    return max(jac, dice) * (1.0 if ratio >= 0.6 else ratio / 0.6)


def prov_from_coord(lon, lat):
    try:
        lon, lat = float(lon), float(lat)
    except (TypeError, ValueError):
        return ""
    hits = [j for j, (x0, x1, y0, y1) in PROV_BBOX.items()
            if x0 <= lon <= x1 and y0 <= lat <= y1]
    return hits[0] if len(hits) == 1 else ""


def load_modern_bands():
    """ISC bands, enriched with Wikidata QIDs and the reserves they hold today."""
    isc = pd.read_csv(ROOT / "registries/external/isc/first_nations_location_2026-08-30.csv")
    isc.columns = [c.strip().lstrip("﻿") for c in isc.columns]
    isc["band_no"] = isc.BAND_NUMBER.map(lambda v: digits(v))
    isc["jur"] = [prov_from_coord(lo, la) for lo, la in zip(isc.LONGITUDE, isc.LATITUDE)]
    isc["key"] = isc.BAND_NAME.map(norm_band)

    wd = pd.read_csv(ROOT / "registries/external/wikidata/bands_wd.csv", dtype=str)
    wd["qid"] = wd.item.str.rsplit("/", n=1).str[-1]
    wd["band_no"] = wd.bandno.map(lambda v: digits(v) if pd.notna(v) else "")
    by_no, extra = {}, []
    for qid, g in wd.groupby("qid", sort=False):
        label = next((s for s in g.itemLabel.dropna() if not re.match(r"^Q\d+$", s)), "")
        jur = next((PROVINCE_JUR[a] for a in g.adminLabel.dropna() if a in PROVINCE_JUR), "")
        nos = [n for n in g.band_no.unique() if n]
        if nos:
            by_no.setdefault(nos[0], (qid, label))
        elif label:
            extra.append(dict(band_no="", name=label, key=norm_band(label), jur=jur, qid=qid))

    sc = pd.read_csv(ROOT / "registries/external/statcan/band_csd_2016.csv", dtype=str)
    csd_to_band = {}
    band_csds = collections.defaultdict(list)
    for r in sc.itertuples():
        csd_to_band[str(r.csd_code)] = r.band_name
        band_csds[norm_band(r.band_name)].append(str(r.csd_code))

    rows = []
    for r in isc.itertuples():
        qid, label = by_no.get(r.band_no, ("", ""))
        rows.append(dict(band_no=r.band_no, name=r.BAND_NAME, key=r.key, jur=r.jur,
                         qid=qid, wd_label=label,
                         csds=";".join(band_csds.get(r.key, []))))
    known = {r["key"] for r in rows}
    rows += [e | dict(wd_label=e["name"], csds="") for e in extra if e["key"] not in known]
    return pd.DataFrame(rows), csd_to_band


def reserve_chain_evidence(csd_to_band):
    """1902-Schedule band name -> modern band names, evidenced by reserve geography."""
    path = ROOT / "registries/crosswalks/reserve_wikidata.csv"
    if not path.exists():
        return {}
    rw = pd.read_csv(path, dtype=str).fillna("")
    ev = collections.defaultdict(collections.Counter)
    for r in rw.itertuples():
        if not r.hist_band:
            continue
        names = {csd_to_band[c] for c in r.statcan_code.split(";") if c in csd_to_band}
        # P131 on the reserve item is often the band government that holds it
        names |= {a for a in r.wd_admin.split(";")
                  if re.search(r"First Nation|Band|Nation|Tribe", a, re.I)}
        for n in names:
            ev[norm_band(r.hist_band)][norm_band(n)] += 1
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", type=float, default=0.86)
    args = ap.parse_args()

    modern, csd_to_band = load_modern_bands()
    hist = pd.read_parquet(ROOT / "registries/entities/bands_census.parquet")
    evidence = reserve_chain_evidence(csd_to_band)
    print(f"modern bands: {len(modern):,} "
          f"({(modern.qid != '').sum():,} with a Wikidata QID, "
          f"{(modern.jur != '').sum():,} placed in a province, "
          f"{(modern.csds != '').sum():,} with StatCan reserves) | "
          f"historical bands: {len(hist):,} | "
          f"1902 band names with reserve-chain evidence: {len(evidence):,}")

    by_jur = collections.defaultdict(list)
    for m in modern.itertuples():
        by_jur[m.jur].append(m)
    all_modern = list(modern.itertuples())

    links, review = [], []
    for h in hist.itertuples():
        jurs = POOL_TO_JUR.get(str(h.province_pool).strip().lower(), set())
        cands = [m for j in jurs for m in by_jur.get(j, [])] if jurs else all_modern
        variants = set()
        for v in str(h.name_variants).split("|"):
            variants |= band_variants(v)
        variants |= band_variants(h.name)
        if not variants or not cands:
            review.append(dict(queue="no_candidates", band_id=h.band_id, name=h.name,
                               province=h.province_pool, detail="", tier="", score=""))
            continue

        scored = sorted(((max(sim_band(v, m.key) for v in variants), m) for m in cands),
                        key=lambda t: -t[0])
        best, second = scored[0], (scored[1] if len(scored) > 1 else (0.0, None))

        tier = pick = None
        chain = evidence.get(norm_band(h.name), {})
        if chain:
            hits = [(s, m) for s, m in scored if m.key in chain]
            if hits and hits[0][0] >= 0.5:
                tier, pick, score = "M1", hits[0][1], hits[0][0]
        if tier is None and jurs:
            if best[0] >= args.sim and second[0] < best[0] - 0.02:
                tier, pick, score = "M2", best[1], best[0]
            elif best[0] >= args.sim - 0.06 and second[0] < best[0] - 0.05:
                tier, pick, score = "M3", best[1], best[0]
        if tier is None and not jurs and best[0] >= 0.92 and second[0] < best[0] - 0.05:
            tier, pick, score = "M4", best[1], best[0]

        if tier:
            links.append(dict(tier=tier, score=round(score, 3), band_id=h.band_id,
                              hist_name=h.name, province_pool=h.province_pool,
                              agency=h.agency, first_year=h.first_year,
                              last_year=h.last_year,
                              modern_name=pick.name, band_no=pick.band_no,
                              modern_jur=pick.jur, qid=pick.qid,
                              wd_label=pick.wd_label, statcan_csds=pick.csds))
        else:
            review.append(dict(
                queue="ambiguous" if best[0] >= 0.6 else "no_match",
                band_id=h.band_id, name=h.name, province=h.province_pool,
                detail="; ".join(f"{m.name} [{m.band_no}] ({s:.2f})" for s, m in scored[:3]),
                tier="", score=round(best[0], 3)))

    ldf = pd.DataFrame(links)
    out = ROOT / "registries/crosswalks"
    ldf.to_csv(out / "band_modern.csv", index=False)
    fields = ["queue", "band_id", "name", "province", "detail", "tier", "score"]
    with open(out / "band_modern_review.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(review)

    print(f"\nlinked {len(ldf):,} of {len(hist):,} ({100*len(ldf)/len(hist):.0f}%)")
    if len(ldf):
        print("by tier:", dict(ldf.tier.value_counts()))
        print(f"with a Wikidata QID: {(ldf.qid != '').sum():,}")
        print(f"distinct modern bands reached: {ldf.modern_name.nunique():,} "
              f"of {len(modern):,}")
        print("\nsample links:")
        print(ldf.head(10)[["tier", "score", "hist_name", "province_pool",
                            "modern_name", "band_no", "qid"]].to_string(index=False))
    print("review:", dict(collections.Counter(r["queue"] for r in review)))


if __name__ == "__main__":
    main()
