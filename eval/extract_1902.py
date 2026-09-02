#!/usr/bin/env python3
"""Structured extraction of the 1902 Schedule of Indian Reserves with an
OpenAI-compatible model server (vLLM), on the SAME 14 page-aligned chunks the
Sonnet extraction used — so the outputs are directly comparable
(`eval/compare_1902.py` vs `registries/entities/reserves_1902.parquet`).

    python3 eval/extract_1902.py --base-url http://localhost:8000/v1 --model Qwen/Qwen3.8-27B-FP8 \
        --out eval/results/sched1902_qwen38 [--chunks 0,1,5] [--reasoning-effort medium]
    python3 eval/extract_1902.py --dry          # prompt sizes only, no server

Output: <out>/out_NN.jsonl (one row per line, same 12-field schema as the
Sonnet outputs) plus <out>/raw_NN.txt (the model's full reply, for
diagnosis) and <out>/run.json (timing, tokens, parse status per chunk).
Uses plain HTTP (urllib) so it runs inside the vLLM container without extra
packages.
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHUNKS = ROOT / "registries/external/reserves_1902_extraction/chunks"

SYSTEM = """You are a meticulous archival data-extraction assistant. You convert OCR text of
a printed 1902 government schedule into structured JSON rows. You never invent
data: every value comes from the text, OCR artifacts included. You output ONLY a
JSON array — no prose, no markdown fences."""

PROMPT = """Below is OCR text (pdftotext -layout) from the Department of Indian Affairs
"Schedule of Indian Reserves in the Dominion" ({edition}), pages {pages}. It is a
printed table listing reserves under section headers: a province, then a
division (a COUNTY in the eastern provinces, an AGENCY in British Columbia and
elsewhere, or a TREATY NO. N section in the West). Columns vary by section but
are drawn from: No., Name, Where situated / Locality, Tribe or Band, Area
(acres; the header may read 'Area, Acres.'), Remarks. Page breaks appear as
'<!-- page N -->'. Column text is often wrapped over several lines and
interleaved between columns; reassemble each printed row.

The text begins with a CONTEXT block (up to the '=== EXTRACT FROM HERE ===' marker):
do NOT extract rows from the context; use it only to know which province /
division headers are in effect when the extract part begins.

Extract EVERY printed row in the extract part as a JSON object with exactly these keys:
  page        integer printed page (from the nearest preceding '<!-- page N -->')
  row_type    "reserve" for a reserve row; "total" for a printed section total; "note" for
              a free-text row that is not a reserve (rare)
  province    province/territory header in effect, upper case as printed
  division    the county / agency / treaty header in effect, as printed (e.g. "Kent.",
              "COWICHAN AGENCY", "TREATY NO. 7")
  reserve_no  the No. column as printed (string, e.g. "2", "27", "A"), or null.
              Numbering restarts per band in British Columbia — copy what is printed.
  name        reserve name as printed (keep OCR spelling), or null if the row has no name
  location    the Where situated / Locality text, reassembled, or null
  tribe_band  the Tribe or Band text, or null (in eastern sections tribe is a column; in
              BC the band name often heads a group of numbered reserves — repeat it on
              each row it applies to)
  acres_text  the area exactly as printed (e.g. "2,202 3/4", "546.76", "..."), or null
  acres       acres_text as a number (fractions converted, commas removed), or null
  remarks     the Remarks text, reassembled, or null
  confidence  "high" if every field is unambiguous; "medium" if OCR damage or column
              interleaving made any field uncertain; "low" if the row is badly damaged

