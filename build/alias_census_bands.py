#!/usr/bin/env python3
"""Alias census-band identities that the minter split for want of a province.

`build/mint_bands_from_census.py` keys a band on (name, agency, province
pool) and pools names within a province so agency transfers do not
fragment identity. Where the printed row carried no province (219
identities have a null pool) or the agency heading was swallowed into the
name, the same band was minted twice or three times:

    band_c01523  Pi-a-pot   (pool None, Muscowpetung, 1886)
    band_c01540  Piapot     (pool None, Muscowpetung, 1893)
    band_c02877  Piapot     (Prairies/NWT, Qu'Appelle Agency, 1886–1929)

    band_c01825  Six Nations on Grand River       (Ontario, 1903–1909)
    band_c01842  Six Nations on the Grand River   (Ontario, 1881–1902, 1910–1929)

Re-minting would renumber every band_c id that observations, crosswalks and
trust-fund links key on, so identities are *aliased* instead, exactly as
persons are unified on the LINCS URI: this script writes
registries/crosswalks/band_canonical.csv (band_id → canonical_band_id) and
the index/wiki resolve through it. Ids never change.

Rule: two identities merge when a name variant of one equals a name variant
of the other after folding (case, accents, hyphens, apostrophes, OCR "tlie",
the/of/on/at wrappers, a trailing "band"/"indians"), their province pools are
equal or one is null, and either their attested year spans do not overlap or
(v2, 2026-09-02) the overlap never conflicts: no shared year where both print
a different population, agency strings equal or one blank/continuation, and
neither name a province or recapitulation line. Overlaps that do conflict —
a real second band, or one identity that swallowed another — go to
registries/crosswalks/band_canonical_review.csv.

    python3 build/alias_census_bands.py
"""
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REG = ROOT / "registries/entities/bands_census_grounded.parquet"
OUT = ROOT / "registries/crosswalks/band_canonical.csv"
REVIEW = ROOT / "registries/crosswalks/band_canonical_review.csv"

OCR = {"tlie": "the", "tbe": "the", "aud": "and"}
DROP = {"the", "of", "on", "at", "a", "band", "bands", "indians", "indian", "tribe",
        "reserve", "res"}


def fold(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)              # "(No. 2)" / "(Treaty 4)" annotations
    s = re.sub(r"[^a-z0-9 ]+", " ", s.replace("-", "").replace("'", ""))
    toks = [OCR.get(t, t) for t in s.split()]
    toks = [t for t in toks if t not in DROP]
    return "".join(toks)


def variants(row):
    vs = [row.name]
    nv = row.name_variants
    if nv is None:
        pass
    elif isinstance(nv, str):
        vs += nv.split("|")
    else:
        vs += list(nv)
    keys = {fold(v) for v in vs if v and str(v) != "nan"}
    return {k for k in keys if len(k) >= 4}


NOT_A_BAND = re.compile(r"^(british columbia|ontario|quebec|nova scotia|new brunswick|manitoba|"
                        r"saskatchewan|alberta|yukon( territory)?|prince edward island|north ?west "
                        r"territories|total|recapitulation|grand total)\b", re.I)


def agency_key(a):
    a = str(a or "")
    if not a.strip() or re.search(r"^con\.?$|recapitulation", a, re.I):
        return None
    a = unicodedata.normalize("NFKD", a).encode("ascii", "ignore").decode().lower()
    a = re.sub(r"\b(agency|ag'cy|superintendency|inspectorate|indian|treaty no \d+|con)\b", " ", a)
    return re.sub(r"[^a-z0-9]+", "", a.replace("'", "")) or None


