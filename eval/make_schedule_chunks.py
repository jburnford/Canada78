#!/usr/bin/env python3
"""Generate page-aligned extraction chunks for the Schedule of Indian Reserves
editions embedded in ARs 1897-1901 (found 2026-08-26: full editions in 1900/
1901, summary editions in 1897-99), in the same format as the 1902 chunks so
eval/extract_1902.py consumes them unchanged.

    python3 eval/make_schedule_chunks.py [--pages-per-chunk 14] [--context-chars 1500]

Output: registries/external/reserves_schedules/chunks_{year}/chunk_NN_pA-B.txt
(pdf-page numbering of the AR volume; the printed Part II pagination differs —
page provenance is recorded as pdf pages, consistent with dia_segments).
"""
import argparse
import re
from pathlib import Path

DIA_MD = Path.home() / "DeptIndianAffairs/markdown"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "registries/external/reserves_schedules"

# (tag, first pdf page of the schedule, last pdf page) — located by scanning
# for the SCHEDULE heading and the following CENSUS RETURN heading.
EDITIONS = {
    1897: ("dia_ar_1897", 570, 598),
    1898: ("dia_ar_1898", 639, 673),
    1899: ("dia_ar_1899", 718, 759),
    1900: ("dia_ar_1900", 780, 944),
    1901: ("dia_ar_1901", 743, 911),
}

CTX = "=== CONTEXT (do NOT extract rows from this part; use only to identify the section headers in effect) ==="
EXT = "=== EXTRACT FROM HERE (pages {a}-{b}) ==="


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages-per-chunk", type=int, default=14)
    ap.add_argument("--context-chars", type=int, default=1500)
    args = ap.parse_args()
    for year, (tag, p0, p1) in EDITIONS.items():
        raw = (DIA_MD / f"{tag}.md").read_text(encoding="utf-8", errors="replace")
        pos = {int(m.group(1)): m.start() for m in re.finditer(r"<!-- page (\d+) -->", raw)}
        end_of = {p: (pos.get(p + 1, len(raw))) for p in pos}
        d = OUT / f"chunks_{year}"
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("chunk_*.txt"):
            f.unlink()
        starts = list(range(p0, p1 + 1, args.pages_per_chunk))
        for i, a in enumerate(starts):
            b = min(a + args.pages_per_chunk - 1, p1)
            body = raw[pos[a]:end_of[b]]
            ctx = raw[max(pos[p0] - 200, pos[a] - args.context_chars):pos[a]] if a > p0 else ""
            text = (f"{CTX}\n{ctx}\n" if ctx else "") + EXT.format(a=a, b=b) + "\n" + body
            (d / f"chunk_{i:02d}_p{a}-{b}.txt").write_text(text, encoding="utf-8")
        n = len(list(d.glob("chunk_*.txt")))
        print(f"{year}: {n} chunks, pdf pp. {p0}-{p1}, {sum(len(f.read_text()) for f in d.glob('chunk_*.txt')):,} chars → {d}")


if __name__ == "__main__":
    main()