Rules: keep rows in printed order; one object per printed row; do not merge or
split rows; do not normalize spellings or expand abbreviations; preserve '...'
where the schedule prints it; never extract from the CONTEXT block. Output a
single JSON array and nothing else.
{window}
TEXT:
{text}"""

CENSUS_PROMPT = """Below is OCR text (pdftotext -layout) from the Department of Indian Affairs
annual report for {edition}: the band CENSUS table (titled 'Census Return of Resident
and Nomadic Indians', 'Tabular Statement No. 4', 'Census of Indians', or 'Table No. 1 —
Census' depending on the year), pages {pages}. Rows are bands (or sub-bands / localities)
grouped under PROVINCE and AGENCY (or superintendency / treaty) headers, with numeric
columns whose definitions are printed once in the TABLE HEADER block — wrapped over
many lines and interleaved, so reconstruct them carefully. Typical columns: number in
band / population, religions (Anglican, Methodist, Roman Catholic, Presbyterian, Other
Christian, Aboriginal beliefs, …), sex and age bins (male/female under 6, 6–15, 16–20,
21–65, over 65), births, deaths, and in some years occupations or land/agriculture.
Page breaks appear as '<!-- page N -->'. '...' means an empty cell.

The text has a TABLE HEADER block, an optional CONTEXT block (do NOT extract rows from
it; use it only to know the province/agency in effect), and the EXTRACT part after the
'=== EXTRACT FROM HERE ===' marker.

Output a JSON array. Its FIRST element defines the columns you reconstructed:
  {{"row_type": "headers", "columns": ["Number in band", "Anglican", ...]}}
using the column labels as printed, in printed order, for every numeric column of the
table (do not include the band-name column). Then one object per printed row:
  page        integer pdf page (nearest preceding '<!-- page N -->')
  row_type    "band" for a band/sub-band/locality row; "total" for a printed total or
              sub-total row; "note" for a free-text row
  province    province/territory header in effect, as printed
  agency      agency / superintendency / treaty header in effect, as printed (or null)
  band        the row label as printed (band, tribe, locality, or 'Total …'), OCR spelling kept
  population  the row's total number of Indians (the 'Number in band' / 'Population' /
              'Total' column) as an integer, or null
  values      the row's numeric cells as strings, in printed order, aligned one-to-one
              with the headers array (use null for '...' or blank cells); keep the
              printed form ("1,204")
  confidence  "high" if alignment is unambiguous; "medium" if wrapping/interleaving made
              any cell uncertain; "low" if the row is badly damaged
Rules: keep rows in printed order; one object per printed row; do not merge or split
rows; do not normalize band names; never extract from the CONTEXT block; if the
number of numbers in a row does not match the headers, still output them in order and
set confidence "medium" or "low". Output a single JSON array and nothing else.
{window}
TEXT:
{text}"""

SCHOOL_PROMPT = """Below is OCR text (pdftotext -layout) from the Department of Indian Affairs
annual report for {edition}: a SCHOOL STATEMENT table (day, boarding, or industrial
schools), pages {pages}. Rows are schools grouped under PROVINCE headers (and sometimes
a school-type header). Identity columns: School, Reserve, Agency, Teacher (or
Principal), Denomination; then numeric columns whose definitions are printed once in
the TABLE HEADER block — wrapped over several lines — such as number on roll (boys,
girls, total), average attendance, pupils per Standard I–VI, salary, grant, or in
earlier years pupils by subject. Cells wrap across lines and interleave; reassemble
each printed row. Page breaks appear as '<!-- page N -->'. '...' means an empty cell.

The text has a TABLE HEADER block, an optional CONTEXT block (do NOT extract rows from
it), and the EXTRACT part after the '=== EXTRACT FROM HERE ===' marker.

Output a JSON array. Its FIRST element defines the numeric columns you reconstructed:
  {{"row_type": "headers", "columns": ["Number on roll: boys", "Number on roll: girls", ...]}}
(labels as printed, printed order, numeric columns only). Then one object per printed row:
  page          integer pdf page (nearest preceding '<!-- page N -->')
  row_type      "school" | "total" | "note"
  province      province/territory header in effect, as printed
  school_type   "day" | "boarding" | "industrial" | null — from the statement title or
                a section header, if stated
  school        school name as printed (keep marks such as '*' or '(R.C.)')
  reserve       Reserve column as printed, or null
  agency        Agency column as printed, or null
  teacher       Teacher / Principal column as printed, or null
  denomination  Denomination column as printed, or null
  values        the numeric cells as strings, in printed order, aligned one-to-one with
                the headers array (null for '...' or blank); keep the printed form
  confidence    "high" | "medium" | "low" as for alignment certainty
