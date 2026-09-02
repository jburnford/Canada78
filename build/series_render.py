#!/usr/bin/env python3
"""Render observation series as HTML for the wiki (KG_BUILD_PLAN §1.4, step 8).

Imported by `build/gen_wiki.py`; run directly for a quick look:

    python3 build/series_render.py band_c00094
    python3 build/series_render.py --list

`registries/annotations/observations.parquet` holds one row per
(entity, series, year, value) across the census, agricultural and school
families.  A wiki page wants that as a small number of readable tables — the
headline series with a sparkline, the rest folded away — not 60 rows of tuples.

Series are grouped by the prefix of their id (`religion_`, `age_`, `crop_`,
`livestock_`…), which the column maps already assign, so a new series added to
a map appears here without any change to this file.
"""
import argparse
import html
from collections import OrderedDict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OBS = ROOT / "registries/annotations/observations.parquet"

# prefix -> (section heading, sort order); anything unmatched lands in "Other"
GROUPS = OrderedDict([
    ("population", ("Population", 0)),
    ("religion_", ("Religion as recorded", 2)),
    ("age_", ("Age and sex", 3)),
    ("sex_", ("Age and sex", 3)),
    ("births", ("Births, deaths and migration", 4)),
    ("deaths", ("Births, deaths and migration", 4)),
    ("migration_", ("Births, deaths and migration", 4)),
    ("land_", ("Land", 5)),
    ("crop_", ("Crops", 6)),
    ("livestock_", ("Live stock", 7)),
    ("building_", ("Buildings", 8)),
    ("implement_", ("Implements and vehicles", 9)),
    ("effects_", ("General effects", 10)),
    ("engaged_", ("Occupations", 11)),
    ("income_", ("Income", 12)),
    ("value_", ("Value of property", 13)),
    ("roll_", ("Enrolment", 1)),
    ("average_attendance", ("Enrolment", 1)),
    ("days_taught", ("Enrolment", 1)),
    ("standard_", ("Standards", 2)),
    ("trade_", ("Trades taught", 3)),
    ("grant_", ("Grant", 4)),
    ("per_capita_", ("Grant", 4)),
    ("trustfund_", ("Trust fund balances (Return B)", 14)),
])
# the one series per family worth a sparkline at the top of the page
HEADLINE = {"census": "population", "school": "roll_total", "agstat": "land_under_cultivation"}
ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]


def esc(s):
    return html.escape(str(s), quote=True)


def load():
    return pd.read_parquet(OBS) if OBS.exists() else pd.DataFrame()


def pretty(series_id):
    """`crop_other_roots_acres_sown` -> 'Other roots, acres sown'."""
    for prefix in GROUPS:
        if series_id.startswith(prefix) and prefix.endswith("_"):
            rest = series_id[len(prefix):]
            break
    else:
        rest = series_id
    for suffix, label in (("_acres_sown", ", acres sown"), ("_harvested", ", harvested"),
                          ("_tons", ", tons")):
        if rest.endswith(suffix):
            rest, tail = rest[: -len(suffix)], label
            break
    else:
        tail = ""
    rest = rest.replace("_", " ").strip()
    if rest in ROMAN:
        rest = rest.upper()
    return (rest[:1].upper() + rest[1:] if rest else series_id) + tail


def group_of(series_id):
    for prefix, (heading, order) in GROUPS.items():
        if series_id.startswith(prefix):
            return heading, order
    return "Other", 99


