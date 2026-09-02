#!/usr/bin/env python3
"""Link the Qwen-extracted Schedule editions (1897-1901) to the minted 1902
reserve registry — attestations, never registry edits.

    python3 build/link_schedule_editions.py

Inputs:  eval/results/sched{year}_qwen38_medium/out_*.jsonl  (1897-1901)
         registries/entities/reserves.parquet                (anchor, minted from 1902)
Outputs: registries/annotations/reserve_attestations.parquet
             one row per extracted edition row: raw fields + reserve_id (nullable),
             match_tier, match_note; 1902 rows included as tier "anchor"
         registries/crosswalks/schedule_edition_review.csv
             review queues: unmatched rows (candidate + score), ambiguous rows,
             matched rows whose acreage changed >20% between editions
         docs/SCHEDULE_EDITIONS.md   coverage + linkage report

Design (per the 1897-1902 formats, profiled 2026-08-26):
- Sections differ: eastern provinces (NS/NB/PEI/QC) carry a STABLE provincial
  numbering across editions (1897 No.15 Richibucto = 1902 No.15) with the
  county in `location` (compact editions) or `division` (1902); Ontario
  numbers per band; prairie treaties number per treaty (with letter
  suffixes); BC numbers per band within an agency.
- Matching is tiered inside a province group, one-to-at-most-one per edition:
    T1  reserve_no + band                       (BC, ON)
    T2  reserve_no + name (exact or Jaccard≥.6) (all)
    T3  name exact-normalized, unique on both sides in the group
    T4  reserve_no + acreage equal              (name badly OCR'd)
  Equal-best competing candidates -> "ambiguous", never a guess.
- The compact 1897-99 editions omit bands in the east and put county in
  location; county agreement is used only as a +bonus, never required.
"""
import json
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EDITIONS = [1897, 1898, 1899, 1900, 1901]
# alphabetical BC reserve->agency index pages inside an edition's page range
# (name-only rows; kept in attestations as 'bc_index', excluded from linking)
INDEX_PAGES = {1900: (929, 944)}

# ---------------------------------------------------------------- normalizers

