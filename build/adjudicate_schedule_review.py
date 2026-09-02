#!/usr/bin/env python3
"""Adjudicate the schedule-edition review queues into curated dispositions.

    python3 build/adjudicate_schedule_review.py

Reads  registries/annotations/reserve_attestations.parquet
       registries/crosswalks/schedule_edition_review.csv
       curation/schedule_area_units.csv
Writes curation/schedule_edition_adjudications.csv — one row per review item
       with a disposition, confidence, and evidence. Consumed by later builds
       (attestation upgrades, wiki); never edits generated outputs directly.

Dispositions (auto-classified; spot-checked 2026-08-27):
  degenerate_row     name+band+no all null — extraction placeholder, drop
  duplicate_emission same (no, name/acres) as a matched row of the same
                     edition within ±2 pages — window-boundary double, drop
  renumbered_link    same band + name/acres agreement but different number —
                     proposed link (reserve renumbered between editions)
  greedy_steal       an exact registry candidate exists but was claimed by
                     another row of the same edition — proposed link to it
  absence_candidate  none of the above — plausibly a pre-1902 reserve that
                     was renamed/absorbed/omitted by 1902; historian review
  unit_mismatch      acres_change where the 1902 side is a square-miles
                     division (curation/schedule_area_units.csv)
  ocr_digit          acres_change with ratio ≈ ×10/×100/×1000 — digit error
  real_change_candidate  acres_change 1.2–9× — allotment/survey era signal
"""
import re
from pathlib import Path

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from link_schedule_editions import norm, norm_no, norm_band, prov_group, sim, acres_close  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main():
    A = pd.read_parquet(ROOT / "registries/annotations/reserve_attestations.parquet")
    RV = pd.read_csv(ROOT / "registries/crosswalks/schedule_edition_review.csv")
    units = pd.read_csv(ROOT / "curation/schedule_area_units.csv")
    sq_divs = {str(d).upper() for d, u in units[["division_norm", "unit"]].values if u == "square_miles"}
    reg = pd.read_parquet(ROOT / "registries/entities/reserves.parquet")
    matched = A[A.reserve_id.notna() & (A.match_tier != "anchor")]

    out = []
    for r in RV.itertuples():
        d = dict(queue=r.queue, edition=r.edition, page=r.page, province=r.province,
                 division=r.division, reserve_no=r.reserve_no, name=r.name,
                 tribe_band=r.tribe_band, acres_text=r.acres_text, note=r.note,
                 disposition=None, proposed_reserve_id=None, confidence=None, evidence=None)

        if r.queue == "acres_change":
            rid = r.name  # linker stores reserve_id in the name column for this queue
            m = re.match(r"(\d+): ([\d.]+) → (\d+): ([\d.]+)", str(r.note))
            a, b = (float(m.group(2)), float(m.group(4))) if m else (None, None)
            ratio = max(a, b) / max(min(a, b), 0.01) if a and b else None
            div = str(reg.set_index("reserve_id").division_norm.get(rid, "")).upper()
            if div in sq_divs:
                d.update(disposition="unit_mismatch", confidence="high",
                         evidence=f"1902 division {div} prints square miles under an Acres header")
            elif ratio and any(lo < ratio < hi for lo, hi in [(9, 11), (95, 105), (950, 1050)]):
                d.update(disposition="ocr_digit", confidence="medium",
                         evidence=f"ratio ≈ ×{round(ratio)} — decimal/digit OCR error; verify against page")
            elif ratio and ratio < 9:
                d.update(disposition="real_change_candidate", confidence="medium",
                         evidence="moderate change consistent with allotment/survey activity")
            else:
                d.update(disposition="real_change_candidate", confidence="low",
                         evidence=f"large irregular ratio {ratio and round(ratio, 1)} — verify against both pages")
            out.append(d)
            continue

        # unmatched / ambiguous rows
        name_null = pd.isna(r.name) or str(r.name) in ("", "None")
        band_null = pd.isna(r.tribe_band) or str(r.tribe_band) in ("", "None")
        no_null = pd.isna(r.reserve_no) or str(r.reserve_no) in ("", "None")
        if name_null and band_null and no_null:
            d.update(disposition="degenerate_row", confidence="high",
                     evidence="no name, band, or number — placeholder row")
            out.append(d)
            continue

        same_ed = matched[matched.edition == r.edition]
        kno, kn = norm_no(r.reserve_no), norm(r.name)
        # duplicate of an already-matched row nearby?
        dup = same_ed[(same_ed.reserve_no.map(norm_no) == kno)
                      & (abs(pd.to_numeric(same_ed.page, errors="coerce").fillna(-99) - (r.page or -1)) <= 2)]
        dup = dup[[sim(r.name, x) >= 0.7 or (not name_null and norm(x) == kn) for x in dup.name]] if len(dup) else dup
        if len(dup):
            x = dup.iloc[0]
            d.update(disposition="duplicate_emission", confidence="high",
                     proposed_reserve_id=x.reserve_id,
                     evidence=f"same no.+name as matched row p{x.page} → {x.reserve_id}")
            out.append(d)
            continue

        # exact registry row exists but was taken (greedy steal), or renumbering
        g = prov_group(r.province, r.division)
        cand = reg[[prov_group(p, dv) == g for p, dv in zip(reg.province, reg.division_norm)]]
        exact = cand[(cand.reserve_no.map(norm_no) == kno) & (cand.name.map(norm) == kn)] if kno and kn else cand.iloc[0:0]
        if len(exact) == 1:
            d.update(disposition="greedy_steal", confidence="medium",
                     proposed_reserve_id=exact.iloc[0].reserve_id,
                     evidence="exact registry row exists; one-to-one matching assigned it to another row — review both")
            out.append(d)
            continue
        if not band_null:
            kb = norm_band(r.tribe_band)
            same_band = cand[[norm_band(b) == kb or sim(str(b), str(r.tribe_band)) >= 0.75 for b in cand.band_norm]]
            near = same_band[[sim(r.name, x) >= 0.6 or acres_close_txt(r.acres_text, at) for x, at in
                              zip(same_band.name, same_band.acres_text)]] if len(same_band) else same_band
            if len(near) == 1:
                d.update(disposition="renumbered_link", confidence="medium",
                         proposed_reserve_id=near.iloc[0].reserve_id,
                         evidence=f"same band, name/acres agree, number differs "
                                  f"({r.reserve_no} → {near.iloc[0].reserve_no}) — renumbered between editions")
                out.append(d)
                continue
        # eastern editions lack bands and renumbered between editions (NS 1897
        # No.11 Medway -> 1902 No.14 Port Medway River): unique strong name match
        if g in ("NS", "NB", "PE", "QC") and kn:
            near = cand[[sim(r.name, x) >= 0.7
                         or (len(kn) >= 5 and kn in norm(x))
                         or (len(norm(x)) >= 5 and norm(x) in kn) for x in cand.name]]
            if len(near) == 1:
                d.update(disposition="renumbered_link", confidence="low",
                         proposed_reserve_id=near.iloc[0].reserve_id,
                         evidence=f"unique name match '{near.iloc[0]['name']}' in {g}; "
                                  f"number differs ({r.reserve_no} → {near.iloc[0].reserve_no}) — "
                                  f"eastern renumbering between editions; verify")
                out.append(d)
                continue
        d.update(disposition="absence_candidate", confidence="low",
                 evidence="no registry counterpart found — possibly renamed, absorbed, sold, or omitted by 1902")
        out.append(d)

    df = pd.DataFrame(out)
    df.to_csv(ROOT / "curation/schedule_edition_adjudications.csv", index=False)
    print(df.groupby(["queue", "disposition"]).size().to_string())
    print(f"\n→ curation/schedule_edition_adjudications.csv ({len(df)} rows)")


def acres_close_txt(a_txt, b_txt):
    def p(t):
        if t is None or (isinstance(t, float) and pd.isna(t)):
            return None
        s = str(t).replace(",", "")
        m = re.match(r"^(\d+) (\d{2})$", s)
        if m:
            return float(m.group(1)) + float(m.group(2)) / 100
        try:
            return float(s)
        except ValueError:
            return None
    a, b = p(a_txt), p(b_txt)
    return a is not None and b is not None and abs(a - b) < 0.01


if __name__ == "__main__":
    main()