def main():
    b = pd.read_parquet(REG)
    # population by (band, year) — the conflict test for overlapping spans
    obs = pd.read_parquet(ROOT / "registries/annotations/observations.parquet")
    obs = obs[(obs.entity_type == "band") & (obs.series_id == "population")]
    pop = {(i, int(y)): v for i, y, v in zip(obs.entity_id, obs.year, obs.value)}

    def compatible_overlap(ra, rc):
        """Same-name identities whose spans overlap merge only when they never
        disagree: no year where both print a population and the figures
        differ, agency strings equal (or one blank / a continuation line),
        and neither is a province or recapitulation line minted as a band."""
        if NOT_A_BAND.search(str(ra["name"])) or NOT_A_BAND.search(str(rc["name"])):
            return False, "province/recap line"
        ka, kc = agency_key(ra.agency), agency_key(rc.agency)
        if ka and kc and ka != kc and not (ka in kc or kc in ka):
            return False, f"different agencies ({ra.agency} / {rc.agency})"
        ys = range(max(ra.first_year, rc.first_year), min(ra.last_year, rc.last_year) + 1)
        shared = [(pop.get((ra.name, y)), pop.get((rc.name, y))) for y in ys]   # .name = band_id label
        shared = [(x, z) for x, z in shared if x is not None and z is not None]
        if any(abs(x - z) >= 1 for x, z in shared):
            return False, "population differs in a shared year"
        return True, ("identical population in shared years" if shared else "no conflicting population")

    keys = {r.band_id: variants(r) for r in b.itertuples()}
    by_key = defaultdict(set)
    for bid, ks in keys.items():
        for k in ks:
            by_key[k].add(bid)
    info = b.set_index("band_id")
    parent = {bid: bid for bid in b.band_id}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    merges, review = [], []
    for k, ids in by_key.items():
        if len(ids) < 2:
            continue
        ids = sorted(ids)
        for i, a in enumerate(ids):
            for c in ids[i + 1:]:
                ra, rc = info.loc[a], info.loc[c]
                pa, pc = ra.province_pool, rc.province_pool
                if pa and pc and pa != pc:
                    continue
                overlap = not (ra.last_year < rc.first_year or rc.last_year < ra.first_year)
                rec = dict(band_id=a, other_id=c, key=k, name_a=ra["name"], name_b=rc["name"],
                           pool_a=pa, pool_b=pc, years_a=f"{ra.first_year}–{ra.last_year}",
                           years_b=f"{rc.first_year}–{rc.last_year}",
                           agency_a=ra.agency, agency_b=rc.agency)
                if overlap:
                    ok, why = compatible_overlap(ra, rc)
                    if not ok:
                        review.append(dict(rec, reason=f"spans overlap; {why}"))
                        continue
                    rec["rule"] = f"overlap: {why}"
                parent[find(a)] = find(c)
                merges.append(rec)

    rule_of = {}
    for m_ in merges:
        if m_.get("rule"):
            rule_of[m_["band_id"]] = m_["rule"]
            rule_of[m_["other_id"]] = m_["rule"]
    groups = defaultdict(list)
    for bid in b.band_id:
        groups[find(bid)].append(bid)
    rows = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # union-find is transitive; the span test was pairwise. A group whose
        # members overlap through a third identity is not a clean split.
        # union-find is transitive: a stray variant can pull an unrelated band
        # into the group. Keep the members that are compatible with the
        # best-attested identity and with each other; send the rest to review.
        canon = max(members, key=lambda x: (info.loc[x].n_rows, -info.loc[x].first_year))
        kept = [canon]
        for m in sorted((x for x in members if x != canon), key=lambda x: -info.loc[x].n_rows):
            rm = info.loc[m]
            ok, why = True, ""
            for k_ in kept:
                rk = info.loc[k_]
                overlap = not (rm.last_year < rk.first_year or rk.last_year < rm.first_year)
                if overlap:
                    ok, why = compatible_overlap(rk, rm)
                    if not ok:
                        break
            if ok:
                kept.append(m)
            else:
                rc = info.loc[canon]
                review.append(dict(band_id=m, other_id=canon, key="", name_a=rm["name"], name_b=rc["name"],
                                   pool_a=rm.province_pool, pool_b=rc.province_pool,
                                   years_a=f"{rm.first_year}–{rm.last_year}",
                                   years_b=f"{rc.first_year}–{rc.last_year}",
                                   agency_a=rm.agency, agency_b=rc.agency,
                                   reason=f"transitive group member conflicts; {why}"))
        members = kept
        if len(members) < 2:
            continue
        for m in members:
            r = info.loc[m]
            rows.append(dict(band_id=m, canonical_band_id=canon, name=r["name"],
                             province_pool=r.province_pool, agency=r.agency,
                             first_year=r.first_year, last_year=r.last_year,
                             n_rows=r.n_rows, rule=rule_of.get(m, "variant_key+pool+disjoint_years")))
    out = pd.DataFrame(rows).sort_values(["canonical_band_id", "first_year"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    pd.DataFrame(review).to_csv(REVIEW, index=False)
    n_groups = out.canonical_band_id.nunique() if len(out) else 0
    print(f"{len(out)} identities in {n_groups} groups → {OUT}")
    print(f"{len(review)} same-name overlapping pairs → {REVIEW}")
    for cid, g in list(out.groupby("canonical_band_id"))[:8]:
        print(f"  {cid}: " + " | ".join(f"{r.band_id} {r['name']} {r.first_year}–{r.last_year}"
                                       for _, r in g.iterrows()))


if __name__ == "__main__":
    main()
