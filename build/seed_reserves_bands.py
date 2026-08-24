#!/usr/bin/env python3
"""Seed the reserve and band facets of the entity registry from the 1902
Schedule extraction (mint-first decision, identity-model workshop #4).

- Normalizes division labels into typed admin context:
    agency  ("BABINE AGENCY, BRITISH COLUMBIA." -> BABINE AGENCY)
    treaty  ("TREATY NO. 4")
    county  ("Kent." -> KENT; eastern sections)
- Mints stable, human-legible reserve ids keyed on
  (province, division, band, reserve_no, name) — reserve numbers are
  band-relative, so the number alone is never an identity.
- Seeds bands_attested from normalized tribe_band values + the BC
  band->agency index.
- Crosswalks reserve-bearing agencies to the agency registry
  (registries/entities/agencies.parquet).

Outputs:
  registries/entities/reserves.parquet        minted reserve facet
  registries/entities/bands_attested.parquet  band attestations
  registries/crosswalks/reserve_agency_1902.csv  agency agreement report
"""
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
URI_BASE = "https://jimclifford.ca/canada50/reserves/"


def slugify(text: str, maxlen: int = 40) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:maxlen].rstrip("-") or "x"


def norm_division(div, province):
    if div is None or (isinstance(div, float) and pd.isna(div)):
        return None, None
    d = str(div).strip().rstrip(".,")
    d = re.sub(r",?\s*BRITISH COLUMBIA$", "", d, flags=re.I).strip().rstrip(".,")
    up = re.sub(r"\s+", " ", d.upper())
    if "TREATY" in up:
        m = re.search(r"TREATY NO\.? ?(\d+)", up)
        return ("treaty", f"TREATY NO. {m.group(1)}") if m else ("treaty", up)
    if "AGENCY" in up:
        up = re.sub(r"\s*-\s*", " - ", up)  # KAMLOOPS-OKANAGAN spacing variants
        return "agency", up
    # county/district (eastern sections); strip the word County for the key
    name = re.sub(r"\s+COUNTY$", "", up)
    return "county", name


def norm_band(b):
    if b is None or (isinstance(b, float) and pd.isna(b)):
        return None
    s = re.sub(r"\s+", " ", str(b).strip().rstrip(".,"))
    return s


def main():
    df = pd.read_parquet(ROOT / "registries/entities/reserves_1902.parquet")
    r = df[df.row_type == "reserve"].copy()

    nd = r.apply(lambda x: norm_division(x.division, x.province), axis=1)
    r["division_type"] = [t for t, _ in nd]
    r["division_norm"] = [n for _, n in nd]
    r["band_norm"] = r.tribe_band.map(norm_band)

    # deterministic order before id assignment
    r = r.sort_values(
        ["province", "division_norm", "band_norm", "page", "reserve_no", "name"],
        na_position="last",
    ).reset_index(drop=True)

    used, ids = set(), []
    for x in r.itertuples():
        base = "-".join(
            p for p in (
                slugify(x.name) if isinstance(x.name, str) else None,
                slugify(x.reserve_no) if isinstance(x.reserve_no, str) else None,
                slugify(x.band_norm, 20) if isinstance(x.band_norm, str) else None,
            ) if p
        ) or f"p{x.page}"
        sid = base
        n = 2
        while sid in used:
            sid = f"{base}-{n}"
            n += 1
        used.add(sid)
        ids.append(sid)
    r["reserve_id"] = ids
    r["uri"] = URI_BASE + r.reserve_id
    r["uri_source"] = "minted"
    r["grounding_status"] = "minted"
    r["source"] = "schedule_1902"
    r["source_doc"] = "prov:dia_reserves_1902"

    out_cols = [
        "reserve_id", "uri", "uri_source", "grounding_status", "name",
        "reserve_no", "band_norm", "division_type", "division_norm",
        "province", "location", "acres", "acres_text", "remarks", "page",
        "confidence", "source", "source_doc",
    ]
    r[out_cols].to_parquet(ROOT / "registries/entities/reserves.parquet", index=False)

    # band facet: attestations from the main table + the band->agency index
    bands = (
        r.dropna(subset=["band_norm"])
        .groupby("band_norm")
        .agg(
            n_reserves=("reserve_id", "size"),
            provinces=("province", lambda s: sorted(set(s))),
            divisions=("division_norm", lambda s: sorted({d for d in s if d})),
            total_acres=("acres", "sum"),
        )
        .reset_index()
        .rename(columns={"band_norm": "name"})
    )
    bidx = pd.read_parquet(
        ROOT / "registries/entities/reserves_1902_bc_band_index.parquet"
    )
    bidx["name_norm"] = bidx.name.map(norm_band)
    bands = bands.merge(
        bidx[["name_norm", "agency"]].rename(
            columns={"name_norm": "name", "agency": "index_agency"}
        ),
        on="name", how="left",
    )
    bands["source"] = "schedule_1902"
    bands.to_parquet(ROOT / "registries/entities/bands_attested.parquet", index=False)

    # agency agreement: schedule agencies vs the agency registry
    ag = pd.read_parquet(ROOT / "registries/entities/agencies.parquet")
    sched_ag = (
        r[r.division_type == "agency"]
        .groupby("division_norm").size().rename("n_reserves").reset_index()
    )
    sched_ag["in_agency_registry"] = sched_ag.division_norm.isin(set(ag.name))
    sched_ag.to_csv(
        ROOT / "registries/crosswalks/reserve_agency_1902.csv", index=False
    )

    print(f"reserves minted: {len(r)} "
          f"({r.division_type.value_counts(dropna=False).to_dict()})")
    print(f"bands attested: {len(bands)} "
          f"({bands.index_agency.notna().sum()} with index agency)")
    print("schedule agencies vs registry:")
    print(sched_ag.to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