Rules: keep printed order; one object per printed row; do not merge or split rows; do
not normalize names; never extract from the CONTEXT block; if a row's numbers do not
match the headers count, still output them in order with confidence "medium" or "low".
Output a single JSON array and nothing else.
{window}
TEXT:
{text}"""

OFFICERS_PROMPT = """Below is OCR text (pdftotext -layout) from the Department of Indian Affairs
annual report for {edition}: RETURN A, the list of OFFICERS AND EMPLOYÉS of the
Department (headquarters, then each agency / superintendency / school), pages {pages}.
Columns (wrapped over several lines and interleaved): Designation (office held), Name,
Annual Salary ($ cts.), When appointed to Department, By whom appointed, Date of first
appointment to the Civil Service; some years add Remarks or a Residence column. Section
headers name the agency or office in effect. Page breaks appear as '<!-- page N -->'.
'...' means an empty cell. Names may be split across lines ("Hon. E. / Dewdney").

The text has a TABLE HEADER block, an optional CONTEXT block (do NOT extract rows from
it), and the EXTRACT part after the '=== EXTRACT FROM HERE ===' marker.

Output a JSON array. Its FIRST element declares any numeric/extra columns beyond the
fixed ones: {{"row_type": "headers", "columns": [...]}} (may be empty). Then one object
per printed person row:
  page            integer pdf page
  row_type        "officer" | "total" | "note"
  section         the agency / office header in effect, as printed (or null)
  designation     office held, as printed
  name            person's name, reassembled, as printed
  salary          annual salary as printed ("1,600", "3,200 00"), or null
  appointed       'When appointed to Department' as printed, or null
  appointed_by    'By whom appointed' as printed, or null
  first_civil     'Date of first appointment to the Civil Service' as printed, or null
  extras          remaining cells as strings aligned to the headers array, or []
  confidence      "high" | "medium" | "low"
Rules: printed order; one object per person; do not normalize names or dates; never
extract from the CONTEXT block. Output a single JSON array and nothing else.
{window}
TEXT:
{text}"""

TABLE_PROMPT = """Below is OCR text (pdftotext -layout) of a statistical table from the Department of
Indian Affairs annual report for {edition}, pages {pages}. The table's title and column
definitions are printed once in the TABLE HEADER block — wrapped over several lines and
interleaved — followed by rows whose first column is a label (a band, agency, reserve,
school, province, township, or item) and whose remaining columns are numbers or short
texts. Page breaks appear as '<!-- page N -->'. '...' means an empty cell.

The text has a TABLE HEADER block, an optional CONTEXT block (do NOT extract rows from
it), and the EXTRACT part after the '=== EXTRACT FROM HERE ===' marker.

Output a JSON array. Its FIRST element defines the columns you reconstructed, in
printed order, excluding the label column:
  {{"row_type": "headers", "title": "<table title as printed>", "columns": [...]}}
Then one object per printed row:
  page        integer pdf page
  row_type    "row" | "total" | "note"
  section     the province / agency / group header in effect, as printed (or null)
  label       the row's first-column label as printed
  values      the remaining cells as strings, aligned one-to-one with the headers array
              (null for '...' or blank), printed form kept
  confidence  "high" | "medium" | "low"
