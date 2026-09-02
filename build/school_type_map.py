#!/usr/bin/env python3
"""Page-positional school type for the School Statement family.

The printed statement gives a school's class (day / boarding / industrial /
residential) as a *heading*, not a column: a running title on every page of
each statement ("STATEMENT of Boarding Schools in the Dominion…", from 1905)
and, in the earlier volumes and the year-end recapitulation, bare sub-headings
("BOARDING SCHOOLS."). The extractor's per-row `school_type` field is a guess
that defaults to "day" — a six-page chunk that starts after the heading has no
way to know better, so in 1921 it typed all 336 rows "day" against 74
residential schools printed, and Lejac (1928) became a day school.

This script reads the chunk text the extractor saw, walks the `<!-- page N -->`
markers, and records the type in effect on every page:

    registries/external/school_tables/type_by_page.csv
    year, page, type, n_titles_on_page, heading_as_printed, chunk

`type` is one of day / boarding / industrial / residential, or "mixed" when a
page carries a title change part-way down (the minter then falls back to the
row's own field). Pages before the first title in a year inherit nothing and
are left unmapped.

Usage: python3 build/school_type_map.py [--years 1913,1928]
"""
import argparse
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHUNKS = ROOT / "registries/external/school_tables"
OUT = CHUNKS / "type_by_page.csv"

PAGE = re.compile(r"<!--\s*page\s+(\d+)\s*-->")
# running title of a statement: "STATEMENT of Indian Boarding Schools in the
# Dominion (from which returns have been received) for the Fiscal Year …"
# 1896-1901 print it as "SHOWING the Condition of Indian Boarding Schools …"
TITLE = re.compile(r"\b(?:STATEMENT\s+of|SHOWING\s+the\s+Condition\s+of)\s+(?:the\s+)?(?:Combined\s+Public\s+and\s+)?(?:Indian\s+)?"
                   r"(Day|Boarding|Industrial|Residential)\s+Schools?\b", re.I)
EXT = re.compile(r"=== EXTRACT FROM HERE \(pages (\d+)-(\d+)\)")
# bare sub-heading, alone on its line (the 1896-1913 statements and the
# year-end recapitulation by province)
SUBHEAD = re.compile(r"^\W{0,4}(DAY|BOARDING|INDUSTRIAL|RESIDENTIAL)\s+SCHOOLS?\W{0,4}$", re.I)


# When OCR lost the title (1900, 1919, 1921) the statements still differ in
# their header row: day schools print "School. Reserve. Agency. Teacher.",
# the industrial statement "School. Situation. Principal.", and the 1900-era
# boarding statement "School. District. Teacher." with no reserve or agency.
HDR_INDUSTRIAL = re.compile(r"\bSchool\.?\s+Situation\.?\s+Principal\b", re.I)
HDR_BOARDING = re.compile(r"\bSchool\.?\s+District\.?\s+(?!.*\b(Reserve|Agency)\b).*\bTeacher\b", re.I)
# 1923-1930: the residential statement heads its columns "School Reserve Agency
# Principal", the day and combined-public statements "… Teacher" (or nothing);
# the combined statement follows the residential one and OCR mangles its title
# ("COMBINED PUBLIC AND INDUSTRIAL SCHOOLS", 1925)
HDR_RESIDENTIAL = re.compile(r"^\W{0,3}School\.?\s+Reserve\.?\s+Agency\.?\s+Principal\b", re.I)
HDR_DAY_LATE = re.compile(r"^\W{0,3}School\.?\s+Reserve\.?\s+Agency\.?\s+Teacher\b", re.I)
COMBINED = re.compile(r"\bCOMBINED\s+PUBLIC\s+AND\b.*\bSCHOOLS\b", re.I)
# the statements end here; what follows (the Indian Land Statement, land sales
# in dollars) reached the extractor under the school header in 1897 and 1900
END = re.compile(r"^\W{0,3}(SUMMARY\s+OF\s+SCHOOL\s+STATEMENT|INDIAN\s+LAND\s+STATEMENT|STATEMENT\s+showing\s+the\s+enrolment\s+by\s+Provinces)", re.I)


# Each statement lists the provinces in the same printed order, so a province
# heading that jumps back to the start of the order (ONTARIO after BRITISH
# COLUMBIA) begins a new statement even when OCR lost its title (1919, 1921)
PROV_ORDER = ["ONTARIO", "QUEBEC", "NOVA SCOTIA", "NEW BRUNSWICK", "PRINCE EDWARD ISLAND",
              "MANITOBA", "SASKATCHEWAN", "ALBERTA", "NORTH-WEST TERRITORIES", "NORTHWEST TERRITORIES",
              "BRITISH COLUMBIA", "YUKON"]
# the heading may carry dot leaders ("ONTARIO ... ... ...") or "- Con."; a
# continuation is not a restart
PROV_LINE = re.compile(r"^\W{0,3}(" + "|".join(PROV_ORDER) + r")(?P<con>\s*[-–—]\s*Con(?:t|cluded)?\.?)?[\s.…]*$", re.I)
NEXT_TYPE = {"day": "boarding", "boarding": "industrial"}


