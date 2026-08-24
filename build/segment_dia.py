#!/usr/bin/env python3
"""Segment the DIA annual reports (51 volumes, 1880-1930) into stable,
citable segments.

Era-aware v1:
  - 1880-1914 ("letters era"): per-agency/superintendency letters anchored on
    'SIR,' openings with a header walk-back; tabular statements and returns
    anchored on printed captions.
  - 1915-1916 (transitional) and 1917-1930 ("thematic era"): standalone
    ALL-CAPS headings in Part I; captions in Part II.

Output per volume: registries/documents/segments_dia/{tag}.segments.jsonl
  (doc_id, segment_id, ordinal, kind, heading, province, page_start/end,
   char_start/end, text_version) - offsets into the source markdown BODY
   (after frontmatter); text itself stays in ~/DeptIndianAffairs.
Plus a combined registries/documents/dia_segments.parquet.

Segment IDs are content-anchored: p{page:04d}-{heading-slug}[-N].
"""
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DIA_MD = Path.home() / "DeptIndianAffairs/markdown"
OUT = ROOT / "registries/documents/segments_dia"

PAGE_RE = re.compile(r"<!-- page (\d+) -->")
SIR_RE = re.compile(r"^\s*SIR\s?, ?[-—]", re.M)
CAPTION_RE = re.compile(
    r"^(TABULAR STATEMENT No\. ?\d+[A-Za-z]?\.?|RETURN [A-Z](?: ?\(\d+\))?\.?|"
    r"STATEMENT [A-Z0-9][A-Za-z0-9 ,.()'-]{0,70}|"
    r"SCHOOL STATEMENT[A-Za-z0-9 ,.()'-]{0,60}|"
    r"APPENDIX[A-Za-z0-9 ,.()'-]{0,60})\s*$",
    re.M,
)
PART_RE = re.compile(r"^PART [IVX]+\.?\s*$", re.M)
PROVINCES = (
    "ONTARIO|QUEBEC|NOVA SCOTIA|NEW BRUNSWICK|PRINCE EDWARD ISLAND|MANITOBA|"
    "BRITISH COLUMBIA|SASKATCHEWAN|ALBERTA|NORTH-?WEST TERRITORIES|"
    "NORTHWEST TERRITORIES|YUKON|MANITOBA AND (THE )?NORTH-?WEST( TERRITORIES)?"
)
PROVINCE_RE = re.compile(rf"^(?:PROVINCE OF )?({PROVINCES})[ .,]*$", re.M)
UNIT_WORD_RE = re.compile(
    r"\b(AGENCY|AGENCIES|SUPERINTENDENCY|INSPECTORATE|COMMISSIONER|SURVEY|"
    r"SCHOOL|OFFICE|RESERVE|BAND)\b"
)
CAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 ,.&:;()'’-]{3,70}$")
CONTENTS_RE = re.compile(r"^(INDEX|CONTENTS)\.?\s*$", re.M)
CLOSING_RE = re.compile(
    r"(obedient|humble)\s+servant|I have the hono[u]?r to be|I have, ?&|"
    r"I am, ?(Sir|&)|Yours (very )?(truly|sincerely|respectfully)",
    re.I,
)


def slugify(text: str, maxlen: int = 30) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:maxlen].rstrip("-") or "section"


def read_body(md_path: Path):
    raw = md_path.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n.*?\n---\n", raw, re.S)
    doc_type = "annual_report"
    if m:
        dt = re.search(r'^document_type:\s*"?([\w]+)"?', m.group(0), re.M)
        if dt:
            doc_type = dt.group(1)
    return (raw[m.end():] if m else raw), doc_type


def page_at(pages, pos):
    """Printed page number in effect at char pos (pages: sorted (pos, num))."""
    cur = pages[0][1] if pages else 1
    for p, num in pages:
        if p > pos:
            break
        cur = num
    return cur


def letter_walkback(lines, line_starts, sir_line_idx):
    """Walk up from a 'SIR,' line over the header block (short/caps/address
    lines); return (start_line_idx, heading)."""
    heading = None
    start = sir_line_idx
    for i in range(sir_line_idx - 1, max(-1, sir_line_idx - 15), -1):
        line = lines[i].strip()
        if not line or PAGE_RE.match(line):
            start = i
            continue
        if len(line) > 110:  # long prose: previous segment's body
            break
        upper_ratio = sum(c.isupper() for c in line if c.isalpha()) / max(
            1, sum(c.isalpha() for c in line)
        )
        if upper_ratio > 0.7 and UNIT_WORD_RE.search(line.upper()):
            heading = line.rstrip(".,")
            start = i
            continue
        # address lines: dates, places, "Dear Sir" variants, short mixed case
        if len(line) <= 90:
            start = i
            continue
        break
    # trim: never swallow the previous letter's closing block (formula lines
    # plus the name/title lines that immediately follow them)
    formula = [
        i for i in range(start, sir_line_idx) if CLOSING_RE.search(lines[i])
    ]
    if formula:
        s = formula[-1] + 1
        while s < sir_line_idx and lines[s].strip() and not PAGE_RE.match(
            lines[s].strip()
        ):
            up = lines[s].strip()
            ratio = sum(c.isupper() for c in up if c.isalpha()) / max(
                1, sum(c.isalpha() for c in up)
            )
            if ratio > 0.7 and UNIT_WORD_RE.search(up.upper()):
                break  # agency header: keep it
            s += 1
        start = s
        heading = None
        for i in range(start, sir_line_idx):
            line = lines[i].strip()
            if not line:
                continue
            ratio = sum(c.isupper() for c in line if c.isalpha()) / max(
                1, sum(c.isalpha() for c in line)
            )
            if ratio > 0.7 and UNIT_WORD_RE.search(line.upper()):
                heading = line.rstrip(".,")
                break
    if heading is None:
        # first non-blank line of the block
        for i in range(start, sir_line_idx + 1):
            if lines[i].strip() and not PAGE_RE.match(lines[i].strip()):
                heading = lines[i].strip().rstrip(".,")
                break
    return start, heading or "letter"


