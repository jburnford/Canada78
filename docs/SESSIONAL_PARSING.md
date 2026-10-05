# Parsing the Sessional Papers — design notes (Phase 3)

_2026-08-27, written while the OCR run finishes (366/372 volumes). The corpus
is Canadiana series `oocihm.9_08052`: **372 bound volumes, sessions 1–34,
1867–1900** (post-1900 sessions are a different acquisition — not in scope;
the 31 DIA reports 1901–1930 keep their `prov:` ids). Text is Chandra-2
markdown, ~620 MB of paper text so far (235 volumes) → ~1 GB complete._

## Step 1 — break volumes into the papers listed in the table of contents

This already exists in `~/sessional_papers` (scripts 10–13) and is the
right design; it needs a full rerun, not a rewrite.

- `10_parse_index.py`: each volume reprints the whole session's *List of
  Sessional Papers* (four typographic eras); copies are **majority-voted per
  session** so OCR damage in one copy is repaired by the others. More
  volumes → better reconciliation, automatically.
- `11_segment.py`: boundaries from title-page imprint clusters, `RETURN (N)`
  captions, running heads; paper numbers assigned monotonically against
  the reconciled list; fuzzy title matching and title-shingle search for
  caption-less papers; pages by image-anchor interpolation.
- `12_extract_papers.py`: one markdown + sidecar per paper (multi-volume
  papers concatenated); front matter (incl. the **alphabetical subject
  index** — a subject-heading vocabulary worth keeping) per volume.
- `13_validate.py`: badness-ranked review queue (41 volumes ≥ 15 at 235
  vols; worst are late catch-all returns volumes where index titles differ
  from body wording).

