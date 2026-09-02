# canada50-mcp and the retrieval evaluation

_2026-08-25, revised 2026-09-02. Phase 2 (docs/PLAN.md): the retrieval/access
layer over the KG-build artifacts, and the harness that tests the project's
thesis — that an agent restricted to wiki navigation, graph traversal and
lexical search answers real research questions with correct citations, with
no embeddings._

## Build

```
python3 build/classify_agstat_identities.py   # identity_kind on agstat units
python3 build/alias_census_bands.py           # band_canonical.csv
python3 build/alias_agency_chains.py          # agency_chain_canonical.csv
python3 build/attribute_segments.py           # segment_attribution.parquet
git clean -fdX site/ && python3 build/gen_wiki.py   # site/  (~18,000 pages, ~5 min)
python3 -m canada50_mcp.index                 # site/_data/canada50.sqlite (~260 MB, 20 s)
pip install -e .                              # `canada50` CLI;  `pip install -e ".[mcp]"` adds the MCP server
```

Both outputs are build artifacts under gitignored `site/` (local-only, per the
publication constraint). `CANADA50_SITE` / `CANADA50_DB` point the tools at a
different location.

## What the index holds (v2)

| table | rows | content |
|---|---|---|
| `entities` | 12,408 | 600 agencies (chains + agstat-only units), 3,330 bands (1902 Schedule + census tables), 6,150 persons (every LINCS agent, Return A officers, teachers, signatories), 1,422 reserves (with Wikidata QIDs), 1,115 schools; `kind`, attested span, one-line summary |
| `redirects` | 912 | superseded id → canonical: minted persons re-anchored to LINCS, census bands merged by `alias_census_bands`, census bands bridged to 1902 bands, OCR-variant agency chains, agstat units linked to chains or folded with a same-key duplicate ("Halifax" / "Halifax County") |
| `aliases` (+ trigram FTS) | 35,646 | registry names, heading variants, names as printed in tables, Wikidata labels, mention surfaces |
| `edges` | 45,198 | dated where the attestation is: band/reserve ADMINISTERED_BY agency, school IN_AGENCY / ON_RESERVE, person TAUGHT_AT / POSTED_TO / SIGNED_FOR / REPORTED_ON, band OCCUPIES reserve (1902), agency MERGED_INTO / SPLIT_INTO / RENAMED (curated), band/reserve SUCCEEDED_BY Wikidata item |
| `segments` (+ FTS5) | 6,807 | full text with `[p. N]` markers; `agency_id` / `school_id` / `attribution` method from `segment_attribution.parquet` (2,688 segments carry an agency, 626 a school; `letter_kind` classifies the rest) |
| `mentions` | 129,933 | stand-off mentions, ids canonicalised; common-noun surfaces on contents pages demoted to low |
| `observations` / `obs_map` | 389,446 / 319 | the annual series; obs_map reaches the observations of every id that redirects to an entity |

## Tools (DESIGN.md "Retrieval interface")

| tool | does | backed by |
|---|---|---|
| `lookup_entity(name, type?)` | alias search: exact → prefix → substring → token overlap → trigram (OCR variants); ranked with mention counts, then attested years | `aliases`, `aliases_fts` |
| `open_page(ref)` | wiki page as text, links kept as `[label](url)`; entities without a page get a generated hub (summary, names, relations with years, series, mentions) | `site/**/index.html`, index |
| `search(query, year_from?, year_to?, kind?, agency?, school?)` | FTS5 BM25 over segment text with snippets and addresses; agency/school by id or name | `segments_fts` |
| `neighbors(ref, edge_type?, year?)` | one row per related entity with the years it is attested ("1894–1910"); `year=` keeps only relations attested then (undated pass); plus MENTIONED_IN by year / top segments | `edges`, `mentions` |
| `series(ref, series_id?)` | series list, or year-value points with paper, page and the citable segment address | `observations`, `obs_map`, `segments` |
| `get_document(doc_id\|year)` | issue metadata + TOC with agency/school attribution and mention counts | `documents`, `segments` |
| `get_segment(doc_id, segment_id)` | full text with `[p. N]` markers + linked entities (unverified flagged) | `segments`, `mentions` |

