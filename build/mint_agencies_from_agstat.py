#!/usr/bin/env python3
"""Agency identities from the Agricultural & Industrial Statistics series.

    python3 build/mint_agencies_from_agstat.py [--sim 0.86]

The agency registry built from the annual reports' *letters* covers only the
agencies whose superintendent filed a narrative report — 280 chains.  The
agricultural tables are keyed by agency and name many more: the Nova Scotia
county agencies (Annapolis, Hants, Pictou, Inverness …), Moravian, Bécancour,
Mud Lake, The Pas.  This mints an identity for every agency the *series*
attests, exactly as `mint_bands_from_census.py` does for bands, and links it to
an existing chain wherever the name matches.

Section (province) rows and grand totals are not agencies and are dropped.

Outputs: registries/entities/agencies_agstat.parquet     one row per identity
         registries/annotations/agency_attestations.parquet  one row per year
         registries/crosswalks/agency_agstat_review.csv  near-miss chain links
"""
import argparse
import collections
import csv
import glob
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd

from mint_bands_from_census import classify_header

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval/results"

UNIT = re.compile(r"\b(agency|agencies|superintendency|inspectorate|district|division)\b", re.I)
# a row label that is really the province banner the section is printed under
PROVINCES = {
    "ontario", "quebec", "nova scotia", "new brunswick", "prince edward island",
    "manitoba", "saskatchewan", "alberta", "british columbia", "yukon",
    "north west territories", "northwest territories", "keewatin", "athabasca",
    "assiniboia", "manitoba and the north west territories", "grand total",
    "recapitulation", "total", "totals", "summary",
}


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", str(s))
                   if not unicodedata.combining(c))


def norm(s, keep_unit=False):
    s = strip_accents(s).lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(continued|concluded|cont|contd)\b", " ", s)
    if not keep_unit:
        s = UNIT.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


CANON_PROV = [
    "ONTARIO", "QUEBEC", "NOVA SCOTIA", "NEW BRUNSWICK", "PRINCE EDWARD ISLAND",
    "MANITOBA", "SASKATCHEWAN", "ALBERTA", "BRITISH COLUMBIA", "YUKON",
    "NORTH-WEST TERRITORIES", "NORTHWEST TERRITORIES", "KEEWATIN",
]
# the prairies were one unit before 1905 and are split after it, so identity is
# grouped on a *pool* rather than the printed province (as in step 2)
PROV_POOL = {
    "MANITOBA": "Prairies/NWT", "SASKATCHEWAN": "Prairies/NWT",
    "ALBERTA": "Prairies/NWT", "NORTH-WEST TERRITORIES": "Prairies/NWT",
    "NORTHWEST TERRITORIES": "Prairies/NWT", "KEEWATIN": "Prairies/NWT",
}


def norm_prov(section):
    """"NOVA SCOTLIA.", "ONTARIO - Con.", "Ontario" -> "NOVA SCOTIA" / "ONTARIO".

    The province banner is re-set on every continuation page and the OCR
    mangles it freely, so it is matched to the canonical list by similarity
    rather than by equality.
    """
    s = norm(section, keep_unit=True).upper()
    if not s:
        return ""
    if s in CANON_PROV:
        return s
    best = max(((sim(s.lower(), c.lower()), c) for c in CANON_PROV), default=(0, ""))
    return best[1] if best[0] >= 0.85 else ""


def display_name(names):
    """Pick the cleanest printed spelling for the page title.

    The OCR marks uncertain rows with a leading "(t)" or "*", and simply taking
    the longest variant made those the canonical name on every page.  Prefer a
    variant that starts with a letter, then the most frequent, then the longest.
    """
    counts = collections.Counter(names)
    clean = [n for n in counts if re.match(r"^[A-Za-z]", str(n).strip())]
    pool = clean or list(counts)
    return max(pool, key=lambda n: (counts[n], len(str(n))))


def sim(a, b):
    """Symmetric Dice over bigrams — agency names are short, like band names."""
    if not a or not b:
        return 0.0
    ga = collections.Counter(a[i:i + 2] for i in range(len(a) - 1))
    gb = collections.Counter(b[i:i + 2] for i in range(len(b) - 1))
    inter = sum((ga & gb).values())
    return 2 * inter / max(1, sum(ga.values()) + sum(gb.values()))


