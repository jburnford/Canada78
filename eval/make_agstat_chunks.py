#!/usr/bin/env python3
"""Chunk the band-level Agricultural & Industrial Statistics tables of the DIA
annual reports (1895–1930) for the generic `--task table` extractor.

    python3 eval/make_agstat_chunks.py [--years 1914,1915] [--pages-per-chunk 5] [--dry]

Two eras:
  1895–1913  one very wide "AGRICULTURAL AND INDUSTRIAL STATISTICS" table per band
             (printed over facing pages; "— Continued" per province)
  1914–1930  TABLE No. 2 (grain/roots), No. 3 (land & buildings), No. 4 (live
             stock), No. 5 (value of property), No. 6 (sources of income)
             (1914–16 number them 2–7 with slightly different titles)

The segment registry never headed these tables, so they are located by text
scan: START = the (upper-case) section heading; END = the section that follows
(school statement / officers / trust fund / commutations / census / Table 7).
Inside the span every TABLE No. N heading starts a new sub-table; each
sub-table is chunked separately with ITS OWN header block so a chunk never
carries the column definitions of a different table.  Volumes the scan cannot
place are pinned in RANGES (pdf page ranges, verified by hand).

Output: registries/external/agstat_tables/chunks_{year}/chunk_NN_pA-B.txt
Run on the cluster with TASK=agstat (→ --task table, results agstat{year}_…).
"""
import argparse
import bisect
import re
from pathlib import Path

MD = Path.home() / "DeptIndianAffairs/markdown"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "registries/external/agstat_tables"

# upper-case heading only (the lower-case phrase occurs in narrative text and
# ToCs); tolerate OCR drift in the first word (1906 "AGRICUTURAL") and the
# 1895 mixed-case "AGRICULTURAL and Industrial Statistics".
AG = r"AGRICU[A-Z]*\s+(?:AND|and)\s+(?:INDUSTRIAL|Industrial)\s+(?:STATISTICS|Statistics)"
START = re.compile(r"\n\s*(" + AG + r"[^\n]{0,80}|"
                   r"TABLE,? ?N[Oo][.,]? ?2[.,:]?\s*[-:.—]*\s*(?:GRAIN|ROOTS)[^\n]{0,80})\n")
INNER = re.compile(r"\n\s*(TABLE,? ?N[Oo][.,]? ?([2-7])\b[^\n]{0,90}|" + AG + r"[^\n]{0,80})\n")
END = re.compile(r"\n\s*(SCHOOL STATEMENT[^\n]{0,40}|OFFICERS AND EMPLOY[^\n]{0,40}|INDIAN TRUST FUND[^\n]{0,40}|"
                 r"COMMUTATIONS? OF ANNUIT[^\n]{0,40}|CENSUS\.[^\n]{0,20}|CENSUS RETURN[^\n]{0,60}|"
                 r"TABULAR STATEMENT\s+No\.?\s*4\b[^\n]{0,40}|RELIGIONS OF INDIANS[^\n]{0,40}|"
                 r"TABLE,? ?N[Oo][.,]? ?8\b[^\n]{0,60}|INDIAN LAND STATEMENT[^\n]{0,40})\n", re.I)
# hand-pinned pdf page ranges (start, end) where the scan fails — fill as verified
RANGES = {1902: (587, 648)}   # 1902: heading survives only as "ii AGRICULTURAL…" running heads; ToC Part II pp. 90–151
SKIP = {1922}   # dia_ar_1922.md is garbage OCR
DEFAULT_YEARS = [y for y in range(1895, 1931) if y not in SKIP]

CTX = "=== CONTEXT (do NOT extract rows from this part; use only to identify the section headers in effect) ==="
HDR = "=== TABLE HEADER (the column definitions of this table, printed once at the start of the statement; use to name the columns) ==="
EXT = "=== EXTRACT FROM HERE (pages {a}-{b}) ==="


def page_index(raw):
    pages = {m.start(): int(m.group(1)) for m in re.finditer(r"<!-- page (\d+) -->", raw)}
    pos = sorted(pages)

    def pg(i):
        k = bisect.bisect_right(pos, i) - 1
        return pages[pos[k]] if k >= 0 else 0
    return pages, pos, pg


def locate(raw, pg):
    best = None
    for m in START.finditer(raw):
        if pg(m.start()) <= 15:
            continue
        e = END.search(raw, m.end() + 500)
        if not e:
            continue
        span = e.start() - m.start()
        if best is None or span > best[2]:
            best = (m.start(), e.start(), span)
    return best[:2] if best else None


def subtables(raw, s, e):
    """Split [s, e) at TABLE No. N headings; '— Continued' repeats of the same
    table number stay in the current sub-table."""
    cuts, last_key = [], None
    for m in INNER.finditer(raw, s, e):
        key = m.group(2) or "AG"
        if key != last_key:
            cuts.append((m.start(), key))
            last_key = key
    if not cuts or cuts[0][0] > s + 200:
        cuts.insert(0, (s, cuts[0][1] if cuts else "AG"))
    out = []
    for i, (c, key) in enumerate(cuts):
        c_end = cuts[i + 1][0] if i + 1 < len(cuts) else e
        out.append((c, c_end, key))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default=",".join(map(str, DEFAULT_YEARS)))
    ap.add_argument("--pages-per-chunk", type=int, default=5)
    ap.add_argument("--context-chars", type=int, default=1500)
    ap.add_argument("--header-chars", type=int, default=2500)
    ap.add_argument("--dry", action="store_true", help="report spans only, write nothing")
    args = ap.parse_args()
    total = 0
    for year in [int(y) for y in args.years.split(",")]:
        p = MD / f"dia_ar_{year}.md"
        if not p.exists():
            print(f"{year}: no markdown"); continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        pages, pos, pg = page_index(raw)
        byp = {v: k for k, v in pages.items()}
        if year in RANGES:
            p0, p1 = RANGES[year]
            s, e = byp[p0], byp.get(p1 + 1, len(raw))
        else:
            loc = locate(raw, pg)
            if not loc:
                print(f"{year}: NOT LOCATED — pin RANGES"); continue
            s, e = loc
        subs = subtables(raw, s, e)
        desc = "; ".join(f"T{k}@p{pg(a)}" for a, b, k in subs)
        chars = e - s
        print(f"{year}: pp{pg(s)}-{pg(e - 1)} ({pg(e - 1) - pg(s) + 1} pp, {chars:,} ch) {desc}")
        total += chars
        if args.dry:
            continue
        d = OUT / f"chunks_{year}"
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("chunk_*.txt"):
            f.unlink()
        n = 0
        for a, b, key in subs:
            header = raw[a:a + args.header_chars]
            pa, pb = pg(a), pg(b - 1)
            for lo_p in range(pa, pb + 1, args.pages_per_chunk):
                hi_p = min(lo_p + args.pages_per_chunk - 1, pb)
                lo = max(a, byp.get(lo_p, a))
                hi = min(b, byp.get(hi_p + 1, b))
                body = raw[lo:hi]
                if len(body.strip()) < 200:
                    continue
                ctx = raw[max(a, lo - args.context_chars):lo] if lo > a else ""
                text = f"{HDR}\n{header}\n\n" + (f"{CTX}\n{ctx}\n\n" if ctx else "") + \
                    EXT.format(a=lo_p, b=hi_p) + "\n" + body
                (d / f"chunk_{n:02d}_p{lo_p}-{hi_p}.txt").write_text(text, encoding="utf-8")
                n += 1
        print(f"   → {n} chunks")
    print(f"TOTAL {total:,} chars")


if __name__ == "__main__":
    main()
