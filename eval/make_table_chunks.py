#!/usr/bin/env python3
"""Chunk a family of tabular segments (from registries/documents/dia_segments.parquet)
for Qwen extraction, with each segment's own header block prepended.

    python3 eval/make_table_chunks.py --family school [--years 1897,1898] [--pages-per-chunk 6]
    python3 eval/make_table_chunks.py --family census-seg          # census by heading, where labelled

Families select segments by heading regex; every chunk carries:
  === TABLE HEADER ===   first --header-chars of the segment (column definitions)
  === CONTEXT ===        --context-chars preceding the chunk (section headers in effect)
  === EXTRACT FROM HERE (pages A-B) ===  the chunk body
Output: registries/external/<family>_tables/chunks_{year}/chunk_NN_pA-B.txt
(segment ids are recorded in chunks_{year}/segments.tsv for provenance).
"""
import argparse
import re
from pathlib import Path

import pandas as pd

MD = Path.home() / "DeptIndianAffairs/markdown"
ROOT = Path(__file__).resolve().parent.parent

DENOM = re.compile(r"Roman\s+Catholic|Methodist|Anglican|Church\s+of\s+England|Presbyterian|Undenomina-?\s*tional|"
                   r"Baptist|United\s+Church|\bR\.\s?C\.|\bC\.\s?E\.|Teacher|Principal|Standard|Miss |Rev\. ", re.I)
LEDGER = re.compile(r"Brought forward|Balance, [A-Z][a-z]{2,8}\.? \d|Carried forward|Interest, \d ?p\. ?c|"
                    r"legal services|DR\.\s+CR\.|Transfer (to|from) (Trust|Account)|Casual Revenue|"
                    r"\d+ m\. to (June|Mar|Dec|Sept|Nov|Apr)|\$ ?cts\.\s*\$ ?cts|freight, \$", re.I)
AGSTAT = re.compile(r"INCREASE IN VALUE|Agricultural Products|Root Houses|Milk Houses|Live Stock|Bush\.", re.I)


def school_page(page_text):
    """True while a page still reads as a school statement (teachers,
    denominations, standards) rather than a trust-fund ledger or the
    agricultural statistics that follow it in the volume."""
    d = len(DENOM.findall(page_text))
    l = len(LEDGER.findall(page_text)) + len(AGSTAT.findall(page_text))
    if d >= 3:
        return l < 3
    return d >= 1 and l == 0


OFFICE = re.compile(r"Governor\s+in\s+Council|Superintendent|Appointed|Agent\b|Clerk|Instructor|Interpreter|"
                    r"Constable|Inspector|Physician|Farmer\b|Messenger|Draughtsman|Accountant|Storekeeper|"
                    # Return A's own sub-tables: the missionaries and medical men
                    # are paid staff too, but their columns are Address /
                    # Denomination / Tribe rather than Office, so the gate used to
                    # reject those pages and lose the whole sub-table (1884, 1888;
                    # they are Hoy's "Missionary" and "Medical Man" rows)
                    r"MISSIONARIES receiving|MEDICAL MEN employed|Denomination\b|"
                    r"Name of Tribe they Attend|OFFICERS OF OUTSIDE SERVICE", re.I)
EXPENSE = re.compile(r"Brought forward|Carried forward|fares|board(ing)? and lodging|livery|telegrams|lumber|"
                     r"freight|Debit|Credit|In account with|Service\.|small items|Balance, [A-Z][a-z]+", re.I)


def officers_page(page_text):
    """True while a page still reads as the staff list (Return A), not the
    expenditure ledgers / trust accounts that follow it in the volume."""
    o, e = len(OFFICE.findall(page_text)), len(EXPENSE.findall(page_text))
    return o >= 3 and e < 3 or (o >= 1 and e == 0)


