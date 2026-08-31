#!/usr/bin/env python3
"""Officers (Return A) rows -> LINCS Indian Affairs agent URIs (KG_BUILD_PLAN §1.3, step 6).

    python3 build/link_officers_lincs.py [--accept 0.90] [--margin 0.015]

LINCS is the identity authority for DIA staff, not us: the user's team
resolved Ben Hoy's 14,477 person-year rows into 2,468 person URIs, including
the 21 ambiguous names they deliberately split into separate people. So this
script mints nothing. It attaches a LINCS `agent` URI to each of our Return A
rows and keeps what the LINCS activity model does not carry — per-year
designation, salary, appointment date, first-civil-service date, and our own
page-level provenance.

Matching runs in three passes over `build/person_names.py`:

1. **direct** — within a report year, score our name against every LINCS agent
   with an activity that year; accept a clear winner.
2. **career** — a name string that resolved to exactly one agent in its other
   years carries that identity into a year where LINCS has no activity. This
   is the pass that contributes something: our returns attest years the LINCS
   activity list does not cover, and the name string is the same printed
   string, not a fuzzy neighbour.
3. **review** — everything else, with its candidates, to CSV. Nothing below
   the gate is applied.

The foreign chunks are dropped first. Six— now seven —officers chunk
directories hold a stray chunk cut from the Auditor General's ledgers or
Returns C-G; those rows are real departmental staff (retired allowances,
teachers' salaries) but they are not Return A, and mixing them in would
attest an officer's designation and salary that the return never printed.
They are written to their own file rather than discarded.

Outputs: registries/annotations/officer_attestations.parquet  one row per Return A row
         registries/annotations/officer_rows_foreign.parquet  the non-Return-A residue
         registries/crosswalks/officers_lincs.csv             (name, year) -> agent
         registries/crosswalks/officers_lincs_review.csv      what to check by hand
"""
import argparse
import collections
import csv
import glob
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))
sys.path.insert(0, str(ROOT / "eval"))

from person_names import blocking_keys, generation, parse, score  # noqa: E402

LINCS = ROOT / "registries/external/lincs_ia_activities_dedup.parquet"
DIA_SESSIONAL = ROOT / "registries/crosswalks/dia_sessional.csv"


def foreign_chunks(year, gap=40):
    """Chunk ids cut far outside the Return A page block for this year.

    Shared logic with `eval/compare_officers_hoy.py`: the stray chunks sit
    hundreds of pages from the staff list, so distance from the first chunk
    identifies them without a hand-maintained list.
    """
    rj = ROOT / f"eval/results/officers{year}_qwen38_medium/run.json"
    if not rj.exists():
        return {}
    cfg = json.load(open(rj))
    starts = {k: int(re.match(r"(\d+)", v["pages"]).group(1))
              for k, v in cfg.items() if v.get("pages")}
    if not starts:
        return {}
    base = min(starts.values())
    return {k: cfg[k]["pages"] for k, s in starts.items() if s - base > gap}


def load_officer_rows():
    """Every extracted officer row, tagged with its year, chunk and Return A status."""
    out = []
    for d in sorted(glob.glob(str(ROOT / "eval/results/officers*_qwen38_medium"))):
        year = int(re.search(r"officers(\d{4})", d).group(1))
        skip = set(foreign_chunks(year))
        for path in sorted(glob.glob(d + "/out_*.jsonl")):
            chunk = re.search(r"(out_\d+\.jsonl)$", path).group(1)
            cid = re.search(r"out_(\d+)\.jsonl$", path).group(1)
            for i, line in enumerate(open(path)):
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict) or r.get("row_type") != "officer":
                    continue
                if not r.get("name"):
                    continue
                extras = r.get("extras") or []
                out.append({
                    "year": year,
                    "chunk": chunk,
                    "row_idx": i,
                    "in_return_a": cid not in skip,
                    "page": r.get("page"),
                    "section": r.get("section"),
                    "name_as_printed": str(r["name"]).strip(),
                    "designation": r.get("designation"),
                    "salary": r.get("salary"),
                    "appointed": r.get("appointed"),
                    "appointed_by": r.get("appointed_by"),
                    "first_civil": r.get("first_civil"),
                    "note": "; ".join(str(e) for e in extras if e and e != "..."),
                    "confidence": r.get("confidence"),
                })
    return pd.DataFrame(out)


