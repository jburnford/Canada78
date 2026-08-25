# Wiki generation v1 (DIA corpus, local evaluation build)

_2026-08-24. Deterministic skeleton per DESIGN.md "The wiki layer" and the
EB PLAN_GRAPHRAG Phase 3 pattern: pure code over registry exports, no LLM
overlays yet. **Local build only** — nothing under `site/` is deployed
(publication constraint, DESIGN.md)._

```
python3 build/gen_wiki.py             # full corpus → site/  (~1 min)
python3 build/gen_wiki.py --sample    # gate volumes only (1885/1900/1913/1925)
python3 -m http.server -d site 8000   # browse at http://localhost:8000/
```

## Pages

| path | one per | content |
|---|---|---|
| `agencies/{chain}/` | agency chain (`agency_chains.parquet`) | type, attestation span, LINCS URI; names-as-printed table (chain members); curated chain events (MERGED_INTO/SPLIT_INTO from `curation/agency_chain_events.csv`); the agency's own letters by year; officers who signed for it; reserves it administered (1902 Schedule); cited passages |
| `reserves/{reserve_id}/` | 1902-Schedule reserve | infobox (band, division, province, location, acres, remarks, Schedule page + extraction confidence); cited passages |
| `bands/{band}/` | historical band | provinces, 1902 divisions, reserve count/acreage, **succeeded-by** modern First Nation (Wikidata) — never sameAs; reserves occupied; cited passages |
| `persons/{slug}/` | signatory | minted (`persons_minted.parquet`) or LINCS-matched (`lincs-{id}`, with LINCS postings table); names as printed; reports signed/mentioned |
| `reports/{year}/` | annual-report issue | doc_id / Sessional Paper no. / session; Canadiana volume id (`oocihm.9_08052_{seq}_{vol}`, linked when present in the corpus id list); segment table of contents with agency + mention counts |
| `reports/{year}/{segment_id}/` | segment | full text with page markers as anchors (`#p171`); high/medium mentions rendered as inline links (dotted underline; amber = medium), low as highlighted spans; entity list; prev/next navigation; address + text_version |
| `{facet}/` | facet index | agencies (by name, with region from the Schedule), reserves (province › division), bands (with succession), persons, reports |
| `index.json` | — | canonical URI → `{type, id, name, url}` for every entity page (for `canada50-mcp` `lookup_entity`/`open_page`) |
| `editions.json` | — | `doc_id → {url, segments: {segment_id → url}}` (address → URL map, docs/URL_SCHEME.md) |

URLs are root-relative under `--base` (default empty). Segment URLs use the
report *year* (`reports/1885/p0170-…`), matching the series-first scheme; the
production edition path (`/sp-1885-1900/indian-affairs/1885/…`) is a
rendering of the same `(doc_id, segment_id)` address via `editions.json`.

## Gate decisions as applied

1. **Publication threshold** — high + medium mentions are the primary
   "Mentioned in the annual reports" section (max `--max-passages` per
   report year, overflow linked by segment); low-confidence mentions render
   only in an "Unverified mentions" `<details>` fold and are not linked
   inline in segment text.
2. **Table bare-names** — kept; each passage is labelled with the segment
   kind (`table`, `agency letter`, …) so table hits are distinguishable.
3. **Region** — not shown from segmenter province hints; agency index region
   comes from the 1902 Schedule provinces of the reserves a chain
   administered.
4. **Minted persons** — get pages, flagged "Minted from report signatures …
   no LINCS match".

## Known limitations (v1)

- Passage snippets are ±160 chars around the mention, whitespace-collapsed;
  printed page is computed from page markers preceding the mention.
- LINCS person labels are surname-only in places (e.g. `Mackay`), and one
  LINCS agent can absorb several signature surfaces (A. Mackay / J.W.
  Mackay) — the "Names in reports" row exposes this for review.
- 246 Schedule rows have no band (234 are treaty-section reserves, where the
  reserve name is the band); unnamed rows render as "{band} reserve No. n".
- No treaty pages, no place pages (HGIS), no Pagefind index yet.
- Full build size ≈ 0.5 GB HTML (segment pages carry full text).