Rules: printed order; one object per printed row; do not merge or split rows; if a row's
cell count does not match the headers, still output them in order with confidence
"medium" or "low"; never extract from the CONTEXT block. Output a single JSON array and
nothing else.
{window}
TEXT:
{text}"""

WINDOW = ("\nIMPORTANT: for this call, output ONLY the rows printed on pages {a} to {b}"
          " inclusive (use the '<!-- page N -->' markers); the rest of the text is"
          " provided as context for wrapped rows and section headers.\n")


def load_chunk(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"chunk_(\d+)_p(\d+)-(\d+)", path.name)
    return (int(m.group(1)), f"{m.group(2)}–{m.group(3)}", text,
            int(m.group(2)), int(m.group(3)))


def windows_for(text, p_lo, p_hi, n_windows):
    """Split the chunk's page range into n roughly text-equal page windows,
    based on where the page markers fall in the extract part."""
    marks = [(int(m.group(1)), m.start()) for m in re.finditer(r"<!-- page (\d+) -->", text)]
    pages = sorted({p for p, _ in marks if p_lo <= p <= p_hi}) or [p_lo]
    if n_windows <= 1 or len(pages) < 2:
        return [(p_lo, p_hi)]
    per = max(1, round(len(pages) / n_windows))
    bounds, i = [], 0
    while i < len(pages):
        seg = pages[i:i + per]
        bounds.append((seg[0], seg[-1]))
        i += per
    if len(bounds) > 1 and bounds[-1][1] - bounds[-1][0] == 0:
        a, _ = bounds.pop(-1)
        bounds[-1] = (bounds[-1][0], p_hi)
    bounds[0] = (p_lo, bounds[0][1])
    bounds[-1] = (bounds[-1][0], p_hi)
    return bounds


def parse_json_array(reply):
    """Tolerant parse: strip fences / thinking, find the outermost array."""
    s = re.sub(r"<think>.*?</think>", "", reply, flags=re.S)
    s = re.sub(r"^```(?:json)?|```$", "", s.strip(), flags=re.M).strip()
    if not s:
        return [], "empty-reply"   # window with no table rows (index/plate pages)
    i, j = s.find("["), s.rfind("]")
    if i < 0:
        raise ValueError("no JSON array in reply")
    body = s[i:j + 1] if j > i else s[i:]
    try:
        return json.loads(body), "ok"
    except json.JSONDecodeError:
        # salvage complete objects from a truncated array
        objs, depth, start = [], 0, None
        for k, ch in enumerate(body):
            if ch == "{":
                if depth == 0:
                    start = k
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        objs.append(json.loads(body[start:k + 1]))
                    except json.JSONDecodeError:
                        pass
                    start = None
        if not objs:
            raise
        return objs, "salvaged"


def dedupe(rows):
    """Drop only true double-emissions (same page, name, number, band, AND
    location) — band-relative numbering means (page, name, no) alone collides
    across bands, especially on BC pages with null names."""
    seen, uniq = set(), []
    for r in rows:
        k = (r.get("page"), str(r.get("name")).strip().lower(), str(r.get("reserve_no")),
             str(r.get("tribe_band")).strip().lower(), str(r.get("location") or "")[:40].strip().lower(),
             # census-task fields (all null for schedule rows, so harmless there)
             str(r.get("band")).strip().lower(), str(r.get("agency")).strip().lower(),
             str(r.get("population")), str(r.get("school")).strip().lower(),
             str(r.get("teacher")).strip().lower(), str(r.get("label")).strip().lower(),
             str(r.get("designation")).strip().lower(), str(r.get("section")).strip().lower(), json.dumps(r.get("values"), ensure_ascii=False)[:120]
             if r.get("values") is not None else None,
             json.dumps(r.get("columns"))[:120] if r.get("columns") is not None else None)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq


def remerge(out):
    """Rebuild every out_NN.jsonl from the saved raw_NN_w.txt replies using the
    current parse + window-filter + dedupe logic (no server needed)."""
    runlog = out / "run.json"
    log = json.loads(runlog.read_text()) if runlog.exists() else {}
    for n in sorted({int(p.stem.split("_")[1]) for p in out.glob("raw_*.txt")}):
        raws = sorted(out.glob(f"raw_{n:02d}_*.txt"),
                      key=lambda p: int(p.stem.split("_")[2])) or sorted(out.glob(f"raw_{n:02d}.txt"))
        entries = log.get(f"{n:02d}", {}).get("windows", [])
        rows_all, bad = [], False
        for wi, rp in enumerate(raws):
            e_w = entries[wi] if wi < len(entries) else {}
            if "error" in e_w or "truncated_split" in e_w or "retry" in e_w:
                continue   # truncated / failed / retried reply; its pages were re-covered
            try:
                rows, status = parse_json_array(rp.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"chunk {n:02d} {rp.name}: parse failed ({e})"); bad = True; continue
            a, b = (e_w.get("window") or (None, None))
            for r in rows:
                if not isinstance(r, dict):
                    continue
                r.setdefault("row_type", "reserve")
                try:
                    pg = int(r.get("page") or 0)
                except (TypeError, ValueError):
                    pg = 0
                if a is None or len(raws) == 1 or (a <= pg <= b) or pg == 0:
                    rows_all.append(r)
        if bad and not rows_all:
            continue
        uniq = dedupe(rows_all)
        with (out / f"out_{n:02d}.jsonl").open("w", encoding="utf-8") as fh:
            for r in uniq:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"chunk {n:02d}: remerged {len(uniq)} rows from {len(raws)} raw file(s)")


def chat(base_url, model, messages, max_tokens, temperature, effort, timeout, api_key):
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature, "top_p": 0.8}
    if effort:
        # Qwen3.x via vLLM: reasoning_effort is honoured by the chat template
        body["reasoning_effort"] = effort
        body["chat_template_kwargs"] = {"enable_thinking": effort != "none"}
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="Qwen/Qwen3.8-27B-FP8")
    ap.add_argument("--api-key", default="none")
    ap.add_argument("--out", default=str(ROOT / "eval/results/sched1902_qwen38"))
    ap.add_argument("--chunks", default=None, help="comma list of chunk numbers")
    ap.add_argument("--reasoning-effort", default="medium", choices=["none", "low", "medium", "xhigh"])
    ap.add_argument("--max-tokens", type=int, default=32000)
    ap.add_argument("--max-model-len", type=int, default=40960,
                    help="server's --max-model-len; output budget = this - prompt - margin")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--task", default="schedule", choices=["schedule", "census", "school", "officers", "table"],
                    help="which table family / prompt to use")
    ap.add_argument("--edition", default="1902",
                    help="edition year named in the prompt (page numbers are pdf pages of that AR)")
    ap.add_argument("--chunks-dir", default=str(CHUNKS),
                    help="directory of chunk_NN_pA-B.txt files (default: the 1902 Schedule chunks)")
    ap.add_argument("--parallel", type=int, default=1,
                    help="chunks processed concurrently (vLLM batches the requests)")
    ap.add_argument("--n-windows", type=int, default=None,
                    help="force this many page windows per chunk (for dense chunks that truncate)")
    ap.add_argument("--remerge", action="store_true",
                    help="rebuild out_NN.jsonl from saved raw replies; no server calls")
    ap.add_argument("--force", action="store_true", help="redo chunks that already have output")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.remerge:
        return remerge(out)
    want = {int(c) for c in args.chunks.split(",")} if args.chunks else None
    runlog = out / "run.json"
    log = json.loads(runlog.read_text()) if runlog.exists() else {}
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()

    def process_chunk(path):
        n, pages, text, p_lo, p_hi = load_chunk(path)
        if want is not None and n not in want:
            return
        existing = out / f"out_{n:02d}.jsonl"
        if not args.force and existing.exists() and existing.stat().st_size > 0 and not args.dry:
            print(f"chunk {n:02d}: exists, skip"); return
        # context budget: the whole chunk is sent every call; output must fit in
        # what remains of max-model-len. Output tokens run ≈0.9/char of the text
        # actually extracted (measured: 26.9k tokens for a 26.9k-char chunk at
        # effort=medium, thinking included) — split the page range accordingly.
        # digits tokenize ~1/char, prose ~1/3.5 chars: a digit-dense census page
        # can be 2x the naive estimate (1929 chunks hit HTTP 400 on overflow)
        n_dig = sum(ch.isdigit() for ch in text)
        est_prompt = (len(text) - n_dig) // 3 + n_dig + 700
        budget = min(args.max_tokens, args.max_model_len - est_prompt - 512)
        n_win = max(1, -(-int(len(text) * 0.9) // budget)) if budget > 2000 else 99
        if args.n_windows:
            n_win = args.n_windows
        if budget <= 2000:
            print(f"chunk {n:02d}: prompt too large for context ({est_prompt} est tokens), skipping")
            return
        wins = windows_for(text, p_lo, p_hi, n_win)
        if args.dry:
            print(f"chunk {n:02d} pp. {pages}: {len(text):,} chars, prompt ≈ {est_prompt:,} tokens, "
                  f"out-budget {budget:,} → {len(wins)} window(s) {wins}")
            return
        t0 = time.time()
        print(f"chunk {n:02d} pp. {pages}: {len(text):,} chars, {len(wins)} window(s) {wins} → {args.model} …", flush=True)
        rows_all, entry_w, failed = [], [], False
        queue = [(a, b, 0, args.reasoning_effort) for a, b in wins]   # (lo, hi, split_depth, effort)
        wi = 0
        while queue:
            a, b, depth, effort = queue.pop(0)
            window = WINDOW.format(a=a, b=b) if (len(wins) > 1 or depth) else ""
            prompt = {"census": CENSUS_PROMPT, "school": SCHOOL_PROMPT, "officers": OFFICERS_PROMPT,
                      "table": TABLE_PROMPT}.get(args.task, PROMPT).format(
                pages=pages, text=text, window=window, edition=args.edition)
            tw = time.time()
            try:
                resp = chat(args.base_url, args.model,
                            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                            budget, args.temperature, effort, args.timeout, args.api_key)
            except Exception as e:
                entry_w.append({"window": [a, b], "error": str(e)})
                print(f"  window p{a}-{b}: ERROR {e}"); failed = True; continue
            choice = resp["choices"][0]
            msg = choice["message"]
            reply = msg.get("content") or ""
            reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
            ctoks = (resp.get("usage") or {}).get("completion_tokens") or 0
            salvaged = False
            if not reply.strip() and reasoning and "[" in reasoning:
                # answer emitted inside the thinking block (reasoning parser
                # stripped it): salvage the JSON array from the reasoning text
                reply = reasoning[reasoning.find("["):]
                salvaged = True
                print(f"  window p{a}-{b}: empty content, salvaging array from reasoning")
            (out / f"raw_{n:02d}_{wi}.txt").write_text(reply, encoding="utf-8")
            if reasoning:
                (out / f"reasoning_{n:02d}_{wi}.txt").write_text(reasoning, encoding="utf-8")
            wi += 1
            if not reply.strip() and ctoks > 1500:
                if effort != "none":   # retry once without thinking: direct JSON, no leak
                    queue.insert(0, (a, b, depth, "none"))
                    entry_w.append({"window": [a, b], "retry": "empty content → retry with thinking off",
                                    "completion_tokens": ctoks})
                    print(f"  window p{a}-{b}: EMPTY after {ctoks} tokens — retrying with thinking off")
                    continue
                entry_w.append({"window": [a, b], "error": f"empty content after {ctoks} completion tokens",
                                "completion_tokens": ctoks})
                print(f"  window p{a}-{b}: EMPTY after {ctoks} tokens — marking failed for rerun")
                failed = True
                continue
            if choice.get("finish_reason") == "length":
                # Thinking ate the budget (table written inside the reasoning block,
                # or a dense single page): retry with thinking off BEFORE splitting /
                # failing — the thinking-off reply is compact and usually fits.
                # (2026-08-29: 1902 census — every "TRUNCATED at budget on a single
                # page" window had a salvaged-from-reasoning reply; the windows that
                # got the thinking-off retry all succeeded.)
                if effort != "none" and (salvaged or a == b or depth >= 3):
                    queue.insert(0, (a, b, depth, "none"))
                    entry_w.append({"window": [a, b], "retry": "truncated → retry with thinking off",
                                    "completion_tokens": ctoks})
                    print(f"  window p{a}-{b}: TRUNCATED after {ctoks} tokens"
                          f"{' (reply salvaged from reasoning)' if salvaged else ''} — retrying with thinking off")
                    continue
                if b > a and depth < 3:   # adaptive: split the window and retry halves
                    mid = (a + b) // 2
                    queue = [(a, mid, depth + 1, effort), (mid + 1, b, depth + 1, effort)] + queue
                    entry_w.append({"window": [a, b], "truncated_split": [[a, mid], [mid + 1, b]],
                                    "completion_tokens": (resp.get("usage") or {}).get("completion_tokens")})
                    print(f"  window p{a}-{b}: TRUNCATED — splitting into p{a}-{mid} + p{mid + 1}-{b}")
                    continue
                entry_w.append({"window": [a, b], "error": "truncated at max_tokens (finish=length)",
                                "completion_tokens": (resp.get("usage") or {}).get("completion_tokens")})
                print(f"  window p{a}-{b}: TRUNCATED at budget on a single page — marking failed")
                failed = True
                continue
            try:
                rows, status = parse_json_array(reply)
            except Exception as e:
                rows = None
                if reasoning and "[" in reasoning:   # array may live in the thinking block
                    try:
                        rows, status = parse_json_array(reasoning[reasoning.find("["):])
                        status += "-from-reasoning"
                        print(f"  window p{a}-{b}: content unparseable, array recovered from reasoning")
                    except Exception:
                        rows = None
                if rows is None:
                    if effort != "none":   # retry once without thinking
                        queue.insert(0, (a, b, depth, "none"))
                        entry_w.append({"window": [a, b], "retry": f"parse failed ({e}) → retry with thinking off",
                                        "completion_tokens": ctoks})
                        print(f"  window p{a}-{b}: PARSE FAILED ({e}) — retrying with thinking off")
                        continue
                    entry_w.append({"window": [a, b], "parse": f"failed: {e}", "completion_tokens": ctoks})
                    print(f"  window p{a}-{b}: PARSE FAILED: {e}"); failed = True; continue
            kept = []
            for r in rows:
                if not isinstance(r, dict):   # model emitted strings/None inside the array
                    continue
                r.setdefault("row_type", "reserve")
                try:
                    pg = int(r.get("page") or 0)
                except (TypeError, ValueError):
                    pg = 0
                if not window or a <= pg <= b or pg == 0:
                    kept.append(r)
            rows_all += kept
            entry_w.append({"window": [a, b], "rows": len(kept), "dropped_out_of_window": len(rows) - len(kept),
                            "parse": status, "seconds": round(time.time() - tw, 1),
                            "finish_reason": choice.get("finish_reason"),
                            "completion_tokens": (resp.get("usage") or {}).get("completion_tokens")})
            print(f"  window p{a}-{b}: {len(kept)} rows ({status}), {entry_w[-1]['seconds']}s, "
                  f"finish={entry_w[-1]['finish_reason']}, tokens={entry_w[-1]['completion_tokens']}")
        entry = {"seconds": round(time.time() - t0, 1), "pages": pages, "windows": entry_w,
                 "rows": len(rows_all), "complete": not failed}
        if rows_all and not failed:
            # de-dupe overlapping boundary rows (same page+name+no emitted by two windows)
            uniq = dedupe(rows_all)
            with (out / f"out_{n:02d}.jsonl").open("w", encoding="utf-8") as fh:
                for r in uniq:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            entry["rows"] = len(uniq)
            print(f"  chunk {n:02d}: {len(uniq)} rows total, {entry['seconds']}s")
        elif failed:
            print(f"  chunk {n:02d}: INCOMPLETE (window failure) — no out file written, rerun to retry")
        with lock:
            log[f"{n:02d}"] = entry
            runlog.write_text(json.dumps(log, indent=1))

    def safe_process(path):
        """One chunk's exception must never take the whole run down."""
        try:
            process_chunk(path)
        except Exception as e:  # noqa: BLE001
            import traceback
            n = int(re.search(r"chunk_(\d+)", path.name).group(1))
            print(f"chunk {n:02d}: CRASHED {type(e).__name__}: {e}")
            traceback.print_exc()
            with lock:
                log[f"{n:02d}"] = {"error": f"{type(e).__name__}: {e}", "complete": False}
                runlog.write_text(json.dumps(log, indent=1))

    paths = sorted(Path(args.chunks_dir).glob("chunk_*.txt"))
    if not paths:
        print(f"no chunk_*.txt in {args.chunks_dir}"); return 1
    if args.parallel > 1 and not args.dry:
        with ThreadPoolExecutor(max_workers=args.parallel) as ex:
            list(ex.map(safe_process, paths))
    else:
        for p in paths:
            safe_process(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