def province_rank(line):
    m = PROV_LINE.match(line.strip())
    if not m or m.group("con"):
        return None
    name = m.group(1).upper().replace("NORTHWEST", "NORTH-WEST")
    return PROV_ORDER.index(name) if name in PROV_ORDER else None


def titles_in(line, year=0):
    if END.search(line.strip()):
        return "end", "[end] " + line.strip()
    if COMBINED.search(line):
        return "day", "[combined public] " + line.strip()
    m = TITLE.search(line)
    if m:
        return m.group(1).lower(), line.strip()
    m = SUBHEAD.match(line.strip())
    if m:
        return m.group(1).lower(), line.strip()
    if HDR_INDUSTRIAL.search(line):
        return "industrial", "[header] " + line.strip()
    if HDR_BOARDING.search(line):
        return "boarding", "[header] " + line.strip()
    if year >= 1923:
        if HDR_RESIDENTIAL.search(line):
            return "residential", "[header] " + line.strip()
        if HDR_DAY_LATE.search(line):
            return "day", "[header] " + line.strip()
    return None


def chunk_files(year_dir):
    def key(p):
        m = re.match(r"chunk_(\d+)_p(\d+)", p.name)
        return (int(m.group(2)), int(m.group(1))) if m else (10**9, 0)
    return sorted(year_dir.glob("chunk_*.txt"), key=key)


def map_year(year_dir):
    """Yield (page, type, n_titles, heading, chunk) for every page in the year's chunks."""
    year = int(re.search(r"chunks_(\d{4})", year_dir.name).group(1))
    current = None                      # type carried from the previous page
    last_page = None
    last_rank = -1                      # position in PROV_ORDER of the last province heading
    seen = {}                           # page -> record (chunk overlap: first wins unless it was unmapped)
    for f in chunk_files(year_dir):
        # the family's chunks also cover far-off pages the segmenter labelled
        # "SCHOOL STATEMENT (cont.)" (principals' reports, agricultural tables);
        # a type must not carry across a gap in the page sequence
        m = re.match(r"chunk_\d+_p(\d+)", f.name)
        if m and last_page is not None and int(m.group(1)) > last_page + 1:
            current = None
        # only the EXTRACT section counts: the TABLE HEADER block repeats the
        # *first* statement's title (always the day schools) on every chunk,
        # and the CONTEXT block is the tail of the previous pages
        page = None
        per_page = {}
        in_ext = False
        for line in f.read_text(errors="replace").splitlines():
            em = EXT.search(line)
            if em:
                in_ext = True
                page = int(em.group(1))       # lines before the first marker sit on the first page
                per_page.setdefault(page, [])
                continue
            if not in_ext:
                continue
            pm = PAGE.search(line)
            if pm:
                page = int(pm.group(1))
                per_page.setdefault(page, [])
                continue
            t = titles_in(line, year)
            if t:
                per_page[page].append(t)
                last_rank = -1
                continue
            rk = province_rank(line)
            if rk is not None:
                if last_rank >= 6 and rk <= 1 and current in NEXT_TYPE and not per_page[page]:
                    nxt = "residential" if year >= 1923 and current == "day" else NEXT_TYPE[current]
                    per_page[page].append((nxt, f"[province restart] {line.strip()}"))
                last_rank = rk
        for page in sorted(per_page):
            found = per_page[page]
            # a header-shape signal is weak: it may confirm the statement in
            # effect but never demotes a residential run back to "day" (the
            # 1923-25 residential statement heads its columns "… Teacher" too)
            kept, run = [], current
            for k, h in found:
                if h.startswith("[header]") and k == "day" and run == "residential":
                    continue
                kept.append((k, h))
                run = k
            found = kept
            kinds = [k for k, _ in found]
            if not kinds:
                typ, head = current, ""
            elif len(set(kinds)) == 1:
                typ, head = kinds[0], found[0][1]
                current = typ
            else:
                # a title change part-way down the page: the rows above it belong
                # to the old statement, the rows below to the new one
                typ, head = "mixed", " || ".join(h for _, h in found)
                current = kinds[-1]
            rec = (year, page, typ or "", len(found), head[:120], f.name)
            if page not in seen or (not seen[page][2] and typ):
                seen[page] = rec
            last_page = page
    return [seen[p] for p in sorted(seen)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", help="comma-separated subset, default all")
    args = ap.parse_args()
    years = {int(y) for y in args.years.split(",")} if args.years else None
    rows = []
    for d in sorted(CHUNKS.glob("chunks_*")):
        y = int(d.name.split("_")[1])
        if years and y not in years:
            continue
        rows.extend(map_year(d))
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "page", "type", "n_titles_on_page", "heading_as_printed", "chunk"])
        w.writerows(rows)
    # report
    from collections import Counter
    by_year = {}
    for y, p, t, n, h, c in rows:
        by_year.setdefault(y, Counter())[t or "unmapped"] += 1
    for y in sorted(by_year):
        print(y, dict(by_year[y]))
    print(f"wrote {OUT} ({len(rows)} pages)")


if __name__ == "__main__":
    main()
