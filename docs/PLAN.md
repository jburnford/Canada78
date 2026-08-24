# Canada50 — Project Plan

## Context

Canada50 integrates six open-data corpora (HGIS census LOD, Imperial Careers,
DIA annual reports, Dominion Sessional Papers, UK/Canada trade statistics,
Encyclopaedia Britannica) into a knowledge graph whose **primary text store is
a generated static wiki on GitHub Pages** — navigation, lexical search, and
graph traversal instead of embeddings. Architecture is settled in
`/home/jic823/Canada50/DESIGN.md` (two spines: entity registry + document
registry; template: `~/encyclopedia2-26/PLAN_GRAPHRAG.md` Phase 3).

This plan turns the design into ordered work. Ordering principle: **start
where the data is clean and ungated** (LOD is done; DIA is clean pdftotext
text with sessional metadata already in frontmatter), prove the approach on
the full DIA corpus, then scale up as sessional-papers OCR completes
(235/372 volumes done, 137 queued), then trade data, then further layers.

Division of labour per the global workflow: Claude builds code locally in
WSL; all cluster jobs (OCR, Infinity Parser 2, batch LLM passes) are
submitted by the user on Nibi and results reported back.

## Decisions taken (user-confirmed)

- **Pilot scope: the whole DIA corpus** — all 51 volumes, 1880–1930, in the
  first pass. Mitigation for scale risk: a validation gate mid-phase (see
  Phase 1 step 4) before corpus-wide mention linking runs.
- **Access model: tiered, hosted later.**
  - Tier 0 — the static site itself: any agent with web fetch navigates the
    wiki/edition directly; publish machine-readable indexes (JSON sitemaps,
    llms.txt) to help.
  - Tier 1 — `canada50-mcp`, a pip/uvx-installable MCP server living in the
    Canada50 repo. It queries **published static artifacts over HTTP**:
    DuckDB httpfs range-requests against parquet on GitHub Pages/releases,
    plus a downloadable graph DB file (Ladybug/Kuzu, the col_matching
    pattern) cached locally on first use. No cloud server, no repo clone.
  - Tier 2 (deferred) — hosted Streamable-HTTP MCP endpoint on the existing
    Arbutus VM (206.12.90.118, already runs Neo4j), added in Phase 5 once
    the tool API has stabilized. Neo4j is never required for public access;
    it stays a research-side backend.

## Phase 0 — Foundations (ungated, first)

Scaffold the `Canada50` repo and settle the load-bearing early decisions.

1. Repo scaffold: `registries/` (entities, documents, crosswalks),
   `curation/`, `build/`, `mcp/`, `site/` output dirs; README pointing at
   DESIGN.md; init git, publish to GitHub.
2. **Registry schemas** (entity + document), drawing on:
   - hgiscanada URI minting + Wikidata grounding
     (`~/Canada-History-Knowledge-Graph/`, `e53_place_uri.csv` pattern)
   - sessional catalog IDs (`~/sessional_papers/export/*.parquet`:
     volumes/sessions/papers/segments, series ids)
3. **Crosswalk audit** (read-only over the corpora): which identifiers each
   corpus carries; complete the DIA↔sessional `paper_id` crosswalk
   (frontmatter already carries sessional paper number/session/volume —
   `~/DeptIndianAffairs/structured/sessional_links.json` is the seed);
   map the 17 T&N volumes to paper_ids; test overlap between col_matching
   officials and DIA agents / LINCS Indian Affairs Agents.
4. **Fix URL scheme + repo split** for the future full-text edition (by
   session range; two Pages repos budgeted at ~1.1 GB total) — decided now
   because citations depend on URLs never moving.
5. **Segment addressing + text-versioning spec**: segment IDs permanent;
   text carries a version hash; stand-off annotations bind to
   (segment ID, text-version); adopted from DESIGN.md.

Deliverable: registries populated with places (HGIS export) and documents
(sessional catalog + DIA crosswalk); schemas documented.

## Phase 1 — DIA corpus (ungated; the approach test)