def iter_agstat_rows():
    """(year, chunk, page, section, label) for every agency row of the series.

    Rows are gated by their governing `headers` block, as in step 2: the
    agstat page spans run on into the lists of chiefs and the band tables that
    sit beside them in the volume, and those rows name a *person* or a *band*
    in the same `label` field ("Hall, Miss E.", "Okanagan Lake Band"), which
    would otherwise mint thousands of phantom agencies.
    """
    for d in sorted(RESULTS.glob("agstat*_qwen38_medium")):
        m = re.search(r"agstat(\d{4})", d.name)
        if not m:
            continue
        year = int(m.group(1))
        for f in sorted(d.glob("out_*.jsonl")):
            section, is_agri = "", False
            for line in f.read_text(errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict):
                    continue
                if r.get("row_type") == "headers":
                    is_agri = classify_header(r) == "agri"
                    continue
                if r.get("section"):
                    section = str(r["section"])
                if not is_agri or r.get("row_type") != "row":
                    continue
                label = r.get("label") or r.get("band") or ""
                if not label:
                    continue
                key = norm(label)
                # the province banner is repeated as a row label; so is the
                # section total that closes each province block
                if not key or key in PROVINCES or key == norm(section):
                    continue
                yield year, f.name, r.get("page"), section, str(label), key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", type=float, default=0.86,
                    help="threshold for linking to an existing agency chain")
    ap.add_argument("--sim-cluster", type=float, default=0.80,
                    help="threshold for clustering printed name variants")
    args = ap.parse_args()

    members = pd.read_parquet(ROOT / "registries/entities/agency_chain_members.parquet")
    by_year, anyyear, canon = {}, {}, {}
    for r in members.itertuples():
        by_year[(int(r.report_year), norm(r.name))] = r.chain_id
        anyyear.setdefault(norm(r.name), r.chain_id)
        anyyear.setdefault(norm(r.canonical), r.chain_id)
        canon.setdefault(norm(r.canonical), r.chain_id)

    rows = list(iter_agstat_rows())
    print(f"agstat agency rows: {len(rows):,}")

    # ---- group the printed keys, then cluster their name variants ----------
    by_key = collections.defaultdict(list)
    for year, chunk, page, section, label, key in rows:
        by_key[key].append((year, chunk, page, norm_prov(section), section, label))
    # a key's pool is the province it is printed under most often
    pool_of = {}
    for key, hits in by_key.items():
        provs = collections.Counter(p for *_, p, _, _ in hits if p)
        prov = provs.most_common(1)[0][0] if provs else ""
        pool_of[key] = PROV_POOL.get(prov, prov)

    in_pool = collections.defaultdict(list)
    for key in by_key:
        in_pool[pool_of[key]].append(key)

    # complete linkage, as in step 2: a variant joins a cluster only if it is
    # similar to *every* member, so "a~b, b~c" cannot chain unrelated agencies
    # together.  Longest key first, so the fullest spelling is the canonical.
    cluster_of, clusters = {}, []
    for pool, keys in sorted(in_pool.items()):
        local = []
        for k in sorted(keys, key=lambda k: (-len(k), k)):
            for cl in local:
                if all(sim(k, m) >= args.sim_cluster for m in cl[1]):
                    cl[1].add(k)
                    break
            else:
                local.append([k, {k}])
        for canonical, variants in local:
            idx = len(clusters)
            clusters.append((canonical, variants, pool))
            for v in variants:
                cluster_of[v] = idx

    att = []
    groups = collections.defaultdict(list)
    for year, chunk, page, section, label, key in rows:
        idx = cluster_of[key]
        groups[idx].append((year, chunk, page, section, label))
        att.append(dict(cluster=idx, year=year, name_as_printed=label,
                        province=section, page=page, chunk=chunk))

    linked = matched_year = matched_any = matched_fuzzy = 0
    entities, review = [], []
    for idx, hits in sorted(groups.items()):
        key, variants, pool = clusters[idx]
        years = sorted({y for y, *_ in hits})
        provs = sorted({p for *_, p, _ in hits if p})
        names = sorted({n for *_, n in hits})
        chain, how, score = "", "", 0.0
        for y in years:
            if (y, key) in by_year:
                chain, how, score = by_year[(y, key)], "same_year", 1.0
                matched_year += 1
                break
        if not chain and key in anyyear:
            chain, how, score = anyyear[key], "other_year", 1.0
            matched_any += 1
        if not chain:
            best = max(((sim(key, c), cid) for c, cid in canon.items()),
                       default=(0.0, ""))
            if best[0] >= args.sim:
                chain, how, score = best[1], "name_similarity", best[0]
                matched_fuzzy += 1
            elif best[0] >= 0.6:
                review.append(dict(queue="near_miss", agency_key=key,
                                   printed=" | ".join(names[:3]),
                                   provinces="; ".join(provs[:3]),
                                   first_year=years[0], last_year=years[-1],
                                   candidate=best[1], score=round(best[0], 3)))
        linked += bool(chain)
        entities.append(dict(
            agency_id=chain or f"AGT-{key.replace(' ', '-')}",
            minted=not chain, chain_id=chain, link_method=how,
            link_score=round(score, 3), cluster=idx, key=key, pool=pool,
            name=display_name([n for *_, n in hits]),
            name_variants="|".join(sorted(names)[:8]),
            provinces="; ".join(provs[:4]),
            first_year=years[0], last_year=years[-1], n_years=len(years),
            n_rows=len(hits)))

    ent = pd.DataFrame(entities)
    a = pd.DataFrame(att).merge(ent[["cluster", "agency_id"]], on="cluster", how="left")
    ent.drop(columns=["key", "cluster"]).to_parquet(
        ROOT / "registries/entities/agencies_agstat.parquet", index=False)
    a.drop(columns=["cluster"]).to_parquet(
        ROOT / "registries/annotations/agency_attestations.parquet", index=False)
    with open(ROOT / "registries/crosswalks/agency_agstat_review.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["queue", "agency_key", "printed", "provinces",
                                          "first_year", "last_year", "candidate", "score"])
        w.writeheader()
        w.writerows(sorted(review, key=lambda r: -r["score"]))

    print(f"agency identities: {len(ent):,} "
          f"({linked:,} linked to an existing chain — {matched_year:,} same-year, "
          f"{matched_any:,} other-year, {matched_fuzzy:,} by name similarity; "
          f"{len(ent) - linked:,} minted from the series alone)")
    print(f"attestations: {len(a):,} | review queue: {len(review):,}")
    print(f"years {ent.first_year.min()}-{ent.last_year.max()} | "
          f"identities seen in >1 year: {(ent.n_years > 1).sum():,}")
    print("\nsample minted (series-only) agencies:")
    print(ent[ent.minted].nlargest(8, "n_rows")[
        ["agency_id", "name", "provinces", "first_year", "last_year", "n_rows"]
    ].to_string(index=False))


if __name__ == "__main__":
    main()
