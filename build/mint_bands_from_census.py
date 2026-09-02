#!/usr/bin/env python3
"""Build the band registry (v2) from the extracted band-census series.

    python3 build/mint_bands_from_census.py [--report] [--min-pop-frac 0.3]

The Department's own annual census tables are the authoritative list of bands
per agency per year, so the band registry is minted from them rather than from
mentions (KG_BUILD_PLAN.md §1.1).

Inputs : eval/results/census{year}_qwen38_medium/out_*.jsonl   (38 volumes)
Outputs: registries/entities/bands_census.parquet        one row per band identity
         registries/annotations/band_attestations.parquet one row per band-year
         registries/crosswalks/band_census_review.csv     things a historian must judge

Pipeline
  1. page gate  — the census span sometimes over-runs into the following table
     (1895 p.664 "George Bird" is an individual farmer). Keep only pages where
     at least --min-pop-frac of band rows carry a population; report the rest.
  2. normalise  — province, agency, band name (possessives, wrappers, OCR noise).
  3. cluster    — within (province, agency) group name variants by similarity so
     OCR drift and spelling changes collapse into one identity.
  4. attest     — one row per (band identity, year) with population + source.
"""
import argparse
import collections
import csv
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval/results"

PROV_CANON = {
    "ontario": "Ontario", "quebec": "Quebec", "nova scotia": "Nova Scotia",
    "new brunswick": "New Brunswick", "prince edward island": "Prince Edward Island",
    "manitoba": "Manitoba", "british columbia": "British Columbia",
    "saskatchewan": "Saskatchewan", "alberta": "Alberta",
    "north-west territories": "North-West Territories",
    "northwest territories": "North-West Territories",
    "north west territories": "North-West Territories",
    "keewatin": "Keewatin", "yukon": "Yukon", "athabaska": "Athabaska",
    "eastern townships": "Quebec",
}
# the 1905 split of the North-West Territories and the districts that preceded
# the prairie provinces must not fragment a band identity
PROV_POOL = {
    "North-West Territories": "Prairies/NWT", "Saskatchewan": "Prairies/NWT",
    "Alberta": "Prairies/NWT", "Athabaska": "Prairies/NWT",
    "Assiniboia": "Prairies/NWT", "Keewatin": "Prairies/NWT",
    "Territories": "Prairies/NWT", "Manitoba": "Manitoba",
}
# noise the model sometimes leaves in a band cell
NOT_A_BAND = re.compile(
    r"^(total|recapitulation|grand total|carried forward|brought forward|"
    r"summary|\W*)$|^no agents|^the following|^note[ .:]|^\*", re.I)
TRAILING_JUNK = re.compile(r"\s*(?:at\s*[-–—]?|,?\s*including.*)?[\s.\-–—:]*$", re.I)


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_province(p):
    if not p:
        return None
    s = re.sub(r"^\s*(province|district)\s+of\s+", "", str(p).strip(), flags=re.I)
    # "Alberta Inspectorate", "Manitoba Superintendency" are administrative
    # wrappers around the province name, not separate jurisdictions
    s = re.sub(r"\s*[-–—]\s*(continued|concluded|con)\.?$", "", s, flags=re.I)
    s = re.sub(r"\s+(inspectorate|superintendency|agencies|agency|district|division)\.?$",
               "", s, flags=re.I)
    s = re.sub(r"\s*[-–—]\s*(continued|concluded|con)\.?$", "", s, flags=re.I)
    s = re.sub(r"^north\s*-?\s*west(\s+territor\w*)?$", "North-West Territories", s, flags=re.I)
    s = re.sub(r"[.,]+$", "", s).strip()
    return PROV_CANON.get(strip_accents(s).lower(), s.title() if s.isupper() else s)


def norm_agency(a):
    """Agency cell also carries treaty groupings ('CHIPPEWAS AND CREES OF TREATY
    NO. 1 AT') and inspectorates; keep them but canonicalise shape."""
    if not a:
        return None
    s = re.sub(r"\s+", " ", str(a).strip())
    s = re.sub(r"\s*[-–—:]*\s*$", "", s)
    s = re.sub(r"\s+at$", "", s, flags=re.I)
    s = re.sub(r"\s*\bagency\b\.?$", " Agency", s, flags=re.I)
    return s.title() if s.isupper() else s


