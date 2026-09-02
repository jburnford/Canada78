#!/usr/bin/env python3
"""Carry the curated 1902-Schedule band grounding onto the census band registry.

    python3 build/merge_band_registries.py [--sim 0.86]

Two band registries exist and each holds half of what a band page needs:

  registries/entities/bands.parquet        236 bands from the 1902 Schedule,
                                           **180 with a hand-adjudicated
                                           Wikidata succession link** and 20
                                           with a "people" QID
  registries/entities/bands_census.parquet 3,377 identities from the annual
                                           census series — the ones the
                                           observations and attestations are
                                           keyed by

KG_BUILD_PLAN §1.1 makes the census registry canonical, so this moves the
curated grounding onto it rather than the other way round: nothing
hand-checked may be lost, and any curated band that fails to match is reported
rather than dropped.  The modern band-government links from
`band_modern.csv` are folded in at the same time, so a band page can show both
anchors (Wikidata item and ISC band number) beside its series.

Both links are *succeeded-by*, never sameAs.

Output: registries/entities/bands_census_grounded.parquet
        registries/crosswalks/band_registry_merge_review.csv
"""
import argparse
import collections
import csv
import re
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ENT = ROOT / "registries/entities"

PROV_POOL = {
    "MANITOBA": "Prairies/NWT", "SASKATCHEWAN": "Prairies/NWT", "ALBERTA": "Prairies/NWT",
    "NORTH-WEST TERRITORIES": "Prairies/NWT", "NORTHWEST TERRITORIES": "Prairies/NWT",
    "NORTH WEST TERRITORIES": "Prairies/NWT", "KEEWATIN": "Prairies/NWT",
    "MANITOBA AND THE NORTHWEST TERRITORIES": "Prairies/NWT",
    "MANITOBA AND THE NORTH-WEST TERRITORIES": "Prairies/NWT",
}
NOISE = re.compile(r"\b(band|bands|indians|indian|tribe|of|the|first nation|nation)\b", re.I)


def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s or ""))
                if not unicodedata.combining(c)).lower()
    # the parenthetical is the discriminator, not noise: "Nicola (Lower)" and
    # "Nicola (Upper)" are different bands, and stripping it made them tie at
    # 1.00 so the uniqueness rule threw both away
    s = NOISE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def variants(raw):
    """The printer syllabified names it did not know: "Nim-keesh" for Nimkeesh,
    "Opet-ches-aht" for Opetchisaht.  Closing the hyphens recovers the other
    spelling, so both readings compete."""
    out = {norm(raw)}
    if "-" in str(raw):
        out.add(norm(str(raw).replace("-", "")))
    return {v for v in out if v}


def best_sim(keys, other):
    return max((sim(k, other) for k in keys), default=0.0)


def sim(a, b):
    if not a or not b:
        return 0.0
    ga = collections.Counter(a[i:i + 2] for i in range(len(a) - 1))
    gb = collections.Counter(b[i:i + 2] for i in range(len(b) - 1))
    inter = sum((ga & gb).values())
    return 2 * inter / max(1, sum(ga.values()) + sum(gb.values()))


