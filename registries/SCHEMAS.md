# Registry Schemas

Two registries are the project's spines (see `../DESIGN.md`). Every join
between corpora goes through them; nothing joins to anything directly.
Registries are stored as parquet (analysis) built by `build/` scripts from
the source corpora plus `curation/` overrides; the build is one-directional
and rerunnable.

## Entity registry — `registries/entities/`

One row per canonical entity. **Wikidata QID where grounded, minted URI
otherwise** (continuing the hgiscanada minting policy; grounding via
WikidataMCP vector search only, never the REST search API).

Common columns (per-facet tables may extend):

| column | meaning |
|---|---|
| `entity_id` | stable Canada50 key (facet-prefixed, e.g. `PLACE_ON103002`) |
| `uri` | canonical URI: `http://www.wikidata.org/entity/Qxxx` or minted |
| `uri_source` | `wikidata` \| `minted` |
| `wikidata_qid`, `wikidata_label` | when grounded |
| `grounding_status` | `matched` \| `minted` \| `pending` |
| `source` | corpus that contributed the entity (e.g. `hgiscanada`) |

Facet tables (populated in phases): `places` (seeded from hgiscanada
`e53_place_uri.csv`), `persons`, `agencies`, `reserves`, `bands`,
`organizations`, `treaties`, `commodities`, `countries`.

Identity-model note (Phase 1): band ≠ reserve ≠ agency are distinct facets
with explicit relations (band OCCUPIES reserve; reserve ADMINISTERED_BY
agency); agencies get persistent chains across renames/repartitions,
borrowing the hgiscanada persistent-place-chain approach.

## Document registry — `registries/documents/`

One row per document, keyed to the sessional-papers catalog wherever the
document is a sessional paper. `documents.parquet`:

| column | meaning |
|---|---|
| `doc_id` | catalog `paper_id` (`{session_year}_{paper_num}`, e.g. `1888_15`) or provisional `prov:{tag}` |
| `id_status` | `catalog` (from the catalog itself) \| `catalog_ref` (corpus doc resolved to a paper_id) \| `provisional` (awaiting catalog coverage — reconciled, never renumbered, when the catalog extends) |
| `doc_kind` | `sessional_paper` (more kinds as corpora register: `col_edition`, `uk_annual_statement`, `eb_edition`, …) |
| `corpus` | contributing corpus (`sessional_papers`, `dia_annual_reports`, …) |
| `seq`, `session_year`, `paper_num` | parliamentary session + paper number |
| `series_id` | cross-session series (e.g. `annual_report:indian_affairs`) |
| `title`, `disposition`, `presented`, `mover` | from the session index |
| `md_path`, `page_start`, `page_end` | text location where extracted |

## Segment addressing & text versioning

Adopted from DESIGN.md; applies to all text corpora:

- Address: `doc_id / segment_id / paragraph`. **Segment IDs are permanent.**
- Each segment's text carries a `text_version` hash. Stand-off annotations
  (mention links, etc.) bind to `(segment_id, text_version)` and are
  migrated or flagged for re-linking when better text lands (re-OCR,
  double-key reconciliation, alternative witness).
- Citations cite the segment; pages render the current best text and name
  the witness/engine that produced it.

## Crosswalks — `registries/crosswalks/`

- `dia_sessional.csv` — DIA volume tag → session/paper_num/paper_id +
  `link_status` (`linked_*` auto, `curated_*` from
  `curation/dia_sessional_overrides.csv`, `pending_catalog_post1900` until
  the catalog covers 1901–1931 sessions).
- `tn_sessional.csv` — uk_trade_db Trade & Navigation volume → paper_id.

## Curation — `curation/`

Human adjudications the build consumes (CSV, one file per decision type,
with `confidence` and `evidence` columns). Never edit generated outputs;
corrections flow through curation files and a rebuild.
