# Identity-Model Workshop: band ≠ reserve ≠ agency

_2026-08-24. Phase 1 step 3. The proposed model below is worked against real
extracted data; the **Decisions** section lists what needs the historian's
call before mention linking runs. Sources: DIA segment headings (1,800
agency attestations), the 1902 Schedule of Indian Reserves, and the LINCS
Indian Affairs Agents graph (119 E74 groups, 604 GeoNames places, 14,138
dated postings — cached in `registries/external/`)._

## Proposed entity types and relations

| type | what it is | CIDOC shape | grounding source |
|---|---|---|---|
| **Band** (First Nation) | a people/community; persists through moves | E74 Group | Wikidata (modern First Nations pages), LINCS groups |
| **Reserve** | a legal land parcel | E53 Place | 1902 Schedule (name, no., agency, acreage), Wikidata where covered |
| **Agency / Superintendency / Inspectorate** | an administrative unit of DIA; renames/splits/merges | E74 Group with a **persistent chain** (hgiscanada pattern) | LINCS agency groups (81 already name-matched: `registries/crosswalks/agency_lincs.csv`) |
| **Agency headquarters** | the place an agency operates from | E53 Place | LINCS `P7_took_place_at` → GeoNames |
| **Treaty** | numbered treaty; groups bands and territory | E7/E5 event + E74 signatory group | Wikidata (well covered) |

Relations: band `OCCUPIES` reserve (time-scoped, n:m); reserve
`ADMINISTERED_BY` agency (time-scoped); agency `PART_OF_CHAIN` persistent
agency chain; chain events `RENAMED` / `SPLIT_INTO` / `MERGED_INTO` /
`TRANSFERRED_TO`; band `ADHERED_TO` treaty; person (agent) postings come
from LINCS activities (agent → agency group + GeoNames place + dates).

**Time-scoping rule** (from the EB plan's temporal rule): every relation
attaches to a dated attestation (report year, schedule edition, LINCS
posting), never to the entity "timelessly."

## Real hard cases (from the extracted data)

**1. Merge–unmerge with orthographic chaos — Kamloops/Okanagan, BC.**
Separate agencies 1881–85 → merged ~1889–1910 under 8+ spellings
("KAMLOOPS - OKANAGAN", "KAMLOOPS AND OKANAGON", "KAMLOOPS-OKANAGAN INDIAN
AGENCY") → separate again by 1907 ("KAMLOOPS AGENCY" and "OKANAGAN AGENCY"
both attested to 1915). The chain model must represent: two chains, a
merged period, and the successor chains' relation to the merged one.

**2. Rename with textual evidence — Indian Head → File Hills, Treaty 4.**
"INDIAN HEAD AGENCY" attested 1890; "FILE HILLS AGENCY" 1894–1910; and the
1885 report narrates the transition ("the farm instructor at File Hills was
appointed acting agent"). Also shows the "ASSINIBOIA - FILE HILLS" district
prefixing of 1896–1900 — same agency, name decorated with the district.

**3. Unit-type distinction — Qu'Appelle.** "QU'APPELLE AGENCY" (1901–1915)
and "QU'APPELLE INSPECTORATE" (1898–1905) coexist: different unit types,
same toponym, overlapping years. Name matching alone would conflate them;
unit_type is part of identity.

**4. Normalization load — Northern Superintendency, Ontario.** 20 heading
variants for 4 divisions ("2ND DIVISION" / "DIVISION NO. 2" / "DIVISION
NO.L" (OCR) …). Chain-building needs a variant-collapsing pass before any
matching; 346 of 570 normalized names are single-year attestations, mostly
this kind of variant.

**5. What counts as a "group" — LINCS mixes kinds.** LINCS E74 groups
include agencies, residential schools ("Battleford Industrial School"), and
bands ("Chippewas of Rama"). Useful — it confirms band-as-E74-group — but
the crosswalk must type LINCS groups before merging, not treat them all as
agencies.

**6. Name collisions across types — "Duck Lake".** An agency (LINCS),
a town (GeoNames, agency HQ), and nearby reserves. Same toponym, three
entity types; mention linking needs the type context, which is why reserve
and band mentions can't just match against one flat gazetteer.

**7. Reserve numbering is band-relative (1902 Schedule).** Maritime/Ontario
sections list reserves by county with band ("Micmac") as an attribute;
western sections list by agency with numbered reserves tied to bands
(e.g. Pasquah No. 79). One band ↔ many reserves and one reserve ↔ many
bands both occur. OCCUPIES must be n:m or the West won't fit.

## Decisions — RESOLVED 2026-08-24 (user-confirmed)

1. **Bands**: mint historical band entities as attested; chain to modern
   First Nation QIDs via succession/sameAs edges.
2. **Agency merges**: a merged period is its own chain — MERGED_INTO from
   predecessors, SPLIT_INTO to successors (hgiscanada CD lineage semantics).
3. **Unit types**: one agency facet, `unit_type` distinguishes
   agency/superintendency/inspectorate (proposal accepted implicitly).
4. **Reserves**: mint-first from the 1902 Schedule as authority list;
   Wikidata sameAs overlay via MCP search.
5. **1902 Schedule**: LLM extraction pass (first structured-extraction job),
   validated against acreage/count totals.

## Decision questions as originally posed

1. **Band identity across time**: modern First Nations QIDs are
   descendants of 1880s bands after amalgamations/splits. Ground historical
   bands to modern QIDs directly (simple, slightly anachronistic), or mint
   historical band entities chained to modern QIDs (faithful, more work)?
2. **Agency chain granularity**: is the merged "Kamloops–Okanagan" period
   one chain that later splits, or two chains sharing a merged interval?
   (hgiscanada SPLIT_FROM/MERGED_INTO handles either; pick one convention.)
3. **Superintendencies and inspectorates**: same registry facet as agencies
   with `unit_type`, or separate facets? (Proposal: same facet, typed —
   they're all DIA administrative units; case 3 shows type must be primary.)
4. **Reserve grounding target**: Wikidata coverage of reserves is patchy.
   Mint URIs for all reserves from the 1902 Schedule and link to Wikidata
   where found, or attempt Wikidata-first? (Proposal: mint-first from the
   Schedule as the authority list; Wikidata as sameAs overlay.)
5. **The 1902 Schedule parse**: its layout-flattened tables resist clean
   deterministic parsing. Accept a heuristic parse now (noisy but useful
   for the workshop-scale registry), or make it the first LLM-extraction
   job (batch on Nibi, per the architecture's enrichment-pass design)?

## Ready inputs

- `registries/entities/agencies.parquet` — 570 normalized names, spans.
- `registries/entities/agencies_attested.parquet` — 1,800 (name, year)
  attestations with segment ids.
- `registries/crosswalks/agency_lincs.csv` — 81 name-matches to LINCS
  agency URIs (with GeoNames HQ places derivable from postings).
- `registries/external/lincs_ia_activities_dedup.parquet` — 14,138 dated
  agent postings, 14,087 with GeoNames places.
