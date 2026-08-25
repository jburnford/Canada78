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
registry of agencies, reserves (Schedule of 1902), historical bands, and officers.
Workflow: lookup_entity (find the entity) → neighbors / open_page (its relations and
hub page with cited passages) → get_segment (read the full report text at a cited
address) ; or search (BM25 over segment text) → get_segment. Every answer should cite
an address `doc_id / segment_id` and printed page ([p. N] markers in segment text).
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
        """Find registry entities by name or alias (as printed in the reports).
        type: agency | reserve | band | person. Returns ranked candidates with
        id, uri, page url, mention count, and a one-line summary."""
        return _dump(store.lookup_entity(name, type, limit))

    @mcp.tool()
    def open_page(ref: str) -> str:
        """Read a wiki page as text: an entity's hub page (infobox, relations,
        cited passages with addresses), a report's contents page, or a segment
        page. ref = canonical URI, page url (/agencies/x/), or typed id
        (agency:AG-x, reserve:x, band:BAND-x, person:PERSON-x)."""
        return _dump(store.open_page(ref))

    @mcp.tool()
    def search(query: str, year_from: Optional[int] = None, year_to: Optional[int] = None,
               kind: Optional[str] = None, agency: Optional[str] = None, limit: int = 10) -> str:
        """BM25 full-text search over report segments. FTS5 syntax: "exact phrase",
        AND / OR / NOT, prefix*. Filters: report year range; kind (agency_letter,
        tabular_statement, thematic_section, return, appendix, presentation_letter);
        agency (id or name). Returns address, heading, year, url, snippet."""
        return _dump(store.search(query, year_from, year_to, kind, agency, limit))

    @mcp.tool()
    def neighbors(ref: str, edge_type: Optional[str] = None, limit: int = 50) -> str:
        """Graph edges of an entity: OCCUPIES (band→reserve), ADMINISTERED_BY
        (reserve→agency), MERGED_INTO / SPLIT_INTO (agency chains, curated),
        SIGNED_FOR (person→agency), SUCCEEDED_BY (band→modern First Nation QID),
        plus MENTIONED_IN: mention counts per report year and top segments."""
        return _dump(store.neighbors(ref, edge_type, limit))

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

    return mcp


def main():
    build_server().run()


if __name__ == "__main__":
    main()
