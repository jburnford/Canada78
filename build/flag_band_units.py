#!/usr/bin/env python3
"""Flag the census rows that are administrative units, not bands.

The band census prints district, agency and inspectorate roll-ups, treaty-
level totals (1880s, before bands were itemised) and a Grand Recapitulation
beside the band rows, and `mint_bands_from_census.py` minted them all as
bands: yearly sums of the population series came to about twice the
Department's totals ("West Coast Agency" 71,012; "Mackenzie District" 70,000).
Ids are sequential, so the rows are flagged rather than re-minted:

  registries/crosswalks/band_unit_flags.csv   band_id, kind, name, province_pool, n_years, population_last

kind: unit (agency/district/inspectorate/superintendency), treaty_total,
recap (a total/recapitulation row or anything in the Grand Recapitulation
pool). `apply_column_maps.py` stamps their observations entity_type="unit",
so a sum over entity_type=="band" is a sum over bands.
"""
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
UNIT = re.compile(r"\b(?:agency|agencies|district|inspectorate|superintendency)\b", re.I)
TREATY = re.compile(r"\btreaty\s+no\b|\bindians of treaty\b", re.I)
RECAP = re.compile(r"\b(?:recapitulation|grand total|total)\b|"
                   r"^\W{0,3}(?:ontario|quebec|nova scotia|new brunswick|prince edward island|manitoba|saskatchewan|alberta|"
                   r"north-?west territories|british columbia|yukon(?: territory)?|province of [a-z ]+)\W{0,3}$", re.I)

b = pd.read_parquet(ROOT / "registries/entities/bands_census.parquet")
kind = pd.Series("", index=b.index)
kind[b.name.str.contains(RECAP) | b.province_pool.fillna("").str.contains("recapitulation", case=False)] = "recap"
kind[(kind == "") & b.name.str.contains(TREATY)] = "treaty_total"
kind[(kind == "") & b.name.str.contains(UNIT)] = "unit"
f = b[kind != ""].assign(kind=kind[kind != ""])[["band_id", "kind", "name", "province_pool", "n_years", "population_last"]]
f.to_csv(ROOT / "registries/crosswalks/band_unit_flags.csv", index=False)
print(f"flagged {len(f)} of {len(b)} band identities:", dict(f.kind.value_counts()))