def load_lincs():
    """LINCS agents indexed by (blocking key, year), plus a per-agent year span."""
    li = pd.read_parquet(LINCS)
    li["year"] = pd.to_datetime(li.begin, errors="coerce").dt.year
    parsed, buckets = {}, collections.defaultdict(set)
    for lb, y, ag in zip(li.agent_label, li.year, li.agent):
        if pd.isna(y):
            continue
        y = int(y)
        if ag not in parsed:
            parsed[ag] = (parse(lb), str(lb))
        for k in blocking_keys(parsed[ag][0]):
            buckets[(k, y)].add(ag)
    years = li.groupby("agent").year.agg(["min", "max"]).to_dict("index")
    return li, parsed, buckets, years


def candidates(pn, year, parsed, buckets):
    """LINCS agents active in `year` whose name scores against `pn`, best first."""
    pool = set()
    for k in blocking_keys(pn):
        pool |= buckets.get((k, year), set())
    scored = [(score(pn, parsed[a][0]), a) for a in pool]
    scored = [(s, a) for s, a in scored if s > 0]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accept", type=float, default=0.90,
                    help="minimum score for an automatic link")
    ap.add_argument("--margin", type=float, default=0.015,
                    help="minimum lead over the runner-up agent. 0.015 admits "
                         "an exact-initials match over a merely prefix-"
                         "compatible rival (\"J. Macdonald\" beating \"John A. "
                         "Macdonald\" by 0.018) while still refusing a true tie")
    ap.add_argument("--career-slack", type=int, default=3,
                    help="years a career link may reach beyond the agent's LINCS span")
    args = ap.parse_args()

    rows = load_officer_rows()
    foreign = rows[~rows.in_return_a].copy()
    rows = rows[rows.in_return_a].copy()
    print(f"officer rows: {len(rows)} Return A + {len(foreign)} foreign "
          f"({rows.year.nunique()} years)")

    li, parsed, buckets, spans = load_lincs()
    print(f"LINCS: {li.agent.nunique()} agents, {len(li)} activities "
          f"{int(li.year.min())}-{int(li.year.max())}")

    # ---- pass 1: direct, within the report year -------------------------
    pairs = (rows.groupby(["name_as_printed", "year"]).size()
             .rename("n_rows").reset_index())
    decided, review = {}, {}
    for r in pairs.itertuples():
        pn = parse(r.name_as_printed)
        if not pn[0]:
            review[(r.name_as_printed, r.year)] = ("unparseable", 0.0, 0.0, [])
            continue
        cands = candidates(pn, r.year, parsed, buckets)
        if not cands:
            review[(r.name_as_printed, r.year)] = ("no_candidate", 0.0, 0.0, [])
            continue
        top, runner = cands[0], (cands[1] if len(cands) > 1 else (0.0, None))
        margin = top[0] - runner[0]
        if top[0] >= args.accept and margin >= args.margin:
            decided[(r.name_as_printed, r.year)] = ("direct", top[1], top[0], margin)
        else:
            reason = "below_gate" if top[0] < args.accept else "ambiguous"
            review[(r.name_as_printed, r.year)] = (reason, top[0], margin, cands[:4])

    print(f"pass 1 direct: {len(decided)}/{len(pairs)} name-years "
          f"({len(decided)/len(pairs):.1%})")

    # ---- pass 2: carry a settled identity across years ------------------
    # A printed name string that resolved to exactly one agent is that person;
    # LINCS simply has no activity for the year we are looking at.
    by_name = collections.defaultdict(set)
    for (nm, _y), (_m, ag, _s, _g) in decided.items():
        by_name[nm].add(ag)
    settled = {nm: next(iter(a)) for nm, a in by_name.items() if len(a) == 1}

    carried = 0
    for key in list(review):
        nm, year = key
        ag = settled.get(nm)
        if not ag:
            continue
        reason, sc, margin, cands = review[key]
        # Do not overrule a live competitor in the target year.
        if reason == "ambiguous" and cands and cands[0][1] != ag:
            continue
        lo, hi = spans[ag]["min"], spans[ag]["max"]
        if not (lo - args.career_slack <= year <= hi + args.career_slack):
            continue
        decided[key] = ("career", ag, sc, margin)
        del review[key]
        carried += 1
    print(f"pass 2 career:  +{carried} name-years "
          f"-> {len(decided)}/{len(pairs)} ({len(decided)/len(pairs):.1%})")

    # ---- conflicts: two printed names claiming one agent in one year ----
    # A return really does print one man twice when he holds two posts
    # ("Rand, F.A., M.D." as Indian Agent and as Medical Officer), so a shared
    # agent is not by itself an error. It is an error when the two printed
    # names are *different people*: "F.H. Byshe" and "F.R. Byshe" appear in the
    # same year as Third Class Clerk and Messenger, and only one of them is
    # LINCS's F.R. Byshe. Keep the best claimant; send a rival that does not
    # read as the same name — or that carries a jr/sr the other lacks — back
    # to review rather than silently asserting the link.
    claim = collections.defaultdict(list)
    for (nm, year), (method, ag, sc, margin) in decided.items():
        claim[(ag, year)].append((nm, method, sc))

    conflicts, demoted = {}, 0
    for (ag, year), claimants in claim.items():
        if len(claimants) < 2:
            continue
        claimants.sort(key=lambda t: (-t[2], t[0]))
        keep_nm = claimants[0][0]
        keep = parse(keep_nm)
        same = [claimants[0]]
        for nm, method, sc in claimants[1:]:
            alike = score(keep, parse(nm)) >= 0.98
            if alike and generation(nm) == generation(keep_nm):
                same.append((nm, method, sc))
            else:
                review[(nm, year)] = ("conflict", sc, 0.0,
                                      [(sc, ag)] if ag in parsed else [])
                del decided[(nm, year)]
                demoted += 1
        if len(same) > 1:
            conflicts[(ag, year)] = same
    print(f"conflicts: {demoted} rival claims demoted to review; "
          f"{len(conflicts)} agent-years kept as one person in two posts")

    # ---- emit ------------------------------------------------------------
    label = {a: parsed[a][1] for a in parsed}
    key = list(zip(rows.name_as_printed, rows.year))
    rows["match_method"] = [decided.get(k, ("unlinked",))[0] for k in key]
    rows["lincs_agent"] = [decided[k][1] if k in decided else None for k in key]
    rows["lincs_label"] = [label.get(a) if a else None for a in rows.lincs_agent]
    rows["match_score"] = [round(decided[k][2], 4) if k in decided else None for k in key]
    rows["match_margin"] = [round(decided[k][3], 4) if k in decided else None for k in key]
    rows["conflicted"] = [(a, y) in conflicts for a, y in zip(rows.lincs_agent, rows.year)]

    # Provenance: the sessional paper the report year was tabled as.
    if DIA_SESSIONAL.exists():
        ds = pd.read_csv(DIA_SESSIONAL)
        paper = dict(zip(ds.report_year, ds.paper_id))
        rows["paper_id"] = rows.year.map(paper)
        foreign["paper_id"] = foreign.year.map(paper)

    out_a = ROOT / "registries/annotations/officer_attestations.parquet"
    rows.to_parquet(out_a, index=False)
    foreign.to_parquet(ROOT / "registries/annotations/officer_rows_foreign.parquet",
                       index=False)

    xw = (rows[rows.lincs_agent.notna()]
          .groupby(["name_as_printed", "year", "lincs_agent", "lincs_label",
                    "match_method", "match_score", "match_margin"])
          .size().rename("n_rows").reset_index()
          .sort_values(["name_as_printed", "year"]))
    xw.to_csv(ROOT / "registries/crosswalks/officers_lincs.csv", index=False)

    with open(ROOT / "registries/crosswalks/officers_lincs_review.csv", "w",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name_as_printed", "year", "reason", "top_score", "margin",
                    "n_rows", "designation", "section", "page", "candidates"])
        nrows = dict(zip(zip(pairs.name_as_printed, pairs.year), pairs.n_rows))
        ctx = (rows.groupby(["name_as_printed", "year"])
               .agg(designation=("designation", "first"),
                    section=("section", "first"), page=("page", "first")))
        for (nm, year), (reason, sc, margin, cands) in sorted(
                review.items(), key=lambda kv: (kv[1][0], -kv[1][1])):
            c = ctx.loc[(nm, year)] if (nm, year) in ctx.index else None
            w.writerow([nm, year, reason, round(sc, 4), round(margin, 4),
                        nrows.get((nm, year), 0),
                        "" if c is None else c.designation,
                        "" if c is None else c.section,
                        "" if c is None else c.page,
                        " | ".join(f"{label[a]} ({s:.3f})" for s, a in cands)])

    linked = rows.lincs_agent.notna().sum()
    print(f"\nrows linked: {linked}/{len(rows)} = {linked/len(rows):.1%}")
    print(f"distinct LINCS persons reached: {rows.lincs_agent.nunique()}"
          f"/{li.agent.nunique()}")
    print(f"review queue: {len(review)} name-years "
          f"({sum(nrows.get(k, 0) for k in review)} rows)")
    print("\nper-year link rate:")
    t = rows.assign(ok=rows.lincs_agent.notna()).groupby("year").ok.agg(["sum", "count"])
    t["rate"] = (t["sum"] / t["count"]).round(3)
    print(t.to_string())


if __name__ == "__main__":
    main()