State at 235 volumes: 2,157 papers extracted / 5,363 catalog entries
(1,048 direct numbers, 690 fuzzy-title, 153 title-anchored, 188
self-numbered). **To do:** refresh `markdown/` from Nibi
(`output/<vol>/<vol>/<vol>.md`), rerun 10→15→14 incrementally, re-validate,
then hand the residual queue to an LLM refinement pass (Qwen: "which of
these index titles does this caption-less statement match?") and human
spot-checks. Then the Canada50 document registry re-derives from the
catalog (`registries/documents/documents.parquet`), and the segment-level
edition sites (docs/URL_SCHEME.md) can be generated.

## Step 2 — different approaches for different kinds of paper

What the corpus is made of (papers.parquet at 235 volumes; the ratios will
hold at 372):

| kind | papers | chars | nature | approach |
|---|---|---|---|---|
| **Annual reports** (22 series: Marine & Fisheries 34M, Agriculture 27M, Insurance 26M, Post Office 19M, Indian Affairs 18M, Public Works 17M, Interior 16M, Finance, Railways & Canals, Militia, Trade & Commerce, Justice…) | 199 | 231M | department head report + sub-reports/letters + tabular statements + appendices, in a per-series house style that drifts by era | **A** per-series *within-paper segmenter* (generalize `build/segment_dia.py`: heading grammar → letters / thematic sections / tables / appendices, stable segment ids); narrative → Tier 0/1 linking + wiki hub pages; tables → Qwen table families with a Claude-designed schema each (DIA pattern: locator + schema + validation sample) |
| **Trade & Navigation tables** | ~24 issues | ~30M+ (4.5M each) | the largest single tables in the corpus; uniform structure by year | **B** the `uk_trade_db` pipeline (Phase 4, trade-data session): table-grammar parsing + reconciliation, not generic LLM extraction |
| **Returns to order / address** | 1,506 | 75M | ~92 % narrative: correspondence, reports, petitions (Justice, Militia, Marine, Public Works…); ~8 % tabular (digit share > 0.08) | **C** *letters pipeline*: split by dateline/salutation/signature grammar (already exists for DIA agency letters), date + sender/recipient/place extraction, Tier 0/1 linking against places/persons/organizations — this is the KG-densest material (who wrote to whom about what, where, when); tabular minority routed to **D** |
| **Public Accounts / Auditor General / Estimates** | 41 | 70M | ledgers: account → sub-head → payee → item → amount | **D** ledger schema for Qwen (account, subhead, payee, description, amount, page); huge but regular; payees are entity links; do last |
| **Lists** (bank shareholders, civil-service lists, …) | 10 | 25M | entity-dense tables | **D** generic table task (headers row + positional values — the census/school scheme already is generic) → person/organization linking |
| **"other"** | 374 | 210M | mostly T&N (above) + mis-typed annuals (Postmaster General 12.5M, Militia 8.7M, Inland Revenue 5.8M, Banks 5.1M) | typology refinement first (body-shape classifier: table density, letter grammar, series titles), then A/B/D |
| statements, correspondence, commission reports | 27 | 10M | small | fold into A/C |

Cross-cutting, deterministic, cheap:
- **Typology refinement** (`15_enrich_rules.py`): add body-shape features
  (table-tag density, digit share, letter grammar hits) so "other" shrinks
  and A/C/D routing is automatic.
- **Corpus-wide Tier 0/1 mention linking** (`build/link_mentions.py`
  generalized): gazetteer + cue disambiguation + region guard against the
  registries we hold (8,706 HGIS places, agencies/reserves/bands, LINCS
  persons, DCB persons via hgiscanada). CPU, hours. Residuals → Qwen in
  short prompts (prefill-dominated, days on any GPU).
- **Wiki/MCP generalization**: `gen_wiki.py`, `canada50_mcp/index.py`
  read `dia_segments` today; move them to the catalog's segments table so
  every paper gets a hub page and every department/series a facet page.

Qwen lanes (bulk, fleet): A-tables, D. Claude lanes: segmenter grammars,
schemas, typology rules, validation samples, linking/adjudication.
Nothing runs "LLM over all 1 GB".

## Sequencing (proposed)

1. Ingest rerun on 372 volumes → catalog, registry, validation queue. (user
   runs scripts; Claude reviews the queue)
2. Typology refinement + generic within-paper segmenter for annual reports
   (Claude) → segment-level edition build.
3. Corpus-wide Tier 0/1 linking (Claude/CPU) → wiki for the whole corpus.
4. Two table families to prove the cross-series pattern, chosen by value:
   Marine & Fisheries returns (fisheries statistics by district) and
   Interior (Dominion Lands: homestead entries / land sales) — locator +
   schema + 1-volume validation sample each, then Qwen fleet.
5. Letters pipeline over the returns (C).
6. Ledgers (D) and lists.
7. T&N stays with the trade-data session (B).

## Priorities (user, 2026-08-27)

Two goals, in tension only in ordering: (1) the user's own research is
**foreign trade and trade relations**; (2) the sessional papers are the next
layer of the **general research tool for historians** built on the LOD
projects (the encyclopedia model: a linked, citable wiki + MCP tools).

What the catalog says about (1): **378 of 2,157 extracted papers (96M chars)
are trade/foreign-relations material** by title/subject — 296 of them are
*narrative returns* (correspondence on seizures of vessels, chamber-of-
commerce memorials, reciprocity and tariff negotiations, drawbacks, excise,
tea imports from China/Japan, subsidised steamship lines), 67 are the T&N
tables and similar statements, 9 are Trade & Commerce annual reports. A
further 902 of 5,363 *catalog entries* match by title, 559 of them "not
printed" — titles only, but still nodes (what Parliament asked for, when,
moved by whom).

Consequences for sequencing:
- The general tool comes from the **breadth-first deterministic layer**
  (steps 1–3: full ingest, typology, within-paper segmentation, corpus-wide
  linking, wiki + MCP over the whole catalog). It serves every historian and
  costs no GPU. Do it first.
