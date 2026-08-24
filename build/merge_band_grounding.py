#!/usr/bin/env python3
"""Merge the Wikidata grounding pass into the band facet.

Per identity-model decision #1: historical bands are minted entities;
a matched modern First Nation QID is a SUCCESSION link (succeeded_by_qid),
not an identity claim. 'people' matches land in people_qid (ethnic-group
context, e.g. Micmac -> Mi'kmaq), never in the succession column.

Inputs:  scratchpad band_grounding/grounded_{0,1,2}.jsonl (from the MCP
         vector-search agents), registries/entities/bands_attested.parquet
Outputs: registries/entities/bands.parquet
         registries/crosswalks/band_wikidata_review.csv (medium/low/none)
         registries/external/band_grounding/ (raw agent outputs, provenance)
"""
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SDIR = Path(
    "/tmp/claude-1000/-home-jic823-Canada50/7c5fc6a5-5a1f-415d-bb1b-1114f998121e/scratchpad/band_grounding"
)
URI_BASE = "https://jimclifford.ca/canada50/bands/"


def slugify(text, maxlen=50):
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-").lower()
    return t[:maxlen].rstrip("-") or "band"


def main():
    rows = []
    for f in sorted(SDIR.glob("grounded_*.jsonl")):
        for line in open(f, encoding="utf-8"):
            if line.strip():
                rows.append(json.loads(line))
    g = pd.DataFrame(rows).drop_duplicates(subset=["name"], keep="first")
    print(f"grounding rows: {len(g)}")

    b = pd.read_parquet(ROOT / "registries/entities/bands_attested.parquet")
    m = b.merge(g, on="name", how="left", suffixes=("", "_g"))
    missing = m[m.match_type.isna()]
    if len(missing):
        print(f"WARNING: {len(missing)} attested bands missing grounding: "
              f"{missing.name.tolist()[:5]}")

    used, ids = set(), []
    for n in m.name:
        s = slugify(n)
        k = 2
        while s in used:
            s = f"{slugify(n)}-{k}"
            k += 1
        used.add(s)
        ids.append(s)
    m["band_id"] = ["BAND-" + s for s in ids]
    m["uri"] = URI_BASE + pd.Series(ids)
    m["uri_source"] = "minted"
    m["succeeded_by_qid"] = m.qid.where(m.match_type == "modern_first_nation")
    m["people_qid"] = m.qid.where(m.match_type == "people")
    m["grounding_status"] = m.match_type.map(
        {"modern_first_nation": "succession_linked",
         "people": "people_linked", "none": "minted_only"}
    ).fillna("pending")

    out_cols = ["band_id", "uri", "uri_source", "name", "provinces",
                "divisions", "index_agency", "n_reserves", "total_acres",
                "succeeded_by_qid", "people_qid", "wd_label",
                "wd_description", "grounding_status", "confidence", "notes",
                "source"]
    m[out_cols].to_parquet(ROOT / "registries/entities/bands.parquet",
                           index=False)

    review = m[(m.confidence.isin(["medium", "low"])) |
               (m.match_type == "none")]
    review[["band_id", "name", "match_type", "confidence", "qid", "wd_label",
            "notes"]].to_csv(
        ROOT / "registries/crosswalks/band_wikidata_review.csv", index=False)

    dest = ROOT / "registries/external/band_grounding"
    dest.mkdir(exist_ok=True)
    for f in SDIR.glob("*.json*"):
        shutil.copy2(f, dest / f.name)

    print(f"bands: {len(m)} | status: "
          f"{m.grounding_status.value_counts().to_dict()}")
    print(f"confidence: {m.confidence.value_counts().to_dict()}")
    print(f"review queue: {len(review)} rows")


if __name__ == "__main__":
    sys.exit(main())
