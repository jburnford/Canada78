#!/usr/bin/env python3
"""Collapse attested DIA agency names into persistent agency chains.

Model (identity-model workshop, resolved): one facet for
agency/superintendency/inspectorate with unit_type; a merged period is its
own chain, related to predecessors/successors via MERGED_INTO / SPLIT_INTO
events. Events are curation-backed: this script emits *candidates*
(overlapping token-sharing chains) into a review file; confirmed events live
in curation/agency_chain_events.csv, which the build consumes.

Inputs:  registries/entities/agencies_attested.parquet (1,800 attestations)
         registries/external/lincs_ia_activities_dedup.parquet (LINCS groups)
Outputs: registries/entities/agency_chains.parquet
         registries/entities/agency_chain_members.parquet (variant -> chain)
         registries/crosswalks/agency_chain_lincs.csv
         registries/crosswalks/agency_chain_event_candidates.csv (review)
"""
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
URI_BASE = "https://jimclifford.ca/canada50/agencies/"

OCR_FIXES = {
    "OKANAGON": "OKANAGAN",
    "BATTEFORD": "BATTLEFORD",
    "KAMLOOrS": "KAMLOOPS",
    "SIMILKANEEN": "SIMILKAMEEN",
}
DISTRICT_PREFIXES = ("ASSINIBOIA", "SASKATCHEWAN", "ALBERTA", "MANITOBA")
UNIT_WORDS = ("AGENCY", "SUPERINTENDENCY", "INSPECTORATE", "COMMISSIONER",
              "SUPERINTENDENT")
ORDINALS = {
    "1": "1ST", "2": "2ND", "3": "3RD", "4": "4TH", "5": "5TH", "6": "6TH",
    "L": "1ST", "I": "1ST",  # OCR
}


def canonicalize(name: str):
    """Return (canonical_name, unit_type) or (None, None) if not a unit."""
    h = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    h = re.sub(r"\s+", " ", h.upper()).strip()
    for bad, good in OCR_FIXES.items():
        h = h.replace(bad, good)
    # strip report-title wrappers
    h = re.sub(r"^GENERAL REPORT OF THE ", "", h)
    h = re.sub(r"^(ANNUAL )?REPORT (OF|ON) (THE )?", "", h)
    # cut trailing dateline/address junk: anything after the unit word
    unit = None
    for w in UNIT_WORDS:
        m = re.search(rf"\b{w}\b", h)
        if m:
            unit = w
            # keep division designators after SUPERINTENDENCY
            tail = h[m.end():]
            dm = re.search(
                r"[-, ]*\(?(?:DIVISION\s*(?:NO\.?\s*)?([A-Z0-9]+)|"
                r"([0-9LI])(?:ST|ND|RD|TH)?\s*DIVISION)", tail)
            if dm:
                d = (dm.group(1) or dm.group(2)).strip(". ")
                d = ORDINALS.get(d, d if d.endswith(("ST", "ND", "RD", "TH"))
                                 else d)
                if not d.endswith(("ST", "ND", "RD", "TH")):
                    d = ORDINALS.get(d[-1], d)
                h = h[: m.end()] + f" - {d} DIVISION"
            else:
                h = h[: m.end()]
            break
    if unit is None:
        return None, None
    # normalize joiners and INDIAN infix
    h = re.sub(r"\s+AND\s+", " - ", h)
    h = re.sub(r"\s*-\s*", " - ", h)
    h = h.replace(" INDIAN AGENCY", " AGENCY")
    h = re.sub(r"^INDIAN AGENCY$", "AGENCY", h)
    # strip leading district/treaty prefix: "ASSINIBOIA - FILE HILLS AGENCY",
    # "TREATY NO. 4, INDIAN HEAD AGENCY"
    for d in DISTRICT_PREFIXES:
        h = re.sub(rf"^{d} - (?=.+\b(AGENCY|SUPERINTENDENCY|INSPECTORATE)\b)",
                   "", h)
    h = re.sub(r"^TREATY NO\.? ?\d+[, -]+", "", h)
    # strip leading ordinals/articles and provinces embedded mid-name
    h = re.sub(r", (ONTARIO|N\.? ?-? ?W\.? ?T\.?|B\.? ?C\.?)( -)?", " -", h)
    h = re.sub(r"\s+", " ", h).strip(" -,")
    if h in UNIT_WORDS or len(h.replace(unit, "").strip(" -")) < 3:
        return None, None  # bare "AGENCY" etc. — no identity to chain on
    return h, unit


