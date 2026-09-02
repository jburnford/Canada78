#!/usr/bin/env python3
"""School identities from the annual School Statements, 1896-1930 (KG_BUILD_PLAN §1.2, step 5).

    python3 build/mint_schools.py [--sim 0.85]

The school statements are the department's own annual list of every school it
funded, one row per school per year, already carrying the school's name, type
(day / boarding / industrial / residential), reserve, agency, denomination and
teacher.  This mints one identity per school across the 35 volumes, the way
`mint_bands_from_census.py` does for bands.

Identity is (normalised name, agency) within a province pool: the same name
recurs in different agencies ("Pas", "Ahousaht", "St. Mary's"), so a name-only
key would fuse unrelated schools, while the agency alone changes spelling from
year to year.  Name variants are clustered by COMPLETE linkage — a variant
joins only if it is similar to every member — with an agency-compatibility
guard, so two same-named schools in different agencies stay apart.

Type changes (day -> boarding, boarding -> residential) and renames are emitted
as events rather than splitting the identity, since they are the history the
historian wants to see, not evidence of a different school.

Outputs: registries/entities/schools.parquet              one row per identity
         registries/annotations/school_attestations.parquet  one row per year
         registries/annotations/school_events.parquet      renames, type changes
         registries/crosswalks/school_review.csv           what to check by hand
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
# page-positional type from the printed statement titles (build/school_type_map.py);
# the extractor's own school_type field defaults to "day" and typed all 336 rows
# of 1921 "day" against 74 residential schools printed
TYPE_BY_PAGE = ROOT / "registries/external/school_tables/type_by_page.csv"
RESIDENTIAL = {"boarding", "industrial", "residential"}
# agency / district rows from the neighbouring tabular statements that print a
# roll total per unit and so passed the header gate as schools
UNIT_NAME = re.compile(r"\b(agency|agencies|superintendency|inspectorate|district)\s*\.?\s*$", re.I)
# the Nova Scotia county tables and the medical-officer lists share the school
# chunks and print a salary column, so their rows ("Fergusson, A.G., M.D.",
# "Antigonish and Guysborough Counties") pass the header gate
# the year-end recapitulation lists one row per province under a "Number of
# Schools" header, and those rows were minting schools called "Alberta"
PROVINCE_NAME = re.compile(r"^\W{0,3}(ontario|quebec|nova scotia|new brunswick|prince edward island|manitoba|"
                           r"saskatchewan|alberta|north-?west territories|british columbia|yukon( territory)?|"
                           r"total|grand total)\W{0,3}$", re.I)
RECAP_COLS = re.compile(r"number of schools", re.I)
JUNK_NAME = re.compile(r"\b(M\.?\s?D\.?|Rev\.|Dr\.|Esq\.?|Mrs\.|Miss)(\s|$)|\bcount(y|ies)\b|"
                       r"^[A-Z][a-z']+,\s+[A-Z][a-z]*\.?(\s|$)|"          # "Carter, Thos. H." / "Jones, Joseph"
                       r"\b(Indian Agent|Field Matron|Stenographer|Interpreter|Clerk|Farmer|Instructor|Constable)\b|"
                       r"^Treaty No\.", re.I)


def load_type_map():
    out = {}
    if not TYPE_BY_PAGE.exists():
        return out
    with TYPE_BY_PAGE.open() as fh:
        for row in csv.DictReader(fh):
            if row["type"]:
                out[(int(row["year"]), int(row["page"]))] = row["type"]
    return out


def type_class(t):
    """day / residential / "" — boarding and industrial share a class because the
    Department merged them into "residential" in 1923 and the names carried over."""
    return "residential" if t in RESIDENTIAL else ("day" if t == "day" else "")

UNIT = re.compile(r"\b(agency|agencies|superintendency|inspectorate|district|reserve|"
                  r"school|schools|indian)\b", re.I)
TYPE_WORD = re.compile(r"\b(day|boarding|industrial|residential)\b", re.I)
# a header block that really governs a school statement: roll, attendance and
# the standards/grades the pupils were graded in
SCHOOL_COLS = re.compile(r"number on roll|average attendance|standard\s*[ivx]|grades?\s*[ivx]|"
                         r"appropriation for salary|yearly grant|from what fund", re.I)
PROV_POOL = {
    "MANITOBA": "Prairies/NWT", "SASKATCHEWAN": "Prairies/NWT", "ALBERTA": "Prairies/NWT",
    "NORTH-WEST TERRITORIES": "Prairies/NWT", "NORTHWEST TERRITORIES": "Prairies/NWT",
    "NORTH WEST TERRITORIES": "Prairies/NWT", "N W TERRITORIES": "Prairies/NWT",
    "N W T": "Prairies/NWT", "KEEWATIN": "Prairies/NWT",
    "ASSINIBOIA": "Prairies/NWT", "ATHABASCA": "Prairies/NWT",
}


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", str(s))
                   if not unicodedata.combining(c))


def _norm_raw(s, drop_unit=True):
    s = strip_accents(s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(continued|concluded|cont|contd)\b", " ", s)
    if drop_unit:
        s = UNIT.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


CANON_PROV = [
    "ONTARIO", "QUEBEC", "NOVA SCOTIA", "NEW BRUNSWICK", "PRINCE EDWARD ISLAND",
    "MANITOBA", "SASKATCHEWAN", "ALBERTA", "BRITISH COLUMBIA", "YUKON",
    "NORTH WEST TERRITORIES", "NORTHWEST TERRITORIES", "KEEWATIN",
]


def norm(s, drop_unit=True):
    """OCR glues footnote markers to the name ("1Patapun", "2Deer Lake", "(t)Alert
    Bay"); they are not part of the identity."""
    s = re.sub(r"^[\s\(\[]*[\dtl\*\|]{1,2}[\)\]]?(?=[A-Z])", "", str(s or "").strip())
    return _norm_raw(s, drop_unit)


def norm_prov(p):
    """"PROVINCE OF ONTARIO." / "ONTARIO - Con." / "Ontario" -> "ONTARIO".

    The province cell is not always a province: the printer sometimes repeats
    the agency there ("KAMLOOPS-OKANAGAN AGENCY"), which would otherwise become
    its own identity pool and split that province's schools off from the rest.
    Anything that is not recognisably a province becomes the unknown pool.
    """
    s = norm(p, drop_unit=False).upper()
    s = re.sub(r"^(THE )?PROVINCE OF ", "", s)
    s = re.sub(r"\s*[-.,]?\s*(CON|CONCLUDED|CONTINUED)\.?$", "", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    # headings that are not provinces at all
    if s.startswith("OUTSIDE TREATY") or s in {"RECAPITULATION", "SUMMARY", "TOTAL"}:
        return ""
    if s in CANON_PROV:
        return s
    best = max(((sim(s.lower(), c.lower()), c) for c in CANON_PROV), default=(0.0, ""))
    return best[1] if best[0] >= 0.85 else ""


SCHOOL_NO = re.compile(r"\bno\.?\s*(\d+[a-z]?)\b|\b(\d+[a-z]?)\s*$", re.I)


def school_no(key):
    """"six nations no 10" -> "10".

    The reserves ran several numbered day schools apiece, and the number is the
    only thing that tells them apart; dropping it fused all twelve Six Nations
    schools into one identity whose roll was the average of the lot.
    """
    m = SCHOOL_NO.search(key)
    return (m.group(1) or m.group(2) or "").lower() if m else ""


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
    """Symmetric Dice over bigrams; school names are short and OCR-noisy."""
    if not a or not b:
        return 0.0
    ga = collections.Counter(a[i:i + 2] for i in range(len(a) - 1))
    gb = collections.Counter(b[i:i + 2] for i in range(len(b) - 1))
    inter = sum((ga & gb).values())
    return 2 * inter / max(1, sum(ga.values()) + sum(gb.values()))


def is_school_header(row):
    """The school pages sit beside the census, the officers' salary lists and
    the band tables, and the extractor labels every row it finds; without this
    gate the registry mints schools out of teachers ("Watson, A.M., M.D."),
    farmers and band names."""
    cols = " | ".join(str(c) for c in (row.get("columns") or []))
    return bool(SCHOOL_COLS.search(cols)) and not RECAP_COLS.search(cols)


def school_type(row):
    """day / boarding / industrial / residential, from the column or the name."""
    t = str(row.get("school_type") or "")
    m = TYPE_WORD.search(t) or TYPE_WORD.search(str(row.get("school") or ""))
    return m.group(1).lower() if m else ""


def iter_school_rows():
    type_map = load_type_map()
    # the family's chunks run past the statement into staff lists and census
    # recapitulations ("SCHOOL STATEMENT (cont.)" segments); a row on a page
    # beyond the last page the printed headings could type is not a school
    last_mapped = {}
    for (y, pg), t in type_map.items():
        if t != "end":
            last_mapped[y] = max(last_mapped.get(y, 0), pg)
    seen_sigs = set()
    dropped_units = dropped_dups = 0
    for d in sorted(RESULTS.glob("school*_qwen38_medium")):
        m = re.search(r"school(\d{4})", d.name)
        if not m:
            continue
        year = int(m.group(1))
        for f in sorted(d.glob("out_*.jsonl")):
            carry_prov = carry_ag = None
            in_school_table = False
            for line in f.read_text(errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict):
                    continue
                if r.get("row_type") == "headers":
                    in_school_table = is_school_header(r)
                    continue
                if not in_school_table or r.get("row_type") != "school":
                    continue
                if not (r.get("school") or "").strip():
                    continue
                if (UNIT_NAME.search(str(r["school"]).strip()) and not TYPE_WORD.search(str(r["school"]))) \
                        or JUNK_NAME.search(str(r["school"]).strip()) \
                        or PROVINCE_NAME.match(str(r["school"]).strip()):
                    dropped_units += 1
                    continue
                # the printed table states province and agency once and the
                # rows beneath inherit them
                if r.get("agency") and norm(r["agency"]) != norm(carry_ag or ""):
                    # a new agency ends the previous province's block, so a
                    # stale province must not carry across it (Norway House was
                    # being filed under Quebec this way)
                    carry_ag = r["agency"]
                    if not r.get("province"):
                        carry_prov = None
                if r.get("province"):
                    carry_prov = r["province"]
                r["_year"] = year
                r["_chunk"] = f.name
                r["_prov"] = norm_prov(r.get("province") or carry_prov or "")
                r["_agency"] = norm(r.get("agency") or carry_ag or "")
                r["_key"] = norm(r.get("school"))
                r["_type_extracted"] = school_type(r)
                printed = type_map.get((year, int(r["page"]))) if r.get("page") not in (None, "") else None
                if printed == "end" or (year in last_mapped and r.get("page") not in (None, "")
                                        and int(r["page"]) > last_mapped[year] + 2):
                    dropped_units += 1          # past the last statement (land sales, staff, recap)
                    continue
                # a land-sale row: dollars and cents, no teacher, no denomination
                if not (r.get("teacher") or r.get("denomination")) and \
                        any(re.match(r"^\$?\d[\d,]*\.\d{2}$", str(v or "").strip()) for v in (r.get("values") or [])):
                    dropped_units += 1
                    continue
                r["_type_printed"] = printed if printed in {"day", "boarding", "industrial", "residential"} else ""
                r["_type"] = r["_type_printed"] or r["_type_extracted"]
                # consecutive chunks overlap by a page, so the same printed row can
                # arrive twice with the same values
                sig = (year, r.get("page"), r["_key"], tuple(r.get("values") or []))
                if sig in seen_sigs:
                    dropped_dups += 1
                    continue
                seen_sigs.add(sig)
                if r["_key"]:
                    yield r
    print(f"rows dropped: {dropped_units} unit (agency/district) rows, "
          f"{dropped_dups} chunk-overlap duplicates")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", type=float, default=0.85,
                    help="name similarity needed to merge two printed spellings")
    ap.add_argument("--sim-corroborated", type=float, default=0.72,
                    help="lower threshold when the reserve or agency also agrees")
    args = ap.parse_args()

    rows = list(iter_school_rows())
    print(f"school statement rows: {len(rows):,} "
          f"({len({r['_year'] for r in rows})} volumes, "
          f"{min(r['_year'] for r in rows)}-{max(r['_year'] for r in rows)})")

    for r in rows:
        r["_pool"] = PROV_POOL.get(r["_prov"], r["_prov"])
    # A school's province is a property of the school, so a year where the
    # printer left the column blank must not spawn a second identity in the
    # unknown pool (this was splitting the Mohawk Institute and Shingwauk Home
    # in two).  Fill the blank from the same printed name elsewhere.
    known = collections.defaultdict(collections.Counter)
    for r in rows:
        if r["_pool"]:
            known[r["_key"]][r["_pool"]] += 1
    filled = 0
    for r in rows:
        if not r["_pool"] and known.get(r["_key"]):
            r["_pool"] = known[r["_key"]].most_common(1)[0][0]
            filled += 1
    if filled:
        print(f"province inferred from the same school in another year: {filled:,} rows")

    # agencies a printed name is seen under, so the clusterer can keep two
    # same-named schools in different agencies apart
    agencies_of = collections.defaultdict(set)
    reserves_of = collections.defaultdict(set)
    for r in rows:
        if r["_agency"]:
            agencies_of[(r["_pool"], r["_key"])].add(r["_agency"])
        if r.get("reserve"):
            reserves_of[(r["_pool"], r["_key"])].add(norm(r["reserve"]))

    def corroborated(pool, a, b):
        """Same reserve or same agency — evidence beyond the spelling.

        OCR variants of a school name often fall below the name threshold
        ("Kuper Island" / "Kuper Isl'd"), but the row also names the reserve and
        the agency, and those agreeing is stronger evidence of identity than a
        few more matching bigrams would be.
        """
        ra, rb = reserves_of.get((pool, a), set()), reserves_of.get((pool, b), set())
        if ra & rb:
            return True
        sa, sb = agencies_of.get((pool, a), set()), agencies_of.get((pool, b), set())
        return bool(sa & sb)

    def compatible(pool, a, b):
        sa, sb = agencies_of.get((pool, a), set()), agencies_of.get((pool, b), set())
        if not sa or not sb:
            return True
        if sa & sb:
            return True
        # spellings drift year to year, so near-identical agency names count
        return any(sim(x, y) >= 0.9 for x in sa for y in sb)

    # The class (day / residential) is part of the identity: Crowfoot, Old Sun's,
    # Sarcee and Onion Lake each ran a day school and a boarding school of the
    # same name on the same reserve, and one identity for both had its type
    # flickering year to year and its roll doubling.  Untyped rows take the
    # majority class of the same printed name in the same pool.
    # votes come only from rows whose type the printed heading gave; on a page
    # the map could not type (title lost to OCR, or a chunk gap) the extractor's
    # field is a default, not evidence, so the name's printed history outranks it
    class_votes = collections.defaultdict(collections.Counter)
    for r in rows:
        if r["_type_printed"]:
            class_votes[(r["_pool"], r["_key"])][type_class(r["_type_printed"])] += 1
    untyped = overridden = 0
    for r in rows:
        if r["_type_printed"]:
            r["_class"] = type_class(r["_type_printed"])
            continue
        v = class_votes.get((r["_pool"], r["_key"]))
        if v:
            r["_class"] = v.most_common(1)[0][0]
            if r["_type_extracted"] and type_class(r["_type_extracted"]) != r["_class"]:
                overridden += 1
                r["_type"] = "residential" if r["_class"] == "residential" else "day"
        else:
            r["_class"] = type_class(r["_type_extracted"]) or "day"
        untyped += 1
    print(f"unmapped pages: {untyped:,} rows classed from the name's printed history "
          f"or the extractor; {overridden:,} extractor types overridden")
    print(f"rows typed from the printed heading: "
          f"{sum(1 for r in rows if r['_type_printed']):,} of {len(rows):,}; "
          f"class inferred for {untyped:,} untyped rows")

    # ids are stable across rebuilds: a cluster keeps the id its printed names
    # carried in the previous registry when the class agrees; splits and new
    # schools get fresh ids
    prev_ids = collections.defaultdict(collections.Counter)
    prev_class, next_id = {}, 1
    prev_att = ROOT / "registries/annotations/school_attestations.parquet"
    prev_sch = ROOT / "registries/entities/schools.parquet"
    if prev_att.exists() and prev_sch.exists():
        pa, ps = pd.read_parquet(prev_att), pd.read_parquet(prev_sch)
        pool_of = dict(zip(ps.school_id, ps.province_pool))
        prev_class = {sid: type_class(t) for sid, t in zip(ps.school_id, ps.school_type)}
        for sid, name in zip(pa.school_id, pa.name_as_printed):
            prev_ids[(pool_of.get(sid, ""), norm(name))][sid] += 1
        next_id = 1 + max(int(x.split("-")[1]) for x in ps.school_id)
    used_ids = set()

    def stable_id(pool, klass, variants):
        nonlocal next_id
        votes = collections.Counter()
        for k in variants:
            votes.update(prev_ids.get((pool, k), {}))
        for sid, _ in votes.most_common():
            if sid in used_ids:
                continue
            if prev_class.get(sid, "") in ("", klass):
                used_ids.add(sid)
                return sid
        sid = f"SCH-{next_id:05d}"
        next_id += 1
        used_ids.add(sid)
        return sid

    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["_pool"], r["_class"])].append(r)

    schools, attest, events, review = [], [], [], []
    for (pool, klass), prows in sorted(groups.items(), key=lambda kv: str(kv[0])):
        keys = sorted({r["_key"] for r in prows}, key=lambda k: (-len(k), k))
        clusters = []
        for k in keys:
            for cl in clusters:
                if school_no(k) != school_no(cl[0]):
                    continue
                if all(compatible(pool, k, m) and
                       (sim(k, m) >= args.sim or
                        (sim(k, m) >= args.sim_corroborated and corroborated(pool, k, m)))
                       for m in cl[1]):
                    cl[1].add(k)
                    break
            else:
                clusters.append([k, {k}])
        member_of = {k: i for i, (_, vs) in enumerate(clusters) for k in vs}

        for i, (canonical, variants) in enumerate(clusters):
            crows = sorted((r for r in prows if member_of[r["_key"]] == i),
                           key=lambda r: r["_year"])
            years = sorted({r["_year"] for r in crows})
            names = sorted({str(r["school"]).strip() for r in crows})
            types = [r["_type"] for r in crows if r["_type"]]
            ags = sorted({r["_agency"] for r in crows if r["_agency"]})
            denoms = sorted({str(r["denomination"]).strip() for r in crows
                             if r.get("denomination") and str(r["denomination"]).strip()
                             not in {"...", "-"}})
            reserves = sorted({str(r["reserve"]).strip() for r in crows if r.get("reserve")})
            school_id = stable_id(pool, klass, variants)

            for r in crows:
                attest.append(dict(
                    school_id=school_id, year=r["_year"],
                    name_as_printed=str(r["school"]).strip(),
                    school_type=r["_type"], type_printed=r["_type_printed"],
                    type_extracted=r["_type_extracted"],
                    province=r["_prov"], agency=r["_agency"],
                    reserve=r.get("reserve"), denomination=r.get("denomination"),
                    teacher=r.get("teacher"), page=r.get("page"), chunk=r["_chunk"],
                    confidence=r.get("confidence")))

            # events: the type the department printed changing, and a rename
            seen_type = None
            for r in crows:
                if r["_type"] and r["_type"] != seen_type:
                    if seen_type:
                        events.append(dict(school_id=school_id, year=r["_year"],
                                           event="TYPE_CHANGED",
                                           detail=f"{seen_type} -> {r['_type']}"))
                    seen_type = r["_type"]
            if len(variants) > 1:
                events.append(dict(school_id=school_id, year=years[0], event="NAME_VARIANTS",
                                   detail=" | ".join(sorted(variants))))
            gaps = [(a, b) for a, b in zip(years, years[1:]) if b - a > 1]
            for a, b in gaps:
                events.append(dict(school_id=school_id, year=a, event="ATTESTATION_GAP",
                                   detail=f"not printed {a + 1}-{b - 1}"))

            schools.append(dict(
                school_id=school_id, name=display_name(
                    [str(r["school"]).strip() for r in crows]),
                name_variants="|".join(names[:8]),
                school_type=collections.Counter(types).most_common(1)[0][0] if types else "",
                types_seen="|".join(sorted(set(types))),
                type_class=klass,
                province_pool=pool, agencies="|".join(ags[:4]), n_agencies=len(ags),
                reserves="|".join(reserves[:4]),
                denominations="|".join(denoms[:3]),
                first_year=years[0], last_year=years[-1], n_years=len(years),
                n_rows=len(crows)))

            if len(ags) > 2 or (len(variants) > 1 and len(names) > 3):
                review.append(dict(
                    queue="many_agencies" if len(ags) > 2 else "name_variants",
                    school_id=school_id, name=max(names, key=len),
                    province=pool, agencies="; ".join(ags[:5]),
                    variants=" | ".join(sorted(variants)[:5]),
                    first_year=years[0], last_year=years[-1], n_years=len(years)))

    sdf, adf, edf = pd.DataFrame(schools), pd.DataFrame(attest), pd.DataFrame(events)
    sdf.to_parquet(ROOT / "registries/entities/schools.parquet", index=False)
    adf.to_parquet(ROOT / "registries/annotations/school_attestations.parquet", index=False)
    edf.to_parquet(ROOT / "registries/annotations/school_events.parquet", index=False)
    with open(ROOT / "registries/crosswalks/school_review.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["queue", "school_id", "name", "province",
                                          "agencies", "variants", "first_year",
                                          "last_year", "n_years"])
        w.writeheader()
        w.writerows(review)

    print(f"school identities: {len(sdf):,} | attestations: {len(adf):,} | "
          f"events: {len(edf):,} | review: {len(review):,}")
    print("by type:", dict(sdf.school_type.value_counts()))
    print(f"seen in >1 year: {(sdf.n_years > 1).sum():,} | "
          f"median run {sdf.n_years.median():.0f} yrs | "
          f"longest {sdf.n_years.max()} yrs")
    print("by province pool:", dict(sdf.province_pool.value_counts().head(8)))
    if len(edf):
        print("events:", dict(edf.event.value_counts()))
    print("\nlongest-running schools:")
    print(sdf.nlargest(8, "n_years")[
        ["school_id", "name", "school_type", "province_pool", "first_year",
         "last_year", "n_years"]].to_string(index=False))


if __name__ == "__main__":
    main()