def find_boundaries(body, lines, line_starts, letters_era):
    """Return sorted list of (char_pos, kind, heading)."""
    bounds = []

    if letters_era:
        for m in SIR_RE.finditer(body):
            line_idx = body.count("\n", 0, m.start())
            start_idx, heading = letter_walkback(lines, line_starts, line_idx)
            bounds.append((line_starts[start_idx], "agency_letter", heading))

    for m in CAPTION_RE.finditer(body):
        kind = "tabular_statement" if "STATEMENT" in m.group(1) else (
            "return" if m.group(1).startswith("RETURN") else "appendix"
        )
        bounds.append((m.start(), kind, m.group(1).strip().rstrip(".")))

    if not letters_era:
        # thematic era: standalone ALL-CAPS headings, excluding table rows,
        # provinces (kept as context), captions (already found), page markers
        for i, line in enumerate(lines):
            s = line.strip()
            if (
                CAPS_HEADING_RE.match(s)
                and not CAPTION_RE.match(s)
                and not PROVINCE_RE.match(s)
                and not PART_RE.match(s)
                and not s.startswith("TABLE")
                and 2 <= len(s.split()) <= 10
                and (i + 1 >= len(lines) or not CAPS_HEADING_RE.match(lines[i + 1].strip()) or not lines[i + 1].strip())
            ):
                # require blank line before (heading, not run-on caps text)
                if i > 0 and lines[i - 1].strip():
                    continue
                bounds.append((line_starts[i], "thematic_section", s.rstrip(".,")))

    # dedupe positions (letter walk-back may land on a caption line etc.)
    seen = {}
    for pos, kind, heading in sorted(bounds):
        if pos not in seen:
            seen[pos] = (kind, heading)
    return [(p, k, h) for p, (k, h) in sorted(seen.items())]


SUBSPLIT_THRESHOLD = 150_000  # chars; ~50 printed pages
SUBSPLIT_MAX_PAGES = 60

PROV_SUBHEAD_RE = re.compile(
    rf"^({PROVINCES})\.?( ?[-–—] ?(Continued|Concluded)\.?)?\s*$", re.M
)


def subsplit(seg, body, pages):
    """Split an oversized segment at province sub-headers, falling back to a
    hard page cap, so 700-page statistical tails become citable units."""
    s, e = seg["char_start"], seg["char_end"]
    if e - s <= SUBSPLIT_THRESHOLD:
        return [seg]
    marks = [m.start() + s for m in PROV_SUBHEAD_RE.finditer(body[s:e])]
    cuts = [s] + [m for m in marks if m - s > 500] + [e]
    # enforce page cap between consecutive cuts using page markers
    final_cuts = [cuts[0]]
    page_marks = [(p, n) for p, n in pages if s <= p < e]
    for c in cuts[1:]:
        while page_at(pages, c) - page_at(pages, final_cuts[-1]) > SUBSPLIT_MAX_PAGES:
            target = final_cuts[-1]
            nxt = [p for p, n in page_marks
                   if p > target and n - page_at(pages, target) >= SUBSPLIT_MAX_PAGES]
            if not nxt or nxt[0] >= c:
                break
            final_cuts.append(nxt[0])
        if c - final_cuts[-1] > 500:
            final_cuts.append(c)
    if final_cuts[-1] != e:
        final_cuts.append(e)
    if len(final_cuts) <= 2:
        return [seg]
    out = []
    for i in range(len(final_cuts) - 1):
        cs, ce = final_cuts[i], final_cuts[i + 1]
        m = PROV_SUBHEAD_RE.match(body[cs: cs + 90])
        sub_head = (
            f"{seg['heading']} — {m.group(1).title()}" if m else
            (seg["heading"] if i == 0 else f"{seg['heading']} (cont.)")
        )
        out.append(dict(seg, heading=sub_head, char_start=cs, char_end=ce))
    return out


