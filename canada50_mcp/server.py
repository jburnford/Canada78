"""canada50-mcp — MCP server exposing the Canada50 retrieval tools (stdio).

    canada50-mcp                      # after `pip install -e .` (or uvx from the repo)
    python3 -m canada50_mcp.server

Environment: CANADA50_SITE (wiki dir, default <repo>/site) and CANADA50_DB
(SQLite index, default <site>/_data/canada50.sqlite). Build both with
`python3 build/gen_wiki.py && python3 -m canada50_mcp.index`.

Claude Desktop / Claude Code config:
  {"mcpServers": {"canada50": {"command": "canada50-mcp"}}}
"""
from __future__ import annotations

import json
from typing import Optional

from .store import Store

INSTRUCTIONS = """Canada50: Department of Indian Affairs annual reports, 1880–1930, linked to a
registry of agencies (with OCR-variant chains merged), reserves (Schedule of 1902,
with modern Wikidata succession), historical bands (1902 Schedule + the annual census
tables 1881–1929), schools (School Statements 1896–1930) and persons (LINCS Indian
Affairs agents, Return A officers, teachers, letter signatories).
Workflow: lookup_entity (find the entity; tolerant of OCR spellings) → neighbors (its
relations with the years each is attested; pass year= for a snapshot) / open_page (hub
page with cited passages; entities without a page get a generated hub) / series (annual
statistics) → get_segment (read the full report text at a cited address); or search
(BM25 over segment text, filter by agency/school/year/kind) → get_segment. For a
question about one report year, get_document(year) lists that report's sections
(regional and thematic) — open the relevant one before concluding. Every answer
should cite an address `doc_id / segment_id` and printed page ([p. N] markers).
Mentions marked unverified/low are machine guesses — read the text before relying on
them. Historical bands are distinct from modern First Nations (SUCCEEDED_BY, never
same-as). Local evaluation build: not a public site."""


def _dump(x):
    return json.dumps(x, ensure_ascii=False, default=str, indent=1)


def build_server():
    try:                                   # mcp >= 2.0
        from mcp.server.mcpserver import MCPServer as FastMCP
    except ImportError:                    # mcp 1.x
        from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("canada50", instructions=INSTRUCTIONS)
    store = Store()

    @mcp.tool()
    def lookup_entity(name: str, type: Optional[str] = None, limit: int = 10) -> str:
        """Find registry entities by name or alias (as printed in the reports,
        OCR variants tolerated). type: agency | reserve | band | person | school.
        Returns ranked candidates with id, uri, page url, attested years, mention
        count, and a one-line summary. Prefer the best-attested candidate when two
        share a name."""
        return _dump(store.lookup_entity(name, type, limit))

    @mcp.tool()
    def open_page(ref: str) -> str:
        """Read a wiki page as text: an entity's hub page (infobox, relations,
        cited passages with addresses), a report's contents page, or a segment
        page. Entities without a generated page (census bands, agstat units) get a
        text hub built from the index. ref = canonical URI, page url
        (/agencies/x/), typed id (agency:AG-x, band:band_c…, school:SCH-…) or name."""
        return _dump(store.open_page(ref))

    @mcp.tool()
    def search(query: str, year_from: Optional[int] = None, year_to: Optional[int] = None,
               kind: Optional[str] = None, agency: Optional[str] = None,
               school: Optional[str] = None, limit: int = 10) -> str:
        """BM25 full-text search over report segments. FTS5 syntax: "exact phrase",
        AND / OR / NOT, prefix*. Filters: report year range; kind (agency_letter,
        tabular_statement, thematic_section, return, appendix, presentation_letter);
        agency (id or name) — the letters attributed to that agency; school (id or
        name) — principals' reports on that school. Returns address, heading, year,
        url, snippet."""
        return _dump(store.search(query, year_from, year_to, kind, agency, school, limit))

    @mcp.tool()
    def neighbors(ref: str, edge_type: Optional[str] = None, year: Optional[int] = None,
                  limit: int = 50) -> str:
        """Graph relations of an entity, one row per related entity with the years
        it is attested (e.g. '1894–1910'): ADMINISTERED_BY (band/reserve→agency, per
        census year / Schedule edition), OCCUPIES (band→reserve, 1902), IN_AGENCY /
        ON_RESERVE (school), TAUGHT_AT (person→school), POSTED_TO (person→agency,
        LINCS), SIGNED_FOR / REPORTED_ON (person→agency/school by letter signature),
        MERGED_INTO / SPLIT_INTO / RENAMED (agency chains, curated), SUCCEEDED_BY
        (band/reserve→modern Wikidata item). year= keeps only relations attested in
        that year (undated ones always pass): "what was X in 1896". Plus
        MENTIONED_IN: mention counts per report year and top segments."""
        return _dump(store.neighbors(ref, edge_type, year, limit))

    @mcp.tool()
    def get_document(doc_id: str) -> str:
        """A report issue's metadata and table of contents (segments with
        headings, pages, agency, mention counts). doc_id = sessional paper id
        (1886_4), provisional id (prov:dia_ar_1925), or report year (1885)."""
        return _dump(store.get_document(doc_id))

    @mcp.tool()
    def get_segment(doc_id: str, segment_id: str, with_mentions: bool = True) -> str:
        """Full text of one segment (page markers as [p. N]) with the entities
        linked in it. doc_id may be the report year."""
        return _dump(store.get_segment(doc_id, segment_id, with_mentions))

    @mcp.tool()
    def series(ref: str, series_id: Optional[str] = None, limit: int = 200) -> str:
        """Annual statistical series for a band, agency or school, from the
        DIA tables (census, agricultural statistics, school statements, trust
        funds). Without series_id: list what exists for the entity. With one
        (e.g. 'population', 'roll_total', 'trustfund_capital'): the
        year-by-year values as printed, with paper and page."""
        return _dump(store.series(ref, series_id, limit))

    return mcp


def main():
    build_server().run()


if __name__ == "__main__":
    main()