def pool(provs):
    """`bands.parquet` stores provinces as a numpy array, one band may span two."""
    if provs is None or isinstance(provs, float):
        return ""
    if isinstance(provs, str):
        provs = [provs]
    for p in list(provs):
        s = str(p).upper().strip()
        if s and s != "NONE":
            return PROV_POOL.get(s, s)
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", type=float, default=0.86,
                    help="name similarity for an unambiguous match")
    ap.add_argument("--sim-margin", type=float, default=0.75,
                    help="lower threshold, allowed only with a clear margin over the runner-up")
    args = ap.parse_args()

    cur = pd.read_parquet(ENT / "bands.parquet")
    cen = pd.read_parquet(ENT / "bands_census.parquet")
    print(f"curated 1902 bands: {len(cur):,} ({cur.succeeded_by_qid.notna().sum()} grounded) | "
          f"census bands: {len(cen):,}")

    cen["pool_"] = cen.province_pool.map(lambda p: PROV_POOL.get(str(p).upper(), str(p).upper()))
    cen["key_"] = cen.name.map(norm)
    cen["keys_"] = cen.name.map(variants)
    by_pool = collections.defaultdict(list)
    for r in cen.itertuples():
        by_pool[r.pool_].append(r)
    all_cen = list(cen.itertuples())

    link = {}          # census band_id -> curated row
    review = []
    matched = 0
    for c in cur.itertuples():
        keys = variants(c.name)
        p = pool(c.provinces)
        cands = by_pool.get(p) or all_cen
        scored = sorted(((max(best_sim(keys, k) for k in x.keys_), x)
                         for x in cands if x.keys_), key=lambda t: -t[0])
        if not scored:
            review.append(dict(queue="no_candidates", curated_id=c.band_id, name=c.name,
                               province=p, qid=c.succeeded_by_qid or "", detail="", score=0))
            continue
        best, second = scored[0], (scored[1] if len(scored) > 1 else (0.0, None))
        # a near-perfect tie is usually the census registry holding the same
        # band twice ("Bella Bella" / "Bella-Bella"); grounding both with the
        # one adjudicated QID is right, not a conflict
        tied = [x for s_, x in scored if s_ >= 0.98] if best[0] >= 0.98 else []
        if len(tied) > 1:
            matched += 1
            for x in tied:
                prev = link.get(x.band_id)
                if prev is None or best[0] > prev[0]:
                    link[x.band_id] = (best[0], c)
        # a clear winner below the plain threshold is still a match when it
        # stands well clear of the field — these are OCR variants of the same
        # name ("Skidgate"/"Skidegate", 0.80 against a 0.44 runner-up), and
        # dropping a hand-adjudicated QID over 6/100ths of a bigram score
        # loses more than it protects.  The score is kept on the row.
        elif (best[0] >= args.sim and second[0] < best[0] - 0.02) or \
             (best[0] >= args.sim_margin and second[0] <= best[0] - 0.10):
            matched += 1
            prev = link.get(best[1].band_id)
            # a curated band already claimed by a better match keeps it
            if prev is None or best[0] > prev[0]:
                link[best[1].band_id] = (best[0], c)
        else:
            review.append(dict(
                queue="ambiguous" if best[0] >= 0.6 else "no_match",
                curated_id=c.band_id, name=c.name, province=p,
                qid=c.succeeded_by_qid or "",
                detail="; ".join(f"{x.name} [{x.band_id}] ({s:.2f})" for s, x in scored[:3]),
                score=round(best[0], 3)))

    modern = ROOT / "registries/crosswalks/band_modern.csv"
    mod = pd.read_csv(modern) if modern.exists() else pd.DataFrame()
    mod_by_band = {r.band_id: r for r in mod.itertuples()} if len(mod) else {}

    out = []
    for r in cen.itertuples():
        score, c = link.get(r.band_id, (None, None))
        m = mod_by_band.get(r.band_id)
        out.append(dict(
            band_id=r.band_id, name=r.name, province_pool=r.province_pool,
            agency=r.agency, first_year=r.first_year, last_year=r.last_year,
            n_years=r.n_years, n_rows=r.n_rows,
            population_first=r.population_first, population_last=r.population_last,
            name_variants=r.name_variants,
            # curated 1902-Schedule grounding
            curated_band_id=(c.band_id if c is not None else ""),
            curated_match_score=(round(score, 3) if score else None),
            uri=(c.uri if c is not None else ""),
            succeeded_by_qid=(c.succeeded_by_qid if c is not None else "") or "",
            people_qid=(c.people_qid if c is not None else "") or "",
            wd_label=(c.wd_label if c is not None else "") or "",
            grounding_status=(c.grounding_status if c is not None else "") or "",
            grounding_confidence=(c.confidence if c is not None else "") or "",
            # modern band government (build/link_bands_modern.py)
            modern_band_name=(m.modern_name if m is not None else ""),
            modern_band_no=(str(m.band_no) if m is not None else ""),
            modern_band_qid=(m.qid if m is not None and isinstance(m.qid, str) else ""),
            modern_link_tier=(m.tier if m is not None else ""),
        ))
    df = pd.DataFrame(out)
    df.to_parquet(ENT / "bands_census_grounded.parquet", index=False)

    fields = ["queue", "curated_id", "name", "province", "qid", "detail", "score"]
    with open(ROOT / "registries/crosswalks/band_registry_merge_review.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(review, key=lambda r: -r["score"]))

    grounded = (df.succeeded_by_qid != "").sum()
    modernised = (df.modern_band_qid != "").sum()
    lost = sum(1 for r in review if r["qid"])
    print(f"curated bands matched to a census identity: {len(link):,} of {len(cur):,}")
    print(f"census bands carrying a curated Wikidata succession: {grounded:,}")
    print(f"census bands carrying a modern band government: {modernised:,}")
    print(f"census bands with either anchor: "
          f"{((df.succeeded_by_qid != '') | (df.modern_band_qid != '')).sum():,}")
    print(f"review: {len(review):,} "
          f"({lost} of them carry a hand-adjudicated QID that has not landed — check these)")
    print("\nsample:")
    print(df[df.succeeded_by_qid != ""].head(8)[
        ["band_id", "name", "province_pool", "succeeded_by_qid", "wd_label",
         "modern_band_name"]].to_string(index=False))


if __name__ == "__main__":
    main()
