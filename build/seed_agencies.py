#!/usr/bin/env python3
"""Seed the agency facet of the entity registry from DIA segment headings.

The segmenter's agency_letter/thematic headings name the reporting unit per
year. Normalizing them and grouping across years yields each unit's
attestation span - the raw material for persistent agency chains.

Output: registries/entities/agencies_attested.parquet
  one row per (normalized name, year) attestation, plus
  registries/entities/agencies.parquet - one row per normalized name with
  span, provinces, attestation count.
"""
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

UNIT_RE = re.compile(
    r"\b(AGENCY|SUPERINTENDENCY|INSPECTORATE|COMMISSIONER|SUPERINTENDENT)\b"
)
NOISE_RE = re.compile(
    r"^(OFFICE OF|REPORT OF|DEPARTMENT OF|TO THE|THE) |, (ESQ|Esq)"
)


def normalize(heading: str) -> str | None:
    h = heading.upper().strip()
    h = re.sub(r"[.,;:]+$", "", h)
    h = re.sub(r"\s+", " ", h)
    h = re.sub(r"[’']", "'", h)
    if not UNIT_RE.search(h):
        return None
    if NOISE_RE.search(h):
        return None
    # drop trailing continuation markers and dates
    h = re.sub(r"\s*[-—]\s*(CONTINUED|CONCLUDED).*$", "", h)
    # common OCR: BATTEFORD -> BATTLEFORD
    h = h.replace("BATTEFORD", "BATTLEFORD")
    # unify unit word order: "AGENCY OF X" -> "X AGENCY" left as-is (rare)
    return h


def main():
    df = pd.read_parquet(ROOT / "registries/documents/dia_segments.parquet")
    df = df[df.doc_type == "annual_report"]
    df["report_year"] = df.tag.str.extract(r"(\d{4})").astype(int)
    df = df[df.kind.isin(["agency_letter", "thematic_section"])]
    df["norm"] = df.heading.map(normalize)
    att = df.dropna(subset=["norm"])[
        ["norm", "report_year", "tag", "segment_id", "province", "kind"]
    ].rename(columns={"norm": "name"})
    att.to_parquet(ROOT / "registries/entities/agencies_attested.parquet", index=False)

    grp = (
        att.groupby("name")
        .agg(
            first_year=("report_year", "min"),
            last_year=("report_year", "max"),
            n_attestations=("report_year", "size"),
            n_years=("report_year", "nunique"),
            provinces=("province", lambda s: sorted({p for p in s if p})),
        )
        .reset_index()
        .sort_values("n_attestations", ascending=False)
    )
    grp["unit_type"] = grp.name.str.extract(
        r"\b(AGENCY|SUPERINTENDENCY|INSPECTORATE|COMMISSIONER|SUPERINTENDENT)\b"
    )
    grp.to_parquet(ROOT / "registries/entities/agencies.parquet", index=False)

    print(f"{len(att)} attestations -> {len(grp)} distinct normalized names")
    print(grp.unit_type.value_counts().to_string())
    print("\nlong-lived units (>=15 years):",
          (grp.n_years >= 15).sum())
    print("single-year names (rename/OCR candidates):",
          (grp.n_years == 1).sum())
    print("\nTop 15 by attestations:")
    print(grp.head(15)[["name", "first_year", "last_year", "n_years"]]
          .to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
