# canada50-mcp and the Phase 2 evaluation

_2026-08-25. Phase 2 (docs/PLAN.md): the retrieval/access layer over the
Phase 1 artifacts, and the harness that tests the project's thesis — that an
agent restricted to wiki navigation + lexical search answers real research
questions with correct citations, with no embeddings._

## Build

```
python3 build/gen_wiki.py            # site/            (9,263 pages, ~250 MB)
python3 -m canada50_mcp.index        # site/_data/canada50.sqlite (FTS5, ~190 MB, 8 s)
pip install -e .                     # `canada50` CLI;  `pip install -e ".[mcp]"` adds the MCP server
```

Both outputs are build artifacts under gitignored `site/` (local-only, per the
publication constraint). `CANADA50_SITE` / `CANADA50_DB` point the tools at a
different location (e.g. published parquet/sqlite in Phase 5).

## Tools (DESIGN.md "Retrieval interface")

| tool | does | backed by |
|---|---|---|
| `lookup_entity(name, type?)` | alias search: exact → prefix → substring → token overlap; ranked with mention counts | `aliases` (registry names, heading variants, Wikidata labels, and every high/medium mention surface) |
| `open_page(ref)` | wiki page as text, links kept as `[label](url)` | `site/**/index.html` |
| `search(query, year_from?, year_to?, kind?, agency?)` | FTS5 BM25 over segment text with snippets and addresses | `segments_fts` |
| `neighbors(ref, edge_type?)` | OCCUPIES, ADMINISTERED_BY, MERGED_INTO/SPLIT_INTO, SIGNED_FOR, SUCCEEDED_BY (+ totals, truncation note) and MENTIONED_IN by year / top segments | `edges`, `mentions` |
| `get_document(doc_id|year)` | issue metadata + TOC with per-segment mention counts | `documents`, `segments` |
| `get_segment(doc_id, segment_id)` | full text with `[p. N]` markers + linked entities (unverified flagged) | `segments`, `mentions` |

References accepted everywhere: canonical URI, page URL, or `type:id`.
Same functions serve the MCP server (`canada50-mcp`, stdio; `MCPServer` on
`mcp` ≥ 2.0, `FastMCP` on 1.x — verified 2026-08-25 with an `mcp` 2.1.0
stdio client round-trip), the CLI (`canada50 lookup|page|search|neighbors|doc|segment`),
and the eval harness (in-process — no transport).

Claude Code / Claude Desktop registration (from the repo directory, before
`pip install -e .`):

```json
{"mcpServers": {"canada50": {"command": "python3",
                             "args": ["-m", "canada50_mcp.server"],
                             "cwd": "/home/jic823/Canada50"}}}
```

## Evaluation harness

`eval/questions.jsonl` — 12 questions whose answers were verified against
the data while writing them (agent signatures, Schedule rows, curated chain
events, passages found by search). Each carries `expect` groups (any-of
alternatives, all groups required) and a `cite` regex the answer's citation
must match. `eval/run_eval.py` runs each question through the Anthropic SDK
tool runner with the six tools, records the full tool trace, and scores
`facts` / `cited` / `pass`; results append to `eval/results/*.jsonl`.

```
python3 eval/run_eval.py --dry                    # list questions
python3 eval/run_eval.py                          # claude-opus-5, effort high
python3 eval/run_eval.py --ids q03,q12 --effort medium
```

Needs `ANTHROPIC_API_KEY` (or an `ant auth login` profile). **Not yet run**
— no credentials on the build machine as of 2026-08-25.

What the questions probe: signature → person linking (q01, q04, q09),
band/reserve/agency graph (q02, q06, q11, q12), curated chain events (q03),
passage retrieval by search (q05, q07, q10), and the identity model (q08).
q12 deliberately exceeds the default `neighbors` limit so the agent must use
`in_total` or the page rather than count a truncated list.

## Findings so far (from building the harness)

- **Schedule area units**: the 1902 Schedule's prairie-treaty sections print
  "Area. Acres." over figures that are square miles (Blood No. 148 = 546.76).
  Recorded in `curation/schedule_area_units.csv`; the wiki and index now
  label those areas "sq. miles" and give the acre equivalent.
- `neighbors` needed totals: the Fraser Agency administers 188 reserves; a
  50-row page of edges silently read as "50".
- LINCS surname matching merges signature surfaces (A. Mackay / J.W. Mackay);
  exposed on person pages and in `lookup_entity` results.
- Treaty-section bands (Blackfoot, Blood, Sarcee…) have no band entity —
  the band facet was minted from agency sections of the Schedule only.
  Registry gap for the next extraction pass.