- The trade lane rides on top of it: **C (letters pipeline) before D**,
  applied first to the 296 trade-related returns — that is the
  foreign-relations corpus (who lobbied, who negotiated, which ports,
  which vessels). Topic facets come from the front-matter subject index
  ("TRADE", "TARIFF", "RECIPROCITY", "CUSTOMS", "BOUNTIES"…) and the
  typology, so a "Trade & foreign relations" facet page is a query, not a
  hand-curated list.
- First Qwen table families (step 4) are chosen for trade: the **Trade &
  Commerce** and **Customs / Inland Revenue** annual-report tables, and
  the Marine & Fisheries shipping returns; Interior/Dominion Lands waits.
- **B (T&N tables)** stays with the trade-data session's `uk_trade_db`
  pipeline; Canada50 links its outputs to the same paper ids.
- Post-1900 sessional coverage: not now; note it as the obvious extension
  once the 1867–1900 layer is public.

---

## Addendum 2026-10-04 — a page spine from Canadiana, and how to parse the big volumes

_Written after the series-by-session pass (`build/sessional_series_by_year.py`)
and the viewer (`site_sessional/`). Numbers below are measured on the 235-volume
ingest and the 372 cached IIIF manifests._

### 1. The problem with the current cut is pages, not text

- The Chandra markdown has **no page breaks** (the run used the defaults, which
  also drop `Page-Header`/`Page-Footer` blocks — so the "SESSIONAL PAPER No. N"
  running heads are gone too: 96 hits in a 1,041-page volume). Page numbers in
  `papers.parquet` are *interpolated* from four image anchors per volume and
  per-page token counts.
- Chandra's per-page `token_count` does **not** align with a Qwen2.5 tokenizer
  over the markdown (0.80 ratio on `18_2`; cumulative boundaries land mid
  table-row), so the metadata cannot be turned into exact page offsets.
- Volumes are big: median 3.5 MB of markdown, 55 volumes over 5 MB, the AG
  volumes up to 13.5 MB; **84 papers over 2M chars hold 43% of all text** (T&N
  52M chars, Insurance 44M, Auditor General 39M, Public Accounts 33M, Post
  Office 31M, bank shareholders 28M, Marine 26M).

### 2. Canadiana already labels every scan with its paper

The IIIF v3 manifest (`https://www.canadiana.ca/iiif/{tag}/manifest`, cached
for all 372 volumes in `registries/external/canadiana/pages/{tag}.tsv` by
`build/fetch_canadiana_manifests.py`) gives each canvas a label of the form

```
p. {paper}-{page}          p. 9-78         (paper 9, printed page 78)
p. {paper}-{part}-{page}   p. 1-R-167      (AG report 1900, Part R, p. 167)
p. {paper}-{roman}         p. 11-iv        (front matter of paper 11)
p. {page}                  p. 27           (volume front matter / single-paper volumes)
index (p. 1-3) · table of contents (p. 7) · unnumbered · blank page · title page
```

Median **84% of canvases carry a paper number** (20 volumes under 50%, a few at
0% — mostly 1888–1890 returns volumes). This is a page-exact *paper spine*
from the library's own cataloguing, independent of our OCR. A first comparison
(arabic-page labels only — the grammar above still needs implementing) against
our interpolated boundaries: papers cut by printed number agree within 3 pages
in most cases (median |Δstart| = 3), but `title_match` cuts are off by a median
of **21 pages** and `unassigned` ones by hundreds; and **412 paper ranges that
Canadiana labels inside ingested volumes were never extracted at all**
(1,723 labelled ranges vs 2,157 papers). The spine both fixes boundaries and
finds missing papers.

### 3. Decision: re-OCR with pagination, or align approximately