References accepted everywhere: canonical URI, page URL, `type:id`, a bare
id (`band_c02877`, `SCH-00421`), or a name. The same functions serve the MCP
server (`canada50-mcp`, stdio), the CLI (`canada50 lookup|page|search|neighbors|doc|segment|series`)
and the eval harness.

Claude Code / Claude Desktop registration:

```json
{"mcpServers": {"canada50": {"command": "python3",
                             "args": ["-m", "canada50_mcp.server"],
                             "cwd": "/home/jic823/Canada50"}}}
```

## Evaluation harness

`eval/questions.jsonl` — 18 questions whose answers were verified against the
data while writing them. q01–q12 (2026-08-25) probe signature → person linking,
the band/reserve/agency graph, curated chain events and passage retrieval;
**q13–q18 (2026-09-02) need the v2 index**: census series with page
citations (Piapot, Six Nations, File Hills school roll), time-scoped
ADMINISTERED_BY (Piapot 1893 vs 1910), teacher ↔ school edges (Cape Croker),
LINCS postings + signatures (W.M. Graham). Each carries `expect` groups
(any-of alternatives, all groups required) and a `cite` regex.

Two runners:

- `eval/run_eval_headless.py` — **the one that runs here.** Each question is
  one `claude -p` with the canada50 server as the only MCP server
  (`--strict-mcp-config`, `eval/mcp/canada50.json`), only its tools allowed,
  and the same system prompt; tool traces come from `stream-json`. No
  Anthropic SDK or API key (standing decision). Results append to
  `eval/results/headless_<model>.jsonl`.

  ```
  python3 eval/run_eval_headless.py --dry
  python3 eval/run_eval_headless.py --ids q13,q14 --model sonnet
  python3 eval/run_eval_headless.py --model opus
  ```
- `eval/run_eval.py` — the original SDK tool-runner version, kept for a
  machine that has credentials; same scoring.

Scoring: `facts` = fraction of expect groups matched (case-insensitive
substring), `cited` = cite regex hit, `pass` = both. Per-question cost with
sonnet is ~$0.15–0.40 (the tool schemas and system prompt are cached).

## Results

| run | model | pass | mean facts | cited | cost |
|---|---|---|---|---|---|
| 2026-09-02 `headless_sonnet_v2.jsonl` (v2 index, q01–q18) | sonnet | **17/18** | 0.98 | 17/18 | $4.30 |

The one miss, q07 (1929 northern-Ontario health), was retrieval strategy, not
data: the agent found the department-wide "Indian Health Supervision"
section and stopped, never opening the report's "Ontario and Quebec" section
that `search("influenza ontario", 1929)` or `get_document(1929)` reveals.
The system prompt and server instructions now say to list a report year's
sections for a year-specific question. q13–q18 (series, dated edges,
teachers, LINCS postings) all passed on the first run — the agent used
`lookup_entity → series → get_segment` and `neighbors(year=…)` directly.

## Findings so far

- **Schedule area units**: the 1902 Schedule's prairie-treaty sections print
  "Area. Acres." over figures that are square miles (Blood No. 148 = 546.76).
  Recorded in `curation/schedule_area_units.csv`.
- `neighbors` needed totals and year ranges: the Fraser Agency administers
  188 reserves, and a band's agency changes over time (Piapot: Muscowpetung
  1886–1899, Qu'Appelle 1901–1929).
- Agents spend turns hunting for a citable segment after `series` gives them
  a page; the points now carry the segment address. The 1900 census table
  sits inside a segment captioned "School Statement" — a segmenter caption
  miss, not a store fault.
- Common-noun reserve names ("fishing station", "hay meadow", "the key")
  link correctly inside the agency's own letter via the agency-context rule
  and wrongly on contents pages; 80 high/medium mentions over 19 reserves
  corpus-wide, now demoted where the segment is a contents/title page.
- Treaty-section bands (Blackfoot, Blood, Sarcee…) have no 1902-Schedule
  band entity but do have census-band pages now (`bands/census/`).