def segment_volume(md_path: Path, doc_id: str, tag: str):
    body, doc_type = read_body(md_path)
    lines = body.split("\n")
    line_starts, pos = [], 0
    for ln in lines:
        line_starts.append(pos)
        pos += len(ln) + 1
    pages = [(m.start(), int(m.group(1))) for m in PAGE_RE.finditer(body)]

    n_sir = len(SIR_RE.findall(body))
    letters_era = n_sir >= 10 and doc_type == "annual_report"

    bounds = find_boundaries(body, lines, line_starts, letters_era)

    # fixed opening segments: title page, presentation letter, contents
    fixed = []
    pres = re.search(r"^To .{0,120}(Excellency|Royal Highness)", body, re.M)
    cont = CONTENTS_RE.search(body, pres.end() if pres else 0)
    if pres:
        fixed.append((0, "title_page", "Title page"))
        fixed.append((pres.start(), "presentation_letter", "Presentation letter"))
    if cont:
        fixed.append((cont.start(), "contents", "Contents"))
    body_start = None
    if bounds:
        body_start = bounds[0][0]
    # segment between contents end and first anchor = commissioner/deputy report
    if cont and body_start and body_start > cont.end():
        # report of the SG/Deputy SG opens the body proper; find its start as
        # the first long-prose line after the contents listing
        for i, ln in enumerate(lines):
            if line_starts[i] <= cont.start():
                continue
            if line_starts[i] >= body_start:
                break
            if len(ln.strip()) > 150:
                fixed.append((line_starts[i], "head_report", "Report of the Department"))
                break

    all_bounds = sorted(
        {p: (k, h) for p, k, h in fixed + bounds}.items()
    )
    all_bounds = [(p, k, h) for p, (k, h) in all_bounds]

    # province context
    prov_marks = [
        (line_starts[i], PROVINCE_RE.match(lines[i].strip()).group(1))
        for i in range(len(lines))
        if PROVINCE_RE.match(lines[i].strip())
    ]

    raw_segs = []
    for i, (posn, kind, heading) in enumerate(all_bounds):
        end = all_bounds[i + 1][0] if i + 1 < len(all_bounds) else len(body)
        if end - posn < 40:  # degenerate sliver
            continue
        raw_segs.append(
            dict(kind=kind, heading=heading, char_start=posn, char_end=end)
        )

    split_segs = []
    for seg in raw_segs:
        split_segs.extend(subsplit(seg, body, pages))

    segs = []
    used_ids = set()
    for seg in split_segs:
        posn, end = seg["char_start"], seg["char_end"]
        text = body[posn:end]
        page_s = page_at(pages, posn)
        page_e = page_at(pages, max(posn, end - 1))
        prov = None
        for pp, pv in prov_marks:
            if pp > posn:
                break
            prov = pv
        # a province line in the segment's own opening (address block or
        # sub-header) beats the trailing context
        opening = PROVINCE_RE.search(text[:1200])
        if opening:
            prov = opening.group(1)
        seg_id = f"p{page_s:04d}-{slugify(seg['heading'])}"
        n = 2
        while seg_id in used_ids:
            seg_id = f"p{page_s:04d}-{slugify(seg['heading'])}-{n}"
            n += 1
        used_ids.add(seg_id)
        segs.append(
            dict(
                doc_id=doc_id,
                tag=tag,
                doc_type=doc_type,
                segment_id=seg_id,
                ordinal=len(segs),
                kind=seg["kind"],
                heading=seg["heading"],
                province=prov,
                page_start=page_s,
                page_end=page_e,
                char_start=posn,
                char_end=end,
                text_version=hashlib.sha1(text.encode()).hexdigest()[:12],
            )
        )
    return segs, letters_era, n_sir


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    xw = pd.read_csv(ROOT / "registries/crosswalks/dia_sessional.csv")
    doc_ids = {
        r.tag: (r.paper_id if isinstance(r.paper_id, str) else f"prov:{r.tag}")
        for r in xw.itertuples()
    }
    all_segs = []
    print(f"{'tag':<14}{'era':<10}{'segs':>6}{'letters':>9}{'tables':>8}{'thematic':>10}")
    for md in sorted(DIA_MD.glob("dia_ar_*.md")):
        tag = md.stem
        segs, letters_era, n_sir = segment_volume(md, doc_ids.get(tag, f"prov:{tag}"), tag)
        with open(OUT / f"{tag}.segments.jsonl", "w") as fh:
            for s in segs:
                fh.write(json.dumps(s) + "\n")
        kinds = pd.Series([s["kind"] for s in segs]).value_counts()
        print(
            f"{tag:<14}{'letters' if letters_era else 'thematic':<10}"
            f"{len(segs):>6}{kinds.get('agency_letter', 0):>9}"
            f"{kinds.get('tabular_statement', 0) + kinds.get('return', 0):>8}"
            f"{kinds.get('thematic_section', 0):>10}"
        )
        all_segs.extend(segs)
    df = pd.DataFrame(all_segs)
    df.to_parquet(ROOT / "registries/documents/dia_segments.parquet", index=False)
    print(f"\ntotal segments: {len(df)} across {df.tag.nunique()} volumes")
    print(df.kind.value_counts().to_string())


if __name__ == "__main__":
    sys.exit(main())
