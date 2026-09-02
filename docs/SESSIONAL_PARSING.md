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