def nn(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() in ("", "None", "...") else v


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip() if nn(s) else ""


def norm_no(s):
    """First number(+letter) token: '31H and pt. of 31G' -> '31H'; '24A' -> '24A'."""
    if not nn(s):
        return ""
    m = re.search(r"(\d+\s?[A-Z]?)\b", str(s).upper())
    return m.group(1).replace(" ", "") if m else str(s).upper().strip().rstrip(".").replace(" ", "")


def norm_band(s):
    s = norm(s)
    s = re.sub(r"\b(bands?|of|the|indians?)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


PROV_GROUP = [
    (r"BRITISH COLUMBIA", "BC"), (r"NOVA SCOTIA", "NS"), (r"NEW BRUNSWICK", "NB"),
    (r"PRINCE EDWARD", "PE"), (r"QUEBEC", "QC"), (r"ONTARIO", "ON"),
    (r"MANITOBA|NORTH ?- ?WEST|NORTHWEST|KEEWATIN|ASSINIBOIA|SASKATCHEWAN|ALBERTA", "MBNWT"),
    (r"YUKON", "YT"),
]


def prov_group(prov, division=""):
    s = f"{prov or ''} {division or ''}".upper()
    if re.search(r"TREATY", s):
        return "MBNWT" if not re.search(r"ONTARIO|TREATY NO\. ?3\b", s) or re.search(r"MANITOBA|NORTH", s) else "ON"
    for pat, g in PROV_GROUP:
        if re.search(pat, s):
            return g
    return "?"


def norm_div(s):
    if not nn(s):
        return ""
    u = str(s).upper()
    u = re.sub(r"[,.]?\s*(BRITISH COLUMBIA|B\.? ?C\.?)\s*\.?$", "", u)
    u = re.sub(r"TREATY\s*NO\s*\.?\s*(\d+)", r"TREATY NO. \1", u)
    u = u.replace("KAMLOOPS-OKANAGAN", "KAMLOOPS - OKANAGAN")
    u = re.sub(r"[^A-Z0-9 .-]", " ", u)
    return re.sub(r"\s+", " ", u).strip(" .-")


def toks(s):
    return set(norm(s).split())


def jacc(a, b):
    A, B = toks(a), toks(b)
    return 1.0 if not A and not B else len(A & B) / max(1, len(A | B))


from difflib import SequenceMatcher  # noqa: E402


def sim(a, b):
    """Name similarity robust to OCR/hyphenation drift ('Sic-e-dach' vs
    'Sik-e-dakh'): max of token Jaccard and char-level ratio on squashed text."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    j = jacc(a, b)
    r = SequenceMatcher(None, na.replace(" ", ""), nb.replace(" ", "")).ratio()
    return max(j, r)


def acres_close(a, b):
    a, b = nn(a), nn(b)
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) < 0.01
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- load

def load_edition(year):
    rows = []
    for f in sorted((ROOT / f"eval/results/sched{year}_qwen38_medium").glob("out_*.jsonl")):
        for l in f.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            r = json.loads(l)
            if (r.get("row_type") or "reserve") != "reserve":
                continue
            rows.append(dict(
                edition=year, page=r.get("page"), division=nn(r.get("division")),
                reserve_no=nn(r.get("reserve_no")), name=nn(r.get("name")),
                location=nn(r.get("location")), tribe_band=nn(r.get("tribe_band")),
                acres_text=nn(r.get("acres_text")), acres=r.get("acres"),
                remarks=nn(r.get("remarks")), confidence=r.get("confidence"),
                province=nn(r.get("province"))))
    return rows


def main():
    reg = pd.read_parquet(ROOT / "registries/entities/reserves.parquet")
    R = []
    for r in reg.itertuples():
        R.append(dict(
            reserve_id=r.reserve_id, kg=prov_group(r.province, r.division_norm),
            kno=norm_no(r.reserve_no), kn=norm(r.name), kb=norm_band(r.band_norm),
            kd=norm_div(r.division_norm), acres=r.acres, name=r.name,
            division=r.division_norm, band=r.band_norm))
    by_group = defaultdict(list)
    for x in R:
        by_group[x["kg"]].append(x)
    names_in_group = defaultdict(lambda: defaultdict(int))
    for x in R:
        if x["kn"]:
            names_in_group[x["kg"]][x["kn"]] += 1

    att, review = [], []
    for year in EDITIONS:
        rows = load_edition(year)
        used = set()
        # current section context: model rows carry province; division may be null
        n_t = defaultdict(int)
        # edition-side name uniqueness
        ed_names = defaultdict(int)
        for e in rows:
            e["kg"] = prov_group(e["province"], e["division"])
            e["kno"], e["kn"] = norm_no(e["reserve_no"]), norm(e["name"])
            e["kb"], e["kd"] = norm_band(e["tribe_band"]), norm_div(e["division"])
            if e["kn"]:
                ed_names[(e["kg"], e["kn"])] += 1
        idx_lo, idx_hi = INDEX_PAGES.get(year, (None, None))
        for e in rows:
            if idx_lo is not None and e["page"] is not None and idx_lo <= int(e["page"]) <= idx_hi:
                out = dict(e)
                del out["kg"], out["kno"], out["kn"], out["kb"], out["kd"]
                out.update(reserve_id=None, match_tier="bc_index", match_note=None)
                att.append(out)
                continue
            cands = by_group.get(e["kg"], [])
            scored = []
            for x in cands:
                if x["reserve_id"] in used:
                    continue
                tier = None
                s = 0.0
                nj = sim(e["name"], x["name"])
                bj = sim(e["tribe_band"], x["band"])
                if e["kno"] and e["kno"] == x["kno"]:
                    if e["kb"] and x["kb"] and (e["kb"] == x["kb"] or bj >= 0.7):
                        tier, s = "T1", 3 + nj
                    elif e["kn"] and (e["kn"] == x["kn"] or nj >= 0.6):
                        tier, s = "T2", 2 + nj
                    elif acres_close(e["acres"], x["acres"]):
                        tier, s = "T4", 1 + nj
                if tier is None and e["kn"] and e["kn"] == x["kn"] \
                        and names_in_group[e["kg"]][e["kn"]] == 1 \
                        and ed_names[(e["kg"], e["kn"])] == 1:
                    tier, s = "T3", 1.5
                # T5: numbering drift — strong name + acreage agreement
                if tier is None and nj >= 0.75 and acres_close(e["acres"], x["acres"]):
                    tier, s = "T5", 1 + nj
                if tier:
                    s += 0.3 * jacc(e["division"] or e["location"], x["division"]) \
                        + (0.3 if acres_close(e["acres"], x["acres"]) else 0)
                    scored.append((s, tier, x))
            scored.sort(key=lambda t: -t[0])
            out = dict(e)
            del out["kg"], out["kno"], out["kn"], out["kb"], out["kd"]
            if not scored:
                out.update(reserve_id=None, match_tier="unmatched", match_note=None)
                review.append(dict(queue="unmatched", **{k: out[k] for k in
                              ("edition", "page", "province", "division", "reserve_no",
                               "name", "tribe_band", "acres_text")}, note=""))
            elif len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 1e-9:
                out.update(reserve_id=None, match_tier="ambiguous",
                           match_note="; ".join(f"{t}:{x['reserve_id']}" for _, t, x in scored[:3]))
                review.append(dict(queue="ambiguous", **{k: out[k] for k in
                              ("edition", "page", "province", "division", "reserve_no",
                               "name", "tribe_band", "acres_text")},
                              note=out["match_note"]))
            else:
                s, tier, x = scored[0]
                used.add(x["reserve_id"])
                out.update(reserve_id=x["reserve_id"], match_tier=tier,
                           match_note=f"score {s:.2f}; 1902: {x['name']} | {x['band']} | {x['division']}")
                n_t[tier] += 1
            att.append(out)
        n_m = sum(n_t.values())
        print(f"{year}: {len(rows)} rows → matched {n_m} ({n_m / max(1, len(rows)):.0%}) "
              f"{dict(n_t)} | unmatched {sum(1 for a in att if a['edition'] == year and a['match_tier'] == 'unmatched')} "
              f"| ambiguous {sum(1 for a in att if a['edition'] == year and a['match_tier'] == 'ambiguous')}")

    # 1902 anchor rows for the series
    for r in reg.itertuples():
        att.append(dict(edition=1902, page=r.page, division=r.division_norm,
                        reserve_no=r.reserve_no, name=r.name, location=r.location,
                        tribe_band=r.band_norm, acres_text=r.acres_text, acres=r.acres,
                        remarks=r.remarks, confidence=r.confidence, province=r.province,
                        reserve_id=r.reserve_id, match_tier="anchor", match_note=None))
    A = pd.DataFrame(att)
    A.to_parquet(ROOT / "registries/annotations/reserve_attestations.parquet", index=False)

    # acreage-change review: matched series with >20% jump between attestations
    for rid, g in A.dropna(subset=["reserve_id"]).groupby("reserve_id"):
        g = g.dropna(subset=["acres"]).sort_values("edition")
        vals = list(zip(g.edition, g.acres))
        for (y1, a1), (y2, a2) in zip(vals, vals[1:]):
            if a1 and a2 and max(a1, a2) / max(min(a1, a2), 0.01) > 1.2:
                review.append(dict(queue="acres_change", edition=y2, page=None,
                                   province=None, division=None, reserve_no=None,
                                   name=rid, tribe_band=None, acres_text=None,
                                   note=f"{y1}: {a1} → {y2}: {a2}"))
    RV = pd.DataFrame(review)
    RV.to_csv(ROOT / "registries/crosswalks/schedule_edition_review.csv", index=False)
    print(f"\nattestations: {len(A):,} rows → registries/annotations/reserve_attestations.parquet")
    print(RV.queue.value_counts().to_string())
    print("review → registries/crosswalks/schedule_edition_review.csv")


if __name__ == "__main__":
    main()