def norm_band(b):
    """Normalised comparison key for a band name."""
    if not b:
        return None
    s = strip_accents(str(b))
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^(the\s+)?(indians?|band)\s+of\s+", "", s, flags=re.I)
    s = re.sub(r"\s*\bband\b\.?$", "", s, flags=re.I)
    s = re.sub(r"\((\d+|[a-z])\)\s*$", "", s, flags=re.I)      # "Birdtail - Sioux (2)"
    s = TRAILING_JUNK.sub("", s)
    s = re.sub(r"'s\b", "", s)                                  # Beardy's -> Beardy
    s = re.sub(r"[^\w\s&-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s or None


RESNUM = re.compile(r"\bno\.?\s*(\d+[a-z]?)\b|\b(\d+[a-z]?)$", re.I)


def resnum(k):
    """trailing reserve number, if the name carries one — two reserves of the
    same band ('cayoose creek no 1' / 'no 2') must not be merged."""
    m = RESNUM.search(k)
    return (m.group(1) or m.group(2)).lower() if m else None


def sim(a, b):
    """max(token Jaccard, char-bigram ratio) — same measure as the schedule linker."""
    ta, tb = set(a.split()), set(b.split())
    j = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    c = len(ga & gb) / max(1, min(len(ga), len(gb)))
    return max(j, c)


CENSUS_COLS = re.compile(
    r"census|religio|protestant|catholic|pagan|anglican|methodist|presbyterian|baptist|"
    r"aboriginal belief|other christian|number in band|population|denomination|"
    r"male|female|births|deaths|ages? and sexes", re.I)
AGRI_COLS = re.compile(
    r"acres?|bushel|cultivat|live ?stock|horses|cattle|swine|sheep|poultry|grain|root|hay|"
    r"fenc|thresh|wagon|implement|plough|harrow|produce|value of|area of reserve|"
    r"broken|under crop|under wood|seed|garden", re.I)


STRONG_CENSUS = re.compile(r"census return|number in band|number on band|ages? and sexes", re.I)
DENOMINATION = re.compile(
    r"anglican|roman catholic|presbyterian|methodist|baptist|pagan|"
    r"aboriginal belief|other christian|congregationalist|united church", re.I)


def classify_header(h):
    """census / agri / unknown, from a headers row's column labels + title.

    A header that names the census column or two or more denominations *is* the
    census table even when the same header block also carries acreage columns —
    in the 1920s one chunk straddles Table No. 1 and Table No. 2.
    """
    cols = " | ".join(str(c) for c in (h.get("columns") or []))
    cols += " " + str(h.get("title") or "")
    if STRONG_CENSUS.search(cols) or len(set(DENOMINATION.findall(cols.lower()))) >= 2:
        return "census"
    c, a = len(CENSUS_COLS.findall(cols)), len(AGRI_COLS.findall(cols))
    if c == a == 0:
        return "unknown"
    return "census" if c > a else "agri"


def load_rows():
    """Yield band rows tagged with the class of their governing header block.

    The census page-span sometimes runs on into the Agricultural & Industrial
    Statistics that follow (and in the 1920s a single chunk straddles the two),
    so each row inherits the class of the nearest preceding headers row inside
    the same chunk file.
    """
    for d in sorted(RESULTS.glob("census*_qwen38_medium")):
        m = re.search(r"census(\d{4})", d.name)
        if not m:
            continue
        year = int(m.group(1))
        for f in sorted(d.glob("out_*.jsonl")):
            section = "unknown"
            carry_prov = carry_ag = None
            for line in f.read_text(errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict):
                    continue
                if r.get("row_type") == "headers":
                    section = classify_header(r)
                    continue
                r["_year"], r["_chunk"], r["_section"] = year, f.name, section
                # the printed table states a province/agency once and the rows
                # below inherit it; the extractor only fills it where printed
                if r.get("province"):
                    carry_prov = r["province"]
                elif carry_prov:
                    r["province"] = carry_prov
                if r.get("agency"):
                    carry_ag = r["agency"]
                elif carry_ag:
                    r["agency"] = carry_ag
                yield r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-pop-frac", type=float, default=0.3,
                    help="keep a page only if this fraction of its band rows has a population")
    ap.add_argument("--sim", type=float, default=0.80, help="name-variant clustering threshold")
    ap.add_argument("--report", action="store_true", help="print the coverage report only")
    args = ap.parse_args()

    raw = [r for r in load_rows()]
    band_rows = [r for r in raw if r.get("row_type") == "band" and r.get("band")
                 and not NOT_A_BAND.match(str(r["band"]).strip())]

    # ---- 1. section gate -------------------------------------------------
    # governing header says census -> keep; says agri -> drop; unknown -> fall
    # back to the per-page population-density rule.
    per_page = collections.defaultdict(lambda: [0, 0])
    for r in band_rows:
        k = (r["_year"], r.get("page"))
        per_page[k][0] += 1
        per_page[k][1] += r.get("population") is not None
    page_ok = {k for k, (n, p) in per_page.items() if n and p / n >= args.min_pop_frac}

    kept, dropped = [], []
    for r in band_rows:
        sec = r.get("_section", "unknown")
        ok = sec == "census" or (sec == "unknown" and (r["_year"], r.get("page")) in page_ok)
        (kept if ok else dropped).append(r)

    reason = collections.Counter(
        (r["_year"], r.get("_section", "unknown")) for r in dropped)
    print(f"rows: {len(raw):,} total, {len(band_rows):,} band rows, "
          f"{len(kept):,} kept as census, {len(dropped):,} dropped")
    if args.report:
        print("dropped by year/section:")
        for (y, s), n in sorted(reason.items()):
            print(f"   {y} {s:8s} {n:5d}")
        print("kept per year:", dict(sorted(collections.Counter(r["_year"] for r in kept).items())))
        return

    # ---- 2. normalise ----------------------------------------------------
    for r in kept:
        r["_prov"] = norm_province(r.get("province"))
        r["_agency"] = norm_agency(r.get("agency"))
        r["_key"] = norm_band(r.get("band"))
    kept = [r for r in kept if r["_key"]]

    # ---- 3. cluster name variants within a province pool -----------------
    # Identity must survive an agency being renamed or a band transferred, and
    # the 1905 split of the North-West Territories into Saskatchewan/Alberta,
    # so the grouping key is a province *pool*; agency is evidence recorded per
    # attestation, not part of the identity.
    for r in kept:
        r["_pool"] = PROV_POOL.get(r["_prov"], r["_prov"])
    groups = collections.defaultdict(list)
    for r in kept:
        groups[r["_pool"]].append(r)

    bands, attest, review, events = [], [], [], []
    bid = 0
    for pool, rows in sorted(groups.items(), key=lambda kv: str(kv[0])):
        # cluster the distinct keys in this group with COMPLETE linkage: a name
        # joins a cluster only if it is similar to every member. Single linkage
        # chains ("a~b, b~c" merged unrelated BC bands into one 92-agency blob).
        keys = sorted({r["_key"] for r in rows}, key=lambda k: (-len(k), k))
        clusters = []                       # list of [canonical_key, {variants}]
        for k in keys:
            placed = False
            for cl in clusters:
                if resnum(k) != resnum(cl[0]):
                    continue
                if all(sim(k, m) >= args.sim for m in cl[1]):
                    cl[1].add(k)
                    placed = True
                    break
            if not placed:
                clusters.append([k, {k}])
        clusters = [(cl[0], cl[1]) for cl in clusters]
        member_of = {k: i for i, (_, variants) in enumerate(clusters) for k in variants}

        for i, (ck, variants) in enumerate(clusters):
            crows = sorted((r for r in rows if member_of[r["_key"]] == i),
                           key=lambda r: r["_year"])
            years = sorted({r["_year"] for r in crows})
            names = collections.Counter(str(r["band"]).strip() for r in crows)
            provs = collections.Counter(r["_prov"] for r in crows if r["_prov"])
            agencies = collections.Counter(r["_agency"] for r in crows if r["_agency"])
            bid += 1
            band_id = f"band_c{bid:05d}"
            bands.append(dict(
                band_id=band_id,
                name=names.most_common(1)[0][0],
                name_key=ck,
                province=provs.most_common(1)[0][0] if provs else None,
                province_pool=pool,
                agency=agencies.most_common(1)[0][0] if agencies else None,
                n_agencies=len(agencies),
                first_year=years[0], last_year=years[-1], n_years=len(years),
                n_rows=len(crows),
                name_variants=" | ".join(sorted(names)),
                agency_variants=" | ".join(sorted(agencies)),
                population_first=next((r.get("population") for r in crows
                                       if r["_year"] == years[0] and r.get("population") is not None), None),
                population_last=next((r.get("population") for r in reversed(crows)
                                      if r["_year"] == years[-1] and r.get("population") is not None), None),
            ))
            for r in crows:
                attest.append(dict(
                    band_id=band_id, year=r["_year"], name_as_printed=str(r["band"]).strip(),
                    province=r["_prov"], agency=r["_agency"], population=r.get("population"),
                    page=r.get("page"), chunk=r["_chunk"], paper_id=f"dia_ar_{r['_year']}",
                    confidence=r.get("confidence"),
                ))
            # agency changes become events on the band's timeline
            seen_ag, prev = [], None
            for r in crows:
                a = r["_agency"]
                if a and a != prev:
                    if prev is not None:
                        events.append(dict(band_id=band_id, year=r["_year"], event="AGENCY_CHANGED",
                                           from_value=prev, to_value=a))
                    prev = a
                    seen_ag.append(a)
            gaps = [(years[k], years[k + 1]) for k in range(len(years) - 1)
                    if years[k + 1] - years[k] > 3]
            for g0, g1 in gaps:
                events.append(dict(band_id=band_id, year=g0, event="ATTESTATION_GAP",
                                   from_value=str(g0), to_value=str(g1)))
            if len(variants) > 1:
                review.append(dict(queue="name_variants_clustered", band_id=band_id,
                                   province=pool, agency="|".join(sorted(agencies))[:60],
                                   detail=" | ".join(sorted(names)),
                                   years=f"{years[0]}-{years[-1]}"))
            if len(years) == 1:
                review.append(dict(queue="single_year_band", band_id=band_id,
                                   province=pool, agency="|".join(sorted(agencies))[:60],
                                   detail=names.most_common(1)[0][0], years=str(years[0])))
            if len(agencies) > 3:
                review.append(dict(queue="many_agencies", band_id=band_id, province=pool,
                                   agency="|".join(sorted(agencies))[:60],
                                   detail=names.most_common(1)[0][0],
                                   years=f"{years[0]}-{years[-1]}"))

    bdf = pd.DataFrame(bands)
    adf = pd.DataFrame(attest)
    (ROOT / "registries/entities").mkdir(parents=True, exist_ok=True)
    (ROOT / "registries/annotations").mkdir(parents=True, exist_ok=True)
    edf = pd.DataFrame(events)
    bdf.to_parquet(ROOT / "registries/entities/bands_census.parquet", index=False)
    adf.to_parquet(ROOT / "registries/annotations/band_attestations.parquet", index=False)
    if len(edf):
        edf.to_parquet(ROOT / "registries/annotations/band_events.parquet", index=False)
    with open(ROOT / "registries/crosswalks/band_census_review.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["queue", "band_id", "province", "agency", "detail", "years"])
        w.writeheader()
        w.writerows(review)

    print(f"\nbands: {len(bdf):,}   attestations: {len(adf):,}")
    print("bands by province:", dict(bdf.province.fillna("?").value_counts().head(12)))
    print("bands by n_years:", dict(collections.Counter(
        "1" if n == 1 else "2-5" if n <= 5 else "6-15" if n <= 15 else "16+"
        for n in bdf.n_years).most_common()))
    print("attestations per year:", dict(sorted(collections.Counter(adf.year).items())))
    print("review queues:", dict(collections.Counter(r["queue"] for r in review)))
    print("events:", dict(collections.Counter(e["event"] for e in events)))


if __name__ == "__main__":
    main()
