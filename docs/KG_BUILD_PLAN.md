# Canada50 — DIA Knowledge-Graph Build Plan (v2, 2026-08-30)

Scope: the Department of Indian Affairs annual reports 1880–1930 (`dia_ar_*`),
built out **to completion** before the sessional-papers layer. Trade &
Navigation data is out of scope here (separate session). Everything below
stays local / GitHub-only (no Pages deploy) pending consultation with
Indigenous colleagues (see memory: no-public-html-indigenous).

Division of labour (standing): Qwen on the clusters does bulk extraction —
that part is now essentially finished; every step below is Claude-side
modelling, linking, validation and generation.

## 0. Where we are

| layer | in the graph today | extracted, not yet loaded |
|---|---|---|
| reserves | 1,422 (1902 Schedule) + 5,822 attestations 1897–1902; adjudications | — |
| bands | **236** (thin; residual review found 1,779 mentions naming bands we lack) | census rows name every band, every year: 38 vols / 32K rows |
| agencies | 570 in 280 chains, LINCS-linked | AG Part J sections (agency names per year) |
| persons | 412 minted (signatories) | officers 21 vols / 4.7K rows; AG payees (~200K rows) |
| places | 8,706 | — |
| schools | none | school statements 35 vols / 16K rows; principals' reports (narrative) |
| series | none | census (population, religion, age/sex), agstat 35 vols / 41K rows, Return B trust funds (69K rows incl. Returns), AG Part J |
| mentions | ~130K Tier 0/1/2 | — |

Extraction inventory and cluster provenance: `cluster/FANOUT.md`; result dirs
`eval/results/<family><year>_qwen38_medium/`.

## 1. Identity model decisions

### 1.1 Bands are the hub
The band registry is rebuilt **from the census series** —
the department's own annual list of bands per agency — not from mentions.

- **Band identity key**: (normalised name, agency chain, era). A band record
  persists across years while name-normalised + agency match; renames and
  agency transfers are recorded as `band_event` rows (RENAMED, TRANSFERRED,
  MERGED_INTO, SPLIT_FROM, ABSENT_AFTER) exactly like `agency_chain_events`.
- **Name normalisation**: strip possessives ("Beardy's" → "Beardy"), treaty
  numbers in parentheses, "band of"/"Indians of" wrappers, OCR variants by
  char-similarity within the same agency-year (reuse `link_schedule_editions.sim`).
- **Modern anchor** (never `sameAs`, always *succeeded-by*): Wikidata First
  Nation band government items — 627 exist, **611 carry the ISC band number
  (P2865)**; ISC "First Nations Location" gives the same 638 band numbers with
  names + coordinates. Historical band → modern band government is a
  reviewed crosswalk (`registries/crosswalks/band_modern.csv`), seeded by
  (a) 1902-Schedule reserve → modern reserve (below), (b) name similarity
  within province, (c) the LAC "Index of Bands in Western Canada" (~1,500
  names from FA 10-12, 1871–1959, with agencies — see §4).
- **Reserves → modern reserves**: Wikidata has 3,489 Indian-reserve items
  (3,026 with GeoNames, 3,397 with coordinates, 935 with StatCan geo code,
  263 with reserve number P2887); NRCan Aboriginal Lands has every current
  reserve polygon with the official name+number ("STAR BLANKET I.R. 83D")
  and CLSS code. Reserve-number + province is a strong key for prairie/BC
  reserves; eastern reserves link by name. Output: `reserve_modern.csv`.

### 1.2 Schools
- Entity: `school` = (name, type ∈ {day, boarding, industrial, residential},
  reserve/agency, denomination). Identity across years by (normalised name,
  agency); type changes and renames are events.
- Modern anchors: **Schedule K** (699 federal day schools with name variants,
  open/close dates, location, denomination — downloaded, `registries/external/schools/`)
  and the **NCTR/IRSSA list** (140 residential schools; per-school pages carry
  dates and denominations). Link by name variant + province + date overlap;
  never assume a pre-1930 day school is the Schedule K school of the same
  name without date/location agreement (Schedule K dates mostly start 1920s–50s).
- Teachers (school rows) and principals (narrative reports) → persons
  (§1.3), attested with year + school.

