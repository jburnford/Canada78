#!/usr/bin/env python3
"""Turn extracted table rows into one observation table (KG_BUILD_PLAN §1.4, §2).

    python3 build/apply_column_maps.py [--family census] [--dry-run]

Every extraction family emits the same shape: a `headers` row carrying the
printed column labels, then data rows whose `values` are positionally aligned to
them.  So a family's column-semantics map is a list of rules from *printed
label* to *series id* (`curation/column_maps/<family>.yaml`) rather than the
per-format-era positional map the plan first assumed; a rule can still be
restricted with `years: [from, to]` where a label genuinely changed meaning.

Output: registries/annotations/observations.parquet
  entity_id entity_type series_id year value unit source_family paper_id
  page chunk row_idx confidence

and a coverage report naming every column label the map did not claim, so gaps
are visible rather than silent.  Census rows are attached to the band registry
by (year, chunk, printed name) via `band_attestations.parquet`; a row whose band
cannot be resolved is reported, not dropped into a null entity.
"""
import argparse
import collections
import csv
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd
import yaml

from mint_bands_from_census import classify_header
from mint_schools import is_school_header

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval/results"
MAPS = ROOT / "curation/column_maps"

FAMILIES = {
    # family: (result-dir glob, the row field naming the entity, header class)
    # `section` is the value classify_header() must return for a row to belong
    # to this family: the census page-span runs on into the agricultural tables
    # (and in the 1920s one chunk straddles both), exactly as in step 2.
    "census": ("census*_qwen38_medium", "band", "census"),
    "agstat": ("agstat*_qwen38_medium", "label", "agri"),
    "school": ("school*_qwen38_medium", "school", "school"),
}
NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def norm_label(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def norm_name(s):
    return norm_label(s)


def parse_value(raw):
    """'1,204' -> 1204.0; '...', '-', '*', '' and prose -> None.

    The printers used '...' for nil and '*' for a footnote; both are absence of
    a number, not zero, and must not become 0 in a population series.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in {"...", "..", ".", "-", "--", "—", "*", "†", "nil", "Nil", "N/A"}:
        return None
    m = NUMBER.fullmatch(s.replace(" ", ""))
    if not m:
        m2 = NUMBER.match(s.replace(" ", ""))
        if not m2:
            return None
        s = m2.group(0)
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


class ColumnMap:
    def __init__(self, path):
        cfg = yaml.safe_load(path.read_text())
        self.family = cfg["family"]
        self.entity_type = cfg.get("entity_type", "band")
        self.rules = [(re.compile(r["match"]), r) for r in cfg["series"]]
        self.ignore = [re.compile(p) for p in cfg.get("ignore", [])]
        self.bad_rules = collections.Counter()
        self.units = {r["id"]: r.get("unit", "") for _, r in self.rules if "{" not in r["id"]}
        self.labels = {r["id"]: r.get("label", r["id"]) for _, r in self.rules
                       if "{" not in r["id"]}

    def series_for(self, label, year):
        """(series_id, was_deliberately_ignored) for one printed column label.

        A rule's `id` may interpolate the regex's named groups — the
        agricultural tables print the same handful of shapes over ~1,270 labels
        ("Wheat. Acres Sown.", "Oats. Bush. Harvested.", one per crop), so
        `crop_{crop}_acres_sown` covers a whole family in one rule.
        """
        key = norm_label(label)
        if not key:
            return None, True
        for pat, rule in self.rules:
            lo, hi = (rule.get("years") or [0, 9999])[:2]
            if not (lo <= year <= hi):
                continue
            m = pat.search(key)
            if not m:
                continue
            sid = rule["id"]
            if "{" in sid:
                parts = {k: re.sub(r"\s+", "_", (v or "").strip())
                         for k, v in m.groupdict().items()}
                # A group that did not participate in the match interpolates as
                # "", collapsing every such label into a stub id like `land_`.
                # That happens when a rule alternates and only one branch names
                # the group — always split those into separate rules instead.
                missing = [k for k in re.findall(r"\{(\w+)\}", rule["id"]) if not parts.get(k)]
                if missing:
                    self.bad_rules[(rule["id"], tuple(missing))] += 1
                    return None, True
                sid = sid.format(**parts)
                self.units.setdefault(sid, rule.get("unit", ""))
                self.labels.setdefault(sid, rule.get("label", sid))
            return sid, False
        return None, any(p.search(key) for p in self.ignore)


def band_lookup():
    """(year, chunk, normalised printed name) -> band_id, from step 2's output."""
    att = pd.read_parquet(ROOT / "registries/annotations/band_attestations.parquet")
    out = {}
    for r in att.itertuples():
        out[(int(r.year), str(r.chunk), norm_name(r.name_as_printed))] = r.band_id
        out.setdefault((int(r.year), norm_name(r.name_as_printed)), r.band_id)
    return out


def agency_lookup():
    """Same, for the agencies minted from the agricultural series (step 4b)."""
    path = ROOT / "registries/annotations/agency_attestations.parquet"
    if not path.exists():
        return {}
    att = pd.read_parquet(path)
    out = {}
    for r in att.itertuples():
        out[(int(r.year), str(r.chunk), norm_name(r.name_as_printed))] = r.agency_id
        out.setdefault((int(r.year), norm_name(r.name_as_printed)), r.agency_id)
    return out


def school_lookup():
    """Same, for the schools minted from the school statements (step 5)."""
    path = ROOT / "registries/annotations/school_attestations.parquet"
    if not path.exists():
        return {}
    att = pd.read_parquet(path)
    out = {}
    for r in att.itertuples():
        out[(int(r.year), str(r.chunk), norm_name(r.name_as_printed))] = r.school_id
        out.setdefault((int(r.year), norm_name(r.name_as_printed)), r.school_id)
    return out


RESOLVERS = {"band": band_lookup, "agency": agency_lookup, "school": school_lookup}


def iter_rows(glob_pat, want_section):
    for d in sorted(RESULTS.glob(glob_pat)):
        m = re.search(r"(\d{4})", d.name)
        if not m:
            continue
        year = int(m.group(1))
        for f in sorted(d.glob("out_*.jsonl")):
            columns, section = [], "unknown"
            for idx, line in enumerate(f.read_text(errors="replace").splitlines()):
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict):
                    continue
                if r.get("row_type") == "headers":
                    columns = [str(c) for c in (r.get("columns") or [])]
                    # the school result dirs also carry census header blocks,
                    # and the census dirs carry agricultural ones, so each
                    # family answers to its own header test
                    section = "school" if is_school_header(r) else classify_header(r)
                    continue
                if want_section and section != want_section:
                    continue
                yield year, f.name, idx, columns, r


