# Canada50 — Design Document

_Drafted 2026-08-23. Founding architecture for an integration project that
brings six existing open-data corpora together into a knowledge graph with a
generated wiki as the primary text store. This repo owns the registries, the
merge, the wiki generator, and the retrieval interface; the source repos stay
independent pipelines._

## Governing philosophy

The retrieval substrate is a **generated wiki navigated by lexical search,
links, and graph traversal — not an embedding index**. The full argument is in
`~/encyclopedia2-26/PLAN_GRAPHRAG.md` (Phase 3), which is the template for
this project. The short version:

- Cosine similarity homogenizes: it surfaces the most modern-sounding
  treatment and buries the divergent period material the historian wants.
- Modern embedding models flatten 19th-century vocabulary, spelling, and OCR
  noise; BM25 over the actual tokens is more honest and more debuggable.
- Historical questions are entity-and-time-shaped ("what was happening at
  File Hills Agency in 1896?"). Graph traversal answers them exactly.
- A wiki page cites its sources; an embedding hit is opaque. For scholarship
  provenance is decisive.
- Agents navigate a well-linked wiki the way Claude Code navigates a codebase
  — search, open, follow links — without embeddings.

Embeddings may be added later as one optional recall assist. They are an
optimization, never the foundation.

Everything is open data; everything builds on GitHub. Generation → files →
deploy is strictly one-directional: the whole site is rebuildable from the
source repos' exports at any time. Human curation enters as git-committed
adjudication files that the build consumes, never as hand edits to output.

## Publication constraint — Indigenous-history material (2026-08-24)

The DIA annual reports, reserve and band registries, and everything derived
from them are **not published as a public HTML site** at this stage: no
GitHub Pages wiki pages, no edition slice for DIA volumes. The data and code
live in the GitHub repository for researchers, but a rendered site that the
general public could stumble on waits on **consultation with Indigenous
colleagues**. Site generation for the other corpora is unaffected; any page
that would render DIA/reserve/band content is gated on that consultation.
Phase 1's "publish" step is therefore a GitHub data release only.

## Corpus inventory

| corpus | repo | status | what it contributes |
|---|---|---|---|
| HGIS Canada KG | `~/Canada-History-Knowledge-Graph` | shipped (jimclifford.ca/hgiscanada) | ~13K persistent CSDs, 524 CDs, 1.4M census measurements, ~5,900 DCB persons; Wikidata QIDs + minted URIs; the working prototype of the rag_site pattern |
| Imperial Careers | `~/col_matching` | shipped (Zenodo DOI, CC0) | 46K officials, 300K career events, ~131K events with place QIDs; schools, roles, honours grounded |
| DIA annual reports | `~/DeptIndianAffairs` | OCR'd, unlinked | 51 volumes 1880–1930 as markdown; sessional-paper crosswalk started (`sessional_links.json`) |
| Sessional papers | `~/sessional_papers` | catalog built, text mostly raw | 372 volumes / ~298K pages; volumes→sessions→papers→segments catalog in DuckDB/parquet; 22 cross-session series IDs; enrichment passes 2–4 (NER→grounding→CIDOC) designed, not built |
| UK trade DB | `~/uk_trade_db` | UK side shipped; Canada side WIP | UK imports/exports 1866–1900 with stable commodity/country dimension IDs + aliases; 17 Canadian Trade & Navigation volumes staged in `raw_canada/` (7 year-gaps pending), table parsing not started |
| Encyclopaedia Britannica | `~/encyclopedia2-26` | parsing closeout | 215K articles 1771–1860; the architecture demo; joins Canada50 through shared entity pages (places, persons) rather than through the document spine |

## Architecture: two spines

Every passage of text hangs between two registries. All joins between corpora
go through them; nothing joins to anything directly.

### 1. Entity spine — who / where / what

A registry of canonical entities: **Wikidata QID where grounded, minted URI
otherwise** (continuing the hgiscanada minting policy). Facets:

- **Places**: CSDs/CDs (exists — hgiscanada), reserves, agencies, settlements.
- **Persons**: DCB (exists), Colonial/India Office officials (exists), DIA
  agents (LINCS *Indian Affairs Agents* dataset is the seed).
- **Groups & organizations**: bands, First Nations, departments, railway and
  colonization companies, schools.
- **Commodities & countries**: from `uk_trade_db/build_dimensions.py` output —
  ground these to Wikidata (bounded, mostly mechanical).

Grounding uses the WikidataMCP vector search exclusively (never the REST
search API); the existing disambig skills (`canadian-place-grounding`,
`person-disambig`, etc.) are the machinery.

**Hardest modelling problem — flag early: band ≠ reserve ≠ agency.** The DIA
text conflates them constantly; Wikidata coverage of reserves is patchy, so
expect heavy URI minting. Model them as distinct entity types with explicit
relations (band OCCUPIES reserve; reserve ADMINISTERED_BY agency; agency
boundaries change over time). Borrow the persistent-place-chain approach from
hgiscanada for agencies, whose names and boundaries shift across years.

### 2. Document spine — where it's attested

The sessional papers catalog (`~/sessional_papers/structured/` + parquet
exports) is the document registry: stable `paper_id`s, segment IDs, and
cross-session series IDs (`annual_report:indian_affairs` becomes a series node
with ~50 issue nodes, each with a Canadiana URL). Non-sessional corpora
(Colonial Office List volumes, UK Annual Statements, EB editions) register as
documents in the same scheme.

**Chunk addressing is the load-bearing early decision.** Citations point to
`paper_id / segment / paragraph`; the scheme must survive OCR corrections,
because re-chunking invalidates every annotation. Adopt the sessional-papers
segment scheme as the standard and extend it outward.

Entity mentions in text are **stand-off annotations** (segment ID + char
offsets → QID/URI), never inline markup. CIDOC-CRM framing: documents are
E31, passages E73, attestation via P70; conforms to the LINCS profile so the
graph can eventually federate with the LINCS triplestore.

## The wiki layer

Generated static HTML, one page per hub entity and per document, composed of:

- **Infobox** from the structured graph (census measurements, career events,
  trade series — whatever the entity's corpora contribute).
- **Cited passages** from the text corpora, each carrying source
  (`paper_id`, page, Canadiana URL) — a reserve page accumulates 50 years of
  annual-report passages about it; an agent's page links their LINCS/COL
  career to the reports they signed.
- **Links = graph edges.** Dense linking is the retrieval index.
- **Index pages** as prebuilt facet views (per-series, per-year, per-region,
  per-treaty, new-in-year, etc.).

Deterministic skeleton first (pure code over registry exports); LLM overlays
(abstracts, semantic summaries, mention disambiguation residuals) are
incremental, sample-validated, run on Nibi open models, and always cite the
deterministic layer.

**No wiki software.** No crowdsourced editing is wanted, which removes the
only argument for MediaWiki/Wikibase. Corrections arrive as GitHub PRs via
per-page "suggest a correction" links, get adjudicated into curation files,
and flow through the next build.

## GitHub topology

- **This repo (`Canada50`)**: registries (entities, documents, crosswalks),
  merge/build code, wiki generator, curation files. Small, all committable.
- **Source repos**: unchanged; each grows a hub-keyed JSONL/parquet *export*
  target (the col_matching `graph_stage3/` pattern).
- **Site**: GitHub Pages under the jimclifford.ca custom domain alongside
  hgiscanada and col_matching — e.g. `jimclifford.ca/canada50/` — with stable
  cross-links between the sites. Pagefind for client-side full-text search.
- **Full-text edition**: the complete sessional papers text ships as its own
  Pages site(s), exactly as the EB plan does — measured at 712 MB markdown
  for 235/372 volumes, so ~1.1 GB complete: two Pages repos (~1 GB soft
  limit each), split on a stable boundary (e.g. by session range) with the
  URL scheme fixed up front so citations never break. One page per catalog
  *segment* (not per paper — an 800-page Public Accounts return must
  paginate), each carrying its `paper_id`, Canadiana URL, and per-volume
  OCR-quality flag. This is a major artifact in its own right: no clean,
  segmented, linked reading edition of the Dominion Sessional Papers exists
  anywhere. Publish incrementally as volumes clear OCR and validation; the
  one-directional build makes regeneration cheap.
- **Wiki ↔ edition**: the wiki (entity/document hub pages) is a separate
  small site that quotes passages and deep-links into the full-text edition
  at segment level — the EB nav-site/full-text-site split. Pagefind indexes
  both; the analytical layer (DuckDB FTS, graph DB) stays local for research
  and agent use. Raw parquet/JSONL also published as data (Zenodo, as with
  col_matching).

## Retrieval interface

An MCP server exposing the same API to historians' tooling and to agents:

- `lookup_entity(name, type?)` — registry + alias search, returns URI + page
- `open_page(uri)` — wiki page content
- `search(query)` — BM25 over passages (SQLite/DuckDB FTS)
- `neighbors(uri, edge_type?)` — graph traversal
- `get_document(paper_id)` / `get_segment(segment_id)` — document spine

Nothing more. The one-API-two-consumers thesis from the EB plan: the
historian's search/browse/compare tools and the LLM's tools are the same
endpoints, so the agent can iterate and cite instead of being vector-stuffed.

## Sequencing

**Phase 0 — Crosswalk audit** (cheap, do first). Enumerate which identifiers
each corpus carries and where they already overlap. Concrete outputs:
complete the DIA↔sessional `paper_id` crosswalk; map the 17 T&N volumes to
`paper_id`s; test whether col_matching officials appear as DIA agents; draft
the entity-registry and document-registry schemas from what the audit finds.

**Phase 1 — DIA pilot** (the first customer of the general machinery).
Scope: one region and window — Saskatchewan / Treaty 4 agencies, 1880–1890 —
which also connects to the SaskGraphRAG settlement data.

1. Segment the pilot reports into agency/reserve sections with stable
   segment IDs (the reports' strong internal structure makes this mostly
   deterministic).
2. Seed the entity registry: agencies, reserves, bands (from the reports'
   own tabular sections — which double as the gazetteer), agents (LINCS),
   places (link to HGIS CSDs).
3. Gazetteer-first mention linking, tiered exactly as the EB plan's NER
   design: deterministic matching → rule-based disambiguation → LLM only for
   residuals. Stand-off annotations.
4. Generate pilot wiki pages (agency, reserve/band, agent, report-issue) and
   stand up Pagefind + local FTS + the agent tool set over them.
5. Evaluate: can an agent with only `search`/`open_page`/`neighbors` answer
   real research questions with citations? Fix the annotation scheme while
   it's cheap.

Everything built here is sessional-papers infrastructure with DIA as the
first series; Marine & Fisheries, Interior, etc. follow by turning a crank.

**Phase 2 — Sessional papers at scale.** Finish OCR (165 volumes queued);
run enrichment passes 2–4 against the catalog using the pilot's pipeline;
extend the wiki to more series as grounding coverage grows.

*OCR remediation workstream* (runs alongside Phase 2): rank volumes by
quality (Chandra per-page metadata + validation rollup), then for the bad
tail: (a) **Infinity Parser 2 double-key** — re-run low-quality volumes and
reconcile against the Chandra text, reusing the uk_trade_db cross-engine
consensus pattern (`reconcile.py` A/B/C tiers), generalized from table cells
to running text; (b) **alternative scans** — where the Canadiana images
themselves are the problem, source better digitizations (HathiTrust and
Internet Archive both hold sessional papers runs; LAC as a further option)
and register them as additional witnesses of the same catalog documents.

*Design consequence — text versioning under stable segment IDs.* Re-OCR
changes text, and stand-off annotations pin to char offsets. So: segment IDs
are permanent, but each segment's text carries a version hash; annotations
bind to (segment ID, text-version hash) and are migrated (or flagged for
re-linking) when a better text lands. Citations in the wiki cite the segment
and always render the current best text; the edition page notes which
witness/engine produced it.

**Phase 3 — Canadian trade extraction.** Parse the T&N tables **into
uk_trade_db's existing dimensions** (plus a `reporting_side` axis) so
bilateral triangulation — UK-recorded imports from Canada vs Canada-recorded
exports, same commodity/year — comes free as both a validation instrument
and a finding-generator. Ghost Acres anchors both sides. Commodity/country
pages join the wiki.

**Phase 4 — Full merge.** EB entity pages, DCB persons, Imperial Careers,
census measurements, trade series, and departmental text all composing onto
shared hub pages; MCP retrieval server hardened; site deployed.

## Open questions

1. **Reserve/band/agency identity model** — the hardest one; needs a worked
   design against real 1880s DIA text before Phase 1 step 2. (§Entity spine.)
2. **Minted-URI namespace**: extend `jimclifford.ca/hgiscanada/...` or start
   a `canada50` namespace? (Leaning: one shared namespace for places, since
   hgiscanada URIs are already published; new facets get new paths.)
3. **Property graph vs RDF as the working store**: the EB plan's answer
   (plain property graph, CIDOC/LINCS as an export not a starting schema) is
   probably right here too, but hgiscanada is already CIDOC-native — decide
   whether Canada50's merge layer is CRM-shaped or exports to it.
4. **Repo split boundary and URL scheme for the full-text edition** — fix
   before publishing volume one, since citations depend on it. (Full text
   *does* ship publicly — settled 2026-08-23; see GitHub topology.)
5. **Treaty entities**: treaties are natural hub entities (Treaty 4 connects
   bands, reserves, agencies, dates, places) — worth adding as a facet in the
   pilot?
