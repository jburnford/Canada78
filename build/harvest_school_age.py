#!/usr/bin/env python3
"""Promote the agents' printed school-age denominators into observations.

Between 1910 and 1916 the eastern-format agency letters open with a stock
sentence the tabular School Statement never carries:

    CAUGHNAWAGA AGENCY. Number of children of school age, 508; number of
    pupils enrolled at day schools, 373; average attendance at day schools,
    262; number attending Mount Elgin industrial, 11; number attending
    Spanish River industrial, 41; …

It is the only population denominator the sources offer for school
attendance (the statement's roll is a numerator), and it names the
residential schools the agency's children were sent to.

Reads the generated wiki's report pages (the agency link there resolves the
unit: `/agencies/<slug>/` → `AG-<slug>`, the chain id the observations use).
Writes
  registries/annotations/agency_school_age.csv       one row per agency-year with the named residential schools
  registries/annotations/observations.parquet        rows with source_family = "agency_letter":
      school_age_children · enrolled_day_schools · avg_attendance_day_schools ·
      attending_residential (sum of the named schools) · not_enrolled (school-age minus both)
Existing agency_letter rows are replaced; other families are untouched.
"""
import csv
import glob
import html
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OBS = ROOT / "registries/annotations/observations.parquet"
OUT = ROOT / "registries/annotations/agency_school_age.csv"
NUM = r"(\d[\d,]*)"
HEAD = re.compile(r"(?:<a class='m-high' href='/agencies/([^']+)/'[^>]*>|<b>)([^<]{3,80})</(?:a|b)>\.?\s*"
                  r"Number of children of school age,?\s*" + NUM + r"[;.,]?\s*number of pupils enrolled(?: at day schools?)?,?\s*"
                  + NUM + r"[;.,]?\s*average attendance(?: at day schools?)?,?\s*" + NUM + r"(.{0,900}?)"
                  r"(?:The Indian agent|The agent|Indian agent|reports on the|$)", re.I | re.S)
RES = re.compile(r"number attending ([^,;]{3,80}?),?\s*" + NUM, re.I)


def n(s):
    digits = re.sub(r"\D", "", str(s))
    return int(digits) if digits else 0


def main():
    sess = pd.read_csv(ROOT / "registries/crosswalks/dia_sessional.csv")
    paper_of = {int(r.report_year): r.paper_id for r in sess.itertuples() if pd.notna(r.report_year)}
    rows = []
    for f in sorted(glob.glob(str(ROOT / "site/reports/*/*/index.html"))):
        year = int(f.split("/")[-3])
        pm = re.match(r"p(\d{4})", Path(f).parent.name)
        page = int(pm.group(1)) if pm else None
        t = open(f, encoding="utf-8").read()
        for m in HEAD.finditer(t):
            slug, name, sa, en, at, tail = m.groups()
            tail = re.sub(r"<[^>]+>", "", html.unescape(tail))
            named = [(html.unescape(s).strip(), n(v)) for s, v in RES.findall(tail)]
            rows.append(dict(year=year, agency_id=f"AG-{slug}" if slug else "", agency_as_printed=html.unescape(name).title().strip(),
                             school_age=n(sa), enrolled_day=n(en), avg_attendance_day=n(at),
                             attending_residential=sum(v for _, v in named),
                             residential_schools="; ".join(f"{s} {v}" for s, v in named), page=page,
                             paper_id=paper_of.get(year, "")))
    d = pd.DataFrame(rows).drop_duplicates(["year", "agency_as_printed", "school_age", "enrolled_day"])
    # sanity: enrolment cannot much exceed the school-age count; attendance cannot exceed enrolment
    d["ok"] = (d.school_age > 0) & (d.enrolled_day <= d.school_age * 1.3) & (d.avg_attendance_day <= d.enrolled_day * 1.2) \
        & (d.attending_residential <= d.school_age)
    d.to_csv(OUT, index=False)
    good = d[d.ok & (d.agency_id != "")]
    print(f"agency-years parsed: {len(d)} | plausible and resolved to an agency: {len(good)} "
          f"({good.agency_id.nunique()} agencies, {good.year.min()}-{good.year.max()}) | "
          f"with named residential schools: {(good.attending_residential > 0).sum()}")

    obs = []
    for r in good.itertuples():
        base = dict(entity_id=r.agency_id, entity_type="agency", year=int(r.year), unit="persons",
                    source_family="agency_letter", paper_id=r.paper_id, page=float(r.page) if r.page else None,
                    chunk="", row_idx=0, confidence="high")
        obs.append(dict(base, series_id="school_age_children", value=float(r.school_age)))
        obs.append(dict(base, series_id="enrolled_day_schools", value=float(r.enrolled_day)))
        obs.append(dict(base, series_id="avg_attendance_day_schools", value=float(r.avg_attendance_day)))
        if r.attending_residential > 0:
            obs.append(dict(base, series_id="attending_residential", value=float(r.attending_residential)))
            obs.append(dict(base, series_id="not_enrolled", value=float(max(0, r.school_age - r.enrolled_day - r.attending_residential))))
    new = pd.DataFrame(obs)
    old = pd.read_parquet(OBS)
    old = old[old.source_family != "agency_letter"]
    out = pd.concat([old, new[old.columns]], ignore_index=True)
    out.to_parquet(OBS, index=False)
    print(f"observations: +{len(new):,} agency_letter rows → {len(out):,} total")
    cov = good.groupby("year").agg(n=("agency_id", "size"), sa=("school_age", "sum"), en=("enrolled_day", "sum"), att=("avg_attendance_day", "sum"))
    cov["enrolled_share"] = (cov.en / cov.sa).round(2)
    cov["present_share"] = (cov.att / cov.sa).round(2)
    print(cov.to_string())


if __name__ == "__main__":
    main()
