#!/usr/bin/env python3
"""Chunk the band-census tables of the DIA annual reports for extraction.

    python3 eval/make_census_chunks.py [--years 1897,1898,...] [--pages-per-chunk 8]

The census table ("Census Return of Resident and Nomadic Indians" /
"Tabular Statement No. 4" / "Census of Indians" / "Table No. 1 — Census")
is located per volume by text scan (the segmenter's headings are unreliable
for these): start = the census heading, end = the section that always
follows it (agricultural statistics / Table No. 2 / commutations). Volumes
whose end could not be found (span runs to EOF) are skipped unless a page
range is pinned in RANGES.

Every chunk carries the table's own HEADER block (first ~2,500 chars of the
section) as context, because the column definitions are printed once per
section and wrapped over many lines — the extractor must reconstruct them.

Output: registries/external/census_tables/chunks_{year}/chunk_NN_pA-B.txt
"""
import argparse
import bisect
import re
from pathlib import Path

MD = Path.home() / "DeptIndianAffairs/markdown"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "registries/external/census_tables"

START = re.compile(r"\n\s*(CENSUS\s+RE[TU][UT]RN[^\n]{0,80}|TABULAR STATEMENT\s+No\.?\s*4\.?[^\n]{0,60}|"
                   r"TABLE\s+No\.?\s*1\.?\s*-?\s*CENSUS[^\n]{0,80}|CENSUS OF INDIANS[^\n]{0,80}|"
                   r"CENSUS[^\n]{0,40}(?:INDIANS|ESKIMOS)[^\n]{0,40})\n", re.I)
END = re.compile(r"\n\s*(AGRICULTURAL AND INDUSTRIAL STATISTICS[^\n]{0,40}|TABLE\s+No\.?\s*2\b[^\n]{0,60}|"
                 r"TABULAR STATEMENT\s+No\.?\s*5\b[^\n]{0,40}|SCHOOL STATEMENT[^\n]{0,40}|"
                 r"COMMUTATIONS? OF ANNUIT[^\n]{0,40}|OFFICERS AND EMPLOYEES[^\n]{0,40}|"
                 r"GRAIN, VEGETABLE[^\n]{0,40}|RELIGIONS OF INDIANS[^\n]{0,40}|INDIAN TRUST FUND[^\n]{0,40})\n", re.I)
# hand-pinned pdf page ranges where the scan overran (EOF) — fill in as verified.
# Second batch (2026-08-27 evening, located with the widened END set):
DEFERRED_YEARS = [1886, 1893, 1895, 1896, 1906, 1925, 1926, 1928]
# 2026-08-30: 1925/1926/1928 are RECAPITULATION-ONLY years (like 1918-21, 1923,
# 1927, 1930) — no band-level census was printed. Their DEFERRED extraction
# located the Agricultural & Industrial pages instead and duplicates agstat;
# discard those results. Real Table No. 1 recap: 1925 pp.35-40, 1926 pp.38-43,
# 1928 pp.36-41 (cut into tabstmt_tables, Narval job 2104138).
# Band-level census DOES exist in 1924 (p.32 "arranged under inspectorates,
# agencies and districts") and 1929 (p.51 "under provinces and agencies").
# Third batch (2026-08-28, pinned by hand from the page headings):
#  1881/82  Tabular Statement No. 4 (heading matched; no END marker before Part II)
#  1887–92  the census is "Tabular Statement No. 3" in these volumes (ToC: "No. 3 -
#           Census Returns"); ends at its RECAPITULATION page
#  1910     "CENSUS. Indians and Eskimos — religions, ages, sexes, births and
#           deaths" per band (pp. 879–959; 960 = commutations)
#  1918–23, 1927, 1930: only TABLE No. 1 RECAPITULATION (by inspectorate/
#           province, with religion + age/sex columns) was printed — no band rows.
#           Chunk with --out-dir registries/external/tabstmt_tables and run
#           them through --task table (generic label/headers/values).
#  1922     dia_ar_1922.md is 84 pages of garbage OCR — skipped.
RANGES = {1881: (421, 431), 1882: (386, 396), 1887: (600, 616), 1888: (646, 662),
          1889: (528, 544), 1890: (477, 495), 1891: (502, 520), 1892: (559, 587),
          1910: (879, 959),
          # recapitulation-only years
          1918: (54, 59), 1919: (69, 74), 1920: (38, 43), 1921: (59, 64),
          1923: (27, 32), 1927: (32, 37), 1930: (69, 69)}
