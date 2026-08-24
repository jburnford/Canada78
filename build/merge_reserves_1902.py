#!/usr/bin/env python3
"""Merge the Sonnet extraction of the 1902 Schedule of Indian Reserves
(dia_reserves_1902.md) into registry seeds.

Inputs (scratchpad, produced by the 14-agent extraction pass + 2 code sweeps):
  out_NN.jsonl            main-table rows per chunk (chunk 03 = BC reserve
                          index pages 43-56, schema-mapped)
  band_index_p41_42.json  BC band->agency index (code-parsed)
  reserve_index_p57_61.json  BC reserve->agency index tail (code-parsed)

Outputs:
  registries/entities/reserves_1902.parquet            main table rows
  registries/entities/reserves_1902_bc_reserve_index.parquet
  registries/entities/reserves_1902_bc_band_index.parquet

Boundary-row repairs (rows the chunking truncated, reconstructed from the
source text by hand) are applied here so the merge is rerunnable.
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SDIR = Path(
    "/tmp/claude-1000/-home-jic823-Canada50/7c5fc6a5-5a1f-415d-bb1b-1114f998121e/scratchpad/sched1902"
)

# hand-reconstructed boundary rows (verified against the source markdown)
REPAIRS = {
    # (chunk, page, name) -> replacement fields
    (1, 28, "Sarnia"): dict(
        location="In the township of Sarnia, county of Lambton.",
        tribe_band="Chippewas of Chenail Ecarté and St. Clair.",
        acres_text="4,943",
        acres=4943.0,
        remarks=(
            "Reserved by these Indians in the cession of a large tract in "
            "the London and Western districts made by them in 1827."
        ),
        confidence="high",
    ),
    (10, 154, "Kokyet"): dict(
        location=(
            "Coast district, on Yeo island, at the mouth of Ellerslie channel."
        ),
        tribe_band="Kokyet",
        acres_text="185",
        acres=185.0,
        remarks=(
            "Allotted by Commissioner O'Reilly, August 29, 1882. Surveyed, "
            "1888. Final confirmation, May 18, 1889."
        ),
        confidence="high",
    ),
}


def main():
    rows = []
    for f in sorted(SDIR.glob("out_*.jsonl")):
        chunk = int(f.stem.split("_")[1])
        for line in open(f, encoding="utf-8"):
            if line.strip():
                d = json.loads(line)
                d["chunk"] = chunk
                rows.append(d)
    df = pd.DataFrame(rows)

    # BC reserve index (chunk 03 mapped name->name, agency->location)
    idx = df[df.chunk == 3][["page", "name", "location"]].rename(
        columns={"location": "agency"}
    )
    tail = pd.DataFrame(json.load(open(SDIR / "reserve_index_p57_61.json")))
    reserve_index = pd.concat([idx, tail], ignore_index=True)
    band_index = pd.DataFrame(json.load(open(SDIR / "band_index_p41_42.json")))

    main = df[df.chunk != 3].copy()

    # apply boundary repairs
    for (chunk, page, name), fields in REPAIRS.items():
        mask = (main.chunk == chunk) & (main.page == page) & (main.name == name)
        if mask.sum() != 1:
            # fall back: match by name+chunk only
            mask = (main.chunk == chunk) & (main.name == name)
        for k, v in fields.items():
            main.loc[mask, k] = v
        main.loc[mask, "boundary_repaired"] = True
        print(f"repair {(chunk, page, name)}: {int(mask.sum())} row(s)")

    # drop unidentifiable truncated partials (no name, no reserve_no): their
    # full versions live in the neighbouring chunk
    partial = main[main.name.isna() & main.reserve_no.isna()]
    print(f"dropping {len(partial)} unidentifiable boundary partials "
          f"(chunks {sorted(partial.chunk.unique())})")
    main = main.drop(partial.index)

    # cross-chunk duplicate check at boundaries
    key = main[main.row_type == "reserve"].groupby(
        ["province", "division", "reserve_no", "name"], dropna=False
    ).chunk.nunique()
    dups = key[key > 1]
    print(f"rows appearing in >1 chunk (boundary double-capture): {len(dups)}")
    if len(dups):
        print(dups.head(10).to_string())

    main.to_parquet(ROOT / "registries/entities/reserves_1902.parquet", index=False)
    reserve_index.to_parquet(
        ROOT / "registries/entities/reserves_1902_bc_reserve_index.parquet",
        index=False,
    )
    band_index.to_parquet(
        ROOT / "registries/entities/reserves_1902_bc_band_index.parquet",
        index=False,
    )

    # validation: BC main-table names vs the schedule's own BC reserve index
    bc = main[(main.province == "BRITISH COLUMBIA") & (main.row_type == "reserve")]
    norm = lambda s: s.str.upper().str.replace(r"[^A-Z]", "", regex=True)
    bc_names = set(norm(bc.name.dropna()))
    idx_names = set(norm(reserve_index.name.dropna()))
    inter = bc_names & idx_names
    print(f"\nmain table: {len(main)} rows "
          f"({(main.row_type == 'reserve').sum()} reserve, "
          f"{(main.row_type == 'total').sum()} total)")
    print(f"BC reserve index: {len(reserve_index)} entries; "
          f"band index: {len(band_index)} entries")
    print(f"BC cross-check: {len(bc_names)} distinct main-table names, "
          f"{len(idx_names)} index names, {len(inter)} exact-normalized matches "
          f"({100 * len(inter) / max(1, len(idx_names)):.0f}% of index)")
    print("\nrows by division (top 15):")
    print(main[main.row_type == "reserve"].division.value_counts().head(15).to_string())
    print("\nacres coverage:", main.acres.notna().sum(), "rows;",
          f"total {main[main.row_type=='reserve'].acres.sum():,.0f} acres (reserve rows)")


if __name__ == "__main__":
    sys.exit(main())
