# 1902 Schedule of Indian Reserves — LLM extraction (v1)

_2026-08-24. First structured-extraction job (identity-model decision #5):
`dia_reserves_1902.md` (195 pp, pdftotext of the printed Schedule) extracted
by 14 parallel Sonnet agents over page-aligned chunks, merged and validated
by `build/merge_reserves_1902.py`. Raw agent outputs preserved in
`registries/external/reserves_1902_extraction/`._

## Outputs

| file | rows | content |
|---|---|---|
| `registries/entities/reserves_1902.parquet` | 1,437 | main table: province, division (county/agency/treaty), reserve_no, name, location, tribe_band, acres, remarks, page, confidence |
| `…/reserves_1902_bc_reserve_index.parquet` | 879 | BC alphabetical index: reserve name → agency (pp. 43–61) |
| `…/reserves_1902_bc_band_index.parquet` | 104 | BC band → agency index (pp. 41–42) |

1,422 reserve rows + 15 printed totals; 1,388 rows carry numeric acreage
(≈1.93M acres summed). Confidence: 1,305 high / 745 medium / 38 low across
the raw extraction (all flags preserved per row).

## Method notes

- **Chunking**: 14 page-aligned chunks with a 1,500-char context prefix;
  agents told not to extract from context. Three boundary-truncated rows
  were repaired from source (Sarnia #45, Kokyet #1) or dropped as
  unidentifiable partials whose full rows live in the neighbouring chunk.
- **Discovered structure**: pp. 41–42 are a band→agency index and pp. 43–61
  a reserve→agency index (BC only) — extracted separately (the index pages
  were parsed deterministically by code; last-comma split).
- **Validation**: the Schedule's own BC index vs main-table BC names —
  644/849 (76%) exact-normalized matches; residuals are OCR spelling
  variants between the two settings of the same names. No recapitulation
  table with grand totals survived in the OCR, so acreage-sum validation
  against printed totals was not possible at volume level.
- Reserve numbering is **band-relative** in BC/the West (No. 1 restarts per
  band) — confirmed corpus-wide; any keying must be (division, band, no.),
  never no. alone.

## Known limitations for the registry-seeding pass

1. Division labels need normalization ("BABINE AGENCY, BRITISH COLUMBIA."
   vs "COWICHAN AGENCY"; treaty headers vs agency headers).
2. ~745 medium-confidence rows carry preserved OCR artifacts
   ("Similkaneen", "1991" for 1891, comma/period acreage ambiguities —
   flagged per row, worth a cleanup pass keyed on the flags).
3. Eastern sections (NS/NB/PEI/QC/ON) list by county with tribe as
   attribute; the band→reserve relation there needs the AR tabular
   schedules as a second source.
4. Ten name+number collisions across chunk boundaries (mostly "Grave-yard"
   rows and null-name rows under band-relative numbering) — resolve when
   minting reserve URIs with (division, band, no., name) keys.

## Next (per plan)

Mint reserve URIs from these rows (mint-first decision), attach the two BC
indexes as agency crosswalk evidence, then extend extraction beyond 1902 to
later Schedule editions and the ARs' own tabular schedules.