| | A. Re-run Chandra with `--paginate_output --include-headers-footers --max-output-tokens 24000` | B. Keep the text, align to the spine approximately |
|---|---|---|
| what we get | exact page markers (tropical `output_v3`: markers = pages−1), running heads and folios back, no silent table truncation at the 12,384-token cap | paper boundaries page-exact from Canadiana; *within-paper* char offsets still interpolated (±1–3 pages) |
| cost | ~298K pages at ~9 pp/min/H100 ≈ **550 GPU-h** (Nibi/Trillium), plus the ingest rerun | CPU only, days of Claude work |
| risk | the tropical re-run failed twice from a missing flag — use its RERUN_PLAN checks (fail the job if markers ≠ pages−1) | tables that straddle a page join are split by interpolation; citations are "p. ~78" |

Recommendation: **B now, A later for the table-heavy series only** (T&N,
Public Accounts, AG, bank lists, Insurance: ~70 volumes), where exact page
joins matter for table reconstruction and where the output-token cap most
likely truncated pages. The narrative and letters series do not need it.

### 4. Paper segmentation v2 (replaces imprint clusters as the primary method)

1. Parse the label grammar → per canvas `(paper_num, part, printed_page, kind)`;
   fill `p. N`-only runs by volume context (single-paper volume, or front matter).
2. Paper page ranges come from the labels; the imprint-cluster / `RETURN (N)` /
   title-shingle methods of `11_segment.py` become **witnesses** that locate the
   boundary *within* the text near the interpolated page, instead of deciding
   it. Where labels are absent (the 0% volumes) the old method stands alone.
3. Char offsets: interpolate inside the paper from per-page token counts, then
   snap to the nearest structural break (`</table>`, blank line, heading).
4. Validation: per volume, labelled pages assigned = labelled pages total;
   paper count = distinct label papers; the session-list titles still matched
   by shingle for a second witness. Review queue = volumes where witnesses
   disagree by > 3 pages.
5. Outcome: a `pages` table (volume, canvas, paper, part, printed page, image
   id) that every later segment and table cites — the sessional analogue of
   the DIA `<!-- page N -->` anchors. The viewer's thumbnails already key on it.

### 5. Within-paper parsing by series class (what the structure actually is)

Measured on 1885/1892/1899 papers (`<table>` share of chars, distinct first-row
header signatures):

| class | series | shape | approach |
|---|---|---|---|
| **B. uniform tables** | T&N (98% tables; 1,114 tables, 440 share one header, 4–6 cols), bank shareholders (99%; 33 signatures), criminal statistics (97%), Estimates/Public Accounts votes tables | Chandra already emits **HTML tables** — cells are there, no LLM needed | deterministic: header-signature clustering → one column map per signature family per era (the DIA `column_maps/*.yaml` pattern) → long table of (paper, page, table_id, row, column, value) with the printed wording kept in the id. T&N is the `uk_trade_db` lane. |
| **D. ledgers** | Auditor General (87% tables but **990 signatures / 1,354 tables**, mostly 2–3 col fragments; plus 1,191 letter cues in its correspondence parts), Public Accounts detail, Insurance (966 signatures) | tables fragment per page/sub-head; meaning comes from the **heading above** (Part letter, department, vote) | deterministic table parse + a heading-stack walker (Part → department → vote → sub-head) carried into each fragment; payee rows become the step-9 AG payee layer. Qwen only for rows the parser cannot type. |
| **A. annual reports** | Marine, Militia, Public Works, Interior, Agriculture (59% tables), Post Office, Inland Revenue, Penitentiaries, Railways & Canals | department letter + sub-reports/letters + statements, house style per series | generalize `build/segment_dia.py`: heading grammar + `SIR,` letters + caption-anchored statements; per-series config, not per-series code |
| **C. returns & correspondence** | 1,060 printed returns to order/address (median 1–7K chars), commission reports (1894_21: 8.5M chars, 2% tables — testimony) | letters, orders in council, petitions, Q&A testimony | letters pipeline (dateline/salutation/signature grammar) → who/whom/where/when; testimony → speaker-turn splitter |

Order of work: §4 spine first (everything cites it), then B on T&N and bank
lists (highest value per hour, pure CPU), then A's generic segmenter on
Marine + Public Works + Interior, then D (AG) which needs both the spine and
the heading walker, then C.
