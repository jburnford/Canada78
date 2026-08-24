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

## Corpus impostors (frontmatter `document_type`)

- `dia_ar_1902` is the **Schedule of Indian Reserves**, not an annual
  report — segmented in thematic mode. It is the best single gazetteer
  source for the reserve entity registry (Phase 1 step 2): every reserve
  with agency, location, acreage.
- `dia_ar_1922` is the **Auditor General's report** on Indian Affairs
  expenditure, not the departmental AR. Its 23 "letters" are audit
  correspondence.
- Consequence: the annual-report series has gaps at report years 1902 and
  1922 — worth sourcing the true ARs for those years eventually.

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