RECAP_YEARS = [1918, 1919, 1920, 1921, 1923, 1927, 1930]
DEFAULT_YEARS = [1897, 1898, 1899, 1900, 1901, 1903, 1904, 1905, 1907, 1908, 1909,
                 1911, 1912, 1913, 1914, 1915, 1916, 1917, 1924, 1929]

CTX = "=== CONTEXT (do NOT extract rows from this part; use only to identify the section headers in effect) ==="
HDR = "=== TABLE HEADER (the column definitions of this census table, printed once at the start of the section; use to name the columns) ==="
EXT = "=== EXTRACT FROM HERE (pages {a}-{b}) ==="


def locate(raw):
    pages = {m.start(): int(m.group(1)) for m in re.finditer(r"<!-- page (\d+) -->", raw)}
    pos = sorted(pages)

    def pg(i):
        k = bisect.bisect_right(pos, i) - 1
        return pages[pos[k]] if k >= 0 else 0
    best = None
    for m in START.finditer(raw):
        if pg(m.start()) <= 15:
            continue
        e = END.search(raw, m.end() + 500)
        endpos = e.start() if e else None
        if endpos is None:
            continue
        span = endpos - m.start()
        if best is None or span > best[2]:
            best = (m.start(), endpos, span)
    if not best:
        return None
    return pg(best[0]), pg(best[1]), best[0], best[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default=",".join(map(str, DEFAULT_YEARS)))
    ap.add_argument("--pages-per-chunk", type=int, default=8)
    ap.add_argument("--context-chars", type=int, default=1500)
    ap.add_argument("--header-chars", type=int, default=2500)
    ap.add_argument("--out-dir", default=None,
                    help="write chunks_{year}/ under this dir instead of census_tables "
                         "(use registries/external/tabstmt_tables for the RECAP_YEARS)")
    ap.add_argument("--pin", default=None,
                    help="YEAR:A-B — pdf page range override for a single year (e.g. the 1902 "
                         "School Statement: --years 1902 --pin 1902:529-548 --out-dir "
                         "registries/external/school_tables)")
    ap.add_argument("--append", action="store_true",
                    help="keep existing chunk files and continue numbering (several --pin "
                         "ranges into one chunks_{year}/ dir, each with its own header block)")
    args = ap.parse_args()
    if args.pin:
        y, ab = args.pin.split(":"); a, b = ab.split("-")
        RANGES[int(y)] = (int(a), int(b))
    out_root = Path(args.out_dir) if args.out_dir else OUT
    total = 0
    for year in [int(y) for y in args.years.split(",")]:
        raw = (MD / f"dia_ar_{year}.md").read_text(encoding="utf-8", errors="replace")
        pages = {int(m.group(1)): m.start() for m in re.finditer(r"<!-- page (\d+) -->", raw)}
        if year in RANGES:
            p0, p1 = RANGES[year]
            spos, epos = pages[p0], pages.get(p1 + 1, len(raw))
        else:
            loc = locate(raw)
            if not loc:
                print(f"{year}: census section not located — skipped"); continue
            p0, p1, spos, epos = loc
        header = raw[spos:spos + args.header_chars]
        d = out_root / f"chunks_{year}"
        d.mkdir(parents=True, exist_ok=True)
        n = 0
        if args.append:
            n = len(list(d.glob("chunk_*.txt")))
        else:
            for f in d.glob("chunk_*.txt"):
                f.unlink()
        starts = list(range(p0, p1 + 1, args.pages_per_chunk))
        for i, a in enumerate(starts):
            b = min(a + args.pages_per_chunk - 1, p1)
            lo = pages.get(a, spos)
            hi = pages.get(b + 1, epos)
            hi = min(hi, epos) if b == p1 else hi
            body = raw[lo:hi]
            if len(body.strip()) < 200:
                continue
            ctx = raw[max(spos, lo - args.context_chars):lo] if a > p0 else ""
            text = f"{HDR}\n{header}\n\n" + (f"{CTX}\n{ctx}\n\n" if ctx else "") + EXT.format(a=a, b=b) + "\n" + body
            (d / f"chunk_{n:02d}_p{a}-{b}.txt").write_text(text, encoding="utf-8")
            n += 1
        chars = sum(len(f.read_text()) for f in d.glob("chunk_*.txt"))
        total += chars
        print(f"{year}: {n} chunks, pdf pp. {p0}-{p1}, {chars:,} chars")
    print(f"TOTAL {total:,} chars")


if __name__ == "__main__":
    main()