FAMILIES = {
    # tabular School Statements (day / boarding / industrial), 1896-1930
    # header_require: the segment's header block must look like a school table —
    # the segmenter labels long Auditor-General expenditure tails "SCHOOL
    # STATEMENT (cont.)", which would otherwise inflate the family 5x.
    "school": dict(heading=r"SCHOOL STATEMENT", kinds={"tabular_statement", "return", "thematic_section", "appendix"},
                   out="school_tables",
                   header_require=r"Teacher\.|Teachers\.|Denomination|Number on Roll|Attendance\.|Average Daily|Standard I\b",
                   header_case=True, page_gate=school_page),
    "census-seg": dict(heading=r"CENSUS|TABULAR STATEMENT NO\. ?4|TABLE NO\. ?1\b|TABULAR STATEMENTS — ",
                       kinds=None, out="census_tables_seg"),
    # RETURN A = officers and employés of the Department (staff list), 1880-1904
    "officers": dict(heading=r"^RETURN A\b", kinds=None, out="officers_tables",
                     header_require=r"Designation|Name\.|Salary|Appointed", header_case=True,
                     page_gate=officers_page),
    # the other numbered statements / returns (No. 1-3, B-G): generic table task
    "tabstmt": dict(heading=r"^TABULAR STATEMENT NO\. ?[123]\b|^RETURN [B-G]\b", kinds=None,
                    out="tabstmt_tables"),
}
CTX = "=== CONTEXT (do NOT extract rows from this part; use only to identify the section headers in effect) ==="
HDR = "=== TABLE HEADER (the column definitions of this table, printed once at the start of the statement; use to name the columns) ==="
EXT = "=== EXTRACT FROM HERE (pages {a}-{b}) ==="


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", required=True, choices=sorted(FAMILIES))
    ap.add_argument("--years", default=None)
    ap.add_argument("--pages-per-chunk", type=int, default=6)
    ap.add_argument("--context-chars", type=int, default=1200)
    ap.add_argument("--header-chars", type=int, default=2500)
    ap.add_argument("--min-chars", type=int, default=200,
                    help="skip segments smaller than this (keep low: tiny per-province "
                         "continuation segments must stay in the ordinal chain)")
    args = ap.parse_args()
    fam = FAMILIES[args.family]
    s = pd.read_parquet(ROOT / "registries/documents/dia_segments.parquet")
    s["year"] = s.tag.str.extract(r"(\d{4})").astype(int)
    H = s.heading.str.upper().str.replace(r"\s+", " ", regex=True)
    sel = s[H.str.contains(fam["heading"], regex=True, na=False)]
    if fam["kinds"]:
        sel = sel[sel.kind.isin(fam["kinds"])]
    sel = sel[(sel.char_end - sel.char_start) >= args.min_chars]
    if args.years:
        sel = sel[sel.year.isin({int(y) for y in args.years.split(",")})]
    outroot = ROOT / "registries/external" / fam["out"]
    total = 0
    for year, grp in sel.groupby("year"):
        raw = (MD / f"dia_ar_{year}.md").read_text(encoding="utf-8", errors="replace")
        m = re.match(r"^---\n.*?\n---\n", raw, re.S)
        body = raw[m.end():] if m else raw
        d = outroot / f"chunks_{year}"
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("chunk_*.txt"):
            f.unlink()
        n, prov = 0, []
        current_header, last_ord = None, None
        for seg in grp.sort_values("ordinal").itertuples():
            text = body[seg.char_start:seg.char_end]
            header = text[:args.header_chars]
            is_new = True
            if fam.get("header_require"):
                if re.search(fam["header_require"], header, 0 if fam.get("header_case") else re.I):
                    current_header = header           # a new statement with its own header
                elif current_header is not None and last_ord is not None and seg.ordinal - last_ord <= 4:
                    header = current_header           # continuation (e.g. "— Manitoba"): inherit
                    is_new = False
                else:
                    continue
            last_ord = seg.ordinal
            marks = [(int(mm.group(1)), mm.start()) for mm in re.finditer(r"<!-- page (\d+) -->", text)]
            pages = [(seg.page_start, 0)] + [(p, i) for p, i in marks if p > seg.page_start]
            if fam.get("page_gate"):
                # trim the segment at the first page whose content is no longer
                # this table family — the segmenter lets school statements run
                # on into ledgers for whole volumes. A segment carrying its own
                # header gets its first two pages free; a continuation must pass
                # from its first page.
                free = 1 if is_new else 0
                keep = []
                for j, (p, i) in enumerate(pages):
                    hi_j = pages[j + 1][1] if j + 1 < len(pages) else len(text)
                    if hi_j - i < 1500:            # tiny fragment (segment starts mid-page): look ahead
                        hi_j = min(len(text), i + 3000)
                    if j >= free and not fam["page_gate"](text[i:hi_j]):
                        break
                    keep.append((p, i))
                if not keep:
                    continue
                if len(keep) < len(pages):
                    text = text[:pages[len(keep)][1]]
                pages = keep
            for k in range(0, len(pages), args.pages_per_chunk):
                win = pages[k:k + args.pages_per_chunk]
                a, lo = win[0]
                b = win[-1][0]
                hi = pages[k + args.pages_per_chunk][1] if k + args.pages_per_chunk < len(pages) else len(text)
                chunk = text[lo:hi]
                if len(chunk.strip()) < 300:
                    continue
                ctx = text[max(0, lo - args.context_chars):lo] if lo > 0 else ""
                out = f"{HDR}\n{header}\n\n" + (f"{CTX}\n{ctx}\n\n" if ctx else "") + EXT.format(a=a, b=b) + "\n" + chunk
                (d / f"chunk_{n:02d}_p{a}-{b}.txt").write_text(out, encoding="utf-8")
                prov.append(f"{n:02d}\t{seg.segment_id}\t{seg.heading}\t{a}\t{b}")
                n += 1
        (d / "segments.tsv").write_text("chunk\tsegment_id\theading\tp_lo\tp_hi\n" + "\n".join(prov) + "\n")
        chars = sum(len(f.read_text()) for f in d.glob("chunk_*.txt"))
        total += chars
        print(f"{year}: {n} chunks from {len(grp)} segments, {chars:,} chars")
    print(f"TOTAL {total:,} chars → {outroot}")


if __name__ == "__main__":
    main()