def slugify(text: str) -> str:
    t = re.sub(r"[^A-Za-z0-9]+", "-", text.lower()).strip("-")
    return t


def main():
    att = pd.read_parquet(ROOT / "registries/entities/agencies_attested.parquet")
    canon = att.name.map(lambda n: canonicalize(n))
    att["canonical"] = [c for c, _ in canon]
    att["unit_type"] = [u for _, u in canon]
    att = att.dropna(subset=["canonical"])

    chains = (
        att.groupby(["canonical", "unit_type"])
        .agg(
            first_year=("report_year", "min"),
            last_year=("report_year", "max"),
            n_years=("report_year", "nunique"),
            n_attestations=("report_year", "size"),
            n_variants=("name", "nunique"),
            provinces=("province", lambda s: sorted({p for p in s if p})),
        )
        .reset_index()
        .sort_values("n_attestations", ascending=False)
    )
    chains["chain_id"] = "AG-" + chains.canonical.map(slugify)
    chains["uri"] = URI_BASE + chains.canonical.map(slugify)

    members = att[["name", "canonical", "unit_type", "report_year", "tag",
                   "segment_id"]].merge(
        chains[["canonical", "unit_type", "chain_id"]],
        on=["canonical", "unit_type"], how="left")

    # LINCS crosswalk on canonical names
    lincs = pd.read_parquet(
        ROOT / "registries/external/lincs_ia_activities_dedup.parquet")
    gl = lincs[["group", "group_label"]].dropna().drop_duplicates()
    def key(s):
        s = re.sub(r"\b(AGENCY|SUPERINTENDENCY|INSPECTORATE)\b", "", s.upper())
        return re.sub(r"\s+", " ", re.sub(r"[^A-Z ]", " ", s)).strip()

    lincs_map = {key(lbl): uri for uri, lbl in
                 zip(gl.group, gl.group_label)
                 if lbl != "Department of Indian Affairs"}
    chains["lincs_uri"] = chains.canonical.map(lambda c: lincs_map.get(key(c)))

    # event candidates: chains sharing a name token, temporally adjacent
    toks = {
        c.chain_id: set(re.findall(r"[A-Z]{4,}", c.canonical))
        - {"AGENCY", "SUPERINTENDENCY", "INSPECTORATE", "DIVISION", "INDIAN"}
        for c in chains.itertuples()
    }
    cand = []
    rows = list(chains.itertuples())
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if not (toks[a.chain_id] & toks[b.chain_id]):
                continue
            if a.canonical == b.canonical:
                continue
            gap = max(a.first_year, b.first_year) - min(a.last_year, b.last_year)
            if gap > 3:
                continue
            cand.append(dict(
                chain_a=a.chain_id, span_a=f"{a.first_year}-{a.last_year}",
                chain_b=b.chain_id, span_b=f"{b.first_year}-{b.last_year}",
                shared=" ".join(sorted(toks[a.chain_id] & toks[b.chain_id])),
                suggested="review",
            ))

    chains.to_parquet(ROOT / "registries/entities/agency_chains.parquet",
                      index=False)
    members.to_parquet(
        ROOT / "registries/entities/agency_chain_members.parquet", index=False)
    chains[chains.lincs_uri.notna()][
        ["chain_id", "canonical", "unit_type", "lincs_uri", "first_year",
         "last_year"]
    ].to_csv(ROOT / "registries/crosswalks/agency_chain_lincs.csv", index=False)
    pd.DataFrame(cand).to_csv(
        ROOT / "registries/crosswalks/agency_chain_event_candidates.csv",
        index=False)

    print(f"{len(att)} attestations -> {len(chains)} chains "
          f"(was 570 raw names); {chains.lincs_uri.notna().sum()} LINCS-matched")
    print(chains.unit_type.value_counts().to_string())
    print(f"single-year chains remaining: {(chains.n_years == 1).sum()}")
    print(f"event candidates for review: {len(cand)}")
    ko = chains[chains.canonical.str.contains("KAMLOOPS|OKANAGAN")]
    print("\nKamloops/Okanagan test:")
    print(ko[["canonical", "first_year", "last_year", "n_years",
              "n_variants"]].to_string(index=False))
    ns = chains[chains.canonical.str.contains("NORTHERN SUPERINTENDENCY")]
    print("\nNorthern Superintendency test:")
    print(ns[["canonical", "first_year", "last_year", "n_variants"]]
          .to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
