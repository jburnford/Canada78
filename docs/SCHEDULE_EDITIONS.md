# Schedule of Indian Reserves — six-edition series, 1897–1902

_2026-08-26. Editions 1897–1901 discovered embedded in the ARs (full editions
1900 pdf pp. 780–944 and 1901 pp. 743–911; compact summaries 1897 pp. 570–598,
1898 pp. 639–673, 1899 pp. 718–759), extracted by Qwen3.8-27B-FP8 on plato
(job 5846023, ~8 h on one A100 3g.40gb MIG slice) with the pipeline
benchmarked against Sonnet on the 1902 edition at ~98% field agreement
(docs/MCP_EVAL.md). Division of labor per project practice: Qwen transcribes,
Claude designs schemas, links, and adjudicates._

## Pipeline

1. `eval/make_schedule_chunks.py` — page-aligned chunks per edition (same
   format as the 1902 benchmark chunks).
2. `eval/extract_1902.py` — generic Schedule extractor: `--chunks-dir`,
   `--edition`, `--parallel` (chunk-level concurrency), adaptive page-window
   splitting on truncation, empty-reply = zero-row window, `--remerge` from
   saved raw replies. 12-field row schema identical to the 1902 extraction.
3. `build/link_schedule_editions.py` — links edition rows to the minted 1902
   registry (`reserves.parquet`), **attestations only, no registry edits**.

## Extraction results (raw reserve rows)

| edition | pages | rows | note |
|---|---|---|---|
| 1897 | 29 | 531 | compact: No./Name/Acres/County; counties in `location` |
| 1898 | 35 | 531 | compact |
| 1899 | 42 | 409 | compact; BC section only ~7 pp. that year |
| 1900 | 165 | 1,471 (+152 index rows) | full; pp. 929–944 are the BC index (`bc_index`) |
| 1901 | 169 | 1,352 | full; its BC index (pp. 898–911) yielded no rows |
| 1902 | 195 | 1,422 (curated) | the benchmarked supplement; registry anchor |

## Linking (tiered, within province group; one-to-at-most-one per edition)

T1 no.+band · T2 no.+name (char-level similarity ≥.6 — OCR/hyphenation drift:
"Sic-e-dach"≈"Sik-e-dakh") · T3 unique name · T4 no.+acres · T5 name≥.75+acres
(numbering drift). Compound numbers reduced to their first token
("31H and pt. of 31G" → 31H). Equal-best candidates → `ambiguous`, no guess.

| edition | linked | rate |
|---|---|---|
| 1897 | 417 / 531 | 79% |
| 1898 | 445 / 531 | 84% |
| 1899 | 385 / 409 | 94% |
| 1900 | 1,276 / 1,319 | 97% (index rows excluded) |
| 1901 | 1,329 / 1,352 | 98% |

Eastern provinces carry a **stable provincial numbering across editions**
(1897 No. 15 Richibucto = 1902 No. 15); Ontario numbers per band; treaties
per treaty (letter suffixes); BC per band within agency. Falling match rates
in earlier editions are substantially real: pre-1902 reserves later renamed,
resurveyed, or absent from the 1902 Schedule.

## Outputs

- `registries/annotations/reserve_attestations.parquet` — 5,716 rows: every
  extracted edition row with `reserve_id` (nullable), `match_tier`,
  `match_note`, plus the 1902 registry rows as tier `anchor`. This is the
  reserve **time series**: `groupby(reserve_id)` gives per-reserve
  name/acreage/remarks trajectories 1897→1902.
- `registries/crosswalks/schedule_edition_review.csv` — review queues:
  **282 unmatched** (candidate pre-1902 disappearances or heavy drift; NS/NB
  singles, early-BC, treaty rows with null names), **172 acreage changes**
  >20% between attestations (allotment/survey activity — historically the
  interesting rows — mixed with OCR digit errors, e.g. "10,1000"),
  **3 ambiguous**.

## Adjudication (2026-08-27)

`build/adjudicate_schedule_review.py` → `curation/schedule_edition_adjudications.csv`
(457 rows, each with disposition, confidence, evidence; proposed links are
**proposals for review**, not applied automatically):

| queue | disposition | n | note |
|---|---|---|---|
| unmatched | absence_candidate | 232 | plausible pre-1902 reserves (renamed/absorbed/sold/omitted) — the historian queue |
| unmatched | renumbered_link | 20 | e.g. NS 1897 No. 11 "Medway" → `port-medway-river-14`; eastern numbering was NOT stable |
| unmatched | degenerate_row | 19 | all-null placeholder rows |
| unmatched | duplicate_emission | 7 | window-boundary doubles of matched rows |
| unmatched+ambig | greedy_steal | 5 | exact registry row existed but one-to-one matching gave it away (e.g. Tsimpsean No. 2, 57,742 ac.) |
| ambiguous | renumbered_link | 2 | Anderson Lake No. 3 (1900/01) → `2-anderson-lake` (renumbered by 1902) |
| acres_change | real_change_candidate | 136 | 1.2–9× moves in the allotment/survey era — the signal |
| acres_change | unit_mismatch | 21 | 1902 side is a square-miles division (`schedule_area_units.csv`) |
| acres_change | ocr_digit | 15 | ×10/×100/×1000 ratios — digit errors to verify |

Known caution: 1897 Kent "Indian Island (claimed by Indians)" is proposed to
the Gloucester Indian Island — wrong county; kept low-confidence. Verified
correct in spot-checks: Sea Bird Island per-band rows (Fraser), Wild Cat,
Port Hood, Red Bank part-reserve, St. Regis islands.

## Next

1. Historian review of `absence_candidate` (232) and `real_change_candidate`
   (136); accepted renumbered/greedy links get applied by a rerun of the
   linker consuming the adjudications file.
2. Wiki: "attested in Schedules" row + acreage series on reserve pages from
   the attestations table.
3. Same pattern for the remaining AR table families (Tabular Statements
   5.6M chars; Return A 8.7M; School Statements 26.5M — a schema each, Qwen
   for bulk, V100-fleet or Nibi test for the School Statements).