### 1.3 Persons — **LINCS is the identity authority for DIA staff**
Revised 2026-08-30 once the provenance chain became clear:

    DIA annual reports (Return A) → Ben Hoy's `CAN_DIA_Employees_Checked_
    Complete.xlsx` (14,477 person-YEAR rows, human-checked, geocoded, with
    page + bac-lac URL, but **no person id across years**) → LINCS Indian
    Affairs dataset (14,138 activities resolved to **2,468 person URIs**,
    mean career 5.6 yrs, longest 41: M. Benson 1876–1916)

The user's team did the career-linking that Hoy's rows lack, and it also
*splits* 21 ambiguous names into distinct people (A. McDonald ×2, D.
McDonald ×2 — the "John Macdonald" problem, already solved here). So:

- **Do not mint our own persons for DIA staff.** Use the LINCS `agent` URI
  as the person identity; our officers rows and Hoy's spreadsheet are the
  underlying attestations.
- Our extraction contributes what the LINCS activity model does not carry:
  per-year **designation, salary, appointment date, first-civil-service
  date**, plus our own page-level provenance.
- Join: our officer row → LINCS agent on (normalised name, year). A crude
  surname+initials key already reaches **67%** (3,926/5,881 rows, 1,152 of
  2,468 persons). The residue is name-form, not data: "John O. Arsennault"
  vs "John Arsenault", "Wm. Plummer" vs "William Plummer", and compound
  surnames my key mis-parses ("McGregor Ironsides", "Wm. Van Abbott").
  A proper matcher — surname = last token, expand Wm./Thos./Jas./Chas.,
  fuzzy on surname, require place or year agreement — should exceed 90%.
- Persons *not* in LINCS (teachers from school statements, AG Part J payees,
  letter signatories) are minted by us as before, and checked against LINCS
  before minting.
- AG Part J payees: mostly firms ("Hull Bros.", "Stovel & Strang") and
  local persons; **mint, don't ground**, except where the payee is already
  an officer/teacher/chief in the registry. Deferred until the
  sessional-papers mention-detection design exists (same person/firm/
  commodity problem; see `docs/SESSIONAL_PARSING.md`).
- Indigenous individuals named in ledgers (chiefs, pensioners in Return B,
  commutation lists) are **not** minted as persons in this phase — flagged
  for the consultation.

### 1.4 Series (observations)
One `observation` table, not per-family schemas:
`(entity_id, entity_type, series_id, year, value, unit, source_family,
paper_id, page, chunk, row_idx, confidence)`. Series ids come from the
column-semantics maps (§2). Wiki pages render series as tables/sparklines;
the MCP exposes `series(entity, series_id)`.

**Not every family is keyed by band** (corrected 2026-08-30 against the data).
The census is, and the trust funds are. The **Agricultural & Industrial
Statistics are keyed by *agency*** in every era — 1897 "Walpole Island",
1905 "Grand River Superintendency — Six Nations", 1914-30 "Blackfoot",
"Hobbema", "Lesser Slave Lake" — so agstat observations attach to the agency
chain, not to a band, and a band page shows its agency's economic series as
context rather than as its own. School statements are keyed by school.

## 2. Column-semantics maps (written by hand, one per format era)

| family | eras | notes |
|---|---|---|
| census | 1881–82 (No. 4), 1887–92 (No. 3: census + denominations), 1895–96 wide (census + agricultural cols), 1897–1917 (No. 4/1: population, religion, age/sex, births/deaths), 1918–30 recap only (by inspectorate/province) + 1924/1929 band level | `headers` rows in the output give per-chunk column names; the map assigns `series_id` per column label pattern |
| agstat | 1895–1913 wide table (column groups printed on successive pages → per-page header reconstruction), 1914–16 Tables 2–7, 1917–30 Tables 2–6 | 14 header variants seen in 1900 alone; map by table number + normalised header |
| school | 1896–1930 (1902 rough OCR) | identity cols already structured; values = roll/attendance/standards |
| Return B | 1884–97 | **not** from the Qwen rows — the deterministic two-sided parser (§3.2) |
| AG Part J | 1898–1930 | amount normalisation (dollars/cents split), vote/section headings → `expenditure_category` |

Deliverable: `curation/column_maps/<family>.yaml` + `build/apply_column_maps.py`
that emits observations and a per-family coverage report (columns unmapped,
rows dropped, per-year counts).

**Built 2026-08-30 for the census.** The per-era split turned out to be
unnecessary: the extractor emits a `headers` row of printed labels and data
rows whose `values` are positionally aligned to them, so the map is a list of
*label → series* rules, with an optional `years:` window for the few labels
that changed meaning. `curation/column_maps/census.yaml` (31 series) →
**139,681 observations, 3,068 bands, 1881–1929**, 8 unmapped labels left (556
occurrences, mostly the 1902 age columns that lost their male/female spanner).
Rule ids may interpolate regex named groups (`crop_{crop}_acres_sown`) so the
agstat table's ~1,270 labels can be covered by a few dozen shape rules.

Validation is the census's own redundancy: it prints the population and then
breaks it down twice, by religion and by age/sex. The two breakdowns sum to
the printed population within 2% in **84% / 85%** of band-years; the rest go to
`observations_census_checks.csv` (usually a provincial total line read as a
band, or a run-together OCR digit).

**Built 2026-08-30 for agstat.** `curation/column_maps/agstat.yaml` (34 rules,
which expand to **542 series**) → **164,751 observations over 540 agencies,
1895–1930**; 293 labels left unmapped (20,339 cells, ~11%, listed in
`observations_agstat_coverage.csv`). Enumerating the ~1,270 printed labels was
never going to work — the department invented columns yearly and merged old
ones ("Mowers, Reapers, Binders, Threshers, etc.") — so the rules capture the
varying part and keep the printed wording in the series id
(`crop_wheat_acres_sown`, `value_household_effects`, `effects_sail_boats`).
Spot-checks read true: Blackfoot and Blood lead wheat acreage, and Cowichan's
wheat runs a smooth 34-year curve 5 → 53 acres.

The agstat entity had to be built first: `build/mint_agencies_from_agstat.py`
mints an identity for every agency the *series* attests (the letters-derived
registry has only the 280 that filed narrative reports, and misses the Nova
Scotia county agencies, Moravian, Bécancour, Mud Lake, The Pas). **1,102
identities, 110 linked to an existing chain**, name variants clustered by
complete linkage within a province pool exactly as in step 2, and the rows
gated by their governing header — without that gate the lists of chiefs and
band tables beside these pages mint thousands of phantom agencies from person
names.

## 3. Build steps, in order

1. **Snapshot** (user says "commit"): everything since 8dd27e7.
2. **Band registry v2** — `build/mint_bands_from_census.py`: iterate census
   rows by year → (agency, band) → registry with first/last attested year,
   name variants, provinces; band_events for renames/transfers; load the
   1,779 residual-review band mentions as candidates. Validation: every
   census row links to exactly one band; no band with <2 attestations unless
   1930-only. Then `band_modern.csv` seeding (Wikidata P2865 ∪ ISC locations).
3. **Reserve ↔ modern reserve crosswalk** — Wikidata reserves (name+number+
   province) + NRCan AL names; review CSV for ambiguous; attach QID/CLSS code
   as *succeeded-by* on reserve pages. **DONE 2026-08-30.**
   `build/link_reserves_wikidata.py` does the second hop NRCan → Wikidata
   (bulk SPARQL dump in `registries/external/wikidata/reserves_wd.csv`): 2,930
   of 3,317 modern reserves matched, and **1,012 of the 1,032 historical
   reserves now carry a QID** with coordinates, 961 with GeoNames and 420 with
   a StatCan CSD code. On Wikidata P2887 "reserve number" actually holds the
   5-digit CLSS code, which is a direct join to NRCan's ALCODE — 267 such
   identifier matches, and held out as gold they confirm the name-based rules
   on 244 of 248 (the 4 disagreements are duplicate Wikidata items).
   `build/link_bands_modern.py` then carries historical bands to modern band
   governments (ISC band number ∪ Wikidata P2865 ∪ the StatCan band↔CSD
   table): **368 of 3,377 linked, 358 with a QID**, 310 to review. Recall is
   low by design — a *succeeded-by* claim about a First Nation is worth less
   than the review queue it skipped, so name similarity is symmetric (Dice,
   with a length-ratio guard) rather than the containment score the reserve
   linker uses, which had scored "Halalt" 1.00 against "Haltkum (Adams Lake)".
4. **Column maps + observations** — census, agstat and school **DONE** (see
   §2); recap years attach to inspectorates/provinces. Still to do: Return B,
   after §3.7.
5. **Schools facet** — **registry and series DONE 2026-08-30**;
   Schedule K / NCTR anchors, teachers → persons and the principals' narrative
   reports (1896–1910) still to do. `build/mint_schools.py` →
   `registries/entities/schools.parquet`: **1,115 school identities**
   (795 day, 89 boarding, 89 industrial, 142 untyped), 12,036 attestations,
   2,544 events (TYPE_CHANGED, NAME_VARIANTS, ATTESTATION_GAP), 331 to review.
   `curation/column_maps/school.yaml` → **83,781 observations over 928
   schools**: roll by sex, average attendance, the standards ladder, the grant,
   and the trades taught in the industrial schools. Three things the identity
   depends on, all found by the numbers coming out wrong:
   - the same **header gate** the other families need — without it the registry
     mints schools out of teachers ("Watson, A.M., M.D."), farmers and bands;
   - **the school number is the identity**, not noise: normalising "No. 10"
     away fused all twelve Six Nations day schools into one whose roll was the
     average of the lot (fractional values in the series gave it away);
   - a **blank province must be filled from the same school in another year**,
     or the printer's omission spawns a second identity (this had the Mohawk
     Institute and Shingwauk Home each split in two).
   Spot-checks read true: Kuper Island, Kamloops, Kootenay, Battleford, Mohawk
   Institute, Shingwauk, Coqualeetza, Elkhorn all present with plausible spans
   and denominations; median day-school roll 27 pupils.
6. **Persons v2** — **officers → LINCS DONE 2026-08-31.**
   `build/link_officers_lincs.py` attaches a LINCS agent URI to each Return A
   row: **5,535 of 6,083 rows (91.0%)** across 21 years, reaching **1,361 of
   the 2,468** LINCS persons; the rows carry the per-year designation, salary,
   appointment and first-civil-service dates the LINCS activity model lacks,
   plus page provenance → `registries/annotations/officer_attestations.parquet`
   (`officer_rows_foreign.parquet` holds the 109 non-Return-A rows from the
   seven stray Auditor-General/Returns C–G chunks, kept but not attested as
   officers). Crosswalk `officers_lincs.csv`, review queue
   `officers_lincs_review.csv` (528 name-years: 396 below gate, 82 no
   candidate, 32 ambiguous, 13 demoted conflicts, 5 unparseable).

   **Acceptance: `eval/compare_officers_lincs.py` — precision 0.99, recall
   0.99** on LINCS agent sets per year, against Hoy's rows matched to LINCS by
   the same matcher but from an independent transcription. The only soft years
   are 1899 (rec 0.91) and 1904 (0.97) — the same two `compare_officers_hoy.py`
   flags, so this is the known OCR ceiling, not a linking fault.

   The matcher is `build/person_names.py`, shared by later person work. It does
   not reduce a name to one key: `parse` returns *surname variants* plus given
   initials, and `score` weighs them separately (0.65 surname / 0.35 initials,
   surname floor 0.87). Four rules each fixed a wrong link, and calibrating on
   Hoy→LINCS — where near-100% is the known answer — took it from 61.7% to
   **98.0%**: strip post-nominals *before* reading a comma as `Surname, Given`
   (else "Peter E. Jones, M.D." has the surname "peterejones"); rejoin a
   trailing run of single letters, because `M.D.` tokenises to `m`,`d` and left
   "J. Macdonald, M.D." matching *M.A.* McDonald; keep particles with the
   surname ("Van Abbott", "D'Amour"); and compare full given names, since
   "Agnes Cameron" and "Angus Cameron" tie on the initial alone. A consonant-
   skeleton blocking key catches "McNiell"/"McNeill", which a prefix key misses
   because "Mc" eats the prefix.

   Still to do for step 6: teachers from the school statements and letter
   signatories (mint, after checking against LINCS), and reconciling the
   `officers_lincs_review.csv` queue. Note 1904 and 1910 rows carry no
   `paper_id` — those volumes are the standing `pending_catalog_post1900`
   cases, not a gap introduced here.
7. **Return B parser** — column-split at the CR boundary, Dr/Cr entries,
7. **Return B parser** — column-split at the CR boundary, Dr/Cr entries,
   balance check (Dr total = Cr total per account); failures → Qwen on the
   halves → still checked; emit trust-fund balance series per band.
8. **Wiki + MCP regeneration** — **first pass DONE 2026-08-30**: the wiki now
   builds **10,379 pages** (was 9,263) and `index.json` carries 3,493 entities
   (was 2,378). `build/series_render.py` renders `observations.parquet` as
   grouped tables with an inline-SVG sparkline for the headline series (no
   scripts, no external assets — the site is static files), and `gen_wiki.py`
   calls it on band, agency and the new school pages: **1,165 pages now carry a
   series block** (928 schools, 134 bands, 103 agencies) and 858 a sparkline.
   The new `schools/` facet is 1,116 pages plus an index. Additive throughout —
   no entity id changed, so mentions and passages render exactly as before.
   Still to do: person pages over the officers rows (step 6 first), the agstat
   agencies that have no chain page yet, MCP `series(entity, series_id)`, and
   the regenerated validation-gate packet.

   **Prerequisite done 2026-08-30.** `build/gen_wiki.py` builds band pages from
   `bands.parquet` — the 236 bands of the 1902 Schedule, which carry the
   hand-adjudicated Wikidata grounding but none of the series, since the
   observations are keyed by the 3,377-identity census registry. §1.1 makes the
   census registry canonical, so `build/merge_band_registries.py` moves the
   curated grounding onto it: **146 of 236 curated bands matched, 125 census
   bands now carry an adjudicated Wikidata succession**, 358 a modern band
   government, **433 at least one anchor** →
   `registries/entities/bands_census_grounded.parquet`. Every unmatched curated
   band is reported in `band_registry_merge_review.csv` — 62 still hold a QID
   that has not landed, almost all because the census registry holds two
   near-identical variants of the same band ("Opet-ches-aht" and
   "Opitchisaht" tie at 0.80) and those two should be merged first.
   Matching keeps the parenthetical ("Nicola (Lower)" vs "(Upper)" are
   different bands and stripping it made them tie), closes the printer's
   hyphens ("Nim-keesh" → Nimkeesh), and accepts a sub-threshold winner only
   when it stands ≥0.10 clear of the runner-up, recording
   `curated_match_score` on the row so the claim stays auditable.
9. **AG Part J payee layer** — after the sessional mention-detection design.

Each step ends with a review CSV for the historian and a coverage report;
nothing auto-applies below the confidence gate.

## 4. External datasets (found 2026-08-30)

| dataset | what it gives | status |
|---|---|---|
| ISC First Nations Location (open.canada.ca b6567c5c…) | 638 bands: band number, name, coordinates; daily | downloaded → `registries/external/isc/` |
| Wikidata band governments (Q2882257) | 627 items, 611 with P2865 band number, 582 with coords | query via WikidataMCP |
| Wikidata Indian reserves (Q155239) | 3,489 items; GeoNames 3,026; coords 3,397; StatCan code 935; reserve no. 263 | query via WikidataMCP |
| NRCan Aboriginal Lands of Canada (GeoBase AL) | every current reserve polygon: name+number, CLSS code, type | SK sample kept; full 55 MB zip on demand |
| StatCan Aboriginal Population Profile — band ↔ CSD table (2016) | First Nation/band → census subdivision (IRI) codes (~1,000 rows) | web table only; scrape if needed (P3012 on Wikidata covers 935 reserves) |
| Schedule K — Federal Indian Day Schools | 699 schools, variants, dates, location, denomination | downloaded → `registries/external/schools/` (PDF + text; needs a multi-line row parser) |
| NCTR / IRSSA residential schools | 140 schools; per-school pages with dates, denominations | scrape per-region pages when building the schools facet |
| LAC "Indexes of Western First Nations Bands: languages, agencies, inspectorates, regional offices" (FA 10-12, ~1,500 names, 1871–1959) | historical band ↔ agency ↔ inspectorate crosswalk — the closest thing to our agency chains, from LAC | canada.ca blocks scripted fetches; **user to save the page as HTML** into `registries/external/lac/` |
| LAC "Indian Affairs Annual Reports 1864–1990" (40,700 pp, transcribed) | a second text witness for every DIA AR incl. the unusable 1922 volume; item-level records (`app=indaffannrep&IdNumber=`) | bot-blocked; check whether bulk text is obtainable (user login / request) |
| LAC treaty annuity paylists (RG10; Héritage reels) | per-band, per-year paylists 1870s–1950s: band membership counts and names | images only; out of scope now; note for the consultation |
| Canadian Geographical Names DB (P821 on Wikidata) | reserve/settlement place names | via Wikidata |

## 5. Open questions for the historian
- Which modern identifier is the canonical "successor" link on a band page:
  Wikidata QID (with P2865) or the ISC band number? (Proposal: store both;
  display the First Nation's current name.)
- Treatment of named Indigenous individuals in ledgers/paylists (deferred;
  consultation).
- Whether 1918–23/1927/1930 recap-only years should appear on band pages as
  "no band-level census printed" rather than silently missing.