def sparkline(pairs, width=320, height=34):
    """Inline SVG; no scripts, no external assets — the wiki is static files."""
    pts = [(int(y), float(v)) for y, v in pairs if v is not None]
    if len(pts) < 2:
        return ""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span_x = max(1, x1 - x0)
    span_y = (y1 - y0) or 1
    pad = 3
    coords = " ".join(
        f"{pad + (x - x0) / span_x * (width - 2 * pad):.1f},"
        f"{height - pad - (v - y0) / span_y * (height - 2 * pad):.1f}"
        for x, v in pts)
    last_x, last_v = pts[-1]
    cx = pad + (last_x - x0) / span_x * (width - 2 * pad)
    cy = height - pad - (last_v - y0) / span_y * (height - 2 * pad)
    return (f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="{esc(f"{y0:g} to {y1:g}, {x0} to {x1}")}">'
            f'<polyline fill="none" stroke="#0055aa" stroke-width="1.5" points="{coords}"/>'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.4" fill="#0055aa"/></svg>')


def fmt(v):
    if v is None or pd.isna(v):
        return ""
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def series_section(obs, entity_id, max_open_rows=14):
    """The whole observations block for one entity, or '' if it has none."""
    e = obs[obs.entity_id == entity_id]
    if not len(e):
        return ""
    fam = e.source_family.mode().iat[0]
    years = sorted(e.year.unique())
    out = [f"<h2>Series from the annual tables</h2>",
           f"<p class=cite>{len(e):,} observations, {e.series_id.nunique()} series, "
           f"{years[0]}–{years[-1]}, from the {esc(fam)} tables. "
           f"Values are as printed; a blank is a year the table did not state one.</p>"]

    head = HEADLINE.get(fam)
    if head and (e.series_id == head).any():
        h = e[e.series_id == head].sort_values("year")
        pairs = list(zip(h.year, h.value))
        out.append(f"<div class=headline><b>{esc(pretty(head))}</b> "
                   f"{fmt(pairs[0][1])} ({pairs[0][0]}) → {fmt(pairs[-1][1])} ({pairs[-1][0]}) "
                   f"{sparkline(pairs)}</div>")

    sections = {}
    for sid in sorted(e.series_id.unique()):
        heading, order = group_of(sid)
        sections.setdefault((order, heading), []).append(sid)

    for (order, heading), sids in sorted(sections.items()):
        wide = e[e.series_id.isin(sids)].pivot_table(
            index="year", columns="series_id", values="value", aggfunc="first")
        wide = wide.reindex(sorted(wide.columns), axis=1)
        rows = ["<table><tr><th>Year</th>"
                + "".join(f"<th>{esc(pretty(c))}</th>" for c in wide.columns) + "</tr>"]
        for year, r in wide.iterrows():
            rows.append(f"<tr><td>{int(year)}</td>"
                        + "".join(f"<td>{fmt(v)}</td>" for v in r) + "</tr>")
        rows.append("</table>")
        table = "\n".join(rows)
        if len(wide) > max_open_rows or order > 2:
            out.append(f"<details><summary>{esc(heading)} "
                       f"<span class=badge>{len(wide.columns)} series, {len(wide)} years</span>"
                       f"</summary>{table}</details>")
        else:
            out.append(f"<h3>{esc(heading)}</h3>{table}")
    return "\n".join(out)


CSS = """
  .spark { vertical-align: middle; margin-left: .6em; }
  .headline { background: #f8f9fa; border-left: 3px solid #0066cc; padding: .5em .9em;
              margin: .8em 0; }
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("entity_id", nargs="?")
    ap.add_argument("--list", action="store_true", help="entities with the most observations")
    args = ap.parse_args()
    obs = load()
    if not len(obs):
        print("no observations.parquet — run build/apply_column_maps.py first")
        return
    if args.list or not args.entity_id:
        print(f"{len(obs):,} observations | "
              f"{obs.entity_id.nunique():,} entities | {obs.series_id.nunique()} series")
        top = obs.groupby(["entity_type", "entity_id"]).size().nlargest(12)
        for (et, eid), n in top.items():
            yr = obs[obs.entity_id == eid].year
            print(f"  {n:>6,}  {et:<8} {eid:<16} {yr.min()}-{yr.max()}")
        return
    print(series_section(obs, args.entity_id)[:4000])


if __name__ == "__main__":
    main()
