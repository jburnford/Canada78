# Edition URL Scheme & Repo Split (Phase 0 decision)

_Decided 2026-08-24. URLs are immutable once a page is published; everything
here is chosen so that catalog growth, re-OCR, and re-segmentation never move
a published URL._

## Canonical addresses vs URLs

The registry-level address of any passage is `(doc_id, segment_id)` —
URL-independent. Public URLs are a *rendering* of that address; the graph and
all annotations store addresses, never URLs. A machine-readable
`editions.json` on the wiki site maps address → URL, so tooling (the MCP
server, wiki link generation) always resolves through the map.

## Sites

| site | content | repo |
|---|---|---|
| `jimclifford.ca/canada50/` | wiki: entity + document hub pages, indexes, editions.json | `canada50-site` |
| `jimclifford.ca/sp-1867-1884/` | full-text edition, sessions 1867–1884 | `sp-1867-1884` |
| `jimclifford.ca/sp-1885-1900/` | full-text edition, sessions 1885–1900 | `sp-1885-1900` |
| `jimclifford.ca/sp-1901-1915/` | full-text edition, sessions 1901–1915 | `sp-1901-1915` |
| `jimclifford.ca/sp-1916-1931/` | full-text edition, sessions 1916–1931 | `sp-1916-1931` |

Sizing basis: 712 MB markdown for 235 volumes ≈ 3 MB/volume → ~1.1 GB for
372 volumes; four fixed ranges keep each site ≈ 300–450 MB after HTML
rendering, well under the ~1 GB/site soft limit, with headroom for the
larger 20th-century sessions. Ranges are **fixed now and never rebalanced**;
if a range outgrows its repo, the overflow argument is wrong, not the URLs —
solve with asset trimming, never by moving pages.

## URL patterns

**Series-based (preferred whenever the paper belongs to a named series):**

```
/{site}/{series-slug}/{issue-year}/                      paper landing page
/{site}/{series-slug}/{issue-year}/{segment_id}/         segment page
e.g. /sp-1885-1900/indian-affairs/1885/p0162-touchwood-hills-agency/
```

`issue-year` is the reporting year the issue covers (DIA 1885 = calendar
1885), not the session year. Series URLs do not depend on catalog coverage —
this is what lets the 31 post-1900 DIA reports publish immediately with
provisional doc ids; when the catalog extends, the registry gains the
paper_id but the URL was already right.

**Session-based (papers not in a series):**

```
/{site}/sessions/{session_year}/{paper_num}/{segment_id}/
e.g. /sp-1885-1900/sessions/1888/15/…
```

Session-based pages for series papers exist only as thin stubs linking to
the series URL (one canonical location per text).

**Which site hosts a series issue** is determined by its presenting session
(report year + 1 for annual reports); for provisional docs, by the projected
session. DIA 1880–1883 land in `sp-1867-1884`; DIA 1884–1899 in
`sp-1885-1900`; etc.

## Segment IDs

Permanent, content-anchored, human-legible:

```
p{page_start:04d}-{heading-slug ≤ 30 chars}[-2, -3 … on collision]
e.g. p0162-touchwood-hills-agency, p0003-contents, p0410-no-2-agricultural
```

Anchored on printed page + heading so that re-segmentation tweaks (moving a
boundary a few lines) don't renumber neighbours, unlike ordinal schemes.
IDs freeze at first publication of the segment; a re-segmentation that
splits a published segment mints new ids for the children and keeps the
parent id as an alias.

## Text versioning on pages

The URL always serves the current best text. Each segment page displays its
`text_version` hash and witness/engine (Chandra 2, Infinity-reconciled,
alternative scan); superseded versions are not hosted as pages but remain in
the data releases. Stand-off annotations bind to `(segment_id,
text_version)` per `registries/SCHEMAS.md`.
