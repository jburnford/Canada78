# DIA Segmenter v1 — status and known issues

_2026-08-24. `build/segment_dia.py` → 6,588 segments across 51 volumes
(median 4.9K chars ≈ 1.5 printed pages). Offsets index the source markdown
body after frontmatter; text stays in `~/DeptIndianAffairs`._

## Era handling

- **1880–1913 letters era**: per-agency/superintendency letters anchored on
  `SIR,` openings; header walk-back with closing-formula trim so signatures
  stay with their own letter. 4,437 letters found.
- **1914–1930 thematic era**: standalone ALL-CAPS headings; the reports drop
  individual agent letters after 1913 (1914/1915 are transitional — agency
  headers survive as table sections; 1916 still has 25 letters).
- **Tabular tails** (`TABULAR STATEMENT No. N`, `RETURN A (1)`, school
  statements, appendices): caption-anchored; oversized segments subsplit at
  province sub-headers with a 60-page hard cap (the 1898–1913 school
  statements ran 600+ pages).

## Corpus composition (updated after the 2026-08-24 corpus regeneration)

The DeptIndianAffairs extraction was re-run overnight (all 53 files
re-extracted 2026-08-24T02:14–02:17Z):

- The **true 1902 Annual Report was recovered** (`_recovered/` source, 665
  pp) and now occupies `dia_ar_1902`; the **Schedule of Indian Reserves**
  moved to its own file `dia_reserves_1902.md` (195 pp,
  `document_type: reserve_schedule`). The Schedule is the gazetteer source
  for the reserve registry and gets the LLM extraction pass.
- The recovered 1902 AR has noticeably rougher OCR ("AG EXCY" for AGENCY,
  "Sm," for "SIR,") — the SIR-detector misses its letters, so it segments
  in thematic mode (263 usable sections). Improving letters detection for
  this volume is a known follow-up.
- `dia_ar_1922` remains the **Auditor General's report**, not the
  departmental AR — that series gap still stands.
- Regeneration also fixed the 1881–1883 sessional links upstream: the
  auto-linker now produces exactly the paper_ids our curation overrides
  had assigned (1882_6, 1883_5, 1884_4) — independent confirmation,
  including the medium-confidence 1884_4. The overrides in
  `curation/dia_sessional_overrides.csv` are now redundant but kept as the
  documented evidence trail.
- Segment-ID stability under regeneration: 6,518/6,588 ids survived with
  only 1 text_version change among survivors; 6,807 segments after re-run
  (the new 1902 AR contributes 266).

## Known issues for the validation gate (Phase 1 step 4)

1. **Address-block bleed**: when a letter lacks a caps agency header, a few
   opening address lines (recipient, place, date) can stay attached to the
   previous segment. Boundary error is ≤ a few lines; text is never lost.
2. **Heading quality**: letters without agency headers fall back to the
   first block line (sometimes the addressee, e.g. "FRANK PEDLEY, Esq").
   Mention linking will re-derive the agency from the letter body/address.
3. **Province context**: standalone province headers + segment-opening
   province lines; abbreviations in datelines ("ONT.", "N.B.") are not yet
   used, so a letter under a stale header can carry the wrong province.
   Treat `province` as a hint, not ground truth, until entity linking.
4. **head_report detection** rarely fires (4/51) — the Deputy/SG report at
   the front of the body is usually absorbed into preceding fixed segments
   or the first anchor. Harmless for citation, poor for navigation; revisit
   when building document pages.
5. `presentation_letter`/`contents` detection misses some volumes (OCR
   variants of the openings); those chars land in the neighbouring segment.

## Stability

Segment IDs are `p{page}-{heading-slug}` — content-anchored, so boundary
tweaks don't renumber neighbours. IDs freeze at first publication
(`docs/URL_SCHEME.md`); until then, reruns may change ids where headings or
page assignment change.
