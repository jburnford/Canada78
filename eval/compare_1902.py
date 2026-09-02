#!/usr/bin/env python3
"""Score a model's 1902-Schedule extraction against the Sonnet extraction
(registries/entities/reserves_1902.parquet, 1,422 reserve rows + totals).

    python3 eval/compare_1902.py eval/results/sched1902_qwen38 [--chunks 0,5] [--report out.md]

Matching (per chunk, so chunk-boundary rows are compared like-for-like):
  1. exact key: (page, norm(division), reserve_no, norm(name))
  2. relaxed: (norm(division), reserve_no, norm(name)) within ±1 page
  3. name-only: norm(name) unique on both sides within the chunk
Unmatched Sonnet rows = misses; unmatched model rows = extras (either
hallucinations or rows Sonnet missed — the report lists both for review,
because the reference is itself a model extraction, not gold).

Field agreement on matched pairs: acres (numeric, exact), location, tribe_band,
remarks (normalized text: case/whitespace/punctuation-insensitive; plus a
'near' rate at ≥0.9 token Jaccard), confidence distribution.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "registries/entities/reserves_1902.parquet"


def norm(s):
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def toks(s):
    return set(norm(s).split())


def jacc(a, b):
    A, B = toks(a), toks(b)
    return 1.0 if not A and not B else len(A & B) / max(1, len(A | B))


def load_model(d, chunks):
    rows = []
    for f in sorted(Path(d).glob("out_*.jsonl")):
        c = int(f.stem.split("_")[1])
        if chunks and c not in chunks:
            continue
        for l in f.read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l); r["chunk"] = c; rows.append(r)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model_dir")
    ap.add_argument("--chunks", default=None)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()
    chunks = {int(c) for c in args.chunks.split(",")} if args.chunks else None
    ref = pd.read_parquet(REF)
    ref = ref[ref.chunk != 3]                      # chunk 3 = BC index pages, not table rows
    mod = load_model(args.model_dir, chunks)
    if not mod.empty:
        mod = mod[mod.chunk != 3]                  # same exclusion on the model side
        # pp. 43-61 are the BC reserve->agency index (parsed by code, not part of
        # the main-table reference); drop model rows from the pp.57-61 tail too
        mod = mod[~((mod.chunk == 4) & (pd.to_numeric(mod.page, errors="coerce") <= 61))]
    if mod.empty:
        print("no model rows"); return 1
    have = set(mod.chunk)
    ref = ref[ref.chunk.isin(have)]
    ref = ref[ref.row_type == "reserve"].copy()
    mod = mod[mod.get("row_type", "reserve").fillna("reserve") == "reserve"].copy()
    for df in (ref, mod):
        df["kd"], df["kn"] = df.division.map(norm), df.name.map(norm)
        df["kno"] = df.reserve_no.map(lambda x: norm(x) if x is not None and not (isinstance(x, float) and pd.isna(x)) else "")
        df["page"] = pd.to_numeric(df.page, errors="coerce").fillna(-1).astype(int)

    pairs, used_m = [], set()
    for c in sorted(have):
        R, M = ref[ref.chunk == c], mod[mod.chunk == c]
        m_by_exact, m_by_relax, m_by_name = {}, {}, {}
        for i, m in M.iterrows():
            m_by_exact.setdefault((m.page, m.kd, m.kno, m.kn), []).append(i)
            m_by_relax.setdefault((m.kd, m.kno, m.kn), []).append(i)
            m_by_name.setdefault(m.kn, []).append(i)
        for j, r in R.iterrows():
            cand = [i for i in m_by_exact.get((r.page, r.kd, r.kno, r.kn), []) if i not in used_m]
            how = "exact"
            if not cand:
                cand = [i for i in m_by_relax.get((r.kd, r.kno, r.kn), []) if i not in used_m
                        and abs(M.loc[i, "page"] - r.page) <= 1]
                how = "relaxed"
            if not cand and r.kn and len(m_by_name.get(r.kn, [])) == 1 and (R.kn == r.kn).sum() == 1:
                cand = [i for i in m_by_name[r.kn] if i not in used_m]
                how = "name"
            if cand:
                used_m.add(cand[0]); pairs.append((j, cand[0], how))
        # pass 4 — fuzzy: same reserve_no within ±1 page, accepted on equal
        # acres or name overlap (catches OCR/name variants between the two
        # extractions: 'Tat-sel-a-was' vs 'Tat-selawas', '- Concluded' headers)
        matched_r = {p[0] for p in pairs}
        for j, r in R.iterrows():
            if j in matched_r:
                continue
            best, best_s = None, 0.0
            for i, m in M.iterrows():
                if i in used_m or abs(m.page - r.page) > 1 or m.kno != r.kno:
                    continue
                acres_eq = (not pd.isna(r.acres) and m.get("acres") is not None
                            and not pd.isna(m.get("acres"))
                            and abs(float(r.acres) - float(m.get("acres"))) < 0.01)
                nj = jacc(r["name"], m.get("name"))
                s = (2.0 if acres_eq else 0.0) + nj + 0.5 * jacc(r.division, m.get("division"))
                if (acres_eq or nj >= 0.5) and s > best_s:
                    best, best_s = i, s
            if best is not None:
                used_m.add(best); pairs.append((j, best, "fuzzy"))
    miss = ref.loc[[j for j in ref.index if j not in {p[0] for p in pairs}]]
    extra = mod.loc[[i for i in mod.index if i not in used_m]]

    # field agreement
    agree = Counter(); near = Counter(); n = len(pairs)
    diffs = []
    for j, i, how in pairs:
        r, m = ref.loc[j], mod.loc[i]
        ra, ma = r.acres, m.get("acres")
        if (pd.isna(ra) and (ma is None or pd.isna(ma))) or (not pd.isna(ra) and ma is not None and not pd.isna(ma) and abs(float(ra) - float(ma)) < 0.01):
            agree["acres"] += 1
        else:
            diffs.append(("acres", r.page, r["name"], r.acres_text, m.get("acres_text")))
        for f in ("location", "tribe_band", "remarks"):
            if norm(r[f]) == norm(m.get(f)):
                agree[f] += 1; near[f] += 1
            elif jacc(r[f], m.get(f)) >= 0.9:
                near[f] += 1
            else:
                diffs.append((f, r.page, r["name"], r[f], m.get(f)))
        if r.kno == norm(m.get("reserve_no")):
            agree["reserve_no"] += 1
    hows = Counter(p[2] for p in pairs)
    lines = [f"# 1902 Schedule extraction: {Path(args.model_dir).name} vs Sonnet reference",
             f"chunks compared: {sorted(have)}",
             f"reference reserve rows: {len(ref):,} | model reserve rows: {len(mod):,}",
             f"matched: {n:,} ({n / max(1, len(ref)):.1%} of reference) — {dict(hows)}",
             f"missed (in reference, not found in model): {len(miss):,}",
             f"extra (in model, not in reference): {len(extra):,}", "",
             "## field agreement on matched rows"]
    for f in ("reserve_no", "acres", "location", "tribe_band", "remarks"):
        s = f"- {f}: exact {agree[f] / max(1, n):.1%}"
        if f in near:
            s += f", near (Jaccard≥0.9) {near[f] / max(1, n):.1%}"
        lines.append(s)
    lines += ["", "## confidence distribution",
              f"- reference: {ref.confidence.value_counts().to_dict()}",
              f"- model: {mod.get('confidence', pd.Series(dtype=str)).value_counts().to_dict()}", "",
              f"## missed rows (first 40 of {len(miss)})"]
    lines += [f"- p{r.page} {r.division} | {r.reserve_no} | {r['name']} | {r.acres_text}" for _, r in miss.head(40).iterrows()]
    lines += ["", f"## extra model rows (first 40 of {len(extra)})"]
    lines += [f"- p{r.page} {r.get('division')} | {r.get('reserve_no')} | {r.get('name')} | {r.get('acres_text')}" for _, r in extra.head(40).iterrows()]
    lines += ["", f"## field disagreements (first 60 of {len(diffs)})"]
    lines += [f"- [{f}] p{p} {nm}: ref={str(a)[:90]!r} vs model={str(b)[:90]!r}" for f, p, nm, a, b in diffs[:60]]
    text = "\n".join(lines)
    print(text)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