def consistency(df, tol=0.02):
    """Do the parts sum to the whole?

    The census prints a band's population and then breaks it down twice over —
    by religion and by age/sex — so each breakdown is an independent check on
    the other two.  Agreement means the columns were aligned to the right
    labels and the values read correctly; a large disagreement usually means a
    provincial *total* line was taken for a band, or a digit ran together in
    OCR.  Failures go to a review CSV rather than being corrected here.
    """
    if not len(df):
        return pd.DataFrame()
    w = df.pivot_table(index=["entity_id", "year"], columns="series_id",
                       values="value", aggfunc="first")
    if "population" not in w:
        return pd.DataFrame()
    groups = {
        "age": [c for c in w.columns if c.startswith("age_")],
        "religion": [c for c in w.columns if c.startswith("religion_")
                     and c not in ("religion_protestant", "religion_claimed_roman_catholic")],
    }
    out = []
    for name, cols in groups.items():
        if not cols:
            continue
        sub = w[w[cols].notna().any(axis=1) & w.population.notna()]
        total = sub[cols].sum(axis=1)
        diff = total - sub.population
        ok = (diff.abs() <= sub.population * tol)
        print(f"  {name} breakdown sums to the printed population within {tol:.0%} "
              f"in {ok.mean():6.1%} of {len(sub):,} band-years")
        for (eid, year), d in diff[~ok].items():
            out.append(dict(check=name, entity_id=eid, year=year,
                            population=sub.loc[(eid, year), "population"],
                            parts_total=total.loc[(eid, year)], difference=d))
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="census", choices=sorted(FAMILIES))
    ap.add_argument("--dry-run", action="store_true",
                    help="report coverage without writing the parquet")
    args = ap.parse_args()

    cmap = ColumnMap(MAPS / f"{args.family}.yaml")
    glob_pat, name_field, want_section = FAMILIES[args.family]
    resolve = RESOLVERS.get(cmap.entity_type, dict)()
    # census rows that are districts, agencies, treaty totals or recapitulations
    # keep their observations but are typed "unit", so sums over bands are sums over bands
    flags = ROOT / "registries/crosswalks/band_unit_flags.csv"
    unit_ids = set(pd.read_csv(flags).band_id) if flags.exists() else set()
    sess = pd.read_csv(ROOT / "registries/crosswalks/dia_sessional.csv")
    paper_of = {int(r.report_year): r.paper_id for r in sess.itertuples()
                if pd.notna(r.report_year)}

    obs = []
    unmapped = collections.Counter()
    unmapped_years = collections.defaultdict(set)
    stats = collections.Counter()
    unresolved = collections.Counter()

    for year, chunk, idx, columns, row in iter_rows(glob_pat, want_section):
        name = row.get(name_field)
        if not name:
            stats["rows_without_entity"] += 1
            continue
        stats["rows"] += 1
        key = norm_name(name)
        entity = resolve.get((year, chunk, key)) or resolve.get((year, key))
        if resolve and not entity:
            unresolved[year] += 1
            stats["rows_unresolved_entity"] += 1
            continue
        values = row.get("values") or []
        for i, label in enumerate(columns):
            sid, ignored = cmap.series_for(label, year)
            if sid is None:
                if not ignored:
                    unmapped[norm_label(label)] += 1
                    unmapped_years[norm_label(label)].add(year)
                continue
            v = parse_value(values[i]) if i < len(values) else None
            if v is None:
                stats["cells_empty"] += 1
                continue
            obs.append((entity, "unit" if entity in unit_ids else cmap.entity_type, sid, year, v, cmap.units.get(sid, ""),
                        cmap.family, paper_of.get(year, ""), row.get("page"), chunk,
                        idx, row.get("confidence")))
        stats["cells_seen"] += len(columns)

    df = pd.DataFrame(obs, columns=[
        "entity_id", "entity_type", "series_id", "year", "value", "unit",
        "source_family", "paper_id", "page", "chunk", "row_idx", "confidence"])

    print(f"{args.family}: {stats['rows']:,} data rows "
          f"({stats['rows_unresolved_entity']:,} whose entity could not be resolved, "
          f"{stats['rows_without_entity']:,} with no name), "
          f"{len(df):,} observations over {df.series_id.nunique() if len(df) else 0} series")
    if len(df):
        print(f"years {df.year.min()}-{df.year.max()} | "
              f"distinct entities {df.entity_id.nunique():,}")
        top = df.series_id.value_counts().head(12)
        print("\nobservations per series (top 12):")
        for sid, n in top.items():
            yy = df[df.series_id == sid].year
            print(f"  {n:>7,}  {sid:<32} {yy.min()}-{yy.max()}")

    if unmapped:
        total = sum(unmapped.values())
        print(f"\nunmapped column labels: {len(unmapped)} distinct, {total:,} occurrences")
        for lab, n in unmapped.most_common(15):
            ys = sorted(unmapped_years[lab])
            print(f"  {n:>6,}  {min(ys)}-{max(ys)}  {lab}")

    if cmap.bad_rules:
        print("\nRULES WITH AN UNFILLED ID GROUP (split the alternation into "
              "separate rules — the id would otherwise collapse to a stub):")
        for (rid, groups), n in cmap.bad_rules.most_common():
            print(f"  {n:>6,}  {rid}  missing {', '.join(groups)}")

    if unresolved:
        print("\nrows whose band did not resolve, by year:",
              dict(sorted(unresolved.items())[:12]), "…" if len(unresolved) > 12 else "")

    checks = consistency(df)
    if args.dry_run:
        return
    out = ROOT / "registries/annotations/observations.parquet"
    if out.exists():
        old = pd.read_parquet(out)
        df = pd.concat([old[old.source_family != cmap.family], df], ignore_index=True)
    df.to_parquet(out, index=False)
    rep = ROOT / f"registries/annotations/observations_{args.family}_coverage.csv"
    with open(rep, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "occurrences", "first_year", "last_year"])
        for lab, n in unmapped.most_common():
            ys = sorted(unmapped_years[lab])
            w.writerow([lab, n, min(ys), max(ys)])
    chk = ROOT / f"registries/annotations/observations_{args.family}_checks.csv"
    checks.to_csv(chk, index=False)
    print(f"\nwrote {out} ({len(df):,} rows across all families), {rep.name} "
          f"and {chk.name} ({len(checks):,} band-years whose breakdowns disagree)")


if __name__ == "__main__":
    main()