All 51 volumes, 1880–1930. Reports are clean text with agency sections as
clear headers (`COWICHAN AGENCY.`, per-agency letters "SIR, — I have the
honor…"), plus tabular schedules of reserves/bands.

1. **Segmenter**: deterministic split of each volume into front matter,
   Superintendent-General's report, per-agency/per-superintendency letters,
   and tabular schedules, with stable segment IDs. Expect format drift
   across 50 years — handle era-by-era like the sessional `11_segment.py`
   does.
2. **Entity registry seeding**: agencies, reserves, bands extracted from the
   reports' own tabular schedules (the gazetteer), linked to: HGIS
   CSDs/places, LINCS Indian Affairs Agents (persons), Wikidata via
   WikidataMCP vector search (never REST). Treaties added as entities.
3. **Identity model workshop**: band ≠ reserve ≠ agency modelled explicitly
   (band OCCUPIES reserve, reserve ADMINISTERED_BY agency; agencies get
   persistent chains across renames/repartitions, borrowing the hgiscanada
   persistent-place-chain approach). Worked against real text from several
   decades before linking runs.
4. **VALIDATION GATE**: hand-verify segmentation + registry + a sample of
   mention links on a stratified sample (e.g. 3 volumes × early/middle/late,
   2 regions) before running linking corpus-wide. This is the whole-corpus
   scope's safety valve.
5. **Mention linking, tiered** (EB NER design): Tier 0 deterministic
   gazetteer matching → Tier 1 rule-based disambiguation → Tier 2 LLM for
   residuals only (batch on Nibi, user-submitted). Stand-off annotations.
6. **Wiki generation v1**: pages for agency, reserve/band, agent,
   report-issue, treaty; infoboxes from LOD (HGIS measurements, LINCS/COL
   careers), cited passages with paper_id + Canadiana URL; dense links.
   Reuse the `generate_rag_pages.py` machinery/pattern from hgiscanada.
7. **Publish**: DIA full text as the first slice of the edition site
   (116 MB — fits one repo comfortably); wiki site alongside; Pagefind on
   both.

Deliverable: jimclifford.ca/canada50 (wiki) + edition slice, fully cited.

## Phase 2 — Retrieval/access layer (ungated, overlaps Phase 1 late stage)

1. **`canada50-mcp` (Tier 1)**: `lookup_entity`, `open_page`, `search`
   (FTS), `neighbors`, `get_document`/`get_segment` — implemented against
   the published parquet/graph artifacts over HTTP; pip/uvx-installable
   from the repo.
2. **Agent evaluation harness**: a set of real research questions (with
   known answers from the sources); test whether an agent restricted to the
   MCP tools answers with correct citations. This evaluates the whole
   thesis — fix the annotation scheme and page design while it's cheap.
3. Machine-readable site indexes (Tier 0 support).

## Parallel track — OCR completion + remediation (cluster; user-run)

Runs alongside Phases 0–2; not blocking them.

- **Finish the sessional run**: 137 volumes still queued
  (`~/sessional_papers/05_submit.sh` resubmit loop).
- **Quality ranking now** on the 235 completed volumes (Chandra per-page
  metadata + `structured/validation/rollup.tsv`).
- **Infinity Parser 2 double-key** on the bad tail: reuse the uk_trade_db
  cross-engine pattern (`parse_infinity.py`, `reconcile.py` A/B/C tiers)
  generalized to running text. Claude writes the SLURM + reconcile scripts;
  user runs on Nibi.
- **Alternative scans**: for volumes where the Canadiana images are the
  problem, locate HathiTrust / Internet Archive / LAC copies and register
  them as additional witnesses of the same catalog documents.
- Text-version hashes (Phase 0 spec) mean re-OCR never breaks citations.

## Phase 3 — Sessional papers at scale (GATED on OCR track)

Start when OCR coverage + quality ranking are in hand.

1. Full-text edition sites (URL scheme from Phase 0), per-segment pages,
   per-volume quality flags, incremental publication as volumes clear.
2. Enrichment passes 2–4 (LLM entity extraction → Wikidata grounding →
   CIDOC/LINCS export) built as generic series machinery — DIA (Phase 1)
   already proved the loop; next customers: `annual_report:marine_and
   _fisheries`, Interior, etc.
3. Wiki grows: department pages, series pages, more entity coverage.

## Phase 4 — Canadian trade data (GATED on T&N gap volumes arriving)

1. Parse the T&N tables (`~/uk_trade_db/raw_canada/`, 17+ volumes) **into
   uk_trade_db's existing dimension IDs** plus a `reporting_side` axis.
2. Bilateral triangulation: UK-recorded imports from Canada vs
   Canada-recorded exports (validation instrument + finding-generator;
   Ghost Acres anchors both sides).
3. Commodity/country entity pages join the wiki with time-series infoboxes.

## Phase 5 — Full merge + hosted access

1. Remaining corpora compose onto hub pages (EB entity pages, DCB, Imperial
   Careers already keyed).
2. **Tier 2 hosted MCP** on the Arbutus VM (Streamable HTTP, FastMCP,
   mirroring the WikidataMCP pattern), exposing the by-then-stable tool API
   for zero-install users.
3. Data publication: registries + annotations + graph exports to Zenodo
   with DOI (col_matching pattern).

## Key existing assets to reuse

- `~/Canada-History-Knowledge-Graph/scripts/generate_rag_pages.py` — page
  generation pattern; `join_wikidata_to_places.py` — URI minting/grounding.
- `~/sessional_papers/10_parse_index.py`–`15_enrich_rules.py` — catalog +
  segmentation + series machinery; `export/*.parquet` — document registry.
- `~/uk_trade_db/scripts` (`parse_infinity.py`, `reconcile.py`,
  `build_dimensions.py`) — double-key OCR + dimensions.
- `~/col_matching/data/kg/graph_stage3/` — hub-keyed JSONL export pattern;
  Ladybug loading docs.
- Disambig skills + WikidataMCP for all grounding.
- `~/DeptIndianAffairs/` frontmatter + `sessional_links.json` — DIA
  document crosswalk seed.

## Verification

- Phase 0: crosswalk audit report checked by user; registry schemas load
  round-trip (JSONL → DuckDB → JSONL) without loss.
- Phase 1: validation-gate sample review (user + Claude); post-linking
  precision spot-checks per tier; wiki builds reproducibly from a clean
  clone; Pagefind returns known passages.
- Phase 2: agent evaluation harness — research questions answered with
  correct citations using only the MCP tools; failures triaged into page/
  link/schema fixes.
- OCR track: reconciliation scorecards (A/B/C tier counts) per remediated
  volume, as in uk_trade_db `reports/`.
- Phases 3–5: same gates applied per series/corpus as they land.

## First concrete work session (on approval)

Phase 0 items 1–3: scaffold the repo, draft the two registry schemas, run
the crosswalk audit, and produce the audit report — all local, read-only
over the source corpora, no cluster dependency.
