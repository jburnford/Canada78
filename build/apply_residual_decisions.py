#!/usr/bin/env python3
"""Promote Qwen Tier-2 residual decisions into stand-off mention annotations.

    python3 build/apply_residual_decisions.py

Inputs:  eval/residuals_items.jsonl            (residual + offered candidates)
         eval/results/residuals_qwen38/decisions.jsonl
         registries/annotations/mention_residuals_dia.parquet (for doc/text_version)
Outputs: registries/annotations/mentions_dia_tier2.parquet
             same schema as mentions_dia.parquet; tier=2; confidence from the
             model; note = "tier2: <reason>"; only choices that were in the
             offered candidate list are accepted
         registries/crosswalks/residual_review.csv
             (a) invalid/unoffered choices — mostly bands missing from the
                 registry (mint candidates), (b) prefix-less ids normalised,
                 (c) low-confidence links, (d) 'none' decisions whose reason
                 names a band/people (candidate-generation gap)

Gate (same as Tier 0/1): high+medium are primary on wiki pages; low stays in
the unverified fold. Nothing here edits mentions_dia.parquet or registries/entities.
"""
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ITEMS = ROOT / "eval/residuals_items.jsonl"
DEC = ROOT / "eval/results/residuals_qwen38/decisions.jsonl"
TYPES = {"agency": "agency", "reserve": "reserve", "band": "band"}


def main():
    items = {}
    for l in ITEMS.read_text(encoding="utf-8").splitlines():
        if l.strip():
            it = json.loads(l); items[it["residual_idx"]] = it
    res = pd.read_parquet(ROOT / "registries/annotations/mention_residuals_dia.parquet")
    dec = [json.loads(l) for l in DEC.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"items {len(items):,} | decisions {len(dec):,}")

    out, review, stats = [], [], Counter()
    for d in dec:
        idx = d["residual_idx"]
        it = items.get(idx)
        if it is None:
            stats["orphan"] += 1; continue
        r = res.iloc[idx]
        offered = {c["id"] for c in it["candidates"]}
        choice = str(d.get("choice") or "none").strip()
        conf = d.get("confidence") if d.get("confidence") in ("high", "medium", "low") else "low"
        reason = str(d.get("reason") or "")
        base = dict(edition=None, residual_idx=idx, tag=it["tag"], segment_id=it["segment_id"],
                    surface=it["surface"], choice=choice, confidence=conf, reason=reason[:200])
        if choice.lower() == "none":
            stats["none"] += 1
            if re.search(r"\bband\b|people|tribe|nation", reason, re.I) and not any(c.startswith("band:") for c in offered):
                review.append(dict(base, queue="none_but_band_named",
                                   note="model says it is the band/people; no band candidate was offered"))
                stats["none_band_gap"] += 1
            continue
        # normalise prefix-less ids ("AG-x" → "agency:AG-x", "BAND-x" → "band:BAND-x")
        if ":" not in choice:
            pref = "agency:" if choice.startswith("AG-") else "band:" if choice.startswith("BAND-") else "reserve:"
            fixed = pref + choice
            if fixed in offered:
                review.append(dict(base, queue="prefix_normalised", note=f"{choice} → {fixed}"))
                choice = fixed; stats["prefix_fixed"] += 1
        if choice not in offered:
            q = "unoffered_band_missing_from_registry" if choice.startswith("band:") else "unoffered_id"
            review.append(dict(base, queue=q, note="choice not among offered candidates — not applied"))
            stats["unoffered"] += 1
            continue
        etype, eid = choice.split(":", 1)
        etype = TYPES.get(etype)
        if not etype:
            stats["badtype"] += 1; continue
        out.append(dict(doc_id=r.doc_id, tag=r.tag, segment_id=r.segment_id, text_version=r.text_version,
                        context_agency=r.context_agency, char_start=int(r.char_start), char_end=int(r.char_end),
                        surface=r.surface, entity_type=etype, entity_id=eid, tier=2, confidence=conf,
                        note=f"tier2: {reason[:160]}"))
        stats[f"linked_{conf}"] += 1
        if conf == "low":
            review.append(dict(base, queue="low_confidence_link", note="applied as unverified"))

    T2 = pd.DataFrame(out)
    T2.to_parquet(ROOT / "registries/annotations/mentions_dia_tier2.parquet", index=False)
    RV = pd.DataFrame(review)
    RV.to_csv(ROOT / "registries/crosswalks/residual_review.csv", index=False)
    print(dict(stats))
    print(f"tier-2 mentions: {len(T2):,} → registries/annotations/mentions_dia_tier2.parquet")
    print(f"review rows: {len(RV):,} → registries/crosswalks/residual_review.csv")
    if len(RV):
        print(RV.queue.value_counts().to_string())
    missing = RV[RV.queue == "unoffered_band_missing_from_registry"].choice.value_counts().head(12)
    if len(missing):
        print("\nbands the model wanted that the registry lacks (top):"); print(missing.to_string())


if __name__ == "__main__":
    main()
